# -*- coding: utf-8 -*-
"""API-05：POST /api/v1/locations/estimate（docs/24 §8）。

- 客户端地图版本落后于最新发布 → 409 MAP_VERSION_CONFLICT（触发客户端刷新）；
- 未知版本 → 404 MAP_NOT_PUBLISHED；
- sequence 严格小于已处理值 → 409 STALE_LOCATION_BATCH；
- IMU 仅回显 heading（第一阶段不解算惯性）。
"""
from __future__ import annotations

import logging
import time
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Request
from sqlalchemy import case
from sqlalchemy.orm import Session

from pathai_backend import auth, fingerprint_library
from pathai_backend.config import Settings
from pathai_backend.errors import ApiError
from pathai_backend.location_solver import (
    ROBUST_RSSI_VERSION,
    BeaconInput,
    LocationParams,
    estimate_location,
)
from pathai_backend.map_repository import MapRepository
from pathai_backend.models import LocationSequence
from pathai_backend.observation_time import (
    ADVISORY_STALE_AGE_S,
    is_sane,
    observation_age_s,
)
from pathai_backend.routers.map import get_map_repository
from pathai_backend.routers.field import SITE as FIELD_SITE, frozen_config, frozen_location_params
from pathai_backend.schemas.common import GeoPosition
from pathai_backend.schemas.location import (
    FingerprintMatchInfo,
    LocationEstimateRequest,
    PositionEstimateResponse,
)
from pathai_backend.surroundings import nearby_surroundings

router = APIRouter(prefix="/api/v1/locations", tags=["location"])
logger = logging.getLogger(__name__)

#: 指纹匹配置信度 ≥ 该值时按 reliable 返回，否则 degraded（两者都带坐标）。
FINGERPRINT_RELIABLE_CONFIDENCE = 0.75


def _nearest_area(
    repo_pkg, x: float, y: float, floor_id: str | None
):
    """最近地点（同一楼层内按拓扑节点坐标欧氏距离）。"""
    if floor_id is None:
        return None
    floor = repo_pkg.floors.get(floor_id)
    if floor is None:
        return None
    coords = {n.id: n.coordinates for n in floor.topology.nodes}
    best = None
    best_dist = float("inf")
    for place in repo_pkg.places:
        if place.floorId != floor_id:
            continue
        node_xy = coords.get(place.nodeId)
        if node_xy is None:
            continue
        dist = ((node_xy[0] - x) ** 2 + (node_xy[1] - y) ** 2) ** 0.5
        if dist < best_dist:
            best_dist = dist
            best = place
    return best


def _check_sequence(
    db: Session, subject_id: int, site_id: str, sequence: int
) -> None:
    row = db.get(LocationSequence, {"subject_id": subject_id, "site_id": site_id})
    if row is not None and sequence < row.last_sequence:
        raise ApiError(
            409,
            "STALE_LOCATION_BATCH",
            f"定位批次已过期：sequence={sequence} < {row.last_sequence}",
            details={"lastSequence": row.last_sequence},
        )


def _advance_sequence(
    db: Session, subject_id: int, site_id: str, sequence: int
) -> None:
    # A single database statement owns both initial insertion and monotonic advance.
    # Reading a row before writing cannot guarantee this under concurrent requests.
    dialect = db.get_bind().dialect.name
    if dialect == "sqlite":
        from sqlalchemy.dialects.sqlite import insert
    elif dialect == "postgresql":
        from sqlalchemy.dialects.postgresql import insert
    else:
        raise RuntimeError(f"Unsupported location sequence dialect: {dialect}")
    table = LocationSequence.__table__
    statement = insert(table).values(
        subject_id=subject_id, site_id=site_id, last_sequence=sequence,
        updated_at_epoch=int(time.time()),
    )
    newer = statement.excluded.last_sequence > table.c.last_sequence
    statement = statement.on_conflict_do_update(
        index_elements=[table.c.subject_id, table.c.site_id],
        set_={
            "last_sequence": case((newer, statement.excluded.last_sequence), else_=table.c.last_sequence),
            "updated_at_epoch": case((newer, statement.excluded.updated_at_epoch), else_=table.c.updated_at_epoch),
        },
    )
    db.execute(statement)
    db.commit()
    # Authentication/check_sequence may already have loaded this row into the identity map.
    db.expire_all()


