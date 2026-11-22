# -*- coding: utf-8 -*-
"""nav_service.build_route_info 纯几何单元测试。

坐标系约定：x 东向、y 北向（convert_map_v9.py coordinateSystem 声明）。
转向判定：atan2 数学角系下 diff>0（逆时针）= 左转。
用桩索引隔离真实地图数据，专注几何→指令映射。
"""
from __future__ import annotations

from types import SimpleNamespace

from pathai_backend.nav_service import (
    NavPoint,
    _arrival_node_id,
    _destination_candidates,
    build_route_info,
    origin_anchor_node,
    room_at,
)
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


class _ArrivalIndex:
    """到达锚点判定桩索引：走廊 → 门洞 → 房间中心。"""

    def __init__(self, *, room_promoted: bool = True, with_doorway: bool = True):
        self.nodes = {
            "c": NavPoint("c", "1", 0.0, 0.0),
            "room": NavPoint("room", "1", 8.0, 0.0),
            "dead_end": NavPoint("dead_end", "1", 8.0, 0.0),
        }
        self.node_details = {
            "c": SimpleNamespace(type="intersection", rooms=[]),
            "room": SimpleNamespace(
                type="room", roomId="R1" if room_promoted else None,
                rooms=[], promotedFromRoom=room_promoted,
            ),
            "dead_end": SimpleNamespace(type="intersection", rooms=[]),
        }
        if with_doorway:
            self.nodes["door"] = NavPoint("door", "1", 5.0, 0.0)
            self.node_details["door"] = SimpleNamespace(type="doorway", rooms=["R1"])


def test_arrival_anchor_uses_doorway_for_room_destination():
    """房间类目的地：到达锚点取进入房间的门洞，而不是房间中心。"""
    index = _ArrivalIndex()
    assert _arrival_node_id(["c", "door", "room"], index, None) == "door"


def test_arrival_anchor_falls_back_to_last_node_without_doorway():
    """找不到门洞时回退到终点节点（不抛错）。"""
    index = _ArrivalIndex(with_doorway=False)
    assert _arrival_node_id(["c", "room"], index, None) == "room"


def test_arrival_anchor_keeps_last_node_for_non_room_destination():
    """非房间目的地（未 promotedFromRoom）直接用终点节点。"""
    index = _ArrivalIndex()
    assert _arrival_node_id(["c", "door", "dead_end"], index, None) == "dead_end"


class _SpaceIndex:
    """起点/目的地锚点桩索引：矩形房间 + 门洞 + 走廊节点。"""

    def __init__(self):
        self.nodes = {
            "TI-1": NavPoint("TI-1", "1", 0.0, 4.0),
            "TD-1": NavPoint("TD-1", "1", 5.0, 0.0),
            "TD-2": NavPoint("TD-2", "1", 0.0, 8.0),
            "TR-1": NavPoint("TR-1", "1", 5.0, 5.0),
            "TI-2": NavPoint("TI-2", "1", 20.0, 0.0),
            "TI-E": NavPoint("TI-E", "1", 13.0, -2.0),
            "TI-W": NavPoint("TI-W", "1", 9.0, -2.0),
        }
        self.node_details = {
            "TI-1": SimpleNamespace(type="intersection", roomId=None),
            "TD-1": SimpleNamespace(type="doorway", roomId=None, rooms=["R1"]),
            "TD-2": SimpleNamespace(type="doorway", roomId=None, rooms=["R1"]),
            "TR-1": SimpleNamespace(type="intersection", roomId="R1"),
            "TI-2": SimpleNamespace(type="intersection", roomId=None),
            "TI-E": SimpleNamespace(type="intersection", roomId=None),
            "TI-W": SimpleNamespace(type="intersection", roomId=None),
        }
        self.room_polygons = {"R1": ("1", [(0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (0.0, 10.0)])}


def test_room_at_detects_indoor_and_public_space():
    index = _SpaceIndex()
    assert room_at(index, "1", 5.0, 5.0) == "R1"
    assert room_at(index, "1", 18.0, 4.0) is None


def test_origin_anchor_uses_room_door_when_indoor():
    """室内起点：第一跳取所在房间的门洞节点，而不是最近的房间中心。"""
    index = _SpaceIndex()
    assert origin_anchor_node(index, "1", 6.0, 6.0).node_id == "TD-1"


def test_origin_anchor_uses_public_node_when_outdoor():
    """公共空间起点：第一跳取最近的公共空间节点（走廊），不取门洞。"""
    index = _SpaceIndex()
    assert origin_anchor_node(index, "1", 18.0, 4.0).node_id == "TI-2"


def test_origin_anchor_prefers_forward_public_node():
    """公共空间起点：优先取朝目的地一侧的节点，避免起步先逆行折返。"""
    index = _SpaceIndex()
    east = NavPoint("dest-east", "1", 30.0, -2.0)
    west = NavPoint("dest-west", "1", -10.0, -2.0)
    assert origin_anchor_node(index, "1", 12.0, -2.0, toward=east).node_id == "TI-E"
    assert origin_anchor_node(index, "1", 12.0, -2.0, toward=west).node_id == "TI-W"


def test_origin_anchor_prefers_forward_door_when_indoor():
    """室内起点：多个门时优先取朝目的地一侧的门，而不是单纯最近的门。"""
    index = _SpaceIndex()
    north = NavPoint("dest-north", "1", 4.5, 20.0)
    assert origin_anchor_node(index, "1", 4.5, 4.5, toward=north).node_id == "TD-2"


def test_destination_room_center_replaced_by_door():
    """室内质心目的地：终点改为该房间的门洞节点。"""
    index = _SpaceIndex()
    candidates = _destination_candidates(index, index.nodes["TR-1"], index.nodes["TI-2"])
    # 房间有多个门时全部作为候选，按离起点由近到远（TD-1 更近）
    assert [p.node_id for p in candidates] == ["TD-1", "TD-2"]
