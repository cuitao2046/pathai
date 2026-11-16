# -*- coding: utf-8 -*-
"""路线引擎适盲策略与物理距离口径回归（REQ-COST-01/02/03/04）。

测试全部基于真实发布地图包，不虚构拓扑；轮椅 999 用例仅翻转真实边的
``wheelchairAccessible`` 字段，用于锁定“999 不等于轮椅不可用”的语义。
"""
import unittest

from testutil import CURRENT_MAP_PATH, load_json
from src.topology.route_rules import ModePolicy, RouteGraph


class TestModePolicy(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.geo = load_json(CURRENT_MAP_PATH)
        cls.graph = RouteGraph(cls.geo)

    def _first_edge(self, predicate):
        for edge in self.graph.edges:
            if predicate(edge):
                return edge
        self.fail("真实地图包中未找到满足条件的边")

    def test_blind_rejects_999(self):
        edge = dict(self._first_edge(
            lambda e: int(e["accessibilityLevel"]) == 999
        ))
        self.assertFalse(ModePolicy("blind", {}).is_allowed(edge))

    def test_wheelchair_ignores_999_when_wheelchair_accessible(self):
        edge = dict(self._first_edge(
            lambda e: int(e["accessibilityLevel"]) == 999
        ))
        edge["wheelchairAccessible"] = True
        self.assertTrue(ModePolicy("wheelchair", {}).is_allowed(edge))
        self.assertFalse(ModePolicy("blind", {}).is_allowed(edge))

    def test_wheelchair_rejects_wheelchair_false(self):
        edge = dict(self._first_edge(
            lambda e: int(e["accessibilityLevel"]) == 999
        ))
        edge["wheelchairAccessible"] = False
        self.assertFalse(ModePolicy("wheelchair", {}).is_allowed(edge))
        self.assertTrue(ModePolicy("normal", {}).is_allowed(edge))

    def test_blind_rejects_staircase_cross_floor(self):
        edge = dict(self._first_edge(
            lambda e: e["crossFloor"] and e.get("type") == "staircase"
        ))
        self.assertFalse(ModePolicy("blind", {}).is_allowed(edge))
        self.assertTrue(ModePolicy("normal", {}).is_allowed(edge))

    def test_block_hazard_is_forbidden(self):
        edge = dict(self.graph.edges[0])
        hazards = {edge["id"]: [{"level": "block"}]}
        self.assertFalse(ModePolicy("blind", hazards).is_allowed(edge))
        self.assertFalse(ModePolicy("wheelchair", hazards).is_allowed(edge))
        # normal 模式当前不加载危险层；空危险表时才允许。
        self.assertTrue(ModePolicy("normal", {}).is_allowed(edge))


class TestPhysicalDistance(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.geo = load_json(CURRENT_MAP_PATH)
        cls.graph = RouteGraph(cls.geo)
        cls.room_ids = sorted(
            nid for nid, node in cls.graph.nodes.items() if node["type"] == "room"
        )

    def _sample_routes(self, mode="normal", limit=12):
        seen = 0
        for start in self.room_ids[:limit]:
            for end in self.room_ids[:limit]:
                if start == end:
                    continue
                sp = self.graph.shortest_path(start, end, mode=mode)
                if sp is not None:
                    yield sp
                    seen += 1
                    if seen >= limit:
                        return

    def test_physical_distance_equals_edge_distance_sum(self):
        checked = 0
        for sp in self._sample_routes():
            expected = round(sum(
                self.graph.edge_by_id[eid]["distance"] for eid in sp["edges"]
            ), 2)
            self.assertEqual(sp["physicalDistance"], expected)
            self.assertEqual(len(sp["edgeDistances"]), len(sp["edges"]))
            checked += 1
        self.assertGreater(checked, 0, "真实地图抽样未得到可达路线")

    def test_cost_can_exceed_physical_distance_due_to_door_penalty(self):
        # 真实地图中 F1-TR-0038 → F1-TD-0004 经过 opening 门，门惩罚 1.0m。
        found = self.graph.shortest_path("F1-TR-0038", "F1-TD-0004", mode="normal")
        self.assertIsNotNone(found)
        # 物理距离仍是逐边原始 distance 之和，不因门惩罚变大。
        expected = round(sum(
            self.graph.edge_by_id[eid]["distance"] for eid in found["edges"]
        ), 2)
        self.assertEqual(found["physicalDistance"], expected)
        self.assertGreater(found["distance"], found["physicalDistance"])

    def test_cross_floor_physical_distance_is_positive(self):
        elevator = next(
            (e for e in self.graph.cross_floor_edges if e.get("type") == "elevator"),
            None,
        )
        self.assertIsNotNone(elevator, "真实地图缺少电梯跨层边")
        sp = self.graph.shortest_path(elevator["from"], elevator["to"], mode="blind")
        self.assertIsNotNone(sp, "电梯跨层边在盲模式应可达")
        self.assertGreater(sp["physicalDistance"], 0)
        self.assertGreaterEqual(len(sp["edgeDistances"]), 1)


if __name__ == "__main__":
    unittest.main()
