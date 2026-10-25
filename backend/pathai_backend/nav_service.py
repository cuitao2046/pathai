# -*- coding: utf-8 -*-
"""导航服务：路线规划、指令生成、进度投影与到达判定（REQ-NAV）。

- 路由复用已验证基线引擎 src/topology/route_rules.RouteGraph（受限 Dijkstra，
  固定边权；blind 模式强制电梯跨层），消费发布物原始 JSON dict；
- 指令段由路径几何生成：转向角阈值/电梯进出/到达，规则版本 "1.0.0"（工程初始值）；
- 阈值常量均为工程初始值，须经现场标定后冻结（docs/24 §12）。

注意：src 包位于仓库根目录，需以仓库根为运行目录（python -m pytest / uvicorn）。
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from pathai_backend.errors import ApiError
from pathai_backend.map_package.schema import MapPackage
from pathai_backend.schemas.common import (
    GeoPosition,
    InstructionAction,
    InstructionPriority,
    NavigationInstruction,
    RouteFallbackType,
    Trigger,
    UserMode,
)
from pathai_backend.schemas.nav import RouteInfo

ROUTE_RULES_VERSION = "1.0.0"
LOCATION_ALGORITHM_VERSION = "location-0.1"
#: 工程初始阈值。
ORIGIN_MIN_CONFIDENCE = 0.5
ARRIVAL_RADIUS_M = 5.0
OFF_ROUTE_M = 8.0
DECISION_POINT_RADIUS_M = 3.0
TURN_ANGLE_DEG = 30.0


@dataclass(frozen=True)
class NavPoint:
    node_id: str
    floor_id: str
    x: float
    y: float


class PackageIndex:
    """发布物快速索引：节点坐标/楼层、边（含跨层）、地点目录。"""

    def __init__(self, pkg: MapPackage):
        self.nodes: dict[str, NavPoint] = {}
        for floor_id, floor in pkg.floors.items():
            for node in floor.topology.nodes:
                self.nodes[node.id] = NavPoint(node.id, floor_id, node.coordinates[0], node.coordinates[1])
        self.edges: dict[str, dict] = {}
        for floor in pkg.floors.values():
            for e in floor.topology.edges:
                self.edges[e.id] = {"id": e.id, "from": e.from_node, "to": e.to,
                                    "distance": e.distance, "crossFloor": False, "type": None}
        self.cross_edges: dict[str, dict] = {}
        for e in pkg.crossFloorEdges:
            self.cross_edges[e.id] = {"id": e.id, "from": e.from_node, "to": e.to,
                                      "distance": e.distance, "crossFloor": True, "type": e.type}
        self.places = {p.placeId: p for p in pkg.places}

    def edge_between(self, a: str, b: str) -> dict | None:
        for edge in self.edges.values():
            if (edge["from"], edge["to"]) in ((a, b), (b, a)):
                return edge
        for edge in self.cross_edges.values():
            if (edge["from"], edge["to"]) in ((a, b), (b, a)):
                return edge
        return None


def _bearing(a: NavPoint, b: NavPoint) -> float:
    return math.degrees(math.atan2(b.y - a.y, b.x - a.x))


def _angle_diff(b1: float, b2: float) -> float:
    diff = (b2 - b1 + 180.0) % 360.0 - 180.0
    return diff


def snap_to_node(pkg_index: PackageIndex, floor_id: str, x: float, y: float) -> NavPoint:
    """吸附到同层最近拓扑节点（起点豁免中间节点白名单，任意类型可用）。"""
    best, best_d = None, float("inf")
    for point in pkg_index.nodes.values():
        if point.floor_id != floor_id:
            continue
        d = math.hypot(point.x - x, point.y - y)
        if d < best_d:
            best, best_d = point, d
    if best is None:
        raise ApiError(422, "LOCATION_UNRELIABLE", "起点楼层没有可用拓扑节点")
    return best


def _instruction(
    sequence: int, action: InstructionAction, text: str, distance_m: float | None,
    priority: InstructionPriority, trigger: Trigger,
) -> NavigationInstruction:
    return NavigationInstruction(
        instructionId=f"ins_{sequence}",
        instructionSequence=sequence,
        action=action,
        text=text,
        distanceM=None if distance_m is None else round(distance_m, 2),
        landmark=None,
        priority=priority,
        trigger=trigger,
        expiresAt=None,
    )


def build_route_info(
    route: dict, pkg: MapPackage, index: PackageIndex, path_points: list[NavPoint]
) -> RouteInfo:
    """从引擎输出构建 RouteInfo 与 segments（首条指令=segments[0]）。"""
    node_ids: list[str] = route["path"]
    segments: list[NavigationInstruction] = []
    seq = 0
    # 剩余距离后缀和：remaining[i] = 从 path[i] 沿路线到终点的边长和
    edge_dists: list[float] = []
    for a_id, b_id in zip(node_ids[:-1], node_ids[1:]):
        edge = index.edge_between(a_id, b_id)
        edge_dists.append(edge["distance"] if edge else 0.0)
    remaining = [0.0] * len(node_ids)
    for i in range(len(node_ids) - 2, -1, -1):
        remaining[i] = remaining[i + 1] + edge_dists[i]

    prev_bearing: float | None = None
    i = 1
    pending = 0.0  # 自上一条已发指令起累计的直行距离
    first_walk_emitted = False

    def _emit_walk(remaining_m: float) -> None:
        nonlocal seq, pending, first_walk_emitted
        if pending < 0.5:  # 触发距离必须 >0；过短直行并入转向指令
            return
        seq += 1
        if not first_walk_emitted:
            trigger = Trigger(type="immediate")
        else:
            trigger = Trigger(type="distance", valueM=round(pending, 2))
        segments.append(_instruction(
            seq, InstructionAction.CONTINUE,
            f"请沿当前通道直行 {pending:.0f} 米。", remaining_m,
            InstructionPriority.NORMAL, trigger,
        ))
        first_walk_emitted = True
        pending = 0.0

    while i < len(node_ids):
        a, b = path_points[i - 1], path_points[i]
        edge = index.edge_between(a.node_id, b.node_id)
        if edge and edge["crossFloor"] and edge.get("type") == "elevator":
            _emit_walk(remaining[i - 1])
            seq += 1
            segments.append(_instruction(
                seq, InstructionAction.ENTER_ELEVATOR,
                f"请进入电梯，前往 {b.floor_id} 楼。", remaining[i],
                InstructionPriority.SAFETY,
                Trigger(type="decision_point", nodeId=a.node_id),
            ))
            seq += 1
            segments.append(_instruction(
                seq, InstructionAction.EXIT_ELEVATOR,
                f"已到达 {b.floor_id} 楼，请退出电梯。", remaining[i],
                InstructionPriority.SAFETY,
                Trigger(type="immediate"),
            ))
            prev_bearing = None
            i += 1
            continue
        bearing = _bearing(a, b)
        if prev_bearing is not None:
            diff = _angle_diff(prev_bearing, bearing)
            if abs(diff) >= TURN_ANGLE_DEG:
                _emit_walk(remaining[i - 1])
                action = (
                    InstructionAction.TURN_LEFT if diff < 0 else InstructionAction.TURN_RIGHT
                )
                seq += 1
                segments.append(_instruction(
                    seq, action,
                    "前方路口请左转。" if action is InstructionAction.TURN_LEFT
                    else "前方路口请右转。",
                    remaining[i - 1], InstructionPriority.TURN,
                    Trigger(type="decision_point", nodeId=a.node_id),
                ))
        pending += edge["distance"] if edge else 0.0
        prev_bearing = bearing
        i += 1

    # 末段直行：其 distanceM 即该直行段长度（至终点）
    _emit_walk(pending)
    dest = path_points[-1]
    seq += 1
    segments.append(_instruction(
        seq, InstructionAction.ARRIVE, "已到达目的地附近，请留意周围环境。",
        0.0, InstructionPriority.NORMAL, Trigger(type="decision_point", nodeId=dest.node_id),
    ))

    fallback = RouteFallbackType.NONE
    safe_for_blind = True
    risk_notices: list[str] = []
    if route.get("wall_fallback"):
        fallback = RouteFallbackType.WALL_FALLBACK
        safe_for_blind = False
        risk_notices = ["路线包含贴墙引导段，未经盲道验证，请谨慎通行。"]
    return RouteInfo(
        routeId=f"route_{_short_id()}",
        mapVersion=pkg.mapVersion,
        routeRulesVersion=ROUTE_RULES_VERSION,
        distanceM=round(route["distance"], 2),
        estimatedDurationSec=None,
        polyline=[
            GeoPosition(x=p.x, y=p.y, floorId=p.floor_id) for p in path_points
        ],
        segments=segments,
        fallbackType=fallback,
        safeForBlind=safe_for_blind,
        riskNotices=risk_notices,
    )


def _short_id() -> str:
    import uuid

    return uuid.uuid4().hex[:16]


def plan_route(
    raw_data: dict, pkg: MapPackage, index: PackageIndex, origin: NavPoint,
    dest_place_id: str, user_mode: UserMode,
) -> tuple[RouteInfo, NavPoint]:
    """规划路线；错误码区分 ROUTE_NOT_FOUND 与 ACCESSIBLE_ROUTE_NOT_FOUND。"""
    from src.topology.route_rules import RouteGraph

    place = index.places.get(dest_place_id)
    if place is None:
        raise ApiError(404, "PLACE_NOT_FOUND", f"地点不存在：{dest_place_id}")
    dest_node = index.nodes.get(place.nodeId)
    if dest_node is None:
        raise ApiError(404, "PLACE_NOT_FOUND", f"地点节点不存在：{place.nodeId}")

    graph = RouteGraph(raw_data)
    mode = "blind" if user_mode is UserMode.BLIND else user_mode.value
    route = graph.generate_route(origin.node_id, dest_node.node_id, mode=mode)
    if not route.get("reachable"):
        if user_mode is UserMode.BLIND:
            fallback = graph.generate_route(origin.node_id, dest_node.node_id, mode="normal")
            if fallback.get("reachable"):
                raise ApiError(
                    422, "ACCESSIBLE_ROUTE_NOT_FOUND",
                    "盲模式无可达电梯路线（存在普通路线）",
                )
        raise ApiError(422, "ROUTE_NOT_FOUND", "当前起终点之间没有可达路线")
    if route.get("wall_fallback") and user_mode is UserMode.BLIND:
        # 盲模式不公开贴墙兜底路线（REQ-NAV：公开路线默认不得是 wall_fallback）
        raise ApiError(422, "ROUTE_NOT_FOUND", "当前起终点之间没有适盲路线")
    path_points = [index.nodes[nid] for nid in route["path"]]
    return build_route_info(route, pkg, index, path_points), dest_node
