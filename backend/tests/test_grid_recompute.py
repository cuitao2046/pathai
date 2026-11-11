# -*- coding: utf-8 -*-
"""grid_recompute 单元测试：staging/manifest/overlay 逻辑与全部失败分支。

src 网格生成器（generate_full_grid）以假实现替换（小样本），被测对象是
grid_recompute 自身逻辑：基线就绪判定、版本读取、overlay 统计、四种
GridRecomputeError 分支、staging 产物与 manifest 完整性。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from pathai_backend import grid_recompute
from pathai_backend.config import Settings
from pathai_backend.grid_recompute import GridRecomputeError, recompute_grid

BASELINE_FILES = (
    "school_building_01_map_v9.geojson",
    "ble_deployment.json",
    "beacon_routes.json",
    "simulated_beacons.json",
    "fingerprint_grid.json",
)


class _FakeGridGen:
    """小样本生成器替身：楼层 F1 固定 N 个点，行为由类属性控制。"""

    GRID_VERSION = "9.9"
    #: 测试用例可覆盖的产物
    next_grid: dict | None = None
    error: Exception | None = None
    last_beacons: list | None = None

    @staticmethod
    def load_beacons(map_package):
        return [{"beaconId": "BK-1"}]

    @staticmethod
    def load_sim_beacons(path):
        return []

    @staticmethod
    def generate_full_grid(geo, beacons, sim_beacons, routes, *, beacons_label,
                           a, n, grid_version, progress):
        if _FakeGridGen.error is not None:
            raise _FakeGridGen.error
        _FakeGridGen.last_beacons = beacons
        if _FakeGridGen.next_grid is not None:
            return _FakeGridGen.next_grid, [], []

        def point(i, collectable=True, sim=0):
            return {
                "fpId": f"F1-p{i}",
                "collectablePlan10m": collectable,
                "collectableGeo15m": collectable,
                "collectableWithSim": bool(sim),
                "simAddedBy": ["sim"] * sim,
                "visible10m": 3,
                "strongestRssi": -60,
            }

        return {"floors": {"1": {"points": [point(1), point(2)]}}}, [], []


@pytest.fixture()
def settings(tmp_path, monkeypatch):
    baseline = tmp_path / "baseline"
    baseline.mkdir()
    for name in BASELINE_FILES:
        (baseline / name).write_text("{}", encoding="utf-8")
    maps = tmp_path / "maps" / "school-building-01"
    maps.mkdir(parents=True)
    (maps / "2026-11-01.1.json").write_text("{}", encoding="utf-8")
    _FakeGridGen.next_grid = None
    _FakeGridGen.error = None
    monkeypatch.setattr(grid_recompute, "gridgen", _FakeGridGen)
    return Settings.from_env({
        "PATHAI_GRID_BASELINE_DIR": str(baseline),
        "PATHAI_MAP_DATA_DIR": str(tmp_path / "maps"),
        "PATHAI_CALIBRATION_DATA_DIR": str(tmp_path / "calibration"),
        "PATHAI_DATABASE_URL": "sqlite:///:memory:",
    })


class TestBaselineStatus:
    def test_ready_when_all_files_present(self, settings):
        status = grid_recompute.baseline_status(settings)
        assert status["ready"] is True
        assert status["missing"] == []

    def test_missing_files_listed_and_not_ready(self, settings):
        (Path(settings.calibration_grid_baseline_dir) / "ble_deployment.json").unlink()
        status = grid_recompute.baseline_status(settings)
        assert status["ready"] is False
        assert status["missing"] == ["ledger"]

    def test_read_base_grid_version_prefers_baseline_file(self, settings):
        grid_file = Path(settings.calibration_grid_baseline_dir) / "fingerprint_grid.json"
        grid_file.write_text(json.dumps({"version": "v-baseline"}), encoding="utf-8")
        assert grid_recompute.read_base_grid_version(settings) == "v-baseline"

    def test_read_base_grid_version_falls_back_to_generator_constant(self, settings):
        assert grid_recompute.read_base_grid_version(settings) == _FakeGridGen.GRID_VERSION


class TestRecomputeFailures:
    def test_baseline_missing(self, settings):
        (Path(settings.calibration_grid_baseline_dir) / "ble_deployment.json").unlink()
        with pytest.raises(GridRecomputeError) as exc:
            recompute_grid(settings, run_id="r1", params_version="p1", a=-52.0, n=3.5)
        assert exc.value.code == "grid_baseline_missing"

    def test_map_package_missing(self, settings):
        for p in Path(settings.map_data_dir, "school-building-01").glob("*.json"):
            p.unlink()
        with pytest.raises(GridRecomputeError) as exc:
            recompute_grid(settings, run_id="r1", params_version="p1", a=-52.0, n=3.5)
        assert exc.value.code == "map_package_missing"

    def test_generator_exception_wrapped(self, settings):
        _FakeGridGen.error = RuntimeError("boom")
        with pytest.raises(GridRecomputeError) as exc:
            recompute_grid(settings, run_id="r1", params_version="p1", a=-52.0, n=3.5)
        assert exc.value.code == "grid_generate_failed"

    def test_empty_grid_rejected(self, settings):
        _FakeGridGen.next_grid = {"floors": {}}
        with pytest.raises(GridRecomputeError) as exc:
            recompute_grid(settings, run_id="r1", params_version="p1", a=-52.0, n=3.5)
        assert exc.value.code == "grid_empty"

    def test_empty_floor_rejected(self, settings):
        _FakeGridGen.next_grid = {
            "floors": {
                "1": {"points": [{"fpId": "p1", "collectablePlan10m": True,
                                  "collectableGeo15m": True, "collectableWithSim": True,
                                  "simAddedBy": [], "visible10m": 1, "strongestRssi": -60}]},
                "2": {"points": []},
            }
        }
        with pytest.raises(GridRecomputeError) as exc:
            recompute_grid(settings, run_id="r1", params_version="p1", a=-52.0, n=3.5)
        assert exc.value.code == "grid_floor_empty"

    def test_no_collectable_rejected(self, settings):
        _FakeGridGen.next_grid = {
            "floors": {"1": {"points": [
                {"fpId": "p1", "collectablePlan10m": False, "collectableGeo15m": False,
                 "collectableWithSim": False, "simAddedBy": [], "visible10m": 0,
                 "strongestRssi": None},
            ]}}
        }
        with pytest.raises(GridRecomputeError) as exc:
            recompute_grid(settings, run_id="r1", params_version="p1", a=-52.0, n=3.5)
        assert exc.value.code == "grid_no_collectable"


class TestRecomputeHappyPath:
    def test_staging_artifacts_and_manifest(self, settings, tmp_path):
        ledger = {
            "beacons": [{"beaconId": "BK-1", "installHeight": 2.5}]
        }
        (Path(settings.calibration_grid_baseline_dir) / "ble_deployment.json").write_text(
            json.dumps(ledger), encoding="utf-8"
        )
        result = recompute_grid(
            settings, run_id="run-abc123", params_version="pv-1", a=-52.0, n=3.5
        )
        assert result.point_count == 2
        assert result.collectable_count == 2
        assert result.map_version == "2026-11-01.1"
        assert result.grid_version.startswith("g-") and "run-ab" in result.grid_version
        # staging 三件套落盘
        assert result.grid_path.exists() and result.overlay_path.exists()
        assert result.manifest_path.exists()
        # 台账安装高度已覆盖进信标（_apply_install_heights）
        assert _FakeGridGen.last_beacons[0]["installHeightM"] == 2.5
        manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))
        assert manifest["runId"] == "run-abc123"
        assert manifest["params"] == {"rssiAt1mDbm": -52.0, "pathLossN": 3.5}
        assert manifest["counts"]["points"] == 2
        assert manifest["counts"]["collectable"] == 2
        assert set(manifest["inputs"]) == {"geo", "ledger", "routes", "simBeacons", "baselineGrid"}
        assert all(entry["sha256"] for entry in manifest["inputs"].values())
        overlay = json.loads(result.overlay_path.read_text(encoding="utf-8"))
        assert overlay["schemaVersion"] == "1.0"
        assert overlay["paramsVersion"] == "pv-1"
        assert overlay["floors"]["1"]["points"] == 2

    def test_staging_rerun_replaces_previous_dir(self, settings):
        first = recompute_grid(settings, run_id="r1", params_version="p1", a=-52.0, n=3.5)
        second = recompute_grid(settings, run_id="r1", params_version="p1", a=-52.0, n=3.5)
        assert first.staging_dir == second.staging_dir
        assert second.grid_path.exists()
