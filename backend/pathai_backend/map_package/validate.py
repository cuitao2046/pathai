# -*- coding: utf-8 -*-
"""地图包语义校验器（REQ-MAP：发布前校验通过方可发布）。

校验分两层：
- schema 校验：MapPackage.model_validate（结构/枚举/范围）；
- 语义校验：validate_map_package —— 引用完整性、ID 唯一性、拓扑连通、
  盲模式跨层可达（电梯存在）、地点/信标归属。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from pathai_backend.map_package.schema import MapPackage


@dataclass
class _Report:
    problems: list[str] = field(default_factory=list)

    def add(self, message: str) -> None:
        self.problems.append(message)


def validate_map_package(pkg: MapPackage) -> tuple[str, ...]:
    """返回问题列表；空 tuple 表示通过。"""
    report = _Report()
    node_ids: dict[str, str] = {}   # node id -> floorId
    edge_ids: set[str] = set()
    for floor_id, floor in pkg.floors.items():
        for node in floor.topology.nodes:
            if node.id in node_ids:
                report.add(f"节点 ID 重复：{node.id}")
            node_ids[node.id] = floor_id
        for edge in floor.topology.edges:
            if edge.id in edge_ids:
                report.add(f"边 ID 重复：{edge.id}")
            edge_ids.add(edge.id)
    for edge in pkg.crossFloorEdges:
        if edge.id in edge_ids:
            report.add(f"跨层边 ID 重复：{edge.id}")
        edge_ids.add(edge.id)

    _check_references(pkg, node_ids, report)
    _check_connectivity(pkg, node_ids, report)
    _check_blind_cross_floor(pkg, report)
    _check_places(pkg, node_ids, report)
    _check_beacons(pkg, report)
    return tuple(report.problems)


def _check_references(pkg: MapPackage, node_ids: dict[str, str], report: _Report) -> None:
    for floor_id, floor in pkg.floors.items():
        for edge in floor.topology.edges:
            for endpoint in (edge.from_node, edge.to):
                if endpoint not in node_ids:
                    report.add(f"边 {edge.id} 引用不存在的节点：{endpoint}")
                elif node_ids[endpoint] != floor_id:
                    report.add(f"边 {edge.id} 跨楼层引用同层边：{endpoint}")
    for edge in pkg.crossFloorEdges:
        for endpoint in (edge.from_node, edge.to):
            if endpoint not in node_ids:
                report.add(f"跨层边 {edge.id} 引用不存在的节点：{endpoint}")
        for fid in (edge.fromFloor, edge.toFloor):
            if fid not in pkg.floors:
                report.add(f"跨层边 {edge.id} 引用不存在的楼层：{fid}")
        if edge.fromFloor == edge.toFloor:
            report.add(f"跨层边 {edge.id} 起终楼层相同：{edge.fromFloor}")


def _check_connectivity(pkg: MapPackage, node_ids: dict[str, str], report: _Report) -> None:
    adjacency: dict[str, set[str]] = {nid: set() for nid in node_ids}
    for floor in pkg.floors.values():
        for edge in floor.topology.edges:
            # setdefault：边引用未知节点时（_check_references 已报）仍可继续连通分析
            adjacency.setdefault(edge.from_node, set()).add(edge.to)
            adjacency.setdefault(edge.to, set()).add(edge.from_node)
    for edge in pkg.crossFloorEdges:
        adjacency.setdefault(edge.from_node, set()).add(edge.to)
        adjacency.setdefault(edge.to, set()).add(edge.from_node)

    isolated = sorted(nid for nid, nb in adjacency.items() if not nb)
    if isolated:
        report.add(f"存在孤立节点（无边连接）：{', '.join(isolated)}")

    # 全图连通性：跨层边参与连通判定，任意两节点必须互相可达
    start = next(iter(adjacency))
    seen = {start}
    stack = [start]
    while stack:
        u = stack.pop()
        for v in adjacency[u]:
            if v not in seen:
                seen.add(v)
                stack.append(v)
    if len(seen) != len(adjacency):
        unreachable = sorted(set(adjacency) - seen)
        report.add(f"拓扑不连通，不可达节点：{', '.join(unreachable)}")


def _check_blind_cross_floor(pkg: MapPackage, report: _Report) -> None:
    """盲模式跨层只能走电梯：多层场所必须存在电梯跨层边。"""
    if len(pkg.floors) < 2:
        return
    elevator_edges = [e for e in pkg.crossFloorEdges if e.type == "elevator"]
    if not elevator_edges:
        report.add("多层场所但无电梯跨层边，盲模式无法跨层导航")


def _check_places(pkg: MapPackage, node_ids: dict[str, str], report: _Report) -> None:
    place_ids: set[str] = set()
    for place in pkg.places:
        if place.placeId in place_ids:
            report.add(f"地点 ID 重复：{place.placeId}")
        place_ids.add(place.placeId)
        if place.floorId not in pkg.floors:
            report.add(f"地点 {place.placeId} 引用不存在的楼层：{place.floorId}")
        if place.nodeId not in node_ids:
            report.add(f"地点 {place.placeId} 引用不存在的节点：{place.nodeId}")
        elif node_ids[place.nodeId] != place.floorId:
            report.add(
                f"地点 {place.placeId} 楼层 {place.floorId} 与节点 {place.nodeId}"
                f" 所在楼层 {node_ids[place.nodeId]} 不一致"
            )


def _check_beacons(pkg: MapPackage, report: _Report) -> None:
    beacon_ids: set[str] = set()
    ibeacon_keys: dict[tuple[str, int, int], str] = {}  # (uuid,major,minor) -> beaconId
    for beacon in pkg.beacons:
        if beacon.beaconId in beacon_ids:
            report.add(f"信标 ID 重复：{beacon.beaconId}")
        beacon_ids.add(beacon.beaconId)
        if beacon.floorId not in pkg.floors:
            report.add(f"信标 {beacon.beaconId} 引用不存在的楼层：{beacon.floorId}")
        if beacon.uuid is not None:
            key = (beacon.uuid, beacon.major, beacon.minor)
            if key in ibeacon_keys:
                report.add(
                    f"iBeacon 三元组重复：{beacon.beaconId} 与 {ibeacon_keys[key]}"
                    f"（uuid={beacon.uuid}, major={beacon.major}, minor={beacon.minor}）"
                )
            else:
                ibeacon_keys[key] = beacon.beaconId
