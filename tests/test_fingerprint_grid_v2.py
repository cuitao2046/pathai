# -*- coding: utf-8 -*-
"""指纹网格 v2（设计层 / 采集层）回归测试。

覆盖：
1. 格位整数性：全部点整数米；2m 底网偶-偶；1m 点至少一奇；
2. 格位键唯一、最近邻 >= 1.0m、无 > 2m 空洞；
3. 编号 FP-{f}-C{col}{row}[-JDS] 可解析、与坐标/类型自洽；
4. riskNodes 类型分流：outward_door→门口（1m），stair/elevator→高风险（3m）；
5. 可采集判定边界（3D 斜距 RSSI>=-85dBm / 3D 15m / >=3 信标 / 墙与门衰减）。
"""
import json
import math
import re
import unittest
from pathlib import Path

try:
    from testutil import PROJECT_ROOT, load_json
except ImportError:
    from tests.testutil import PROJECT_ROOT, load_json

from src.tools.generate_fingerprint_grid import (
    FP_ID_RE,
    DOOR_RADIUS_M,
    RISK_RADIUS_M,
    coverage_flags,
    build_wall_model,
    collect_triggers,
    order_route_points,
    project_on_route,
)

GRID_PATH = PROJECT_ROOT / "result" / "fingerprint_grid.json"
MAP_PACKAGE = PROJECT_ROOT / "backend" / "maps_data" / "school-building-01" / "2026-11-08.2.json"

SUFFIX_BY_REGION = {"intersection": "-J", "doorway": "-D", "high_risk": "-S", "normal": ""}


def _dist(a, b):
    return math.hypot(a[0] - b[0], a[1] - b[1])


