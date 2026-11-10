# -*- coding: utf-8 -*-
"""标定控制台业务层（specs/calibration-console Part B）。

安全边界（用户明确决定：不做鉴权）：
- 服务只允许私网访问（路由层依赖）；
- apply 需要二次确认字段、版本 CAS、样本可部署标记；
- 网格重算失败或校验不通过时，参数与网格均不生效；
- 所有 apply/回滚写审计事件。
"""
from __future__ import annotations

import hashlib
import json
import sys
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select

from pathai_backend.config import Settings
from pathai_backend.grid_recompute import (
    SITE_ID,
    GridRecomputeError,
    baseline_status,
    publish_staging,
    recompute_grid,
    GridRecomputeResult,
)
from pathai_backend.models import (
    AdminAuditEvent,
    CalibrationActivePointer,
    CalibrationRun,
    CalibrationSample,
    FingerprintGridVersion,
    LocationParamVersion,
)
from pathai_backend.runtime_params import RuntimeParams

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.tools import fit_location_params as fitter  # noqa: E402

MAX_SAMPLE_BYTES = 8 * 1024 * 1024
MAX_KEEP_PAIRS = 20
STATIC_TYPE = "positioning_calibration_static"
LOGDIST_TYPE = "positioning_calibration_logdist"


class CalibrationServiceError(Exception):
    def __init__(self, code: str, message: str, *, status_code: int = 400) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


def _now() -> int:
    return int(time.time())


def _epoch_iso(epoch: int) -> str:
    return datetime.fromtimestamp(epoch, tz=timezone.utc).isoformat()


def audit(db, event_type: str, success: bool, details: dict) -> None:
    db.add(
        AdminAuditEvent(
            event_type=event_type,
            success=success,
            created_at_epoch=_now(),
            details_json=json.dumps(details, ensure_ascii=False),
        )
    )


def _sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


# ---------------------------------------------------------------------------
# 读取 / 就绪
# ---------------------------------------------------------------------------


def _latest_map_package(settings: Settings) -> Path | None:
    directory = Path(settings.map_data_dir) / SITE_ID
    if not directory.exists():
        return None
    candidates = sorted(directory.glob("*.json"), key=lambda p: p.stat().st_mtime)
    return candidates[-1] if candidates else None


def _active_pointer(db) -> CalibrationActivePointer | None:
    return db.get(CalibrationActivePointer, SITE_ID)


def readiness(db, settings: Settings) -> dict:
    baseline = baseline_status(settings)
    map_package = _latest_map_package(settings)
    latest_sample = db.scalars(
        select(CalibrationSample)
        .where(CalibrationSample.site_id == SITE_ID)
        .order_by(CalibrationSample.uploaded_at_epoch.desc())
        .limit(1)
    ).first()
    pointer = _active_pointer(db)
    files = [
        {
            "key": "mapPackage",
            "label": "地图包（含信标台账）",
            "required": True,
            "present": map_package is not None,
            "path": str(map_package) if map_package else None,
        },
        {
            "key": "geo",
            "label": "楼层几何 GeoJSON",
            "required": True,
            "present": "geo" not in baseline["missing"],
            "path": baseline["files"]["geo"],
        },
        {
            "key": "ledger",
            "label": "部署台账 ble_deployment.json",
            "required": True,
            "present": "ledger" not in baseline["missing"],
            "path": baseline["files"]["ledger"],
        },
        {
            "key": "routes",
            "label": "路线定义 beacon_routes.json",
            "required": True,
            "present": "routes" not in baseline["missing"],
            "path": baseline["files"]["routes"],
        },
        {
            "key": "simBeacons",
            "label": "补装仿真信标 simulated_beacons.json",
            "required": False,
            "present": "simBeacons" not in baseline["missing"],
            "path": baseline["files"]["simBeacons"],
        },
        {
            "key": "baselineGrid",
            "label": "内置指纹网格快照",
            "required": True,
            "present": "baselineGrid" not in baseline["missing"],
            "path": baseline["files"]["baselineGrid"],
        },
    ]
    ready = all(f["present"] for f in files if f["required"])
    return {
        "siteId": SITE_ID,
        "files": files,
        "ready": ready,
        "latestSample": _sample_summary(latest_sample) if latest_sample else None,
        "active": {
            "paramVersion": pointer.param_version if pointer else None,
            "gridVersion": pointer.grid_version if pointer else None,
        },
        "baselineDir": str(settings.calibration_grid_baseline_dir),
    }


