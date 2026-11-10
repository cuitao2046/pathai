# -*- coding: utf-8 -*-
"""指纹网格重算（specs/calibration-console §7）。

约束：
- 只以库方式复用 `src/tools/generate_fingerprint_grid.py`，不复制算法；
- 使用 apply 的实测全局 A/n；墙/门衰减、-85 dBm、≥3 信标口径保持工程常量；
- 全部产物先写 staging，校验通过后才由 apply 与参数同事务发布；
- `fpId` 由坐标派生，A/n 变化不影响编号。
"""
from __future__ import annotations

import hashlib
import json
import shutil
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from pathai_backend.config import Settings

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.tools import generate_fingerprint_grid as gridgen  # noqa: E402

SITE_ID = "school-building-01"
GEO_FILE = "school_building_01_map_v9.geojson"
LEDGER_FILE = "ble_deployment.json"
ROUTES_FILE = "beacon_routes.json"
SIM_FILE = "simulated_beacons.json"
BASELINE_GRID_FILE = "fingerprint_grid.json"


class GridRecomputeError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass
class GridRecomputeResult:
    grid_version: str
    base_grid_version: str
    staging_dir: Path
    grid_path: Path
    overlay_path: Path
    manifest_path: Path
    map_version: str | None
    point_count: int
    collectable_count: int
    elapsed_s: float


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def baseline_files(settings: Settings) -> dict[str, Path]:
    base = Path(settings.calibration_grid_baseline_dir)
    return {
        "geo": base / GEO_FILE,
        "ledger": base / LEDGER_FILE,
        "routes": base / ROUTES_FILE,
        "simBeacons": base / SIM_FILE,
        "baselineGrid": base / BASELINE_GRID_FILE,
    }


def baseline_status(settings: Settings) -> dict:
    files = baseline_files(settings)
    missing = [name for name, path in files.items() if not path.exists()]
    return {
        "baselineDir": str(settings.calibration_grid_baseline_dir),
        "files": {name: str(path) for name, path in files.items()},
        "missing": missing,
        "ready": len(missing) == 0,
    }


def read_base_grid_version(settings: Settings) -> str:
    path = baseline_files(settings)["baselineGrid"]
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data.get("version"), str):
                return data["version"]
        except (OSError, ValueError):
            pass
    return str(gridgen.GRID_VERSION)


def _latest_map_package(settings: Settings) -> Path | None:
    directory = Path(settings.map_data_dir) / SITE_ID
    if not directory.exists():
        return None
    candidates = sorted(directory.glob("*.json"), key=lambda p: p.stat().st_mtime)
    return candidates[-1] if candidates else None


def _apply_install_heights(beacons: list[dict], ledger_path: Path) -> None:
    """按部署台账逐信标覆盖安装高度（台账字段为 installHeight）。"""
    try:
        ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    heights = {
        b.get("beaconId"): b.get("installHeight")
        for b in ledger.get("beacons", [])
        if b.get("beaconId") and isinstance(b.get("installHeight"), (int, float))
    }
    for beacon in beacons:
        height = heights.get(beacon.get("beaconId"))
        if height is not None:
            beacon["installHeightM"] = float(height)


def _overlay_doc(
    grid: dict,
    *,
    base_grid_version: str,
    grid_version: str,
    params_version: str,
    a: float,
    n: float,
) -> dict:
    points: list[dict] = []
    per_floor: dict[str, dict[str, int]] = {}
    counts: dict[str, int] = {}
    for floor_key, floor in grid.get("floors", {}).items():
        floor_counts = {
            "points": 0,
            "collectable": 0,
            "collectable15m": 0,
            "collectableWithSim": 0,
        }
        for p in floor.get("points", []):
            floor_counts["points"] += 1
            if p.get("collectablePlan10m"):
                floor_counts["collectable"] += 1
            if p.get("collectableGeo15m"):
                floor_counts["collectable15m"] += 1
            if p.get("collectableWithSim"):
                floor_counts["collectableWithSim"] += 1
            points.append(
                {
                    "fpId": p.get("fpId"),
                    "collectable": bool(p.get("collectablePlan10m")),
                    "collectable15m": bool(p.get("collectableGeo15m")),
                    "collectableWithSim": bool(p.get("collectableWithSim")),
                    "simAddedBy": list(p.get("simAddedBy") or []),
                    "visible10m": int(p.get("visible10m") or 0),
                    "strongestRssi": p.get("strongestRssi"),
                }
            )
        per_floor[str(floor_key)] = floor_counts
        counts[str(floor_key)] = floor_counts["collectable"]
    return {
        # 与客户端 grid-overlay 校验口径一致（spec §7.3.3）
        "schemaVersion": "1.0",
        "baseGridVersion": base_grid_version,
        "gridVersion": grid_version,
        "paramsVersion": params_version,
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "params": {"a": a, "n": n},
        "counts": counts,
        "floors": per_floor,
        "points": points,
    }


