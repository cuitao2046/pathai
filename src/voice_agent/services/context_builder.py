# -*- coding: utf-8 -*-
"""自我中心空间上下文构建（spec REQ-VOICE-02）。

输入：发布包原始 dict（`PublishedMap.data`）+ 用户位置/朝向/运动状态。
输出：`UserSpatialContext`（时钟方向 + 米/步数 + 区域 + 周边要素），
供后续 LLM 直答或模板播报消费；严禁输出拓扑编号与绝对坐标。
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Iterable

from ..schemas.spatial_context import (
    MotionState,
    NearbyElement,
    UserSpatialContext,
)
from .spatial_transformer import describe, steps_for

FLOOR_LABEL = {"1": "一楼", "2": "二楼", "3": "三楼", "4": "四楼"}
NEARBY_RADIUS_M = 8.0
NEARBY_MAX_ELEMENTS = 8
TACTILE_HIT_RADIUS_M = 1.0

#: 语义房间类型 → 区域类型
_REGION_BY_ROOM_TYPE = {
    "corridor": "corridor",
    "lobby": "lobby",
    "elevator_lobby": "elevator_hall",
    "stair_lobby": "stair_hall",
    "staircase": "stair_hall",
}
_NON_LABEL_ROOM_TYPES = {"infrastructure", "corridor", "staircase", "stair_lobby", "elevator_lobby"}


def _floor_label(floor_id: str) -> str:
    return FLOOR_LABEL.get(floor_id, f"{floor_id}楼")


def _rings_of(geometry: dict) -> list[list[list[float]]]:
    if not isinstance(geometry, dict):
        return []
    if geometry.get("type") == "Polygon":
        return geometry.get("coordinates") or []
    if geometry.get("type") == "MultiPolygon":
        return [ring for polygon in geometry.get("coordinates") or [] for ring in polygon]
    return []


def _centroid_of(geometry: dict) -> tuple[float, float] | None:
    rings = _rings_of(geometry)
    if not rings:
        if isinstance(geometry, dict) and geometry.get("type") == "Point":
            return tuple(geometry["coordinates"][:2])
        return None
    outer = rings[0]
    if not outer:
        return None
    return (
        sum(p[0] for p in outer) / len(outer),
        sum(p[1] for p in outer) / len(outer),
    )


def _point_in_ring(x: float, y: float, ring: list[list[float]]) -> bool:
    """射线法；ring 为闭合折线（首尾点相同也可）。"""
    inside = False
    count = len(ring)
    for i in range(count):
        x1, y1 = ring[i][0], ring[i][1]
        x2, y2 = ring[(i + 1) % count][0], ring[(i + 1) % count][1]
        if (y1 > y) != (y2 > y):
            cross_x = x1 + (y - y1) * (x2 - x1) / (y2 - y1)
            if x < cross_x:
                inside = not inside
    return inside


def _point_in_geometry(x: float, y: float, geometry: dict) -> bool:
    rings = _rings_of(geometry)
    return any(_point_in_ring(x, y, ring) for ring in rings)


def _zone_suffix(centroid: tuple[float, float], bounds: tuple[float, float, float, float]) -> str:
    minx, miny, maxx, maxy = bounds
    xr = 0.5 if maxx <= minx else (centroid[0] - minx) / (maxx - minx)
    yr = 0.5 if maxy <= miny else (centroid[1] - miny) / (maxy - miny)
    horiz = "东" if xr >= 2 / 3 else ("西" if xr <= 1 / 3 else "中")
    vert = "北" if yr >= 2 / 3 else ("南" if yr <= 1 / 3 else "中")
    if horiz == "中" and vert == "中":
        return "中段"
    if horiz == "中":
        return f"{vert}段"
    if vert == "中":
        return f"{horiz}侧"
    return f"{horiz}{vert}角"


@dataclass(frozen=True)
class RoomInfo:
    room_id: str
    label: str
    room_type: str
    room_sub_type: str | None
    centroid: tuple[float, float]
    geometry: dict
    public: bool
    accessible: bool


@dataclass(frozen=True)
class NodeInfo:
    node_id: str
    node_type: str
    floor_id: str
    coordinates: tuple[float, float]
    door_type: str | None
    is_normally_open: bool | None
    facility_type: str | None
    label: str | None
    rooms: tuple[str, ...]


@dataclass(frozen=True)
class RiskInfo:
    risk_id: str
    risk_type: str
    risk_level: float
    label: str | None
    coordinates: tuple[float, float]


@dataclass
class FloorSpatial:
    floor_id: str
    nodes: list[NodeInfo]
    rooms: list[RoomInfo]
    columns: list[tuple[str, tuple[float, float]]]
    risks: list[RiskInfo]
    ramps: list[dict]
    tactile_paths: list[dict]
    material_changes: list[dict]
    bounds: tuple[float, float, float, float]


class SpatialIndex:
    """发布包 dict → 空间索引（纯数据，无外部依赖）。"""

    def __init__(self, floors: dict[str, FloorSpatial], defaults: dict):
        self.floors = floors
        self.defaults = defaults or {}

    @classmethod
    def from_package(cls, pkg: dict) -> "SpatialIndex":
        floors: dict[str, FloorSpatial] = {}
        for floor_id, floor in pkg["floors"].items():
            rooms = cls._rooms(floor)
            nodes = [
                NodeInfo(
                    node_id=n["id"],
                    node_type=n.get("type", ""),
                    floor_id=floor_id,
                    coordinates=(n["coordinates"][0], n["coordinates"][1]),
                    door_type=n.get("doorType"),
                    is_normally_open=n.get("isNormallyOpen"),
                    facility_type=n.get("facilityType"),
                    label=n.get("label"),
                    rooms=tuple(n.get("rooms") or ()),
                )
                for n in floor["topology"]["nodes"]
            ]
            geometry = floor.get("geometry") or {}
            columns = []
            for feature in geometry.get("columns") or []:
                point = _centroid_of(feature.get("geometry") or {})
                if point is not None:
                    columns.append((feature.get("id", ""), point))
            accessibility = floor.get("accessibility") or {}
            risks = [
                RiskInfo(
                    risk_id=r.get("id", ""),
                    risk_type=r.get("type", ""),
                    risk_level=float(r.get("riskLevel") or 0),
                    label=r.get("label"),
                    coordinates=(r["coordinates"][0], r["coordinates"][1]),
                )
                for r in accessibility.get("riskNodes") or []
                if r.get("coordinates")
            ]
            floors[floor_id] = FloorSpatial(
                floor_id=floor_id,
                nodes=nodes,
                rooms=rooms,
                columns=columns,
                risks=risks,
                ramps=list(accessibility.get("ramps") or []),
                tactile_paths=list(accessibility.get("tactilePaths") or []),
                material_changes=list(accessibility.get("groundMaterialChanges") or []),
                bounds=cls._bounds(rooms),
            )
        return cls(floors, pkg.get("accessibilityDefaults") or {})

    @staticmethod
    def _rooms(floor: dict) -> list[RoomInfo]:
        semantic = {r["id"]: r for r in (floor.get("semantic") or {}).get("rooms") or []}
        result: list[RoomInfo] = []
        for feature in (floor.get("geometry") or {}).get("rooms") or []:
            props = feature.get("properties") or {}
            room = semantic.get(props.get("roomId"))
            if room is None:
                continue
            centroid = _centroid_of(feature.get("geometry") or {}) or tuple(room["centroid"])
            result.append(
                RoomInfo(
                    room_id=room["id"],
                    label=room["label"],
                    room_type=room["type"],
                    room_sub_type=room.get("roomSubType"),
                    centroid=(centroid[0], centroid[1]),
                    geometry=feature.get("geometry") or {},
                    public=bool(room.get("public")),
                    accessible=bool(room.get("accessible")),
                )
            )
        return result

    @staticmethod
    def _bounds(rooms: list[RoomInfo]) -> tuple[float, float, float, float]:
        if not rooms:
            return (0.0, 0.0, 0.0, 0.0)
        xs = [r.centroid[0] for r in rooms]
        ys = [r.centroid[1] for r in rooms]
        return min(xs), min(ys), max(xs), max(ys)


def _region_of(floor: FloorSpatial, x: float, y: float) -> tuple[str, str]:
    """返回（区域类型, 区域名称）。"""
    containing = [r for r in floor.rooms if _point_in_geometry(x, y, r.geometry)]
    if containing:
        room = min(containing, key=lambda r: abs(r.centroid[0] - x) + abs(r.centroid[1] - y))
    elif floor.rooms:
        corridors = [r for r in floor.rooms if r.room_type == "corridor"] or floor.rooms
        room = min(corridors, key=lambda r: math.hypot(r.centroid[0] - x, r.centroid[1] - y))
    else:
        return "corridor", f"{_floor_label(floor.floor_id)}走道"

    if room.room_type in _REGION_BY_ROOM_TYPE:
        region_type = _REGION_BY_ROOM_TYPE[room.room_type]
    elif room.public and room.room_sub_type == "activity":
        region_type = "activity_zone"
    elif room.public:
        region_type = "lobby"
    else:
        region_type = "room"

    label = _floor_label(floor.floor_id) + room.label
    if room.room_type in ("corridor", "lobby", "stair_lobby"):
        label = f"{_floor_label(floor.floor_id)}{_zone_suffix(room.centroid, floor.bounds)}{room.label}"
    return region_type, label


def _door_element(
    node: NodeInfo,
    rooms_by_id: dict[str, RoomInfo],
    origin: tuple[float, float],
    heading_deg: float,
) -> NearbyElement | None:
    label = None
    for room_id in node.rooms:
        room = rooms_by_id.get(room_id)
        if room is not None and room.room_type not in _NON_LABEL_ROOM_TYPES:
            label = room.label
            break
    if node.door_type == "opening":
        element_type, note = "open_doorway_dk", "无摆弧门洞，可直接穿过"
        name = label or "门洞"
    elif node.door_type == "fire":
        if node.is_normally_open:
            element_type, note = "fire_door_normally_open", "常开防火门，可直接通行"
        else:
            element_type, note = "fire_door_normally_closed", "常闭防火门，可能需要推开"
        name = label or "防火门"
    else:
        element_type, note = "room_door", None
        name = label or "门"
    return _element(name, element_type, node.coordinates, origin, heading_deg, note)


def _element(
    name: str,
    element_type: str,
    coordinates: tuple[float, float],
    origin: tuple[float, float],
    heading_deg: float,
    note: str | None,
) -> NearbyElement:
    clock, distance = describe(origin[0], origin[1], coordinates[0], coordinates[1], heading_deg)
    rounded = round(distance, 1)
    return NearbyElement(
        name=name,
        element_type=element_type,  # type: ignore[arg-type]
        clock_direction=clock,
        distance_meters=rounded,
        distance_steps=steps_for(rounded),
        accessibility_note=note,
    )


_RISK_NOTES = {
    "outward_door": "门向外开，注意避让",
    "stair_entrance": "楼梯口，视障不建议通行",
    "elevator_entrance": "电梯口，注意轿厢位置",
}


def _risk_name(risk: RiskInfo) -> str:
    if risk.risk_type == "outward_door":
        return "外开门"
    if risk.risk_type == "elevator_entrance":
        return risk.label or "电梯口"
    if risk.risk_type == "stair_entrance":
        return risk.label or "楼梯口"
    return risk.label or "风险点"


def build_spatial_context(
    index: SpatialIndex,
    floor_id: str,
    x: float,
    y: float,
    heading_deg: float,
    motion_state: MotionState = "stationary",
) -> UserSpatialContext:
    floor = index.floors.get(floor_id)
    if floor is None:
        raise ValueError(f"楼层不存在：{floor_id}")

    region_type, region_name = _region_of(floor, x, y)
    rooms_by_id = {r.room_id: r for r in floor.rooms}
    origin = (x, y)
    placed: list[NearbyElement] = []

    for node in floor.nodes:
        if node.node_type == "doorway":
            element = _door_element(node, rooms_by_id, origin, heading_deg)
            if element is not None:
                placed.append(element)
        elif node.node_type in ("facility", "facility_entrance") and node.facility_type in (
            "elevator",
            "staircase",
        ):
            if node.facility_type == "elevator":
                placed.append(
                    _element(
                        node.label or "无障碍电梯", "elevator", node.coordinates,
                        origin, heading_deg, None,
                    )
                )
            else:
                placed.append(
                    _element(
                        node.label or "楼梯口", "stairs", node.coordinates,
                        origin, heading_deg, "楼梯，视障不建议通行",
                    )
                )

    for risk in floor.risks:
        placed.append(
            _element(
                _risk_name(risk), "hazard", risk.coordinates, origin, heading_deg,
                _RISK_NOTES.get(risk.risk_type, "风险点，注意避让"),
            )
        )
    for _column_id, point in floor.columns:
        placed.append(
            _element("独立柱子", "pillar", point, origin, heading_deg, "注意绕行")
        )
    for ramp in floor.ramps:
        point = ramp.get("coordinates") or _centroid_of(ramp.get("geometry") or {})
        if point is None:
            continue
        placed.append(
            _element("坡道", "ramp", (point[0], point[1]), origin, heading_deg, "坡度通道，注意脚下")
        )

    nearby = sorted(
        (e for e in placed if e.distance_meters <= NEARBY_RADIUS_M),
        key=lambda e: e.distance_meters,
    )[:NEARBY_MAX_ELEMENTS]

    tactile = _on_tactile_paving(floor, index.defaults, x, y)
    return UserSpatialContext(
        floor=floor_id,
        current_region_type=region_type,  # type: ignore[arg-type]
        current_region_name=region_name,
        motion_state=motion_state,
        on_tactile_paving=tactile,
        floor_surface=_floor_surface(floor, index.defaults, x, y),
        nearby_elements=nearby,
    )


def _on_tactile_paving(floor: FloorSpatial, defaults: dict, x: float, y: float) -> bool:
    if defaults.get("tactilePaving") == "none":
        return False
    for path in floor.tactile_paths:
        if _distance_to_feature(path, x, y) <= TACTILE_HIT_RADIUS_M:
            return True
    return False


def _floor_surface(floor: FloorSpatial, defaults: dict, x: float, y: float) -> str | None:
    for change in floor.material_changes:
        if change.get("geometry") and _point_in_geometry(x, y, change["geometry"]):
            return change.get("label")
    material = defaults.get("groundMaterial")
    return {"normal": "普通地面"}.get(material, material)


def _distance_to_feature(feature: dict, x: float, y: float) -> float:
    geometry = feature.get("geometry") or {}
    rings = _rings_of(geometry)
    best = float("inf")
    for ring in rings:
        for i in range(len(ring) - 1):
            best = min(best, _point_segment_distance(x, y, ring[i], ring[i + 1]))
    if geometry.get("type") == "LineString":
        coords = geometry.get("coordinates") or []
        for i in range(len(coords) - 1):
            best = min(best, _point_segment_distance(x, y, coords[i], coords[i + 1]))
    return best


def _point_segment_distance(
    px: float, py: float, a: Iterable[float], b: Iterable[float]
) -> float:
    ax, ay = float(a[0]), float(a[1])
    bx, by = float(b[0]), float(b[1])
    dx, dy = bx - ax, by - ay
    length2 = dx * dx + dy * dy
    if length2 < 1e-12:
        return math.hypot(px - ax, py - ay)
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / length2))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))