class TestGridLattice(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.grid = load_json(GRID_PATH)

    def _points(self):
        for fl, data in self.grid["floors"].items():
            for p in data["points"]:
                yield fl, p

    def test_all_points_integer_meters(self):
        for fl, p in self._points():
            with self.subTest(fpId=p["fpId"]):
                self.assertEqual(p["x"], int(p["x"]), "x 必须为整数米")
                self.assertEqual(p["y"], int(p["y"]), "y 必须为整数米")
                self.assertEqual(p["col"], int(p["col"]))
                self.assertEqual(p["row"], int(p["row"]))
                self.assertEqual(p["x"], self.grid["origin"]["x"] + p["col"])
                self.assertEqual(p["y"], self.grid["origin"]["y"] + p["row"])

    def test_base_2m_is_even_even(self):
        for fl, p in self._points():
            if p["isBase2m"]:
                with self.subTest(fpId=p["fpId"]):
                    self.assertTrue(p["col"] % 2 == 0 and p["row"] % 2 == 0,
                                    "2m 底网必须为偶-偶格位")

    def test_split_1m_has_odd_axis(self):
        for fl, p in self._points():
            if not p["isBase2m"]:
                with self.subTest(fpId=p["fpId"]):
                    self.assertTrue(p["col"] % 2 != 0 or p["row"] % 2 != 0,
                                    "1m 细分点至少一轴为奇")

    def test_lattice_key_unique(self):
        for fl, data in self.grid["floors"].items():
            keys = [(p["col"], p["row"]) for p in data["points"]]
            self.assertEqual(len(keys), len(set(keys)), f"F{fl} 存在重复 1m 格位键")

    def test_nearest_neighbor_at_least_1m_and_no_holes(self):
        for fl, data in self.grid["floors"].items():
            pts = [(p["x"], p["y"]) for p in data["points"]]
            worst_min, worst_max = math.inf, 0.0
            for i, a in enumerate(pts):
                best = min(_dist(a, b) for j, b in enumerate(pts) if i != j)
                worst_min = min(worst_min, best)
                worst_max = max(worst_max, best)
            with self.subTest(floor=fl):
                self.assertGreaterEqual(worst_min, 1.0 - 1e-9, "最近邻必须 >= 1.0m")
                self.assertLessEqual(worst_max, 2.0 + 1e-9, "不得出现 > 2m 空洞")


class TestFpId(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.grid = load_json(GRID_PATH)
        cls.pattern = re.compile(cls.grid["parameters"]["fpIdPattern"])

    def test_regex_and_coordinate_derivation(self):
        for fl, data in self.grid["floors"].items():
            for p in data["points"]:
                with self.subTest(fpId=p["fpId"]):
                    self.assertRegex(p["fpId"], FP_ID_RE)
                    m = self.pattern.match(p["fpId"])
                    self.assertIsNotNone(m, "fpId 必须可机器解析")
                    suffix = SUFFIX_BY_REGION[p["regionType"]]
                    expected = f"FP-{p['floor']}-C{p['col']:03d}R{p['row']:03d}{suffix}"
                    self.assertEqual(p["fpId"], expected, "编号必须由坐标/类型派生")
                    self.assertEqual(p["shortId"], expected.split("-", 2)[2])

    def test_regex_rejects_legacy_ids(self):
        for bad in ("F1-FP-0123", "FP-1-001", "FP-1-C56R100", "FP-1-C056R100-X"):
            with self.subTest(bad=bad):
                self.assertIsNone(re.fullmatch(FP_ID_RE, bad))


class TestRiskNodeTypes(unittest.TestCase):
    def test_synthetic_risk_nodes_split(self):
        floor = {
            "topology": {"nodes": [], "edges": []},
            "accessibility": {"riskNodes": [
                {"id": "OD-1", "type": "outward_door", "coordinates": [0.0, 0.0]},
                {"id": "ST-1", "type": "stair_entrance", "coordinates": [0.0, 0.0]},
                {"id": "EV-1", "type": "elevator_entrance", "coordinates": [0.0, 0.0]},
            ]},
        }
        triggers = {t["node_id"]: t for t in collect_triggers(floor)}
        self.assertEqual(triggers["OD-1"]["kind"], "doorway")
        self.assertEqual(triggers["OD-1"]["priority"], 2)
        self.assertEqual(triggers["OD-1"]["radius"], DOOR_RADIUS_M)
        for nid in ("ST-1", "EV-1"):
            self.assertEqual(triggers[nid]["kind"], "high_risk")
            self.assertEqual(triggers[nid]["priority"], 3)
            self.assertEqual(triggers[nid]["radius"], RISK_RADIUS_M)

    def test_current_map_package_split(self):
        pkg = load_json(MAP_PACKAGE)
        for fl, data in pkg["floors"].items():
            risk = (data.get("accessibility") or {}).get("riskNodes") or []
            outward = {r["id"] for r in risk if r.get("type") == "outward_door"}
            high = {r["id"] for r in risk if r.get("type") in ("stair_entrance", "elevator_entrance")}
            by_id = {t["node_id"]: t for t in collect_triggers(data)}
            for nid in outward:
                with self.subTest(floor=fl, node=nid):
                    self.assertEqual(by_id[nid]["radius"], DOOR_RADIUS_M)
            for nid in high:
                with self.subTest(floor=fl, node=nid):
                    self.assertEqual(by_id[nid]["radius"], RISK_RADIUS_M)


class TestRouteOrdering(unittest.TestCase):
    """F2 清单按路线投影累计里程排序（本层局部里程，从入口线段起点算起）。"""

    SEGMENTS = [{
        "from": "F2-A", "to": "F2-B", "a": (0.0, 0.0), "b": (10.0, 0.0),
        "c0": 100.0, "lengthM": 10.0,
    }]
    GEOM = {"R2": {"label": "R2", "segments": {"2": SEGMENTS}}}

    def test_projection_mileage_is_floor_local(self):
        proj = project_on_route(3.0, 2.0, self.SEGMENTS)
        self.assertAlmostEqual(proj["distanceM"], 2.0, places=6)
        self.assertAlmostEqual(proj["mileageM"], 3.0, places=6)
        self.assertEqual(proj["from"], "F2-A")

    def test_route_points_first_and_sorted_by_mileage(self):
        pts = [
            {"fpId": "P3", "x": 8.0, "y": 0.0, "routeTags": ["R2"], "row": 8, "col": 8},
            {"fpId": "P2", "x": 2.0, "y": 0.0, "routeTags": ["R2"], "row": 2, "col": 2},
            {"fpId": "P1", "x": 0.0, "y": 0.0, "routeTags": ["R2"], "row": 0, "col": 0},
            {"fpId": "NP", "x": 1.0, "y": 9.0, "routeTags": [], "row": 9, "col": 1},
        ]
        routed, plain = order_route_points(pts, 2, self.GEOM)
        self.assertEqual([p["fpId"] for _, _, p in routed], ["P1", "P2", "P3"])
        self.assertEqual([p["fpId"] for p in plain], ["NP"])


class TestCoverageBoundary(unittest.TestCase):
    """v11 口径：RSSI = -52 - 35·log10(d3) - 墙衰减；d3=sqrt(dxy²+1.3²)。

    参考值：dxy=8.6 → d3≈8.698 → RSSI≈-84.9（通过）；dxy=8.8 → RSSI≈-85.2（不通过）。
    """

    @staticmethod
    def _beacons(dists):
        return [
            {"beaconId": f"BK-{i}", "floorId": "1", "x": d, "y": 0.0, "installHeightM": 2.5}
            for i, d in enumerate(dists)
        ]

    @classmethod
    def _flags(cls, dists, wall_model=None):
        return coverage_flags(0.0, 0.0, cls._beacons(dists), wall_model)

    @staticmethod
    def _flags_against(px, py, beacon_xy, wall_model=None):
        beacons = [
            {"beaconId": f"BK-{i}", "floorId": "1", "x": bx, "y": by, "installHeightM": 2.5}
            for i, (bx, by) in enumerate(beacon_xy)
        ]
        return coverage_flags(px, py, beacons, wall_model)

    @staticmethod
    def _wall_model(walls, doors=()):
        geo_floor = {
            "topology": {"nodes": [], "edges": []},
            "geometry": {
                "walls": [
                    {"geometry": {"type": "LineString", "coordinates": c},
                     "properties": {"material": m}}
                    for c, m in walls
                ],
                "doors": [
                    {"geometry": {"type": "Point", "coordinates": list(dp)}} for dp in doors
                ],
            },
        }
        return build_wall_model(geo_floor)

    def test_plan_ok_at_rssi_boundary(self):
        f = self._flags([8.6, 8.6, 8.6])
        self.assertEqual(f["visible10m"], 3)
        self.assertGreaterEqual(f["strongestRssi"], -85.0)
        self.assertTrue(f["collectablePlan10m"], "3D 斜距下 RSSI≈-84.9 应通过")

    def test_plan_fails_beyond_rssi_reach(self):
        f = self._flags([8.8, 8.8, 8.8])
        self.assertEqual(f["visible10m"], 0)
        self.assertFalse(f["collectablePlan10m"])

    def test_plan_requires_three_beacons(self):
        f = self._flags([5.0, 5.0])
        self.assertEqual(f["visible10m"], 2)
        self.assertFalse(f["collectablePlan10m"])

    def test_rssi_uses_3d_slant_not_horizontal(self):
        # 水平 9.0m 的 2D 口径 RSSI≈-84.2 会通过；3D 斜距 d3≈9.093 → RSSI≈-85.6 不通过
        f = self._flags([9.0, 9.0, 9.0])
        self.assertFalse(f["collectablePlan10m"])

    def test_geo15_uses_3d_slant(self):
        f = self._flags([14.9, 14.9, 14.9])  # d3≈14.956 <= 15
        self.assertEqual(f["visible15m"], 3)
        self.assertFalse(f["collectablePlan10m"])
        self.assertTrue(f["collectableGeo15m"])

    def test_geo15_boundary_excludes_15m_horizontal(self):
        f = self._flags([15.0, 15.0, 15.0])  # d3≈15.056 > 15
        self.assertEqual(f["visible15m"], 0)
        self.assertFalse(f["collectableGeo15m"])

    def test_wall_attenuation_blocks_plan(self):
        # 信标在 (0,-6)，brick 墙横穿 y=-3：无墙 RSSI≈-79.6，加 12dB → ≈-91.6 不通过
        model = self._wall_model([([[-10.0, -3.0], [10.0, -3.0]], "brick")])
        f = self._flags_against(0.0, 0.0, [(0.0, -6.0)] * 3, model)
        self.assertFalse(f["collectablePlan10m"])
        self.assertEqual(f["visible10m"], 0)

    def test_door_proximity_counts_as_2db(self):
        # 同一堵墙但交点 1.2m 内有门：仅 -2dB → RSSI≈-81.6 仍通过
        model = self._wall_model([([[-10.0, -3.0], [10.0, -3.0]], "brick")], doors=[(0.5, -3.0)])
        f = self._flags_against(0.0, 0.0, [(0.0, -6.0)] * 3, model)
        self.assertTrue(f["collectablePlan10m"])
        self.assertGreaterEqual(f["strongestRssi"], -85.0)

    def test_wall_cluster_takes_max_material(self):
        # 两堵墙在交点 0.45m 内重叠（partition+concrete）：按 concrete 15dB 计一次
        model = self._wall_model([
            ([[-10.0, -3.0], [10.0, -3.0]], "partition"),
            ([[-10.0, -3.25], [10.0, -3.25]], "concrete"),
        ])
        # 3 信标同位同衰减：RSSI≈-52-27.6-15≈-94.6，不通过
        f = self._flags_against(0.0, 0.0, [(0.0, -6.0)] * 3, model)
        self.assertFalse(f["collectablePlan10m"])

    def test_criteria_flags_consistent_in_output(self):
        grid = load_json(GRID_PATH)
        for fl, data in grid["floors"].items():
            for p in data["points"]:
                with self.subTest(fpId=p["fpId"]):
                    self.assertEqual(p["collectable"], p["collectablePlan10m"])
                    self.assertEqual("plan10m85" in p["criteria"], p["collectablePlan10m"])
                    self.assertEqual("geo15m" in p["criteria"], p["collectableGeo15m"])


if __name__ == "__main__":
    unittest.main()
