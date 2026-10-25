# -*- coding: utf-8 -*-
"""API-05：POST /api/v1/locations/estimate（docs/24 §8）。

- 客户端地图版本落后于最新发布 → 409 MAP_VERSION_CONFLICT（触发客户端刷新）；
- 未知版本 → 404 MAP_NOT_PUBLISHED；
- sequence 严格小于已处理值 → 409 STALE_LOCATION_BATCH；
- IMU 仅回显 heading（第一阶段不解算惯性）。
"""
from __future__ import annotations

import time
import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from pathai_backend import auth
from pathai_backend.errors import ApiError
from pathai_backend.location_solver import BeaconInput, estimate_location
from pathai_backend.map_repository import MapRepository
from pathai_backend.models import LocationSequence
from pathai_backend.routers.map import get_map_repository
from pathai_backend.schemas.common import GeoPosition
from pathai_backend.schemas.location import LocationEstimateRequest, PositionEstimateResponse

router = APIRouter(prefix="/api/v1/locations", tags=["location"])


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
        )


def _advance_sequence(
    db: Session, subject_id: int, site_id: str, sequence: int
) -> None:
    row = db.get(LocationSequence, {"subject_id": subject_id, "site_id": site_id})
    if row is None:
        db.add(
            LocationSequence(
                subject_id=subject_id,
                site_id=site_id,
                last_sequence=sequence,
                updated_at_epoch=int(time.time()),
            )
        )
    elif sequence > row.last_sequence:
        row.last_sequence = sequence
        row.updated_at_epoch = int(time.time())
    db.commit()


@router.post("/estimate", response_model=PositionEstimateResponse)
def estimate(
    body: LocationEstimateRequest,
    user: auth.AuthenticatedUser = Depends(auth.get_current_user),
    db: Session = Depends(auth.get_db_session),
    repo: MapRepository = Depends(get_map_repository),
) -> PositionEstimateResponse:
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
    if latest is not None and published.map_version != latest.map_version:
        raise ApiError(
            409,
            "MAP_VERSION_CONFLICT",
            f"地图版本不一致：客户端 {published.map_version}，最新 {latest.map_version}",
        )

    _check_sequence(db, user.subject_id, body.siteId, body.sequence)

    estimate = estimate_location(
        pkg=published.pkg,
        observations=[BeaconInput(beacon_id=b.beaconId, rssi=b.rssi) for b in body.beacons],
        floor_hint=body.floorHint,
    )

    position = (
        GeoPosition(x=estimate.x, y=estimate.y, floorId=estimate.floor_id)
        if estimate.x is not None and estimate.y is not None
        else None
    )
    area = (
        _nearest_area(published.pkg, estimate.x, estimate.y, estimate.floor_id)
        if position is not None
        else None
    )

    _advance_sequence(db, user.subject_id, body.siteId, body.sequence)

    heading = body.imu.headingDeg if body.imu is not None else None
    if heading is not None:
        heading = heading % 360.0
    return PositionEstimateResponse(
        estimateId=f"loc_{uuid.uuid4().hex}",
        sequence=body.sequence,
        mapVersion=published.map_version,
        position=position,
        headingDeg=heading,
        confidence=estimate.confidence,
        quality=estimate.quality,
        matchedArea=None
        if area is None
        else {"placeId": area.placeId, "name": area.name},
        algorithmVersion="location-0.1",
        observedAt=body.collectedAt,
        ignoredBeaconIds=list(estimate.ignored_beacon_ids),
    )
