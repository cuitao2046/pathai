# -*- coding: utf-8 -*-
"""阶段一对数等距拟合回归：type 分派、单信标约束、A/n 反演与不可部署标记。"""
import json
import math
import tempfile
import unittest
from pathlib import Path

from src.tools.fit_location_params import (
    CalibrationError,
    fit_logdist,
    is_logdist_samples,
    run,
    validate_logdist_samples,
)

A_TRUE = -52.0
N_TRUE = 3.5
INSTALL_H = 2.5
STOPS = [0, 1, 1.5, 2, 3, 4.5, 6.5, 9, 13, 18]


def _sample(beacon_id: str = "BK-01-007", stops: list[float] | None = None) -> dict:
    stops_data = []
    for index, h in enumerate(stops if stops is not None else STOPS):
        distance = math.sqrt(h ** 2 + (INSTALL_H - 1.2) ** 2)
        rssi = round(A_TRUE - 10 * N_TRUE * math.log10(distance))
        stops_data.append(
            {
                "stopId": f"LD-{index:02d}",
                "horizontalDistanceM": h,
                "slantDistanceM": round(distance, 2),
                "sessions": [
                    {
                        "startedAt": f"2026-11-09T09:0{i}:00Z",
                        "durationS": 60,
                        "captureIndex": i + 1,
                        "installHeightM": INSTALL_H,
                        "observations": [
                            {
                                "beaconId": beacon_id,
                                "n": 20,
                                "rssiMedian": rssi,
                                "rssiMin": rssi - 3,
                                "rssiMax": rssi + 3,
                                "rssiStd": 1.5,
                                "truthDistanceM": round(distance, 2),
                            }
                        ],
                    }
                    for i in range(3)
                ],
            }
        )
    return {
        "schemaVersion": "1.0",
        "type": "positioning_calibration_logdist",
        "deployable": False,
        "operator": "tester",
        "mapVersion": "2026-11-08.2",
        "beacon": {"beaconId": beacon_id},
        "installHeightM": INSTALL_H,
        "phoneHeightM": 1.2,
        "stops": stops_data,
    }


class LogdistFitTest(unittest.TestCase):
    def test_type_dispatch(self):
        self.assertTrue(is_logdist_samples(_sample()))
        self.assertFalse(is_logdist_samples({"type": "positioning_calibration_static"}))

    def test_rejects_multi_beacon(self):
        data = _sample()
        data["stops"][0]["sessions"][0]["observations"].append(
            {
                "beaconId": "BK-01-008",
                "n": 20,
                "rssiMedian": -60,
                "rssiMin": -63,
                "rssiMax": -57,
                "rssiStd": 1.0,
                "truthDistanceM": 1.3,
            }
        )
        with self.assertRaises(CalibrationError):
            validate_logdist_samples(data)

    def test_rejects_deployable_true(self):
        data = _sample()
        data["deployable"] = True
        with self.assertRaises(CalibrationError):
            validate_logdist_samples(data)

    def test_fit_recovers_params_and_marks_not_deployable(self):
        with tempfile.TemporaryDirectory() as tmp:
            samples_path = Path(tmp) / "logdist_samples.json"
            samples_path.write_text(json.dumps(_sample()), encoding="utf-8")
            out_path = Path(tmp) / "logdist_result.json"
            doc = fit_logdist(samples_path, out_path)
            # 合成样本的 rssi 取整会带来 <0.1 的回归偏差
            self.assertAlmostEqual(doc["params"]["rssiAt1mDbm"], A_TRUE, delta=0.2)
            self.assertAlmostEqual(doc["params"]["pathLossExponent"], N_TRUE, delta=0.05)
            self.assertFalse(doc["deployable"])
            self.assertEqual(doc["type"], "positioning_calibration_logdist_result")
            self.assertEqual(doc["metrics"]["stops"], len(STOPS))
            self.assertEqual(doc["metrics"]["pairs"], len(STOPS) * 3)
            self.assertTrue(doc["stability"]["passed"])
            self.assertTrue(out_path.exists())

    def test_constant_rssi_rejected_with_actionable_message(self):
        data = _sample()
        for stop in data["stops"]:
            for session in stop["sessions"]:
                for observation in session["observations"]:
                    observation["rssiMedian"] = -100
        with tempfile.TemporaryDirectory() as tmp:
            samples_path = Path(tmp) / "logdist_samples.json"
            samples_path.write_text(json.dumps(data), encoding="utf-8")
            with self.assertRaises(CalibrationError) as ctx:
                fit_logdist(samples_path, Path(tmp) / "out.json")
            message = str(ctx.exception)
            self.assertIn("-100 dBm", message)
            self.assertIn("没有随距离衰减", message)
            self.assertIn("重新采集", message)

    def test_run_dispatches_logdist_without_ledger(self):
        with tempfile.TemporaryDirectory() as tmp:
            samples_path = Path(tmp) / "logdist_samples.json"
            samples_path.write_text(json.dumps(_sample(stops=[0, 2, 9])), encoding="utf-8")
            out_path = Path(tmp) / "result.json"
            doc = run(samples_path, Path(tmp) / "missing_ledger.json", out_path)
            self.assertEqual(doc["type"], "positioning_calibration_logdist_result")
            self.assertEqual(doc["metrics"]["stops"], 3)

    def test_too_few_pairs_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            samples_path = Path(tmp) / "logdist_samples.json"
            samples_path.write_text(json.dumps(_sample(stops=[0])), encoding="utf-8")
            with self.assertRaises(CalibrationError):
                fit_logdist(samples_path, Path(tmp) / "out.json")


if __name__ == "__main__":
    unittest.main()
