# -*- coding: utf-8 -*-
"""nav_service.build_route_info 纯几何单元测试。

坐标系约定：x 东向、y 北向（convert_map_v9.py coordinateSystem 声明）。
转向判定：atan2 数学角系下 diff>0（逆时针）= 左转。
用桩索引隔离真实地图数据，专注几何→指令映射。
"""
from __future__ import annotations

from pathai_backend.nav_service import NavPoint, build_route_info
from pathai_backend.schemas.common import InstructionAction


class _StubIndex:
    """最小边索引：任意相邻节点间 10m 直边。"""

    def edge_between(self, a: str, b: str):
        return {"id": f"e-{a}-{b}", "from": a, "to": b,
                "distance": 10.0, "crossFloor": False, "type": None}


class _StubPkg:
    mapVersion = "test"


def _actions_of_path(points: list[NavPoint]) -> list[InstructionAction]:
    route = {"path": [p.node_id for p in points], "distance": 10.0 * (len(points) - 1)}
    info = build_route_info(route, _StubPkg(), _StubIndex(), points)
    return [s.action for s in info.segments]


def test_east_then_north_is_left_turn():
    """面向东（0°）转向北（90°）= 左转（逆时针，diff=+90）。"""
    points = [
        NavPoint("a", "1", 0.0, 0.0),
        NavPoint("b", "1", 10.0, 0.0),
        NavPoint("c", "1", 10.0, 10.0),
    ]
    assert InstructionAction.TURN_LEFT in _actions_of_path(points)


def test_east_then_south_is_right_turn():
    """面向东（0°）转向南（-90°）= 右转（顺时针，diff=-90）。"""
    points = [
        NavPoint("a", "1", 0.0, 0.0),
        NavPoint("b", "1", 10.0, 0.0),
        NavPoint("c", "1", 10.0, -10.0),
    ]
    assert InstructionAction.TURN_RIGHT in _actions_of_path(points)


def test_straight_continues_without_turn():
    """同向 40m 直行 = 只发直行，无转向指令。"""
    points = [
        NavPoint("a", "1", 0.0, 0.0),
        NavPoint("b", "1", 20.0, 0.0),
        NavPoint("c", "1", 40.0, 0.0),
    ]
    actions = _actions_of_path(points)
    assert InstructionAction.TURN_LEFT not in actions
    assert InstructionAction.TURN_RIGHT not in actions
