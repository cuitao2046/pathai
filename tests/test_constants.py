"""constants.py 全局常量唯一来源回归测试。

数值为 v9 验收口径（docs/设计决策记录.md 校准），改动需同步更新文档。
"""
import unittest

from src.common.constants import (
    BLIND_WALK_SPEED,
    DOOR_PENALTY,
    NORMAL_WALK_SPEED,
    ORIGIN_X,
    ORIGIN_Y,
    SCALE,
)


class TestScale(unittest.TestCase):
    def test_scale_value(self):
        self.assertEqual(SCALE, 0.0529)
        self.assertGreater(SCALE, 0)

    def test_origin(self):
        self.assertEqual(ORIGIN_X, 2019.1)
        self.assertEqual(ORIGIN_Y, 1154.8)


class TestWalkSpeed(unittest.TestCase):
    def test_blind_speed(self):
        self.assertEqual(BLIND_WALK_SPEED, 0.8)

    def test_normal_speed(self):
        self.assertEqual(NORMAL_WALK_SPEED, 1.2)

    def test_blind_slower_than_normal(self):
        self.assertLess(BLIND_WALK_SPEED, NORMAL_WALK_SPEED)


class TestDoorPenalty(unittest.TestCase):
    def test_keys(self):
        self.assertEqual(set(DOOR_PENALTY), {"swing", "fire", "opening"})

    def test_values(self):
        self.assertEqual(DOOR_PENALTY["swing"], 0.0)
        self.assertEqual(DOOR_PENALTY["fire"], 0.5)
        self.assertEqual(DOOR_PENALTY["opening"], 1.0)

    def test_nonnegative(self):
        self.assertTrue(all(v >= 0 for v in DOOR_PENALTY.values()))

    def test_ordering(self):
        self.assertLess(DOOR_PENALTY["swing"], DOOR_PENALTY["fire"])
        self.assertLess(DOOR_PENALTY["fire"], DOOR_PENALTY["opening"])


if __name__ == "__main__":
    unittest.main()
