# -*- coding: utf-8 -*-
"""指纹库接口：入库（版本化）、active/versions、诊断查询。无鉴权。

最小防护与校准控制台一致：私网限定、写接口进程内限流、体积/字段白名单。
"""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from pathai_backend import auth, fingerprint_library as svc
from pathai_backend.errors import ApiError
from pathai_backend.map_repository import MapRepository
from pathai_backend.routers.calibration import _rate_limit, require_private_network
from pathai_backend.routers.map import get_map_repository
from pathai_backend.schemas.fingerprint_db import (
    FingerprintIngestRequest,
    FingerprintMatchResponse,
    FingerprintQueryRequest,
    FingerprintRollbackRequest,
)
from pathai_backend.schemas.location import BeaconObservation

router = APIRouter(prefix="/api/v1/fingerprint-db", tags=["fingerprint-db"])

#: 单次入库内容上限（导出 JSON 文本；2109 点 × 5 次约 2MB，留足余量）。
MAX_INGEST_BYTES = 8 * 1024 * 1024


def _call(fn):
    try:
        return fn()
    except svc.FingerprintLibraryError as exc:
        raise ApiError(exc.status_code, exc.code, exc.message) from exc


@router.post("/ingest", dependencies=[Depends(require_private_network)])
def ingest(
    body: FingerprintIngestRequest,
    db: Session = Depends(auth.get_db_session),
) -> dict:
    _rate_limit("fp_ingest", 5.0)
    if len(body.content.encode("utf-8")) > MAX_INGEST_BYTES:
        raise ApiError(413, "CONTENT_TOO_LARGE", "入库内容超过 8MB 上限")
    summary = _call(
        lambda: svc.ingest_export(
            db,
            file_name=body.fileName,
            content=body.content,
            operator=body.operator,
        )
    )
    db.commit()
    return summary


@router.get("/active", dependencies=[Depends(require_private_network)])
def active(db: Session = Depends(auth.get_db_session)) -> dict:
    return {"active": svc.list_versions(db, limit=1)["active"]}


@router.get("/versions", dependencies=[Depends(require_private_network)])
def versions(
    limit: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(auth.get_db_session),
) -> dict:
    return svc.list_versions(db, limit=limit)


@router.get("/manifest", dependencies=[Depends(require_private_network)])
def manifest(
    version: str | None = Query(default=None, max_length=40),
    floor: str | None = Query(default=None, max_length=16),
    db: Session = Depends(auth.get_db_session),
) -> dict:
    """对账清单：入库前客户端比对「库中有、本地无」，差异必须可见。"""
    return _call(lambda: svc.manifest(db, version=version, floor=floor))


@router.post("/rollback", dependencies=[Depends(require_private_network)])
def rollback(
    body: FingerprintRollbackRequest,
    db: Session = Depends(auth.get_db_session),
) -> dict:
    _rate_limit("fp_rollback", 3.0)
    summary = _call(lambda: svc.rollback(db, version=body.version))
    db.commit()
    return summary


@router.post(
    "/query",
    response_model=FingerprintMatchResponse,
    dependencies=[Depends(require_private_network)],
)
def query(
    body: FingerprintQueryRequest,
    db: Session = Depends(auth.get_db_session),
    repo: MapRepository = Depends(get_map_repository),
) -> FingerprintMatchResponse:
    if not repo.is_known_site(body.siteId):
        raise ApiError(404, "SITE_NOT_FOUND", f"场所不存在：{body.siteId}")
    published = (
        repo.latest(body.siteId)
        if body.mapVersion == "latest"
        else repo.get(body.siteId, body.mapVersion)
    )
    if published is None:
        raise ApiError(
            404, "MAP_NOT_PUBLISHED", f"地图版本未发布：{body.siteId}@{body.mapVersion}"
        )
    observations = [
        BeaconObservation(
            beaconId=b.beaconId,
            rssi=b.rssi,
            observedAt=datetime.now(timezone.utc),
        )
        for b in body.beacons
    ]
    match, reason = svc.query(
        db,
        pkg=published.pkg,
        beacons=observations,
        site_id=body.siteId,
        floor_hint=body.floorHint,
    )
    if match is None:
        return FingerprintMatchResponse(matched=False, reason=reason)
    return FingerprintMatchResponse(
        matched=True,
        libraryVersion=match.library_version,
        fpId=match.fp_id,
        floorId=match.floor_id,
        position={"x": match.x, "y": match.y, "floorId": match.floor_id},
        confidence=match.confidence,
        rmseDb=match.rmse_db,
        overlap=match.overlap,
    )