def _sample_summary(sample: CalibrationSample) -> dict:
    return {
        "sampleId": sample.sample_id,
        "fileName": sample.file_name,
        "type": sample.sample_type,
        "deployable": sample.deployable,
        "operator": sample.operator,
        "mapVersion": sample.map_version,
        "sizeBytes": sample.size_bytes,
        "sha256": sample.content_sha256,
        "uploadedAt": _epoch_iso(sample.uploaded_at_epoch),
    }


def _run_summary(run: CalibrationRun) -> dict:
    result = json.loads(run.result_json) if run.result_json else None
    return {
        "runId": run.run_id,
        "sampleId": run.sample_id,
        "status": run.status,
        "result": result,
        "gridVersion": run.grid_version,
        "baseGridVersion": run.base_grid_version,
        "errorCode": run.error_code,
        "errorMessage": run.error_message,
        "createdAt": _epoch_iso(run.created_at_epoch),
        "updatedAt": _epoch_iso(run.updated_at_epoch),
    }


# ---------------------------------------------------------------------------
# 上传
# ---------------------------------------------------------------------------


def _parse_payload(content: bytes) -> dict:
    if len(content) > MAX_SAMPLE_BYTES:
        raise CalibrationServiceError("sample_too_large", "样本文件超过 8 MB")
    try:
        data = json.loads(content.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise CalibrationServiceError("sample_invalid_json", f"JSON 解析失败：{exc}") from exc
    if not isinstance(data, dict):
        raise CalibrationServiceError("sample_invalid_json", "样本根节点必须是对象")
    sample_type = data.get("type")
    if sample_type not in (STATIC_TYPE, LOGDIST_TYPE):
        raise CalibrationServiceError(
            "sample_type_unsupported", f"不支持的样本类型：{sample_type!r}"
        )
    if data.get("schemaVersion") not in ("1.0", 1.0):
        raise CalibrationServiceError("sample_schema_unsupported", "schemaVersion 必须是 1.0")
    if sample_type == STATIC_TYPE:
        if not isinstance(data.get("points"), list) or not data["points"]:
            raise CalibrationServiceError("sample_empty", "静态标定样本没有 points")
        if data.get("deployable") is not True:
            raise CalibrationServiceError(
                "sample_not_deployable", "静态标定样本 deployable 必须为 true 才能进入 apply 链路"
            )
    else:
        if not isinstance(data.get("stops"), list) or not data["stops"]:
            raise CalibrationServiceError("sample_empty", "对数等距样本没有 stops")
        if data.get("deployable") is not False:
            raise CalibrationServiceError(
                "sample_deployable_forbidden", "对数等距样本 deployable 必须为 false"
            )
    return data


def upload_sample(
    db,
    settings: Settings,
    *,
    file_name: str,
    content: bytes,
    operator: str | None,
) -> dict:
    data = _parse_payload(content)
    digest = _sha256_bytes(content)
    existing = db.scalars(
        select(CalibrationSample).where(
            CalibrationSample.site_id == SITE_ID,
            CalibrationSample.content_sha256 == digest,
        )
    ).first()
    if existing is not None:
        return {**_sample_summary(existing), "duplicated": True}

    sample_id = f"s-{uuid.uuid4().hex[:20]}"
    directory = Path(settings.calibration_data_dir) / "samples"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{sample_id}.json"
    path.write_bytes(content)
    row = CalibrationSample(
        sample_id=sample_id,
        site_id=SITE_ID,
        file_name=(file_name or "samples.json")[:160],
        content_sha256=digest,
        sample_type=str(data.get("type")),
        deployable=bool(data.get("deployable")),
        operator=(operator or data.get("operator") or None),
        map_version=(str(data.get("mapVersion"))[:32] if data.get("mapVersion") else None),
        size_bytes=len(content),
        stored_path=str(path),
        uploaded_at_epoch=_now(),
    )
    db.add(row)
    audit(db, "calibration_sample_upload", True, {
        "sampleId": sample_id, "sha256": digest, "size": len(content),
    })
    db.commit()
    return {**_sample_summary(row), "duplicated": False}


# ---------------------------------------------------------------------------
# 计算
# ---------------------------------------------------------------------------


def _fit_document(settings: Settings, sample: CalibrationSample, run_id: str, out_dir: Path) -> dict:
    samples_path = Path(sample.stored_path)
    out_path = out_dir / "fit_result.json"
    out_dir.mkdir(parents=True, exist_ok=True)
    if sample.sample_type == LOGDIST_TYPE:
        try:
            return fitter.fit_logdist(samples_path, out_path)
        except fitter.CalibrationError as exc:
            raise CalibrationServiceError("fit_failed", str(exc)) from exc
    ledger = Path(settings.calibration_grid_baseline_dir) / "ble_deployment.json"
    if not ledger.exists():
        raise CalibrationServiceError("ledger_missing", "部署台账缺失，无法计算静态标定参数")
    map_package = _latest_map_package(settings)
    try:
        return fitter.run(
            samples_path,
            ledger,
            out_path,
            map_package_path=map_package,
            params_version=f"calc-{run_id}",
        )
    except fitter.CalibrationError as exc:
        raise CalibrationServiceError("fit_failed", str(exc)) from exc


def _result_summary(
    sample: CalibrationSample,
    doc: dict,
    *,
    run_id: str,
    grid: GridRecomputeResult | None,
    blocked_reason: str | None,
) -> dict:
    if sample.sample_type == LOGDIST_TYPE:
        params = {
            "rssiAt1mDbm": doc["params"]["rssiAt1mDbm"],
            "pathLossN": doc["params"]["pathLossExponent"],
        }
        thresholds = None
        metrics = doc.get("metrics")
        warnings = doc.get("warnings") or []
        groups = None
    else:
        params = {
            "rssiAt1mDbm": doc.get("rssiAt1m"),
            "pathLossN": doc.get("pathLossExponent"),
        }
        thresholds = doc.get("thresholds")
        metrics = {
            "fit": doc.get("fit"),
            "static": doc.get("staticMetrics"),
            "groupDecision": doc.get("groupDecision"),
        }
        warnings = doc.get("warnings") or []
        groups = doc.get("groups")
    result = {
        "runId": run_id,
        "sampleId": sample.sample_id,
        "sampleType": sample.sample_type,
        "deployable": sample.deployable,
        "applyBlockedReason": blocked_reason,
        "params": params,
        "thresholds": thresholds,
        "groups": groups,
        "metrics": metrics,
        "warnings": warnings,
        "generatedAt": doc.get("generatedAt"),
        "gridPreview": None,
    }
    if grid is not None:
        result["gridPreview"] = {
            "gridVersion": grid.grid_version,
            "baseGridVersion": grid.base_grid_version,
            "pointCount": grid.point_count,
            "collectableCount": grid.collectable_count,
            "mapVersion": grid.map_version,
            "elapsedS": grid.elapsed_s,
        }
    return result


#: 网格重算串行化：全楼 2m/1m 网格生成是 CPU 密集任务，避免并发互相拖慢。
_GRID_LOCK = threading.Lock()


def compute_run(db, settings: Settings, sample_id: str) -> dict:
    """创建计算任务并异步执行；客户端轮询 GET /runs/{id}。

    全楼网格重算实测约 3-4 分钟，不能阻塞 HTTP 请求线程。
    """
    sample = db.get(CalibrationSample, sample_id)
    if sample is None:
        raise CalibrationServiceError("sample_not_found", "样本不存在", status_code=404)

    run_id = f"r-{uuid.uuid4().hex[:20]}"
    run = CalibrationRun(
        run_id=run_id,
        site_id=SITE_ID,
        sample_id=sample.sample_id,
        sample_sha256=sample.content_sha256,
        status="queued",
        created_at_epoch=_now(),
        updated_at_epoch=_now(),
    )
    db.add(run)
    db.commit()

    threading.Thread(
        target=_compute_worker, args=(settings, run_id), name=f"calib-{run_id}", daemon=True
    ).start()
    return _run_summary(run)


def _compute_worker(settings: Settings, run_id: str) -> None:
    from pathai_backend.database import session_scope

    db = session_scope()
    try:
        run = db.get(CalibrationRun, run_id)
        if run is None:
            return
        sample = db.get(CalibrationSample, run.sample_id)
        if sample is None:
            run.status = "compute_failed"
            run.error_code = "sample_missing"
            run.error_message = "样本已被删除"
            run.updated_at_epoch = _now()
            db.commit()
            return
        run.status = "computing"
        run.updated_at_epoch = _now()
        db.commit()

        out_dir = Path(settings.calibration_data_dir) / "runs" / run_id
        blocked_reason = None if sample.deployable else (
            "阶段一（对数等距）样本只用于评审，apply 永久禁用"
            if sample.sample_type == LOGDIST_TYPE
            else "样本标记为不可部署"
        )
        doc = _fit_document(settings, sample, run_id, out_dir)
        a = float(
            doc["params"]["rssiAt1mDbm"]
            if sample.sample_type == LOGDIST_TYPE
            else doc["rssiAt1m"]
        )
        n = float(
            doc["params"]["pathLossExponent"]
            if sample.sample_type == LOGDIST_TYPE
            else doc["pathLossExponent"]
        )
        grid: GridRecomputeResult | None = None
        if sample.deployable:
            with _GRID_LOCK:
                grid = recompute_grid(
                    settings,
                    run_id=run_id,
                    params_version=f"calc-{run_id}",
                    a=a,
                    n=n,
                )
        result = _result_summary(
            sample, doc, run_id=run_id, grid=grid, blocked_reason=blocked_reason
        )
        run.status = "computed"
        run.result_json = json.dumps(result, ensure_ascii=False)
        run.grid_version = grid.grid_version if grid else None
        run.base_grid_version = grid.base_grid_version if grid else None
        run.grid_staging_path = str(grid.staging_dir) if grid else None
        run.updated_at_epoch = _now()
        db.commit()
        audit(db, "calibration_compute", True, {
            "runId": run_id, "sampleId": sample.sample_id, "params": result["params"],
        })
        db.commit()
    except GridRecomputeError as exc:
        _fail_run(db, run_id, exc.code, exc.message)
    except CalibrationServiceError as exc:
        _fail_run(db, run_id, exc.code, exc.message)
    except Exception as exc:  # 后台线程必须吞掉异常并落状态
        _fail_run(db, run_id, "compute_failed", str(exc))
    finally:
        db.close()


def _fail_run(db, run_id: str, code: str, message: str) -> None:
    db.rollback()
    run = db.get(CalibrationRun, run_id)
    if run is None:
        return
    run.status = "compute_failed"
    run.error_code = code
    run.error_message = message
    run.updated_at_epoch = _now()
    db.commit()
    audit(db, "calibration_compute", False, {"runId": run_id, "error": code})
    db.commit()


# ---------------------------------------------------------------------------
# apply / 版本 / 回滚
# ---------------------------------------------------------------------------


def _verify_staging(run: CalibrationRun, result: dict) -> tuple[Path, Path]:
    if run.grid_staging_path is None:
        raise CalibrationServiceError("grid_not_computed", "本次计算没有网格产物")
    staging = Path(run.grid_staging_path)
    manifest_path = staging / "manifest.json"
    overlay_path = staging / "overlay.json"
    if not manifest_path.exists() or not overlay_path.exists():
        raise CalibrationServiceError("grid_staging_missing", "网格 staging 产物缺失，请重新计算")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("runId") != run.run_id:
        raise CalibrationServiceError("grid_staging_mismatch", "staging 与 run 不匹配")
    params = result.get("params") or {}
    if abs(float(manifest["params"]["rssiAt1mDbm"]) - float(params["rssiAt1mDbm"])) > 1e-6:
        raise CalibrationServiceError("grid_params_mismatch", "网格 A 与计算结果不一致")
    if abs(float(manifest["params"]["pathLossN"]) - float(params["pathLossN"])) > 1e-9:
        raise CalibrationServiceError("grid_params_mismatch", "网格 n 与计算结果不一致")
    return overlay_path, manifest_path


def apply_run(
    db,
    settings: Settings,
    runtime: RuntimeParams,
    *,
    run_id: str,
    confirm: bool,
    expect_param_version: str | None,
) -> dict:
    if not confirm:
        raise CalibrationServiceError("confirm_required", "apply 必须携带二次确认")
    run = db.get(CalibrationRun, run_id)
    if run is None:
        raise CalibrationServiceError("run_not_found", "计算任务不存在", status_code=404)
    if run.status != "computed":
        raise CalibrationServiceError("run_not_computed", f"任务状态不可 apply：{run.status}")
    sample = db.get(CalibrationSample, run.sample_id)
    if sample is None or not sample.deployable:
        raise CalibrationServiceError("not_deployable", "该样本不可部署，apply 已禁用")

    result = json.loads(run.result_json or "{}")
    overlay_src, _manifest_src = _verify_staging(run, result)

    pointer = _active_pointer(db)
    current = pointer.param_version if pointer else None
    if (expect_param_version or None) != current:
        raise CalibrationServiceError(
            "version_conflict",
            f"当前生效版本已变化（期望 {expect_param_version or None}，实际 {current}）",
            status_code=409,
        )

    params = result["params"]
    thresholds = result.get("thresholds") or {}
    reliable = float(thresholds.get("residualReliableM") or settings.location_residual_reliable_m)
    degraded = float(thresholds.get("residualDegradedM") or settings.location_residual_degraded_m)
    a = float(params["rssiAt1mDbm"])
    n = float(params["pathLossN"])
    if not (a < 0):
        raise CalibrationServiceError("params_invalid", "A 必须为负值（dBm）")
    if not (1.0 <= n <= 6.0):
        raise CalibrationServiceError("params_invalid", "n 超出合理范围 [1, 6]")

    grid_result = GridRecomputeResult(
        grid_version=run.grid_version or "",
        base_grid_version=run.base_grid_version or "",
        staging_dir=Path(run.grid_staging_path or ""),
        grid_path=Path(run.grid_staging_path or "") / "grid.json",
        overlay_path=overlay_src,
        manifest_path=Path(run.grid_staging_path or "") / "manifest.json",
        map_version=sample.map_version,
        point_count=int((result.get("gridPreview") or {}).get("pointCount") or 0),
        collectable_count=int((result.get("gridPreview") or {}).get("collectableCount") or 0),
        elapsed_s=0.0,
    )
    try:
        overlay_path, manifest_path = publish_staging(settings, grid_result)
    except GridRecomputeError as exc:
        raise CalibrationServiceError(exc.code, exc.message, status_code=409) from exc

    version = f"loc-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}-{run_id[-4:]}"
    try:
        db.add(
            FingerprintGridVersion(
                version=run.grid_version or "",
                site_id=SITE_ID,
                base_grid_version=run.base_grid_version or "",
                map_version=sample.map_version,
                rssi_at_1m=a,
                path_loss_n=n,
                overlay_path=overlay_path,
                manifest_path=manifest_path,
                point_count=grid_result.point_count,
                collectable_count=grid_result.collectable_count,
                created_at_epoch=_now(),
            )
        )
        db.add(
            LocationParamVersion(
                version=version,
                site_id=SITE_ID,
                rssi_at_1m=a,
                path_loss_n=n,
                residual_reliable_m=reliable,
                residual_degraded_m=degraded,
                grid_version=run.grid_version or "",
                run_id=run.run_id,
                source="apply",
                created_at_epoch=_now(),
            )
        )
        if pointer is None:
            db.add(
                CalibrationActivePointer(
                    site_id=SITE_ID,
                    param_version=version,
                    grid_version=run.grid_version or "",
                    updated_at_epoch=_now(),
                )
            )
        else:
            pointer.param_version = version
            pointer.grid_version = run.grid_version or ""
            pointer.updated_at_epoch = _now()
        for old in db.scalars(
            select(CalibrationRun).where(
                CalibrationRun.site_id == SITE_ID, CalibrationRun.status == "applied"
            )
        ):
            old.status = "superseded"
        run.status = "applied"
        run.updated_at_epoch = _now()
        audit(db, "calibration_apply", True, {
            "runId": run.run_id, "paramVersion": version,
            "gridVersion": run.grid_version, "params": params,
        })
        db.commit()
    except Exception as exc:
        db.rollback()
        # 回滚已移动的目录，保证线上不可见
        target = Path(settings.calibration_data_dir) / "grids" / (run.grid_version or "")
        staging = Path(run.grid_staging_path or "")
        if target.exists() and staging.parent.exists():
            try:
                if not staging.exists():
                    target.rename(staging)
            except OSError:
                pass
        raise CalibrationServiceError("apply_failed", f"apply 失败：{exc}", status_code=500) from exc

    _prune_versions(db)
    runtime.invalidate()
    return {
        "applied": True,
        "paramVersion": version,
        "gridVersion": run.grid_version,
        "runtime": runtime.snapshot(),
    }


def _prune_versions(db) -> None:
    rows = list(
        db.scalars(
            select(LocationParamVersion)
            .where(LocationParamVersion.site_id == SITE_ID)
            .order_by(LocationParamVersion.created_at_epoch.desc())
        )
    )
    for row in rows[MAX_KEEP_PAIRS:]:
        grid = db.get(FingerprintGridVersion, row.grid_version)
        db.delete(row)
        if grid is not None:
            still_used = db.scalars(
                select(LocationParamVersion).where(
                    LocationParamVersion.grid_version == row.grid_version,
                    LocationParamVersion.version != row.version,
                )
            ).first()
            if still_used is None:
                db.delete(grid)
    db.commit()


def list_versions(db) -> dict:
    pointer = _active_pointer(db)
    rows = list(
        db.scalars(
            select(LocationParamVersion)
            .where(LocationParamVersion.site_id == SITE_ID)
            .order_by(LocationParamVersion.created_at_epoch.desc())
            .limit(MAX_KEEP_PAIRS)
        )
    )
    items = []
    for row in rows:
        grid = db.get(FingerprintGridVersion, row.grid_version)
        items.append(
            {
                "paramVersion": row.version,
                "gridVersion": row.grid_version,
                "baseGridVersion": grid.base_grid_version if grid else None,
                "rssiAt1mDbm": row.rssi_at_1m,
                "pathLossN": row.path_loss_n,
                "residualReliableM": row.residual_reliable_m,
                "residualDegradedM": row.residual_degraded_m,
                "runId": row.run_id,
                "source": row.source,
                "createdAt": _epoch_iso(row.created_at_epoch),
                "active": pointer is not None and pointer.param_version == row.version,
            }
        )
    return {"active": {
        "paramVersion": pointer.param_version if pointer else None,
        "gridVersion": pointer.grid_version if pointer else None,
    }, "items": items}


def rollback(db, runtime: RuntimeParams, *, param_version: str, confirm: bool) -> dict:
    if not confirm:
        raise CalibrationServiceError("confirm_required", "回滚必须携带二次确认")
    target = db.get(LocationParamVersion, param_version)
    if target is None:
        raise CalibrationServiceError("version_not_found", "目标版本不存在", status_code=404)
    pointer = _active_pointer(db)
    if pointer is None:
        raise CalibrationServiceError("no_active_version", "当前没有生效版本", status_code=409)
    if pointer.param_version == target.version:
        raise CalibrationServiceError("already_active", "目标版本已经是当前生效版本", status_code=409)
    current_grid = db.get(FingerprintGridVersion, pointer.grid_version)
    target_grid = db.get(FingerprintGridVersion, target.grid_version)
    if current_grid is None or target_grid is None:
        raise CalibrationServiceError("grid_version_missing", "版本缺网格记录", status_code=409)
    if current_grid.base_grid_version != target_grid.base_grid_version:
        raise CalibrationServiceError(
            "grid_base_mismatch",
            "禁止跨基线回滚：目标网格的内置基线版本与当前不一致",
            status_code=409,
        )
    pointer.param_version = target.version
    pointer.grid_version = target.grid_version
    pointer.updated_at_epoch = _now()
    audit(db, "calibration_rollback", True, {
        "from": current_grid.version, "to": target.version, "gridVersion": target.grid_version,
    })
    db.commit()
    runtime.invalidate()
    return {
        "rolledBack": True,
        "paramVersion": target.version,
        "gridVersion": target.grid_version,
        "runtime": runtime.snapshot(),
    }


def latest_run(db, sample_id: str | None = None) -> dict | None:
    stmt = select(CalibrationRun).where(CalibrationRun.site_id == SITE_ID)
    if sample_id:
        stmt = stmt.where(CalibrationRun.sample_id == sample_id)
    run = db.scalars(stmt.order_by(CalibrationRun.created_at_epoch.desc()).limit(1)).first()
    return _run_summary(run) if run else None
