# -*- coding: utf-8 -*-
"""定位参数拟合脚本回归测试（src/tools/fit_location_params.py，docs/28 §5/§7）。

数据约束（docs/开发规范.md §4）：合成观测必须由**真实地图包节点坐标与真实信标台账**
反推，不虚构点位/信标；真值距离由台账重算并与样本声明的 truthDistanceM 交叉校验。
"""
import contextlib
import copy
import io
import json
import math
import tempfile
import unittest
from pathlib import Path

from tests.testutil import PROJECT_ROOT, RESULT_DIR, load_json
from src.tools.fit_location_params import (
    CalibrationError,
    FitResult,
    build_params_document,
    fit_pairs,
    run,
    should_use_floor_groups,
    validate_samples,
)

LEDGER_PATH = RESULT_DIR / "ble_deployment.json"
MAP_PATH = (
    PROJECT_ROOT / "backend" / "maps_data" / "school-building-01" / "2026-10-25.1.json"
)
LEDGER = load_json(LEDGER_PATH)
MAP = load_json(MAP_PATH)
BEACONS = LEDGER["beacons"]
A_TRUE = -69.0
N_TRUE = 2.31


def _node_coords(floor_id: str) -> list[tuple[str, list[float]]]:
    nodes = MAP["floors"][floor_id]["topology"]["nodes"]
    exact = {(b["coordinates"][0], b["coordinates"][1]) for b in BEACONS}
    return [
        (n["id"], n["coordinates"])
        for n in nodes
        if (n["coordinates"][0], n["coordinates"][1]) not in exact
    ]


