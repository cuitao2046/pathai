"""segments_properly_cross 单元测试（对照 src/geometry/segments.py 精确实现）。

覆盖四类判定：异侧穿越 True；共线/同侧/退化墙线/延长线交点 False。
边界行为（端点接触视为穿透）随实现固化，防止无意改动影响路由穿墙判定。
"""
import unittest

from src.geometry.segments import segments_properly_cross, side


class TestSide(unittest.TestCase):
    """side(p, a, b) 叉积符号：>0 左侧 / <0 右侧 / =0 共线。"""

    def test_left_positive(self):
        a, b = (0.0, 0.0), (2.0, 0.0)
        self.assertGreater(side((1.0, 1.0), a, b), 0)

    def test_right_negative(self):
        a, b = (0.0, 0.0), (2.0, 0.0)
        self.assertLess(side((1.0, -1.0), a, b), 0)

    def test_collinear_zero(self):
        a, b = (0.0, 0.0), (2.0, 0.0)
        self.assertEqual(side((1.0, 0.0), a, b), 0)


class TestSegmentsProperlyCross(unittest.TestCase):
    """segments_properly_cross 判定矩阵。"""

    def test_cross_true(self):
        # 路径 (0,0)->(2,2) 与墙 (0,2)->(2,0) 在 (1,1) 正交叉
        self.assertTrue(segments_properly_cross((0, 0), (2, 2), (0, 2), (2, 0)))

    def test_cross_perpendicular_true(self):
        # 路径 (1,1)->(1,-1) 垂直穿过墙 (0,0)->(2,0)
        self.assertTrue(segments_properly_cross((1, 1), (1, -1), (0, 0), (2, 0)))

    def test_cross_direction_reversed_true(self):
        # 反向路径仍判定穿透（无方向性）
        self.assertTrue(segments_properly_cross((2, 2), (0, 0), (0, 2), (2, 0)))

    def test_collinear_false(self):
        # 共线（沿墙行走）不穿透
        self.assertFalse(segments_properly_cross((0, 0), (4, 4), (1, 1), (3, 3)))

    def test_same_side_false(self):
        # 同侧（墙外平行）不穿透
        self.assertFalse(segments_properly_cross((0, 0), (1, 0), (0, 1), (2, 1)))

    def test_degenerate_wall_false(self):
        # 退化墙线（|b-a| < eps）不穿透
        a, b = (0.0, 0.0), (1e-12, 0.0)
        self.assertFalse(segments_properly_cross((0, 1), (0, -1), a, b))

    def test_extension_point_outside_wall_false(self):
        # 交点在墙延长线上（u 出界）：路径 x=5 穿过 y=1 直线，但墙仅 x∈[0,2]
        self.assertFalse(segments_properly_cross((5, 0), (5, 2), (0, 1), (2, 1)))

    def test_endpoint_on_extension_false(self):
        # p1 落在墙线延长线上，交点 u<0，不算穿透
        self.assertFalse(segments_properly_cross((3, 1), (3, 0), (0, 1), (2, 1)))

    def test_touch_at_wall_endpoint_true(self):
        # 路径自墙端点垂直出发：实现固化 t=0/u=0 交点视为穿透
        self.assertTrue(segments_properly_cross((0, 1), (0, 0), (0, 1), (2, 1)))

    def test_nearly_cross_above_default_eps(self):
        # 墙长 1e-6（大于默认 eps=1e-9）时仍判真
        a, b = (0.0, 0.0), (1e-6, 0.0)
        self.assertTrue(segments_properly_cross((0.5e-6, 1), (0.5e-6, -1), a, b))


if __name__ == "__main__":
    unittest.main()