@router.post("/estimate", response_model=PositionEstimateResponse)
def estimate(
    body: LocationEstimateRequest,
    request: Request,
    user: auth.AuthenticatedUser = Depends(auth.get_current_user),
    db: Session = Depends(auth.get_db_session),
    repo: MapRepository = Depends(get_map_repository),
    settings: Settings = Depends(auth.get_settings),
) -> PositionEstimateResponse:
    now = datetime.now(timezone.utc)
    request_id = request.state.request_id
    # 只拦截明显非法的时钟数据；收藏是否够新由客户端判断，服务端回带观测龄作提示。
    invalid_beacon_times = sum(not is_sane(beacon.observedAt, now) for beacon in body.beacons)
    collected_age_s = observation_age_s(body.collectedAt, now)
    if not is_sane(body.collectedAt, now) or invalid_beacon_times:
        logger.warning(
            "location_rejected request_id=%s sequence=%s map_version=%s reason=observation_time_invalid "
            "observation_age_s=%s invalid_beacon_times=%s submitted=%s",
            request_id, body.sequence, body.mapVersion, collected_age_s, invalid_beacon_times, len(body.beacons),
        )
        raise ApiError(422, 'LOCATION_UNRELIABLE', '定位观测时间戳不可用，请校准设备时间后重新采集', details={'reason': 'observation_time_invalid'})
    if not repo.is_known_site(body.siteId):
        raise ApiError(404, "SITE_NOT_FOUND", f"场所不存在：{body.siteId}")
    latest = repo.latest(body.siteId)
    published = (
        latest
        if body.mapVersion == "latest"
        else repo.get(body.siteId, body.mapVersion)
    )
    if published is None:
        raise ApiError(
            404, "MAP_NOT_PUBLISHED", f"地图版本未发布：{body.siteId}@{body.mapVersion}"
        )
    # Explicit retained versions remain valid; only "latest" follows publication.


    _check_sequence(db, user.subject_id, body.siteId, body.sequence)

    params = LocationParams(
        rssi_at_1m=settings.location_rssi_at_1m,
        path_loss_n=settings.location_path_loss_n,
        residual_reliable_m=settings.location_residual_reliable_m,
        residual_degraded_m=settings.location_residual_degraded_m,
        params_version=settings.location_params_version,
    )
    config = None
    if body.experimentConfigId or body.experimentMethod:
        if not body.experimentConfigId or not body.experimentMethod or body.siteId != FIELD_SITE:
            raise ApiError(422, "INVALID_FIELD_CONFIG", "需要指定冻结配置和实验方法")
        config = frozen_config(db, body.experimentConfigId, published.map_version)
        params = frozen_location_params(config, body.experimentMethod)

    # 指纹库优先：命中阈值（重叠/RMSE/置信度/与次优差距）由服务层判定；
    # 未启用开关时完全不触达指纹库，行为与旧版一致。
    fp_match: fingerprint_library.FingerprintMatch | None = None
    estimate = None
    fallback_reason: str | None = None
    if body.experimentMethod == "C" or (config is None and body.useFingerprintDb):
        try:
            fp_match, fallback_reason = fingerprint_library.query(
                db,
                pkg=published.pkg,
                beacons=body.beacons,
                site_id=body.siteId,
                floor_hint=body.floorHint,
                frozen=config["C"] if config else None,
                min_margin_db=settings.fingerprint_min_margin_db,
            )
        except Exception:  # 指纹库异常不得影响定位主链路：降级三点定位
            logger.exception("指纹库查询失败，降级为三点定位")
            fp_match, fallback_reason = None, "library_unavailable"

    # 冻结实验保持配置指定的算法，日常定位可通过客户端设置切换 BLE 解算。
    robust_rssi = config is None and body.bleAlgorithm == "robust_rssi"
    if fp_match is not None:
        position = GeoPosition(x=fp_match.x, y=fp_match.y, floorId=fp_match.floor_id)
        confidence = fp_match.confidence
        quality = (
            "reliable"
            if fp_match.confidence >= FINGERPRINT_RELIABLE_CONFIDENCE
            else "degraded"
        )
        ignored_beacon_ids: list[str] = []
        algorithm_version = "fingerprint-0.1"
        floor_id = fp_match.floor_id
        used_beacon_count = fp_match.overlap
    else:
        estimate = estimate_location(
            pkg=published.pkg,
            observations=[BeaconInput(beacon_id=b.beaconId, rssi=b.rssi) for b in body.beacons],
            floor_hint=body.floorHint,
            params=params,
            robust_rssi=robust_rssi,
        )
        position = (
            GeoPosition(x=estimate.x, y=estimate.y, floorId=estimate.floor_id)
            if estimate.x is not None and estimate.y is not None
            else None
        )
        confidence = estimate.confidence
        quality = estimate.quality
        ignored_beacon_ids = list(estimate.ignored_beacon_ids)
        algorithm_version = ROBUST_RSSI_VERSION if robust_rssi else "location-0.3"
        floor_id = estimate.floor_id
        used_beacon_count = estimate.used_beacon_count

    area = (
        _nearest_area(published.pkg, position.x, position.y, position.floorId)
        if position is not None
        else None
    )

    _advance_sequence(db, user.subject_id, body.siteId, body.sequence)

    heading = body.imu.headingDeg if body.imu is not None else None
    if heading is not None:
        heading = heading % 360.0

    surroundings = None
    if body.includeSurroundings:
        try:
            surroundings = nearby_surroundings(published, position, quality, heading)
        except (KeyError, TypeError, ValueError):
            # 周边清单是附加信息：构建失败不得影响本次定位主链路。
            logger.exception("查周边构建失败，返回空清单")
            surroundings = []

    estimate_id = f"loc_{uuid.uuid4().hex}"
    failure_reason = estimate.failure_reason if estimate is not None else None
    residual_m = estimate.residual_m if estimate is not None else None
    residual_db = estimate.residual_db if estimate is not None else None
    log_result = logger.info if failure_reason else logger.debug
    log_result(
        "location_estimate request_id=%s estimate_id=%s sequence=%s map_version=%s params_version=%s "
        "algorithm=%s quality=%s reason=%s floor_hint=%s floor=%s observation_age_s=%s "
        "submitted=%s used=%s excluded_counts=%s residual_m=%s residual_db=%s degraded_limit_m=%s fingerprint_fallback=%s",
        request_id, estimate_id, body.sequence, published.map_version, params.params_version,
        algorithm_version, quality, failure_reason, body.floorHint, floor_id,
        collected_age_s, len(body.beacons), used_beacon_count,
        estimate.excluded_counts if estimate is not None else None,
        round(residual_m, 3) if residual_m is not None else None,
        round(residual_db, 3) if residual_db is not None else None,
        None if robust_rssi or fp_match is not None else params.residual_degraded_m, fallback_reason,
    )
    return PositionEstimateResponse(
        estimateId=estimate_id,
        sequence=body.sequence,
        mapVersion=published.map_version,
        position=position,
        headingDeg=heading,
        confidence=confidence,
        quality=quality,
        matchedArea=None
        if area is None
        else {"placeId": area.placeId, "name": area.name},
        algorithmVersion=algorithm_version,
        paramsVersion=params.params_version,
        observedAt=body.collectedAt,
        ignoredBeaconIds=ignored_beacon_ids,
        positionSource="fingerprint" if fp_match is not None else "trilateration",
        fingerprint=None
        if fp_match is None
        else FingerprintMatchInfo(
            libraryVersion=fp_match.library_version,
            fpId=fp_match.fp_id,
            floorId=fp_match.floor_id,
            confidence=fp_match.confidence,
            rmseDb=fp_match.rmse_db,
            overlap=fp_match.overlap,
        ),
        fallbackReason=fallback_reason,
        surroundings=surroundings,
        observationAgeS=None if collected_age_s is None else round(collected_age_s, 1),
        observationStale=collected_age_s is not None and collected_age_s > ADVISORY_STALE_AGE_S,
    )
