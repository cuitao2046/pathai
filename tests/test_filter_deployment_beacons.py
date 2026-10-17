import unittest

from src.rendering.render_interactive import filter_deployment_beacons
from src.tools.validate_beacon_deployment import (
    BEACON_JSON,
    MAP_JSON,
    build_ctx,
    validate_beacon,
)
from tests.testutil import load_json


class TestFilterDeploymentBeacons(unittest.TestCase):
    def setUp(self):
        self.geo = {
            "floors": {
                "1": {
                    "topology": {
                        "nodes": [
                            {"id": "N1", "coordinates": [0, 0]},
                        ]
                    }
                },
                "2": {
                    "topology": {
                        "nodes": [
                            {"id": "SHARED", "coordinates": [0, 0]},
                        ]
                    }
                },
            }
        }

    def test_r5_required_fields_and_finite_coordinates(self):
        cases = [
            ({"coordinates": [0, 0], "floor": 1}, "R5 missing beaconId"),
            ({"beaconId": "missing-coords", "floor": 1}, "R5 missing coordinates"),
            ({"beaconId": "missing-floor", "coordinates": [0, 0]}, "R5 missing floor"),
            ({"beaconId": "unknown-floor", "coordinates": [0, 0], "floor": 3},
             "R5 unknown floor 3"),
            ({"beaconId": "short-coords", "coordinates": [0], "floor": 1},
             "R5 invalid coordinates"),
            ({"beaconId": "string-coords", "coordinates": ["x", 0], "floor": 1},
             "R5 invalid coordinates"),
            ({"beaconId": "nan-coords", "coordinates": [float("nan"), 0], "floor": 1},
             "R5 invalid coordinates"),
            ({"beaconId": "infinite-coords", "coordinates": [0, float("inf")], "floor": 1},
             "R5 invalid coordinates"),
        ]
        filtered, excluded = filter_deployment_beacons([item for item, _ in cases], self.geo)
        self.assertEqual(filtered, [])
        self.assertEqual(
            [(beacon_id, reason) for beacon_id, reason in excluded],
            [(item.get("beaconId") or "?", reason) for item, reason in cases],
        )

    def test_r1_applies_only_to_resolved_node_on_beacon_floor(self):
        beacons = [
            {"beaconId": "at-cutoff", "coordinates": [3, 0], "floor": 1,
             "sourceNodeId": "N1"},
            {"beaconId": "over-cutoff", "coordinates": [3.01, 0], "floor": 1,
             "sourceNodeId": "N1"},
            {"beaconId": "missing-source", "coordinates": [50, 50], "floor": 1},
            {"beaconId": "unresolved-source", "coordinates": [50, 50], "floor": 1,
             "sourceNodeId": "UNKNOWN"},
            {"beaconId": "same-id-other-floor", "coordinates": [50, 50], "floor": 1,
             "sourceNodeId": "SHARED"},
            {"beaconId": "resolved-second-floor", "coordinates": [3.01, 0], "floor": 2,
             "sourceNodeId": "SHARED"},
        ]
        filtered, excluded = filter_deployment_beacons(beacons, self.geo)
        self.assertEqual(
            [beacon["beaconId"] for beacon in filtered],
            ["at-cutoff", "missing-source", "unresolved-source", "same-id-other-floor"],
        )
        self.assertEqual([beacon_id for beacon_id, _ in excluded], ["over-cutoff", "resolved-second-floor"])
        self.assertTrue(all(reason.startswith("R1 ") for _, reason in excluded))

    def test_current_deployment_regression_keeps_warnings_and_manual_points(self):
        beacons = load_json(BEACON_JSON)["beacons"]
        geo = load_json(MAP_JSON)
        filtered, excluded = filter_deployment_beacons(beacons, geo)
        self.assertEqual(
            {beacon_id for beacon_id, _ in excluded},
            {"BK-01-012", "BK-01-021", "BK-01-034", "BK-02-002"},
        )
        self.assertEqual(len(excluded), 4)
        kept_ids = {beacon["beaconId"] for beacon in filtered}
        self.assertTrue({"BK-01-011", "BK-02-003"} <= kept_ids)
        self.assertTrue({"BK-M-1-004", "BK-M-1-001", "BK-M-2-001"} <= kept_ids)
        contexts = {str(floor): build_ctx(geo, floor) for floor in geo["floors"]}
        for beacon_id in ("BK-01-011", "BK-02-003", "BK-M-1-004"):
            beacon = next(item for item in filtered if item["beaconId"] == beacon_id)
            self.assertTrue(any(rule == "R4" and level == "WARN"
                                for rule, level, _ in validate_beacon(
                                    beacon, contexts[str(beacon["floor"])])))


if __name__ == "__main__":
    unittest.main()
