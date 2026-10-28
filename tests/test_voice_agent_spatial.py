# -*- coding: utf-8 -*-
"""空间语义转换测试（spec REQ-VOICE-02）：时钟方向边界、步数、周边要素。

使用当前真实地图包（见 testutil.CURRENT_MAP_VERSION，含 accessibility 图层与防火门真实状态）。
"""
import json
import math
import unittest

from src.voice_agent.services.context_builder import SpatialIndex, build_spatial_context
from src.voice_agent.services.spatial_transformer import (
    align_heading,
    clock_direction,
    steps_for,
    target_bearing_deg,
)
from testutil import CURRENT_MAP_PATH

MAP_PATH = CURRENT_MAP_PATH


def _load_package() -> dict:
    return json.loads(MAP_PATH.read_text(encoding="utf-8"))


class TestBearing(unittest.TestCase):
    def test_cardinal_bearings(self):
        self.assertAlmostEqual(target_bearing_deg(0, 0, 0, 1), 0.0)
        self.assertAlmostEqual(target_bearing_deg(0, 0, 1, 0), 90.0)
        self.assertAlmostEqual(target_bearing_deg(0, 0, 0, -1), 180.0)
        self.assertAlmostEqual(target_bearing_deg(0, 0, -1, 0), 270.0)

    def test_heading_alignment_wraps(self):
        self.assertAlmostEqual(align_heading(350, 20), 10.0)
        self.assertAlmostEqual(align_heading(10), 10.0)


class TestClock(unittest.TestCase):
    def test_front_right_back_left(self):
        cases = [(0, 12), (90, 3), (180, 6), (270, 9)]
        for bearing, expected in cases:
            with self.subTest(bearing=bearing):
                self.assertEqual(clock_direction(bearing, heading_deg=0), expected)

    def test_sector_boundaries(self):
        # 12 点覆盖 [-15, +15)；边界 15° 进入 1 点，345° 回到 12 点
        self.assertEqual(clock_direction(14.9, 0), 12)
        self.assertEqual(clock_direction(15.0, 0), 1)
        self.assertEqual(clock_direction(345.0, 0), 12)
        self.assertEqual(clock_direction(344.9, 0), 11)
        self.assertEqual(clock_direction(359.9, 0), 12)
        self.assertEqual(clock_direction(0.1, 0), 12)

    def test_heading_rotation(self):
        # 用户朝东（90°）时，地图正北在 9 点钟方向
        self.assertEqual(clock_direction(0, 90), 9)
        self.assertEqual(clock_direction(90, 90), 12)


class TestSteps(unittest.TestCase):
    def test_half_up_rounding(self):
        self.assertEqual(steps_for(0.0), 0)
        self.assertEqual(steps_for(0.29), 0)
        self.assertEqual(steps_for(0.3), 1)  # 0.5 步，四舍五入进位
        self.assertEqual(steps_for(0.6), 1)
        self.assertEqual(steps_for(1.2), 2)
        self.assertEqual(steps_for(12.0), 20)


class TestSpatialContext(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pkg = _load_package()
        cls.index = SpatialIndex.from_package(cls.pkg)

    def _fire_door_node(self, normally_open: bool) -> dict:
        for floor in self.pkg["floors"].values():
            for node in floor["topology"]["nodes"]:
                if (
                    node.get("type") == "doorway"
                    and node.get("doorType") == "fire"
                    and node.get("isNormallyOpen") is normally_open
                ):
                    return node
        raise AssertionError("真实地图包缺少对应防火门")

    def test_corridor_context_and_data_gaps(self):
        ctx = build_spatial_context(self.index, "1", -30.0, -84.0, 0.0, "walking")
        self.assertEqual(ctx.floor, "1")
        self.assertEqual(ctx.current_region_type, "corridor")
        self.assertIn("走道", ctx.current_region_name)
        # 施工图无无障碍专篇：盲道/坡道/材质按人工确认口径降级
        self.assertFalse(ctx.on_tactile_paving)
        self.assertEqual(ctx.floor_surface, "普通地面")
        self.assertLessEqual(len(ctx.nearby_elements), 8)
        distances = [e.distance_meters for e in ctx.nearby_elements]
        self.assertEqual(distances, sorted(distances))
        for element in ctx.nearby_elements:
            self.assertGreaterEqual(element.clock_direction, 1)
            self.assertLessEqual(element.clock_direction, 12)
            self.assertLessEqual(element.distance_meters, 8.0)
            self.assertEqual(element.distance_steps, steps_for(element.distance_meters))

    def test_room_region(self):
        room = next(
            r
            for r in self.pkg["floors"]["2"]["semantic"]["rooms"]
            if r["label"] == "音乐教室" or r["label"] == "化学教室"
        )
        ctx = build_spatial_context(
            self.index, "2", room["centroid"][0], room["centroid"][1], 0.0, "stationary"
        )
        self.assertEqual(ctx.current_region_type, "room")
        self.assertIn(room["label"], ctx.current_region_name)

    def test_closed_fire_door_note(self):
        node = self._fire_door_node(False)
        x, y = node["coordinates"]
        ctx = build_spatial_context(self.index, self._floor_of(node["id"]), x, y, 0.0, "stationary")
        closed = [e for e in ctx.nearby_elements if e.element_type == "fire_door_normally_closed"]
        self.assertTrue(closed, "身边常闭防火门应出现在周边要素中")
        self.assertTrue(any("推开" in (e.accessibility_note or "") for e in closed))

    def test_open_fire_door_note(self):
        node = self._fire_door_node(True)
        x, y = node["coordinates"]
        ctx = build_spatial_context(self.index, self._floor_of(node["id"]), x, y, 0.0, "stationary")
        opened = [e for e in ctx.nearby_elements if e.element_type == "fire_door_normally_open"]
        self.assertTrue(opened)
        self.assertTrue(any("可直接通行" in (e.accessibility_note or "") for e in opened))

    def test_open_doorway_dk_note(self):
        node = next(
            n
            for n in self.pkg["floors"]["1"]["topology"]["nodes"]
            if n.get("type") == "doorway" and n.get("doorType") == "opening"
        )
        x, y = node["coordinates"]
        ctx = build_spatial_context(self.index, "1", x, y, 0.0, "stationary")
        dk = [e for e in ctx.nearby_elements if e.element_type == "open_doorway_dk"]
        self.assertTrue(dk)
        self.assertTrue(any("直接穿过" in (e.accessibility_note or "") for e in dk))

    def _floor_of(self, node_id: str) -> str:
        for floor_id, floor in self.pkg["floors"].items():
            if any(n["id"] == node_id for n in floor["topology"]["nodes"]):
                return floor_id
        raise AssertionError(node_id)

    def test_nearby_radius_filter(self):
        node = self._fire_door_node(False)
        x, y = node["coordinates"]
        ctx = build_spatial_context(self.index, self._floor_of(node["id"]), x, y, 0.0)
        self.assertTrue(all(e.distance_meters <= 8.0 for e in ctx.nearby_elements))
        self.assertTrue(
            all(
                math.isfinite(e.distance_meters)
                for e in ctx.nearby_elements
            )
        )


if __name__ == "__main__":
    unittest.main()
