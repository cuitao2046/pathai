# -*- coding: utf-8 -*-
"""客户端运行时配置：定位参数版本、导航判定阈值与指纹网格覆盖层。"""
from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

from fastapi import APIRouter, Depends, Header, Request, Response
from pydantic import BaseModel
from sqlalchemy.orm import Session

from pathai_backend import auth, calibration_service
from pathai_backend.client_permissions import require_tester
from pathai_backend.errors import ApiError
from pathai_backend.models import (
    CalibrationActivePointer,
    FingerprintGridVersion,
    NavPolicy,
)
from pathai_backend.routers.calibration import require_private_network
from pathai_backend.runtime_params import DEFAULT_SITE_ID, RuntimeParams

router = APIRouter(prefix="/api/v1/config", tags=["config"])

#: 可配置范围：防止误填导致导航永远不触发/提前过多。
_ARRIVAL_RADIUS_RANGE_M = (0.5, 20.0)
_TURN_PROMPT_RANGE_M = (0.5, 10.0)


class NavPolicyUpdate(BaseModel):
    """导航判定阈值更新请求（单位：米）。"""

    arrivalRadiusM: float
    turnPromptDistanceM: float


def _nav_policy_payload(runtime: RuntimeParams) -> dict:
    settings = runtime.get()
    return {
        "siteId": DEFAULT_SITE_ID,
        "arrivalRadiusM": settings.nav_arrival_radius_m,
        "turnPromptDistanceM": settings.nav_turn_prompt_distance_m,
        "source": runtime.snapshot()["navPolicySource"],
    }


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
        "navArrivalRadiusM": settings.nav_arrival_radius_m,
        "navTurnPromptDistanceM": settings.nav_turn_prompt_distance_m,
        "navPolicySource": snapshot["navPolicySource"],
        "ttlS": runtime.ttl_s,
    }


@router.get("/nav-policy", dependencies=[Depends(require_private_network)])
def get_nav_policy(request: Request) -> dict:
    """读取当前生效的导航判定阈值（无行时为环境冻结值）。"""
    return _nav_policy_payload(request.app.state.runtime_params)


@router.put(
    "/nav-policy",
    dependencies=[Depends(require_private_network), Depends(require_tester)],
)
def update_nav_policy(
    body: NavPolicyUpdate,
    request: Request,
    db: Session = Depends(auth.get_db_session),
) -> dict:
    """保存导航判定阈值（测试人员），写入 nav_policy 并热生效。"""
    low, high = _ARRIVAL_RADIUS_RANGE_M
    if not (low <= body.arrivalRadiusM <= high):
        raise ApiError(
            422, "INVALID_REQUEST", f"到达判定半径需在 {low}~{high} 米之间"
        )
    low, high = _TURN_PROMPT_RANGE_M
    if not (low <= body.turnPromptDistanceM <= high):
        raise ApiError(
            422, "INVALID_REQUEST", f"转向提前播报距离需在 {low}~{high} 米之间"
        )
    row = db.get(NavPolicy, DEFAULT_SITE_ID)
    if row is None:
        row = NavPolicy(site_id=DEFAULT_SITE_ID)
        db.add(row)
    row.arrival_radius_m = round(body.arrivalRadiusM, 2)
    row.turn_prompt_distance_m = round(body.turnPromptDistanceM, 2)
    row.updated_at_epoch = int(time.time())
    row.updated_by = getattr(request.state, "operator", None)
    calibration_service.audit(
        db,
        "nav_policy",
        True,
        {
            "arrivalRadiusM": row.arrival_radius_m,
            "turnPromptDistanceM": row.turn_prompt_distance_m,
            "updatedBy": row.updated_by,
        },
    )
    db.commit()
    runtime: RuntimeParams = request.app.state.runtime_params
    runtime.invalidate()
    return _nav_policy_payload(runtime)


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
