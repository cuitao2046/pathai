# -*- coding: utf-8 -*-
"""路口转向指令时序回归：转向必须在拐点前播报，并保持到用户越过路口。

背景：服务端旧实现对 continue 的 distance trigger 误用“本段长度”与“到终点剩余”
比较，导致转向指令被跳过；客户端因此只听到少量直行提示，转弯震动也因当前指令
不是 turn_left/turn_right 而不触发。
"""
from __future__ import annotations

import json
import math
from pathlib import Path

from pathai_backend.hazard_layer import HazardRepository
from pathai_backend.map_package.schema import MapPackage
from pathai_backend.nav_service import PackageIndex, plan_route, snap_to_node
from pathai_backend.routers.nav import _advance_current_index
from pathai_backend.schemas.common import InstructionAction, UserMode

MAP_VERSION = "2026-11-08.2"
_BACKEND = Path(__file__).resolve().parent.parent
_MAP_PATH = _BACKEND / "maps_data" / "school-building-01" / f"{MAP_VERSION}.json"
_HAZARD_DIR = _BACKEND / "hazards_data"


def _build_route():
    raw = json.loads(_MAP_PATH.read_text(encoding="utf-8"))
    pkg = MapPackage.model_validate(raw)
    index = PackageIndex(pkg)
    origin = snap_to_node(index, "1", -40, 11)
    hazard = HazardRepository.load(_HAZARD_DIR).get("school-building-01", MAP_VERSION)
    route, _dest = plan_route(
        raw, pkg, index, origin, "F1-RM-0036", UserMode.BLIND, hazard_layer=hazard
    )
    return route, index


def _positions(route, step_m: float = 0.25):
    pts = route.polyline
    for i in range(len(pts) - 1):
        a, b = pts[i], pts[i + 1]
        length = math.hypot(b.x - a.x, b.y - a.y)
        count = max(1, int(length / step_m))
        for k in range(count):
            t = k / count
            yield a.floorId or "1", a.x + (b.x - a.x) * t, a.y + (b.y - a.y) * t
    last = pts[-1]
    yield last.floorId or "1", last.x, last.y


def test_turn_instructions_are_not_skipped_and_stay_current_at_junction():
    route, index = _build_route()
    turn_indices = {
        i for i, seg in enumerate(route.segments)
        if seg.action in (InstructionAction.TURN_LEFT, InstructionAction.TURN_RIGHT)
    }
    assert turn_indices, "真实路线应包含路口转向"

    cur = 0
    seen: list[int] = []
    turn_current_at_node: dict[int, bool] = {}
    for floor_id, x, y in _positions(route):
        cur, _remaining = _advance_current_index(route, index, cur, floor_id, x, y)
        if not seen or seen[-1] != cur:
            seen.append(cur)
        seg = route.segments[cur]
        if cur in turn_indices and seg.trigger.nodeId:
            node = index.nodes[seg.trigger.nodeId]
            if math.hypot(x - node.x, y - node.y) <= 0.3:
                turn_current_at_node[cur] = True

    # 所有转向都必须作为当前指令出现过；这是安全优先级，不能被直行吞掉。
    assert turn_indices.issubset(set(seen))
    # 转向文案按用户要求保持短句：即将左转 / 即将右转。
    for i in turn_indices:
        assert route.segments[i].text in ("即将左转。", "即将右转。")
    # 用户到达拐点时必须仍是该转向指令，客户端才能继续转弯震动。
    assert turn_indices.issubset(set(turn_current_at_node))
