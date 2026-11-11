# -*- coding: utf-8 -*-
"""标定控制台接口（specs/calibration-console Part B）。

写接口需 admin 会话 + CSRF（复用管理后台凭证，评审第二批）；保留最小防护：
私网限定、写接口限流、schema 白名单、apply 二次确认 + 版本 CAS、审计、成对回滚。
"""
from __future__ import annotations

import ipaddress
import time

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from pathai_backend import auth, calibration_service as svc
from pathai_backend.admin.deps import require_admin_ready
from pathai_backend.config import Settings
from pathai_backend.errors import ApiError
from pathai_backend.runtime_params import RuntimeParams
from pathai_backend.schemas.calibration import (
    ApplyRunRequest,
    ComputeRunRequest,
    RollbackRequest,
    SampleUploadRequest,
)

router = APIRouter(prefix="/api/v1/calibration", tags=["calibration"])

#: 进程内简单限流（单场地部署足够；多 worker 时按进程独立计数）。
_LAST_WRITE: dict[str, float] = {}


def require_private_network(request: Request) -> None:
    host = request.client.host if request.client else ""
    if host in ("testclient", "localhost", ""):
        return
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        raise ApiError(403, "PRIVATE_NETWORK_ONLY", "仅允许局域网访问标定控制台")
    if not (ip.is_private or ip.is_loopback):
        raise ApiError(403, "PRIVATE_NETWORK_ONLY", f"仅允许局域网访问：{host}")


def _rate_limit(key: str, interval_s: float) -> None:
    now = time.monotonic()
    last = _LAST_WRITE.get(key, 0.0)
    if now - last < interval_s:
        raise ApiError(429, "RATE_LIMITED", f"操作过于频繁，请 {interval_s:.0f}s 后重试")
    _LAST_WRITE[key] = now


#: 写接口依赖：私网限定（纵深防御）+ admin 会话/CSRF。
_ADMIN_WRITE = [Depends(require_private_network), Depends(require_admin_ready)]


def _call(fn):
    try:
        return fn()
    except svc.CalibrationServiceError as exc:
        raise ApiError(exc.status_code, exc.code.upper(), exc.message) from exc


@router.get("/readiness", dependencies=[Depends(require_private_network)])
def readiness(
    db: Session = Depends(auth.get_db_session),
    settings: Settings = Depends(auth.get_settings),
) -> dict:
    return svc.readiness(db, settings)


@router.post("/samples", dependencies=_ADMIN_WRITE)
def upload_sample(
    body: SampleUploadRequest,
    db: Session = Depends(auth.get_db_session),
    settings: Settings = Depends(auth.get_settings),
) -> dict:
    _rate_limit("samples", 2.0)
    return _call(
        lambda: svc.upload_sample(
            db,
            settings,
            file_name=body.fileName,
            content=body.content.encode("utf-8"),
            operator=body.operator,
        )
    )


@router.post("/runs", dependencies=_ADMIN_WRITE)
def compute_run(
    body: ComputeRunRequest,
    db: Session = Depends(auth.get_db_session),
    settings: Settings = Depends(auth.get_settings),
) -> dict:
    _rate_limit("runs", 3.0)
    return _call(lambda: svc.compute_run(db, settings, body.sampleId))


@router.get("/runs/latest", dependencies=[Depends(require_private_network)])
def latest_run(
    sampleId: str | None = None,
    db: Session = Depends(auth.get_db_session),
) -> dict:
    return {"run": svc.latest_run(db, sampleId)}


@router.get("/runs/{run_id}", dependencies=[Depends(require_private_network)])
def get_run(run_id: str, db: Session = Depends(auth.get_db_session)) -> dict:
    from pathai_backend.models import CalibrationRun

    run = db.get(CalibrationRun, run_id)
    if run is None:
        raise ApiError(404, "RUN_NOT_FOUND", "计算任务不存在")
    return svc._run_summary(run)


@router.post("/runs/{run_id}/apply", dependencies=_ADMIN_WRITE)
def apply_run(
    run_id: str,
    body: ApplyRunRequest,
    request: Request,
    db: Session = Depends(auth.get_db_session),
    settings: Settings = Depends(auth.get_settings),
) -> dict:
    _rate_limit("apply", 5.0)
    runtime: RuntimeParams = request.app.state.runtime_params
    return _call(
        lambda: svc.apply_run(
            db,
            settings,
            runtime,
            run_id=run_id,
            confirm=body.confirm,
            expect_param_version=body.expectParamVersion,
        )
    )


@router.get("/versions", dependencies=[Depends(require_private_network)])
def versions(db: Session = Depends(auth.get_db_session)) -> dict:
    return svc.list_versions(db)


@router.post("/rollback", dependencies=_ADMIN_WRITE)
def rollback(
    body: RollbackRequest,
    request: Request,
    db: Session = Depends(auth.get_db_session),
) -> dict:
    _rate_limit("rollback", 5.0)
    runtime: RuntimeParams = request.app.state.runtime_params
    return _call(
        lambda: svc.rollback(
            db, runtime, param_version=body.paramVersion, confirm=body.confirm
        )
    )
