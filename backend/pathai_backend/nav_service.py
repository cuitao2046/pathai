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
from datetime import datetime, timezone

from pathai_backend.errors import ApiError
from pathai_backend.hazard_layer import (
    LEVEL_RANK,
    STATUS_UNKNOWN,
    HazardLayer,
    HazardZone,
)
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
from pathai_backend.schemas.nav import RouteHazard, RouteInfo

ROUTE_RULES_VERSION = "1.1.0"
LOCATION_ALGORITHM_VERSION = "location-0.3"  # 0.3.3：(x,y,z) 三维联合解算 + z 先验
#: 工程初始阈值（导航阈值冻结值：到达 2.0m / 转向提前 3.0m，见 docs/现场测试方案.md §3.4）。
ORIGIN_MIN_CONFIDENCE = 0.5
ARRIVAL_RADIUS_M = 2.0
OFF_ROUTE_M = 8.0
DECISION_POINT_RADIUS_M = 3.0
TURN_ANGLE_DEG = 30.0
#: 路口转向的提前播报距离（冻结值）。语音在拐点前约 3m 说出转向；
#: 转弯震动仍由客户端在最后 6m 内先接管，两者不依赖同一阈值。
TURN_PROMPT_DISTANCE_M = 3.0


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
    route: dict, pkg: MapPackage, index: PackageIndex, path_points: list[NavPoint],
    hazards: list[RouteHazard] | None = None,
    hazard_layer: HazardLayer | None = None,
    turn_prompt_distance_m: float = TURN_PROMPT_DISTANCE_M,
    approach: tuple[NavPoint, float] | None = None,
) -> RouteInfo:
    """从引擎输出构建 RouteInfo 与 segments（首条指令=segments[0]）。

    ``approach``：实际起点到图上入网节点的首段。路线几何以真实位置为首点，
    不把起点吸附成节点坐标；入网节点只用于图搜索。
    """
    node_ids: list[str] = list(route["path"])
    segments: list[NavigationInstruction] = []
    seq = 0
    # 剩余距离后缀和：remaining[i] = 从 path[i] 沿路线到终点的物理边长和。
    # 优先使用 RouteGraph 返回的逐边物理距离，避免虚拟边/重复边反查失配。
    edge_dists: list[float] = list(route.get("edgeDistances") or [])
    if len(edge_dists) != len(node_ids) - 1:
        edge_dists = []
        for a_id, b_id in zip(node_ids[:-1], node_ids[1:]):
            edge = index.edge_between(a_id, b_id)
            edge_dists.append(edge["distance"] if edge else 0.0)
    if approach is not None:
        # 首段：真实位置 → 入网节点；后续边距离整体后移一位。
        approach_point, approach_distance = approach
        path_points = [approach_point, *path_points]
        node_ids = [approach_point.node_id, *node_ids]
        edge_dists = [approach_distance, *edge_dists]
    remaining = [0.0] * len(node_ids)
    for i in range(len(node_ids) - 2, -1, -1):
        remaining[i] = remaining[i + 1] + edge_dists[i]

    prev_bearing: float | None = None
    i = 1
    pending = 0.0  # 自上一条已发指令起累计的直行距离
    first_walk_emitted = False

    def _emit_walk(remaining_at_end: float, *, final: bool = False) -> None:
        """发出一条直行指令。

        ``remaining_at_end`` 是该直行段结束点（下一个决策点）到终点的剩余距离。
        ``distance`` trigger 的 valueM 表示“剩余距离降到该值时切换到下一条指令”，
        不是本段长度；路口前提前 ``turn_prompt_distance_m`` 切换，确保转向先播。
        """
        nonlocal seq, pending, first_walk_emitted
        if pending < 0.5:  # 触发距离必须 >0；过短直行并入转向指令
            return
        seq += 1
        if final:
            # 末段直行后直接进入到达判定；valueM 必须 >0（schema 约束）。
            trigger = Trigger(type="distance", valueM=0.01)
            distance_m = pending
        elif not first_walk_emitted:
            trigger = Trigger(type="immediate")
            distance_m = remaining_at_end + pending
        else:
            trigger = Trigger(
                type="distance",
                valueM=round(remaining_at_end + turn_prompt_distance_m, 2),
            )
            distance_m = remaining_at_end + pending
        segments.append(_instruction(
            seq, InstructionAction.CONTINUE,
            f"请沿当前通道直行 {pending:.0f} 米。", distance_m,
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
                # 坐标系为 x 东 y 北，atan2 数学角系逆时针为正：diff>0 = 左转
                action = (
                    InstructionAction.TURN_LEFT if diff > 0 else InstructionAction.TURN_RIGHT
                )
                seq += 1
                direction = "左" if action is InstructionAction.TURN_LEFT else "右"
                # 用户明确要求转向文案保持短句，避免“前方约 X 米路口请右转”过长；
                # 提前量仍由 distance trigger 的 10m 阈值保证。
                segments.append(_instruction(
                    seq, action,
                    f"即将{direction}转。",
                    remaining[i - 1], InstructionPriority.TURN,
                    Trigger(type="decision_point", nodeId=a.node_id),
                ))
        pending += edge["distance"] if edge else 0.0
        prev_bearing = bearing
        i += 1

    # 末段直行：distanceM 即该直行段长度（至终点）
    _emit_walk(pending, final=True)
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
        distanceM=round(
            (
                route.get("physicalDistance")
                if route.get("physicalDistance") is not None
                else route["distance"]
            )
            + (approach[1] if approach is not None else 0.0),
            2,
        ),
        estimatedDurationSec=None,
        polyline=[
            GeoPosition(x=p.x, y=p.y, floorId=p.floor_id) for p in path_points
        ],
        segments=segments,
        fallbackType=fallback,
        safeForBlind=safe_for_blind,
        riskNotices=risk_notices,
        hazards=hazards or [],
        hazardLayerStatus=hazard_layer.status if hazard_layer is not None else STATUS_UNKNOWN,
        hazardLayerVersion=hazard_layer.hazard_version if hazard_layer is not None else None,
        hazardLayerNotice=(
            hazard_layer.notice if hazard_layer is not None and hazard_layer.degraded() else None
        ),
    )


def _short_id() -> str:
    import uuid

    return uuid.uuid4().hex[:16]


def _route_hazards(
    active: tuple[HazardZone, ...], route_edges: set[str], baseline_edges: set[str] | None
) -> list[RouteHazard]:
    """筛选与本条路线相关的危险：路线命中（onRoute）或已绕开（avoided）。

    只把「基线路线（不考虑危险）会经过、但最终路线不再经过」的条目判为已绕开，
    避免把全楼危险都塞进响应。
    """
    result: list[RouteHazard] = []
    for hazard in active:
        edges = set(hazard.edge_ids)
        on_route = bool(edges & route_edges)
        if not on_route and not (baseline_edges and edges & baseline_edges):
            continue
        result.append(RouteHazard(
            hazardId=hazard.hazard_id,
            level=hazard.level,
            type=hazard.hazard_type,
            reason=hazard.reason or hazard.hazard_type,
            spokenText=hazard.spoken_text,
            edgeIds=list(hazard.edge_ids),
            onRoute=on_route,
            avoided=not on_route,
            penaltyM=hazard.penalty_m,
        ))
    result.sort(key=lambda h: -LEVEL_RANK[h.level])
    return result


def _blocking_hazards(
    active: tuple[HazardZone, ...], baseline_edges: set[str] | None
) -> list[HazardZone]:
    """导致本次不可达的 block 危险：基线（无危险约束）路线会经过的那些边。"""
    if not baseline_edges:
        return []
    return [
        h for h in active
        if h.level == "block" and set(h.edge_ids) & baseline_edges
    ]


def _hazard_hint(hazards: list[HazardZone]) -> str:
    reasons: list[str] = []
    for hazard in hazards:
        text = hazard.reason or hazard.hazard_type
        if text not in reasons:
            reasons.append(text)
    return "、".join(reasons[:3])


def _hazard_details(hazards: list[HazardZone]) -> dict:
    return {
        "hazards": [
            {
                "hazardId": h.hazard_id,
                "level": h.level,
                "type": h.hazard_type,
                "reason": h.reason,
                "edgeIds": list(h.edge_ids),
            }
            for h in hazards
        ]
    }


def plan_route(
    raw_data: dict, pkg: MapPackage, index: PackageIndex, origin: NavPoint,
    dest_place_id: str, user_mode: UserMode, hazard_layer: HazardLayer | None = None,
    turn_prompt_distance_m: float = TURN_PROMPT_DISTANCE_M,
    origin_position: tuple[str, float, float] | None = None,
) -> tuple[RouteInfo, NavPoint]:
    """规划路线；错误码区分 ROUTE_NOT_FOUND 与 ACCESSIBLE_ROUTE_NOT_FOUND。

    ``origin`` 只是图上入网节点（用于寻路）；``origin_position`` 是真实起点
    (floorId, x, y)。提供时路线以真实位置为首点，不再把起点吸附成节点坐标。

    危险层语义：
    - block：进入 edge_allowed 硬禁行（四层回退都不会放行）；
    - avoid：在边权上加 penaltyM，能绕则绕；
    - notice：不影响选路，只随路线返回；
    - 普通模式保持现有语义（不读 blindAccessible），因此也不受危险层影响；
    - 生效时间在每次规划时按当前时间判定，过期条目不参与。
    """
    from src.topology.route_rules import RouteGraph

    place = index.places.get(dest_place_id)
    if place is None:
        raise ApiError(404, "PLACE_NOT_FOUND", f"地点不存在：{dest_place_id}")
    dest_node = index.nodes.get(place.nodeId)
    if dest_node is None:
        raise ApiError(404, "PLACE_NOT_FOUND", f"地点节点不存在：{place.nodeId}")

    mode = "blind" if user_mode is UserMode.BLIND else user_mode.value
    now = datetime.now(timezone.utc)
    active: tuple[HazardZone, ...] = ()
    if hazard_layer is not None and mode != "normal":
        active = hazard_layer.active(now, mode)

    graph = RouteGraph(raw_data, hazards=[h.to_rule() for h in active])
    route = graph.generate_route(origin.node_id, dest_node.node_id, mode=mode)

    # 基线：不带危险约束的同一模式路线，用于判定「绕开了什么」与「被什么阻断」
    baseline_graph = RouteGraph(raw_data) if active else None
    baseline_edges: set[str] | None = None
    if baseline_graph is not None:
        baseline = baseline_graph.generate_route(origin.node_id, dest_node.node_id, mode=mode)
        if baseline.get("reachable"):
            baseline_edges = set(baseline.get("edges") or ())

    if not route.get("reachable"):
        blocked = _blocking_hazards(active, baseline_edges)
        hint = _hazard_hint(blocked)
        if user_mode is UserMode.BLIND:
            fallback_graph = baseline_graph if baseline_graph is not None else graph
            fallback = fallback_graph.generate_route(
                origin.node_id, dest_node.node_id, mode="normal"
            )
            if fallback.get("reachable"):
                if blocked:
                    raise ApiError(
                        422, "ACCESSIBLE_ROUTE_NOT_FOUND",
                        f"前往「{place.name}」必须经过危险区域：{hint}；当前没有适盲绕行路线",
                        details=_hazard_details(blocked),
                    )
                raise ApiError(
                    422, "ACCESSIBLE_ROUTE_NOT_FOUND",
                    "盲模式无可达电梯路线（存在普通路线）",
                )
        if blocked:
            raise ApiError(
                422, "ROUTE_NOT_FOUND",
                f"前往「{place.name}」必须经过危险区域：{hint}；当前没有可用绕行路线",
                details=_hazard_details(blocked),
            )
        raise ApiError(422, "ROUTE_NOT_FOUND", "当前起终点之间没有可达路线")
    if route.get("wall_fallback") and user_mode is UserMode.BLIND:
        # 盲模式不公开贴墙兜底路线（REQ-NAV：公开路线默认不得是 wall_fallback）
        blocked = _blocking_hazards(active, baseline_edges)
        if blocked:
            raise ApiError(
                422, "ROUTE_NOT_FOUND",
                f"前往「{place.name}」必须经过危险区域：{_hazard_hint(blocked)}；当前没有适盲路线",
                details=_hazard_details(blocked),
            )
        raise ApiError(422, "ROUTE_NOT_FOUND", "当前起终点之间没有适盲路线")
    path_points = [index.nodes[nid] for nid in route["path"]]
    route_hazards = _route_hazards(active, set(route.get("edges") or ()), baseline_edges)
    approach: tuple[NavPoint, float] | None = None
    if origin_position is not None:
        floor_id, x, y = origin_position
        if floor_id == origin.floor_id:
            distance = math.hypot(x - origin.x, y - origin.y)
            # 与节点重合（含浮点噪声）时不加首段，避免零长边。
            if distance > 0.05:
                approach = (NavPoint("__origin__", floor_id, x, y), distance)
    return build_route_info(
        route, pkg, index, path_points, route_hazards, hazard_layer,
        turn_prompt_distance_m=turn_prompt_distance_m, approach=approach,
    ), dest_node