def recompute_grid(
    settings: Settings,
    *,
    run_id: str,
    params_version: str,
    a: float,
    n: float,
) -> GridRecomputeResult:
    """在 staging 中重算并校验网格；失败抛 GridRecomputeError，不触碰线上产物。"""
    started = time.monotonic()
    files = baseline_files(settings)
    status = baseline_status(settings)
    if not status["ready"]:
        raise GridRecomputeError(
            "grid_baseline_missing",
            f"网格基线缺失：{', '.join(status['missing'])}",
        )
    map_package = _latest_map_package(settings)
    if map_package is None:
        raise GridRecomputeError("map_package_missing", "没有可用地图包，无法重算网格")

    try:
        geo = json.loads(files["geo"].read_text(encoding="utf-8"))
        routes = (
            json.loads(files["routes"].read_text(encoding="utf-8")).get("routes", [])
            if files["routes"].exists()
            else []
        )
        beacons = gridgen.load_beacons(map_package)
        _apply_install_heights(beacons, files["ledger"])
        sim_beacons = gridgen.load_sim_beacons(files["simBeacons"])
        grid, _points, _route_geom = gridgen.generate_full_grid(
            geo,
            beacons,
            sim_beacons,
            routes,
            beacons_label=map_package.stem,
            a=a,
            n=n,
            grid_version=f"{gridgen.GRID_VERSION}+{run_id}",
            progress=False,
        )
    except GridRecomputeError:
        raise
    except Exception as exc:  # 生成器内部异常统一转成可回退错误
        raise GridRecomputeError("grid_generate_failed", f"网格生成失败：{exc}") from exc

    base_grid_version = read_base_grid_version(settings)
    grid_version = f"g-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}-{run_id[:6]}"
    overlay = _overlay_doc(
        grid,
        base_grid_version=base_grid_version,
        grid_version=grid_version,
        params_version=params_version,
        a=a,
        n=n,
    )
    per_floor = overlay["floors"]
    empty_floors = [f for f, c in per_floor.items() if c["points"] == 0]
    collectable = sum(c["collectable"] for c in per_floor.values())
    total = sum(c["points"] for c in per_floor.values())
    if total == 0:
        raise GridRecomputeError("grid_empty", "重算结果没有网格点")
    if empty_floors:
        raise GridRecomputeError("grid_floor_empty", f"以下楼层无网格点：{empty_floors}")
    if collectable == 0:
        raise GridRecomputeError("grid_no_collectable", "重算结果没有可采集点，请检查 A/n")

    staging = Path(settings.calibration_data_dir) / "staging" / run_id
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True, exist_ok=True)
    grid_path = staging / "grid.json"
    overlay_path = staging / "overlay.json"
    manifest_path = staging / "manifest.json"
    grid_path.write_text(json.dumps(grid, ensure_ascii=False, indent=2), encoding="utf-8")
    overlay_path.write_text(json.dumps(overlay, ensure_ascii=False), encoding="utf-8")
    manifest = {
        "schemaVersion": "fingerprint_grid_manifest_v1",
        "runId": run_id,
        "siteId": SITE_ID,
        "gridVersion": grid_version,
        "baseGridVersion": base_grid_version,
        "paramsVersion": params_version,
        "mapVersion": map_package.stem,
        "params": {"rssiAt1mDbm": a, "pathLossN": n},
        "generatorVersion": str(gridgen.GRID_VERSION),
        "counts": {
            "points": total,
            "collectable": collectable,
            "floors": per_floor,
        },
        "inputs": {
            name: {"path": str(path), "sha256": _sha256(path)}
            for name, path in files.items()
            if path.exists()
        },
        "mapPackage": {"path": str(map_package), "sha256": _sha256(map_package)},
        "createdAt": datetime.now(timezone.utc).isoformat(),
    }
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return GridRecomputeResult(
        grid_version=grid_version,
        base_grid_version=base_grid_version,
        staging_dir=staging,
        grid_path=grid_path,
        overlay_path=overlay_path,
        manifest_path=manifest_path,
        map_version=map_package.stem,
        point_count=total,
        collectable_count=collectable,
        elapsed_s=round(time.monotonic() - started, 3),
    )


def publish_staging(settings: Settings, result: GridRecomputeResult) -> tuple[str, str]:
    """把 staging 目录原子移动到正式目录；返回 (overlay_path, manifest_path)。"""
    target = Path(settings.calibration_data_dir) / "grids" / result.grid_version
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        raise GridRecomputeError("grid_version_conflict", "网格版本目录已存在")
    shutil.move(str(result.staging_dir), str(target))
    return str(target / "overlay.json"), str(target / "manifest.json")
