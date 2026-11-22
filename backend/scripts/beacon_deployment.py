# -*- coding: utf-8 -*-
"""蓝牙部署点位的安装墙/参考墙离线预计算（与客户端算法同口径）。

事实源：`client/core/collection/beacon-deployment.ts`（纯几何）。本模块是它的
零依赖 Python 移植，在构建地图包时一次性算好，随 `beacons[].deployment` 下发，
客户端选中点位时直接展示，不再实时计算（真机实测单颗约 20s）。

口径（与 TS 版本逐条一致）：
- 公共空间取「连通的公共房间并集」（门洞间隙 ≤ 0.12m 视为连通）；
- 安装墙：公共空间边界上距信标点最近的墙段；
- 参考墙：与安装墙垂直（|dot| ≤ 0.3）、垂足落在墙段内部、沿安装墙偏移 ≥ 0.1m，
  且信标到垂足之间始终位于公共空间内、不被其他墙体阻断，每侧取最近；
- 长度 < 0.5m 的碎墙段（门垛、墙角补段）忽略。

用法：
    analyzer = BeaconDeploymentAnalyzer(walls, rooms)
    plan = analyzer.analyze(x, y)
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Sequence

#: 结果口径版本号，随算法语义变化递增（客户端缓存/回退判定用）。
ALGORITHM_VERSION = "1.0.0"

#: 垂直判定：两墙单位方向向量的点积绝对值上限。
PERPENDICULAR_DOT_MAX = 0.3
#: 参考墙沿安装墙方向的最小偏移（米）：排除信标正对的墙角。
REFERENCE_MIN_OFFSET_M = 0.1
#: 信标点落在公共空间多边形外时，就近吸附的最大距离（米）。
PUBLIC_SPACE_SNAP_M = 1.0
#: 相邻公共房间边界间隙不超过该值（门洞/门槛建模误差）时，视为同一连通公共空间。
PUBLIC_SPACE_CONNECT_GAP_M = 0.12
#: 参考墙垂足距墙段端点的最小比例：端点处无法垂直测距。
REFERENCE_EDGE_MARGIN = 0.02
#: 有效墙面最小长度（米）：门垛、墙角补段等 0.2m 级碎段不作为安装墙或参考墙。
MIN_WALL_LENGTH_M = 0.5
#: 可见性检测允许贴近公共空间边界的距离（米）。
SPACE_EDGE_TOLERANCE_M = 0.3
#: 墙与公共空间边界关联时，沿法线采样的偏移（墙半厚 + 该值）。
WALL_ASSOCIATION_OFFSET_M = 0.3
#: 可见性检测的采样间距（米）。
VISIBILITY_SAMPLE_STEP_M = 0.5
#: 阻挡判定：交点距测量线端点的最小比例 / 距墙段端点的最小距离。
BLOCK_LINE_MARGIN = 0.02
BLOCK_WALL_ENDPOINT_MARGIN_M = 0.05
#: 与测量线夹角小于该点积阈值（约 20°）的墙视为贴墙测量，不算阻挡。
BLOCK_PARALLEL_DOT_MAX = 0.94
#: 距离相等时的比较容差。
DISTANCE_EPSILON = 1e-6
#: 数据缺少 public 标记时，按房间类型兜底识别公共空间。
PUBLIC_ROOM_TYPES = frozenset(
    {
        "corridor",
        "lobby",
        "hall",
        "foyer",
        "elevator_lobby",
        "stair_lobby",
        "staircase",
        "entrance_hall",
    }
)


@dataclass(frozen=True)
class Bounds:
    min_x: float
    min_y: float
    max_x: float
    max_y: float


@dataclass(frozen=True)
class PreparedRoom:
    rings: tuple[tuple[tuple[float, float], ...], ...]
    bounds: Bounds | None


@dataclass(frozen=True)
class WallSegment:
    index: int
    wall_id: str
    x1: float
    y1: float
    x2: float
    y2: float
    thickness: float
    dir_x: float
    dir_y: float


def _room_rings(room: dict) -> tuple[tuple[tuple[float, float], ...], ...]:
    coordinates = ((room.get("geometry") or {}).get("coordinates")) or ()
    rings = []
    for ring in coordinates:
        rings.append(tuple((float(point[0]), float(point[1])) for point in ring))
    return tuple(rings)


def _is_public_room(room: dict) -> bool:
    properties = room.get("properties") or {}
    public = properties.get("public")
    if public is True:
        return True
    if public is False:
        return False
    room_type = properties.get("roomType") or properties.get("type") or ""
    return room_type in PUBLIC_ROOM_TYPES


def _compute_bounds(rings: Sequence[Sequence[tuple[float, float]]]) -> Bounds | None:
    min_x = math.inf
    min_y = math.inf
    max_x = -math.inf
    max_y = -math.inf
    for ring in rings:
        for x, y in ring:
            if x < min_x:
                min_x = x
            if x > max_x:
                max_x = x
            if y < min_y:
                min_y = y
            if y > max_y:
                max_y = y
    if not math.isfinite(min_x):
        return None
    return Bounds(min_x, min_y, max_x, max_y)


def _prepare_room(room: dict) -> PreparedRoom:
    rings = _room_rings(room)
    return PreparedRoom(rings, _compute_bounds(rings))


def _in_ring(x: float, y: float, ring: Sequence[tuple[float, float]]) -> bool:
    """射线法判断点是否在环内。"""
    inside = False
    count = len(ring)
    j = count - 1
    for i in range(count):
        xi, yi = ring[i]
        xj, yj = ring[j]
        if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / (yj - yi) + xi:
            inside = not inside
        j = i
    return inside


def _in_room(x: float, y: float, room: PreparedRoom) -> bool:
    bounds = room.bounds
    if bounds is None:
        return False
    if x < bounds.min_x or x > bounds.max_x or y < bounds.min_y or y > bounds.max_y:
        return False
    return any(_in_ring(x, y, ring) for ring in room.rings)


def _closest_on_segment(
    px: float, py: float, x1: float, y1: float, x2: float, y2: float
) -> tuple[float, float, float, float]:
    """点到线段最近点（x, y, 距离, 参数 t∈[0,1]）。"""
    dx = x2 - x1
    dy = y2 - y1
    length2 = dx * dx + dy * dy
    if length2 <= 0:
        t = 0.0
    else:
        t = min(1.0, max(0.0, ((px - x1) * dx + (py - y1) * dy) / length2))
    x = x1 + t * dx
    y = y1 + t * dy
    return x, y, math.hypot(px - x, py - y), t


def _distance_to_room(x: float, y: float, room: PreparedRoom) -> float:
    best = math.inf
    for ring in room.rings:
        count = len(ring)
        for i in range(count):
            ax, ay = ring[i]
            bx, by = ring[(i + 1) % count]
            _, _, distance, _ = _closest_on_segment(x, y, ax, ay, bx, by)
            if distance < best:
                best = distance
    return best


def _bounds_gap(a: Bounds, b: Bounds) -> float:
    dx = max(0.0, a.min_x - b.max_x, b.min_x - a.max_x)
    dy = max(0.0, a.min_y - b.max_y, b.min_y - a.max_y)
    return math.hypot(dx, dy)


def _bounds_point_gap(bounds: Bounds, x: float, y: float) -> float:
    dx = max(bounds.min_x - x, 0.0, x - bounds.max_x)
    dy = max(bounds.min_y - y, 0.0, y - bounds.max_y)
    return math.hypot(dx, dy)


def _orientation(
    px: float, py: float, qx: float, qy: float, rx: float, ry: float
) -> float:
    return (qy - py) * (rx - qx) - (qx - px) * (ry - qy)


def _segments_intersect(
    ax1: float,
    ay1: float,
    ax2: float,
    ay2: float,
    bx1: float,
    by1: float,
    bx2: float,
    by2: float,
) -> bool:
    o1 = _orientation(ax1, ay1, ax2, ay2, bx1, by1)
    o2 = _orientation(ax1, ay1, ax2, ay2, bx2, by2)
    o3 = _orientation(bx1, by1, bx2, by2, ax1, ay1)
    o4 = _orientation(bx1, by1, bx2, by2, ax2, ay2)
    return (o1 > 0) != (o2 > 0) and (o3 > 0) != (o4 > 0)


def _segment_distance(
    ax1: float,
    ay1: float,
    ax2: float,
    ay2: float,
    bx1: float,
    by1: float,
    bx2: float,
    by2: float,
) -> float:
    if _segments_intersect(ax1, ay1, ax2, ay2, bx1, by1, bx2, by2):
        return 0.0
    return min(
        _closest_on_segment(ax1, ay1, bx1, by1, bx2, by2)[2],
        _closest_on_segment(ax2, ay2, bx1, by1, bx2, by2)[2],
        _closest_on_segment(bx1, by1, ax1, ay1, ax2, ay2)[2],
        _closest_on_segment(bx2, by2, ax1, ay1, ax2, ay2)[2],
    )


def _rooms_connected(
    a: PreparedRoom, b: PreparedRoom, limit: float
) -> bool:
    """两个房间多边形的边界间隙是否不超过 limit（包围盒预筛）。"""
    if a.bounds is None or b.bounds is None or _bounds_gap(a.bounds, b.bounds) > limit:
        return False
    for ra in a.rings:
        count_a = len(ra)
        for i in range(count_a):
            ax1, ay1 = ra[i]
            ax2, ay2 = ra[(i + 1) % count_a]
            for rb in b.rings:
                count_b = len(rb)
                for j in range(count_b):
                    bx1, by1 = rb[j]
                    bx2, by2 = rb[(j + 1) % count_b]
                    if _segment_distance(ax1, ay1, ax2, ay2, bx1, by1, bx2, by2) <= limit:
                        return True
    return False


def _unit_direction(x1: float, y1: float, x2: float, y2: float) -> tuple[float, float] | None:
    """统一墙段单位方向（优先 +x，其次 +y），保证左右判定与坐标点顺序无关。"""
    dx = x2 - x1
    dy = y2 - y1
    length = math.hypot(dx, dy)
    if length <= 1e-9:
        return None
    dx /= length
    dy /= length
    if dx < -1e-9 or (abs(dx) <= 1e-9 and dy < 0):
        dx = -dx
        dy = -dy
    return dx, dy


def _collect_segments(walls: Sequence[dict]) -> list[WallSegment]:
    segments: list[WallSegment] = []
    for wall_index, wall in enumerate(walls):
        coordinates = ((wall.get("geometry") or {}).get("coordinates")) or ()
        thickness = (wall.get("properties") or {}).get("thickness")
        if thickness is None:
            thickness = 0.2
        for i in range(len(coordinates) - 1):
            x1, y1 = float(coordinates[i][0]), float(coordinates[i][1])
            x2, y2 = float(coordinates[i + 1][0]), float(coordinates[i + 1][1])
            if math.hypot(x2 - x1, y2 - y1) < MIN_WALL_LENGTH_M:
                continue
            direction = _unit_direction(x1, y1, x2, y2)
            if direction is None:
                continue
            segments.append(
                WallSegment(
                    index=len(segments),
                    wall_id=str(wall.get("id") or ""),
                    x1=x1,
                    y1=y1,
                    x2=x2,
                    y2=y2,
                    thickness=float(thickness),
                    dir_x=direction[0],
                    dir_y=direction[1],
                )
            )
    return segments


def _empty_result() -> dict:
    return {
        "installationWallId": None,
        "installationDistanceM": None,
        "references": [],
        "algorithmVersion": ALGORITHM_VERSION,
    }


class BeaconDeploymentAnalyzer:
    """单层几何预计算器：房间多边形/包围盒/墙段只解析一次，可复用于多个信标。"""

    def __init__(self, walls: Sequence[dict], rooms: Sequence[dict]) -> None:
        self._rooms = [_prepare_room(room) for room in rooms]
        self._public_room_indices = [
            index for index, room in enumerate(rooms) if _is_public_room(room)
        ]
        self._segments = _collect_segments(walls)

    def _expand_public_spaces(self, seeds: Sequence[int]) -> list[int]:
        """把种子公共房间沿门洞/边界间隙扩展到整个连通的公共空间。"""
        selected = set(seeds)
        queue = list(seeds)
        head = 0
        while head < len(queue):
            current = queue[head]
            head += 1
            current_room = self._rooms[current]
            for index in self._public_room_indices:
                if index in selected:
                    continue
                if not _rooms_connected(
                    current_room,
                    self._rooms[index],
                    PUBLIC_SPACE_CONNECT_GAP_M,
                ):
                    continue
                selected.add(index)
                queue.append(index)
        return queue

    def _find_public_spaces(self, x: float, y: float) -> list[int]:
        if not self._public_room_indices:
            return []
        seeds = [
            index
            for index in self._public_room_indices
            if _in_room(x, y, self._rooms[index])
        ]
        if not seeds:
            seeds = [
                index
                for index in self._public_room_indices
                if _distance_to_room(x, y, self._rooms[index]) <= PUBLIC_SPACE_SNAP_M
            ]
        if not seeds:
            return []
        return self._expand_public_spaces(seeds)

    def _bounds_public_space(self, segment: WallSegment, spaces: Sequence[int]) -> bool:
        """墙段是否围合该公共空间：沿法线偏移半墙厚+余量采样，任一点落在空间内。"""
        offset = segment.thickness / 2 + WALL_ASSOCIATION_OFFSET_M
        nx = -segment.dir_y
        ny = segment.dir_x
        min_x = min(segment.x1, segment.x2) - offset
        max_x = max(segment.x1, segment.x2) + offset
        min_y = min(segment.y1, segment.y2) - offset
        max_y = max(segment.y1, segment.y2) + offset
        for index in spaces:
            room = self._rooms[index]
            bounds = room.bounds
            if (
                bounds is None
                or bounds.max_x < min_x
                or bounds.min_x > max_x
                or bounds.max_y < min_y
                or bounds.min_y > max_y
            ):
                continue
            for step in range(1, 10):
                ratio = step / 10
                x = segment.x1 + (segment.x2 - segment.x1) * ratio
                y = segment.y1 + (segment.y2 - segment.y1) * ratio
                if _in_room(x + nx * offset, y + ny * offset, room) or _in_room(
                    x - nx * offset, y - ny * offset, room
                ):
                    return True
        return False

    def _line_stays_in_public_space(
        self, x: float, y: float, hit_x: float, hit_y: float, spaces: Sequence[int]
    ) -> bool:
        """信标点到参考墙垂足之间是否始终位于公共空间内（允许贴边建模误差）。"""
        length = math.hypot(hit_x - x, hit_y - y)
        steps = max(10, math.ceil(length / VISIBILITY_SAMPLE_STEP_M))
        for step in range(1, steps):
            ratio = step / steps
            sample_x = x + (hit_x - x) * ratio
            sample_y = y + (hit_y - y) * ratio
            in_space = False
            for index in spaces:
                room = self._rooms[index]
                bounds = room.bounds
                if bounds is None or _bounds_point_gap(bounds, sample_x, sample_y) > SPACE_EDGE_TOLERANCE_M:
                    continue
                if _in_room(sample_x, sample_y, room) or _distance_to_room(
                    sample_x, sample_y, room
                ) <= SPACE_EDGE_TOLERANCE_M:
                    in_space = True
                    break
            if not in_space:
                return False
        return True

    def _line_blocked_by_walls(
        self,
        x: float,
        y: float,
        hit_x: float,
        hit_y: float,
        install: WallSegment,
        target: WallSegment,
    ) -> bool:
        """测量线是否被其他墙体阻断：端点相接（T 型墙角/门垛）不算阻挡。"""
        dx = hit_x - x
        dy = hit_y - y
        length = math.hypot(dx, dy)
        if length <= 1e-9:
            return False
        unit_x = dx / length
        unit_y = dy / length
        for segment in self._segments:
            if segment.index == target.index or segment.index == install.index:
                continue
            if segment.wall_id and segment.wall_id == install.wall_id:
                continue
            if abs(unit_x * segment.dir_x + unit_y * segment.dir_y) > BLOCK_PARALLEL_DOT_MAX:
                continue
            wall_dx = segment.x2 - segment.x1
            wall_dy = segment.y2 - segment.y1
            denominator = dx * wall_dy - dy * wall_dx
            if abs(denominator) <= 1e-12:
                continue
            offset_x = segment.x1 - x
            offset_y = segment.y1 - y
            t = (offset_x * wall_dy - offset_y * wall_dx) / denominator
            u = (offset_x * dy - offset_y * dx) / denominator
            if t <= BLOCK_LINE_MARGIN or t >= 1 - BLOCK_LINE_MARGIN:
                continue
            if u <= 0 or u >= 1:
                continue
            wall_length = math.hypot(wall_dx, wall_dy)
            endpoint_dist = min(u, 1 - u) * wall_length
            if endpoint_dist <= BLOCK_WALL_ENDPOINT_MARGIN_M:
                continue
            return True
        return False

    def _pick_installation_wall(
        self, x: float, y: float, segments: Sequence[WallSegment]
    ) -> tuple[WallSegment, float, float] | None:
        """最近墙段（附距离与垂足参数 t）；等距时优先垂足在墙段内部的墙面。"""
        best: tuple[WallSegment, float, float] | None = None
        for segment in segments:
            _, _, distance, t = _closest_on_segment(
                x, y, segment.x1, segment.y1, segment.x2, segment.y2
            )
            if best is None or distance < best[1] - DISTANCE_EPSILON:
                best = (segment, distance, t)
                continue
            if abs(distance - best[1]) > DISTANCE_EPSILON:
                continue
            interior = 1e-9 < t < 1 - 1e-9
            best_interior = 1e-9 < best[2] < 1 - 1e-9
            if interior and not best_interior:
                best = (segment, distance, t)
        return best

    def analyze(self, x: float, y: float) -> dict:
        """计算信标点位的安装墙与左右参考测距墙；无公共空间时不返回参考墙。"""
        spaces = self._find_public_spaces(x, y)
        if not spaces:
            return _empty_result()
        boundary = [
            segment for segment in self._segments if self._bounds_public_space(segment, spaces)
        ]
        install = self._pick_installation_wall(x, y, boundary)
        if install is None:
            return _empty_result()
        install_segment, install_distance, _ = install
        install_dir_x = install_segment.dir_x
        install_dir_y = install_segment.dir_y

        by_side: dict[str, dict | None] = {"left": None, "right": None}
        for segment in boundary:
            if segment.index == install_segment.index:
                continue
            if segment.wall_id and segment.wall_id == install_segment.wall_id:
                continue
            if (
                abs(install_dir_x * segment.dir_x + install_dir_y * segment.dir_y)
                > PERPENDICULAR_DOT_MAX
            ):
                continue
            hit_x, hit_y, distance, t = _closest_on_segment(
                x, y, segment.x1, segment.y1, segment.x2, segment.y2
            )
            # 垂足落在端点说明信标垂线没有打到该墙的墙面，无法垂直测距。
            if t <= REFERENCE_EDGE_MARGIN or t >= 1 - REFERENCE_EDGE_MARGIN:
                continue
            along = (hit_x - x) * install_dir_x + (hit_y - y) * install_dir_y
            if abs(along) < REFERENCE_MIN_OFFSET_M:
                continue
            side = "right" if along > 0 else "left"
            current = by_side[side]
            if current is not None and distance >= current["distanceM"] - DISTANCE_EPSILON:
                continue
            if not self._line_stays_in_public_space(x, y, hit_x, hit_y, spaces):
                continue
            if self._line_blocked_by_walls(
                x, y, hit_x, hit_y, install_segment, segment
            ):
                continue
            by_side[side] = {
                "wallId": segment.wall_id,
                "side": side,
                "distanceM": distance,
                "hit": {"x": hit_x, "y": hit_y},
            }

        references = []
        if by_side["left"] is not None:
            references.append(by_side["left"])
        if by_side["right"] is not None:
            references.append(by_side["right"])
        return {
            "installationWallId": install_segment.wall_id or None,
            "installationDistanceM": install_distance,
            "references": references,
            "algorithmVersion": ALGORITHM_VERSION,
        }


def analyze_beacon_deployment(x: float, y: float, walls: Sequence[dict], rooms: Sequence[dict]) -> dict:
    """单颗信标的一次性计算（批量场景请复用 BeaconDeploymentAnalyzer）。"""
    return BeaconDeploymentAnalyzer(walls, rooms).analyze(x, y)
