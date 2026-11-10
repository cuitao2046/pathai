# -*- coding: utf-8 -*-
"""客户端运行时配置：定位参数版本与指纹网格覆盖层（specs/calibration-console §6.7、§7.3）。"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from fastapi import APIRouter, Depends, Header, Request, Response
from sqlalchemy.orm import Session

from pathai_backend import auth
from pathai_backend.errors import ApiError
from pathai_backend.models import CalibrationActivePointer, FingerprintGridVersion
from pathai_backend.routers.calibration import require_private_network
from pathai_backend.runtime_params import DEFAULT_SITE_ID, RuntimeParams

router = APIRouter(prefix="/api/v1/config", tags=["config"])


@router.get("/location-params", dependencies=[Depends(require_private_network)])
def location_params(request: Request) -> dict:
    runtime: RuntimeParams = request.app.state.runtime_params
    settings = runtime.get()
    snapshot = runtime.snapshot()
    return {
        "siteId": DEFAULT_SITE_ID,
        "paramsVersion": settings.location_params_version,
        "gridVersion": snapshot["gridVersion"],
        "source": snapshot["source"],
        "rssiAt1mDbm": settings.location_rssi_at_1m,
        "pathLossN": settings.location_path_loss_n,
        "residualReliableM": settings.location_residual_reliable_m,
        "residualDegradedM": settings.location_residual_degraded_m,
        "ttlS": runtime.ttl_s,
    }


@router.get("/fingerprint-grid/overlay", dependencies=[Depends(require_private_network)])
def grid_overlay(
    baseGridVersion: str | None = None,
    if_none_match: str | None = Header(default=None, alias="If-None-Match"),
    db: Session = Depends(auth.get_db_session),
) -> Response:
    pointer = db.get(CalibrationActivePointer, DEFAULT_SITE_ID)
    if pointer is None:
        return Response(
            content=json.dumps(
                {"available": False, "reason": "no_active_grid", "baseGridVersion": None}
            ),
            media_type="application/json",
            headers={"Cache-Control": "no-store"},
        )
    row = db.get(FingerprintGridVersion, pointer.grid_version)
    if row is None:
        return Response(
            content=json.dumps(
                {"available": False, "reason": "grid_row_missing", "baseGridVersion": None}
            ),
            media_type="application/json",
            headers={"Cache-Control": "no-store"},
        )
    path = Path(row.overlay_path)
    if not path.exists():
        return Response(
            content=json.dumps(
                {"available": False, "reason": "overlay_file_missing", "baseGridVersion": None}
            ),
            media_type="application/json",
            headers={"Cache-Control": "no-store"},
        )
    raw = path.read_bytes()
    etag = '"' + hashlib.sha256(raw).hexdigest()[:32] + '"'
    if if_none_match is not None and if_none_match == etag:
        return Response(status_code=304, headers={"ETag": etag})
    overlay = json.loads(raw.decode("utf-8"))
    if baseGridVersion is not None and overlay.get("baseGridVersion") != baseGridVersion:
        raise ApiError(
            409,
            "GRID_BASE_MISMATCH",
            f"覆盖层基线 {overlay.get('baseGridVersion')} 与客户端 {baseGridVersion} 不一致",
        )
    return Response(
        content=json.dumps(overlay, ensure_ascii=False),
        media_type="application/json",
        headers={
            "ETag": etag,
            "Cache-Control": "no-cache",
            "X-Grid-Version": row.version,
            "X-Base-Grid-Version": row.base_grid_version,
        },
    )
