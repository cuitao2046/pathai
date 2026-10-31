# -*- coding: utf-8 -*-
"""信标配置台账与真实部署台账 API（docs/27 §5.2 / §6.8）。

- 全部端点要求管理员会话（复用 admin 登录）且已完成首改密；
- 读：`GET /beacons`、`GET /deployment-records`；写：`PUT /deployment-records/{beaconId}`、`POST /commit`（需 CSRF）；
- `GET /beacons` 的 ETag 沿用地图包 sha256，`If-None-Match` 命中返回 304；
- 规划台账只读；真实部署台账按 `(siteId, beaconId)` 幂等 upsert。
"""
from __future__ import annotations

import json
import time
from datetime import datetime

from fastapi import APIRouter, Query, Request, Response

from pathai_backend.admin import deps, service
from pathai_backend.database import session_scope
from pathai_backend.errors import ApiError
from pathai_backend.map_repository import MapRepository, PublishedMap
from pathai_backend.models import BeaconDeploymentRecord, BeaconProvisioningRound
from pathai_backend.schemas.beacon import (
    BeaconConfigItem,
    BeaconsResponse,
    CommitRequest,
    CommitResponse,
    DeploymentRecordRequest,
    DeploymentRecordView,
    DeploymentRecordsResponse,
    ProvisioningSummary,
)

router = APIRouter(prefix="/api/v1/sites", tags=["beacons"])

_TX_TOLERANCE = 0.01


def _repo(request: Request) -> MapRepository:
    repo = getattr(request.app.state, "map_repository", None)
    if repo is None:
        raise ApiError(
            503, "MAP_REPOSITORY_UNAVAILABLE", "地图仓库暂不可用，请联系管理员重启服务", retryable=True
        )
    return repo


def _resolve(repo: MapRepository, site_id: str, version: str) -> PublishedMap:
    if not repo.is_known_site(site_id):
        raise ApiError(404, "SITE_NOT_FOUND", f"场所不存在：{site_id}")
    published = repo.latest(site_id) if version == "latest" else repo.get(site_id, version)
    if published is None:
        raise ApiError(404, "MAP_NOT_PUBLISHED", f"地图版本未发布：{site_id}@{version}")
    return published


def _etag_hit(request: Request, published: PublishedMap) -> bool:
    header = request.headers.get("if-none-match")
    if not header:
        return False
    for candidate in header.split(","):
        candidate = candidate.strip()
        if candidate.startswith("W/"):
            candidate = candidate[2:]
        if candidate == published.etag or candidate == published.sha256:
            return True
    return False


def _derive_device_name(floor_id: str, minor: int) -> str:
    """兼容旧地图包：短码 `F{floor}-{minor 后三位}`（与 convert_map_v9 规则一致）。"""
    return f"F{floor_id}-{minor % 1000:03d}"


def _planned_name(beacon) -> str:
    name = getattr(beacon, "deviceName", None)
    if isinstance(name, str) and name:
        return name
    return _derive_device_name(beacon.floorId, int(beacon.minor))


def _planned_interval(beacon) -> int | None:
    value = getattr(beacon, "broadcastInterval", None)
    return int(value) if value is not None else None


def _config_item(beacon) -> BeaconConfigItem:
    return BeaconConfigItem(
        beaconId=beacon.beaconId,
        deviceName=_planned_name(beacon),
        floorId=beacon.floorId,
        coordinates=[float(beacon.x), float(beacon.y)],
        uuid=beacon.uuid,
        major=beacon.major,
        minor=beacon.minor,
        txPower=beacon.txPower,
        broadcastInterval=_planned_interval(beacon),
        locationDesc=getattr(beacon, "locationDesc", None),
        mountType=getattr(beacon, "mountType", None),
        semanticTag=getattr(beacon, "semanticTag", None),
    )


def _epoch(value: str | None) -> int:
    if not value:
        return int(time.time())
    try:
        return int(datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp())
    except ValueError:
        return int(time.time())


def _iso(epoch: int) -> str:
    return datetime.fromtimestamp(epoch).isoformat(timespec="seconds")


def _record_view(record: BeaconDeploymentRecord) -> DeploymentRecordView:
    return DeploymentRecordView(
        siteId=record.site_id,
        beaconId=record.beacon_id,
        mapVersion=record.map_version,
        deviceMac=record.device_mac,
        deviceName=record.device_name,
        nameWrite=record.name_write,
        written=json.loads(record.written_json),
        verify=json.loads(record.verify_json),
        source=record.source,
        operator=record.operator,
        configuredAt=(
            _iso(record.configured_at_epoch) if record.configured_at_epoch is not None else None
        ),
        updatedAt=_iso(record.updated_at_epoch),
    )


