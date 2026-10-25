# -*- coding: utf-8 -*-
"""不可变地图包 schema（REQ-MAP）。

地图包是 `GET /sites/{siteId}/map` 中 dataUrl 指向的下载内容，冻结结构：
- 顶层为 siteId/mapVersion/publishedAt/coordinateSystem/floors/crossFloorEdges；
- floors 为 {floorId: {topology:{nodes,edges}, geometry:{walls}, semantic:{rooms}}}；
- 另含面向客户端的 places（地点/地标目录）与 beacons（信标映射）。
结构上兼容 src/topology/route_rules.RouteGraph 的输入（节点 id/from/to/distance、
墙体 LineString 等），保证路由引擎可直接消费同一发布物。
"""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import ConfigDict, Field

from pathai_backend.schemas.common import StrictModel

#: 拓扑节点类型（docs/05 路线规划算法：受限 Dijkstra 图模型）
NodeType = Literal["intersection", "doorway", "room", "facility", "facility_entrance"]
DoorType = Literal["swing", "fire", "opening"]
FacilityType = Literal["staircase", "elevator", "entrance"]
CrossFloorType = Literal["staircase", "elevator"]


class _AllowExtraModel(StrictModel):
    """地图包内部结构：冻结关键字段，但容忍附带字段（sourceDoorIds 等）。"""

    model_config = ConfigDict(extra="allow")


class PointXY(_AllowExtraModel):
    x: float
    y: float


class LineStringGeometry(_AllowExtraModel):
    type: Literal["LineString"]
    coordinates: list[list[float]] = Field(min_length=2)


class WallFeature(_AllowExtraModel):
    geometry: LineStringGeometry
    properties: dict = Field(default_factory=dict)


class TopologyNode(_AllowExtraModel):
    id: str = Field(min_length=1)
    type: NodeType
    coordinates: list[float] = Field(min_length=2, max_length=2)
    label: str | None = None
    roomType: str | None = None
    roomId: str | None = None
    doorType: DoorType | None = None
    facilityType: FacilityType | None = None
    rooms: list[str] = Field(default_factory=list)
    accessible: bool | None = None
    blindAccessible: bool | None = None
    wheelchairAccessible: bool | None = None
    riskLevel: float | None = None
    isNormallyOpen: bool | None = None


class TopologyEdge(_AllowExtraModel):
    # 注意："from" 是 Python 保留字，模型用 from_node 承载、alias 映射原字段名。
    # 本模块的 Pydantic 模型仅作为校验视图；发布物与路由引擎（RouteGraph）
    # 始终使用原始 JSON dict（from/to 原字段名）。
    model_config = ConfigDict(extra="allow", populate_by_name=True)

    id: str = Field(min_length=1)
    from_node: str = Field(alias="from", min_length=1)
    to: str = Field(min_length=1)
    distance: float = Field(ge=0)
    accessibilityLevel: int = 0
    walkable: bool = True
    wheelchairAccessible: bool = True
    blindAccessible: bool = True


class CrossFloorEdge(_AllowExtraModel):
    model_config = ConfigDict(extra="allow", populate_by_name=True)

    id: str = Field(min_length=1)
    from_node: str = Field(alias="from", min_length=1)
    to: str = Field(min_length=1)
    fromFloor: str = Field(min_length=1)
    toFloor: str = Field(min_length=1)
    type: CrossFloorType
    distance: float = Field(ge=0)
    accessibilityLevel: int = 0
    walkable: bool = True
    wheelchairAccessible: bool = True
    blindAccessible: bool = True


class FloorTopology(_AllowExtraModel):
    nodes: list[TopologyNode] = Field(min_length=1)
    edges: list[TopologyEdge] = Field(default_factory=list)


class FloorGeometry(_AllowExtraModel):
    walls: list[WallFeature] = Field(default_factory=list)


class SemanticRoom(_AllowExtraModel):
    id: str = Field(min_length=1)
    type: str = Field(min_length=1)
    label: str = Field(min_length=1)
    centroid: list[float] = Field(min_length=2, max_length=2)
    floor: int
    public: bool = False
    accessible: bool = True


class FloorSemantic(_AllowExtraModel):
    rooms: list[SemanticRoom] = Field(default_factory=list)


class FloorData(_AllowExtraModel):
    topology: FloorTopology
    geometry: FloorGeometry = Field(default_factory=FloorGeometry)
    semantic: FloorSemantic = Field(default_factory=FloorSemantic)


class MapCoordinateSystem(_AllowExtraModel):
    name: Literal["local_meters"]
    unit: Literal["m"] = "m"
    originDescription: str = Field(min_length=1)
    headingReference: str = Field(min_length=1)


class PlaceEntry(_AllowExtraModel):
    """地图包地点目录条目：landmark/目的地的唯一合法来源（REQ-MAP）。"""

    placeId: str = Field(min_length=1)
    name: str = Field(min_length=1)
    spokenName: str = Field(min_length=1)
    aliases: list[str] = Field(default_factory=list)
    type: str = Field(min_length=1)
    floorId: str = Field(min_length=1)
    nodeId: str = Field(min_length=1)
    accessible: bool = True


class BeaconEntry(_AllowExtraModel):
    """信标映射：定位解算只允许使用本目录内信标（REQ-LOCATION）。"""

    beaconId: str = Field(min_length=1)
    floorId: str = Field(min_length=1)
    x: float
    y: float
    txPower: float | None = None
    installHeightM: float | None = Field(default=None, gt=0)


class MapPackage(_AllowExtraModel):
    siteId: str = Field(min_length=1)
    mapVersion: str = Field(min_length=1)
    publishedAt: datetime
    coordinateSystem: MapCoordinateSystem
    floors: dict[str, FloorData] = Field(min_length=1)
    crossFloorEdges: list[CrossFloorEdge] = Field(default_factory=list)
    places: list[PlaceEntry] = Field(default_factory=list)
    beacons: list[BeaconEntry] = Field(default_factory=list)