def _pick_points(floor_id: str, count: int) -> list[tuple[str, list[float]]]:
    nodes = sorted(_node_coords(floor_id), key=lambda item: item[0])
    step = max(1, len(nodes) // count)
    return nodes[::step][:count]


def build_samples(points_per_floor: dict[str, int] | None = None) -> dict:
    points_per_floor = points_per_floor or {"1": 8, "2": 6}
    out_points = []
    for floor_id, count in points_per_floor.items():
        floor = int(floor_id)
        for index, (_node_id, truth) in enumerate(_pick_points(floor_id, count), start=1):
            observations = []
            for beacon in BEACONS:
                if beacon["floor"] != floor:
                    continue
                distance = math.hypot(
                    truth[0] - beacon["coordinates"][0],
                    truth[1] - beacon["coordinates"][1],
                )
                if distance < 0.5:
                    continue
                rssi = round(A_TRUE - 10 * N_TRUE * math.log10(distance))
                if not -100 <= rssi <= -20:
                    continue
                observations.append(
                    {
                        "beaconId": beacon["beaconId"],
                        "n": 90,
                        "rssiMedian": rssi,
                        "rssiMin": rssi - 2,
                        "rssiMax": rssi + 2,
                        "rssiStd": 1.0,
                        "truthDistanceM": round(distance, 2),
                    }
                )
            out_points.append(
                {
                    "pointId": f"CP-{floor_id}-{index:02d}",
                    "floor": floor,
                    "truth": truth,
                    "scene": "测试",
                    "envNote": "",
                    "markType": "",
                    "sessions": [
                        {
                            "sessionIndex": 1,
                            "startedAt": "2026-11-01T09:00:00Z",
                            "durationS": 90,
                            "trafficWindow": "peak",
                            "observations": observations,
                        }
                    ],
                }
            )
    return {
        "schemaVersion": "1.0",
        "type": "positioning_calibration_static",
        "venueId": "school-building-01",
        "operator": "测试",
        "device": {"model": "test"},
        "appVersion": "1.0.0",
        "beaconLedgerSha256": "0" * 64,
        "mapVersion": "2026-10-25.1",
        "windowMs": 2000,
        "points": out_points,
    }


class TestFitInversion(unittest.TestCase):
    def test_recovers_global_params_from_real_geometry(self):
        samples = build_samples()
        pairs = validate_samples(samples, LEDGER)
        self.assertGreater(len(pairs), 100)
        fit = fit_pairs(pairs)
        self.assertAlmostEqual(fit.intercept, A_TRUE, delta=0.5)
        self.assertAlmostEqual(fit.path_loss_n, N_TRUE, delta=0.05)
        self.assertGreater(fit.r2, 0.98)
        self.assertEqual(fit.pairs, len(pairs))

    def test_fit_requires_minimum_pairs(self):
        pairs = validate_samples(build_samples(), LEDGER)
        with self.assertRaises(CalibrationError):
            fit_pairs(pairs[:2])


class TestValidation(unittest.TestCase):
    def test_truth_distance_mismatch_rejected(self):
        samples = copy.deepcopy(build_samples())
        first = samples["points"][0]["sessions"][0]["observations"][0]
        first["truthDistanceM"] = round(first["truthDistanceM"] + 1.0, 2)
        with self.assertRaises(CalibrationError) as ctx:
            validate_samples(samples, LEDGER)
        self.assertIn("truthDistanceM", str(ctx.exception))

    def test_unknown_beacon_rejected(self):
        samples = copy.deepcopy(build_samples())
        samples["points"][0]["sessions"][0]["observations"][0]["beaconId"] = "GHOST-9"
        with self.assertRaises(CalibrationError) as ctx:
            validate_samples(samples, LEDGER)
        self.assertIn("台账外信标", str(ctx.exception))

    def test_empty_points_rejected(self):
        samples = build_samples()
        samples["points"] = []
        with self.assertRaises(CalibrationError):
            validate_samples(samples, LEDGER)


class TestGroupDecision(unittest.TestCase):
    @staticmethod
    def _fit(rmse: float, pairs: int) -> FitResult:
        return FitResult(intercept=-69.0, path_loss_n=2.3, r2=0.9, rmse=rmse, pairs=pairs)

    def test_enabled_when_sample_and_improvement_pass(self):
        use, reason = should_use_floor_groups(
            self._fit(2.0, 200), {1: self._fit(1.0, 120), 2: self._fit(1.0, 80)}
        )
        self.assertTrue(use, reason)
        self.assertIn("下降", reason)

    def test_disabled_when_any_floor_sample_short(self):
        use, reason = should_use_floor_groups(
            self._fit(2.0, 200), {1: self._fit(1.0, 120), 2: self._fit(1.0, 49)}
        )
        self.assertFalse(use)
        self.assertIn("样本不足", reason)

    def test_disabled_when_improvement_not_significant(self):
        use, reason = should_use_floor_groups(
            self._fit(2.0, 200), {1: self._fit(1.6, 120), 2: self._fit(1.6, 80)}
        )
        self.assertFalse(use)
        self.assertIn("仅下降", reason)

    def test_disabled_with_single_floor(self):
        use, _ = should_use_floor_groups(self._fit(2.0, 200), {1: self._fit(1.0, 200)})
        self.assertFalse(use)


class TestEndToEnd(unittest.TestCase):
    def test_run_writes_params_document_and_recommends_thresholds(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            samples_path = tmp_path / "static_samples.json"
            samples_path.write_text(
                json.dumps(build_samples(), ensure_ascii=False), encoding="utf-8"
            )
            out_path = tmp_path / "location_params_loc-test.1.json"
            doc = run(
                samples_path,
                LEDGER_PATH,
                out_path,
                map_package_path=MAP_PATH,
                params_version="loc-test.1",
                min_group_pairs=10_000,
            )

            self.assertTrue(out_path.is_file())
            self.assertEqual(doc["paramsVersion"], "loc-test.1")
            self.assertAlmostEqual(doc["rssiAt1m"], A_TRUE, delta=0.5)
            self.assertAlmostEqual(doc["pathLossExponent"], N_TRUE, delta=0.05)
            thresholds = doc["thresholds"]
            self.assertLessEqual(
                thresholds["residualReliableM"], thresholds["residualDegradedM"]
            )
            self.assertGreaterEqual(thresholds["originMinConfidence"], 0.0)
            self.assertLessEqual(thresholds["originMinConfidence"], 1.0)
            static = doc["metrics"]["staticReplay"]
            self.assertEqual(static["sessions"], len(build_samples()["points"]))
            self.assertIsNotNone(static["cep90"])
            self.assertEqual(static["estimated"] + static["lost"], static["sessions"])
            self.assertGreater(static["estimated"], 0)
            self.assertIsNone(doc["groups"], "min_group_pairs 被抬高后应关闭分组")

    def test_cli_rejects_bad_samples_with_nonzero_exit(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            samples_path = tmp_path / "bad.json"
            samples_path.write_text(json.dumps({"schemaVersion": "1.0"}), encoding="utf-8")
            stderr = io.StringIO()
            from src.tools.fit_location_params import main

            with contextlib.redirect_stderr(stderr):
                code = main(
                    [
                        "--samples", str(samples_path),
                        "--ledger", str(LEDGER_PATH),
                        "--out", str(tmp_path / "out.json"),
                    ]
                )
            self.assertEqual(code, 1)
            self.assertIn("type", stderr.getvalue())
            self.assertFalse((tmp_path / "out.json").exists())


if __name__ == "__main__":
    unittest.main()