def _triplet_key(uuid: str, major: int, minor: int) -> tuple[str, int, int]:
    return (uuid.lower(), int(major), int(minor))


def _validate_written(planned, body: DeploymentRecordRequest, beacon_id: str) -> None:
    """写入值必须与规划一致；field_edit 仅允许 txPower / interval 差异。"""
    if planned.uuid is None or planned.major is None or planned.minor is None:
        raise ApiError(
            409, "BEACON_PLAN_INCOMPLETE", f"{beacon_id} 规划缺少三元组，不能写入", retryable=False
        )
    planned_interval = _planned_interval(planned)
    if body.source == "intake" and planned_interval is None:
        raise ApiError(
            409,
            "BEACON_PLAN_INCOMPLETE",
            f"{beacon_id} 规划缺少 broadcastInterval，请重新发布地图包",
            retryable=False,
        )
    if not body.verify.passed:
        raise ApiError(400, "BEACON_VERIFY_NOT_PASSED", "校验未通过，不能上传", retryable=False)

    written = body.written
    if written.uuid.lower() != planned.uuid.lower():
        raise ApiError(409, "BEACON_TRIPLET_MISMATCH", "UUID 与规划不一致", retryable=False)
    if (written.major, written.minor) != (planned.major, planned.minor):
        raise ApiError(409, "BEACON_TRIPLET_MISMATCH", "Major/Minor 与规划不一致", retryable=False)
    if written.name != _planned_name(planned):
        raise ApiError(409, "BEACON_NAME_MISMATCH", "设备名与规划短码不一致", retryable=False)
    if planned.txPower is not None and abs(written.txPower - float(planned.txPower)) > _TX_TOLERANCE:
        if body.source != "field_edit":
            raise ApiError(409, "BEACON_TX_POWER_MISMATCH", "发射功率与规划不一致", retryable=False)
    if planned_interval is not None and written.interval != planned_interval:
        if body.source != "field_edit":
            raise ApiError(409, "BEACON_INTERVAL_MISMATCH", "广播间隔与规划不一致", retryable=False)


def _summary(site_id: str, published: PublishedMap, records: list[BeaconDeploymentRecord]) -> ProvisioningSummary:
    planned_ids = [b.beaconId for b in published.pkg.beacons]
    by_id = {r.beacon_id: r for r in records}
    missing = [bid for bid in planned_ids if bid not in by_id]
    unverified = [r.beacon_id for r in records if not json.loads(r.verify_json).get("passed", False)]

    seen: dict[tuple[str, int, int], str] = {}
    duplicates: list[str] = []
    for record in records:
        written = json.loads(record.written_json)
        key = _triplet_key(written["uuid"], written["major"], written["minor"])
        if key in seen:
            duplicates.append(f"{seen[key]}↔{record.beacon_id}")
        else:
            seen[key] = record.beacon_id

    return ProvisioningSummary(
        siteId=site_id,
        mapVersion=published.map_version,
        total=len(planned_ids),
        recorded=sum(1 for bid in planned_ids if bid in by_id),
        verified=sum(1 for bid in planned_ids if bid in by_id and json.loads(by_id[bid].verify_json).get("passed")),
        missing=missing,
        unverified=unverified,
        duplicates=duplicates,
    )


def _load_records(site_id: str) -> list[BeaconDeploymentRecord]:
    with session_scope() as session:
        return list(
            session.query(BeaconDeploymentRecord)
            .filter(BeaconDeploymentRecord.site_id == site_id)
            .order_by(BeaconDeploymentRecord.beacon_id)
            .all()
        )


@router.get("/{site_id}/beacons", response_model=BeaconsResponse)
def get_beacons(
    site_id: str,
    request: Request,
    version: str = Query(default="latest", min_length=1, max_length=64),
) -> Response:
    deps.require_admin_ready(request)
    published = _resolve(_repo(request), site_id, version)
    if _etag_hit(request, published):
        return Response(status_code=304, headers={"ETag": published.etag})
    body = BeaconsResponse(
        siteId=published.site_id,
        mapVersion=published.map_version,
        items=[_config_item(b) for b in sorted(published.pkg.beacons, key=lambda b: b.beaconId)],
    )
    return Response(
        content=body.model_dump_json(),
        media_type="application/json",
        headers={"ETag": published.etag},
    )


@router.put("/{site_id}/deployment-records/{beacon_id}", response_model=DeploymentRecordView)
def put_deployment_record(
    site_id: str,
    beacon_id: str,
    body: DeploymentRecordRequest,
    request: Request,
) -> DeploymentRecordView:
    context = deps.require_admin_ready(request)
    published = _resolve(_repo(request), site_id, body.mapVersion or "latest")
    planned = next((b for b in published.pkg.beacons if b.beaconId == beacon_id), None)
    if planned is None:
        raise ApiError(404, "BEACON_NOT_FOUND", f"规划台账中不存在：{beacon_id}")
    _validate_written(planned, body, beacon_id)

    with session_scope() as session:
        for other in (
            session.query(BeaconDeploymentRecord)
            .filter(
                BeaconDeploymentRecord.site_id == site_id,
                BeaconDeploymentRecord.beacon_id != beacon_id,
            )
            .all()
        ):
            written = json.loads(other.written_json)
            if _triplet_key(written["uuid"], written["major"], written["minor"]) == _triplet_key(
                body.written.uuid, body.written.major, body.written.minor
            ):
                raise ApiError(
                    409,
                    "BEACON_TRIPLET_CONFLICT",
                    f"三元组已被 {other.beacon_id} 占用",
                    retryable=False,
                )

        record = (
            session.query(BeaconDeploymentRecord)
            .filter(
                BeaconDeploymentRecord.site_id == site_id,
                BeaconDeploymentRecord.beacon_id == beacon_id,
            )
            .one_or_none()
        )
        now = int(time.time())
        payload = {
            "device_mac": body.deviceMac,
            "device_name": body.deviceName,
            "name_write": body.nameWrite,
            "written_json": json.dumps(body.written.model_dump(), ensure_ascii=False),
            "verify_json": json.dumps(body.verify.model_dump(), ensure_ascii=False),
            "map_version": published.map_version,
            "source": body.source,
            "operator": body.operator or context.account.username,
            "configured_at_epoch": _epoch(body.configuredAt),
            "updated_at_epoch": now,
        }
        if record is None:
            record = BeaconDeploymentRecord(site_id=site_id, beacon_id=beacon_id, **payload)
            session.add(record)
        else:
            for key, value in payload.items():
                setattr(record, key, value)
        session.commit()
        session.refresh(record)
        view = _record_view(record)

    service.record_audit(
        "beacon_record",
        True,
        {"siteId": site_id, "beaconId": beacon_id, "source": body.source},
    )
    return view


@router.get("/{site_id}/deployment-records", response_model=DeploymentRecordsResponse)
def list_deployment_records(
    site_id: str,
    request: Request,
    version: str = Query(default="latest", min_length=1, max_length=64),
) -> DeploymentRecordsResponse:
    deps.require_admin_ready(request)
    published = _resolve(_repo(request), site_id, version)
    records = _load_records(site_id)
    return DeploymentRecordsResponse(
        summary=_summary(site_id, published, records),
        items=[_record_view(r) for r in records],
    )


@router.post("/{site_id}/deployment-records/commit", response_model=CommitResponse)
def commit_deployment_records(
    site_id: str,
    body: CommitRequest,
    request: Request,
) -> CommitResponse:
    context = deps.require_admin_ready(request)
    published = _resolve(_repo(request), site_id, body.mapVersion or "latest")
    summary = _summary(site_id, published, _load_records(site_id))
    status = "committed" if not summary.missing and not summary.unverified else "incomplete"
    now = int(time.time())
    operator = body.operator or context.account.username
    with session_scope() as session:
        round_row = BeaconProvisioningRound(
            site_id=site_id,
            map_version=published.map_version,
            status=status,
            operator=operator,
            total=summary.total,
            recorded=summary.recorded,
            verified=summary.verified,
            summary_json=json.dumps(summary.model_dump(), ensure_ascii=False),
            created_at_epoch=now,
        )
        session.add(round_row)
        session.commit()
        session.refresh(round_row)
        round_id = round_row.id

    service.record_audit(
        "beacon_commit",
        True,
        {"siteId": site_id, "mapVersion": published.map_version, "status": status, "roundId": round_id},
    )
    return CommitResponse(
        **summary.model_dump(), status=status, roundId=round_id
    )
