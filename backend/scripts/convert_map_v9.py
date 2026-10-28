# -*- coding: utf-8 -*-
"""将 result/ 下的 v9 完整地图 GeoJSON 转换为后端地图包，并合入信标部署清单。

数据来源（2026-10 真实教学楼测绘产物，legacy 管线生成）：
- school_building_01_map_v9.geojson：两层拓扑（429/层节点、81/层房间）、墙体、语义；
- ble_deployment.json：61 个 iBeacon 实际部署清单（uuid/major/minor/txPower/安装高度）。

转换规则（以 backend/pathai_backend/map_package/schema.py 为契约）：
- 门类型 elevator→opening、设施类型 accessible_entrance→entrance（枚举差异映射）；
- crossFloorEdges 的 fromFloor/toFloor 由 int 归一化为字符串楼层键；
- 地点目录只收录「有拓扑房间节点」且可进入的语义房间（infrastructure/
  corridor/staircase 等通行/设施空间不作为目的地）；
- 楼层补 displayName/level（v9 无此元数据）；
- 信标坐标与 v9 同一局部米制坐标系（origin 2019.1,1154.8pt × 0.0529）。

用法（仓库根目录）：
    python backend/scripts/convert_map_v9.py [--version 2026-10-25.1] [--verify-only]
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_DIR.parent
DEFAULT_GEOJSON = REPO_ROOT / "result" / "school_building_01_map_v9.geojson"
DEFAULT_BEACONS = REPO_ROOT / "result" / "ble_deployment.json"
OUT_DIR = BACKEND_DIR / "maps_data" / "school-building-01"

SITE_ID = "school-building-01"
CN_FLOOR = {1: "一楼", 2: "二楼", 3: "三楼", 4: "四楼", 5: "五楼", 6: "六楼"}

#: 不作为导航目的地的空间类型（通行/垂直交通/设备）
NON_DESTINATION_ROOM_TYPES = {
    "infrastructure",
    "corridor",
    "staircase",
    "stair_lobby",
    "elevator_lobby",
}

#: v9 与后端 schema 的枚举差异映射
DOOR_TYPE_MAP = {"elevator": "opening"}  # 电梯门按无门扇洞口处理
FACILITY_TYPE_MAP = {"accessible_entrance": "entrance"}


def convert_node(node: dict) -> dict:
    out = dict(node)
    if out.get("doorType") in DOOR_TYPE_MAP:
        out["doorType"] = DOOR_TYPE_MAP[out["doorType"]]
    if out.get("facilityType") in FACILITY_TYPE_MAP:
        out["facilityType"] = FACILITY_TYPE_MAP[out["facilityType"]]
    return out


def convert_cross_floor_edge(edge: dict) -> dict:
    out = dict(edge)
    out["fromFloor"] = str(out["fromFloor"])
    out["toFloor"] = str(out["toFloor"])
    return out


def build_places(floor_id: str, rooms: list[dict], nodes: list[dict]) -> list[dict]:
    """语义房间 → 地点目录：仅收录有房间节点且可进入的目的地。"""
    room_node_by_id = {
        n["roomId"]: n
        for n in nodes
        if n.get("type") == "room" and n.get("roomId")
        or n.get("promotedFromRoom") and n.get("roomId")  # 穿堂房间保留目的地资格
    }
    places = []
    for room in rooms:
        if room["type"] in NON_DESTINATION_ROOM_TYPES:
            continue
        if not room.get("accessible", False):
            continue
        node = room_node_by_id.get(room["id"])
        if node is None:
            continue  # 拓扑简化掉的房间（CR/LB/AC 编号）无法导航
        places.append(
            {
                "placeId": room["id"],
                "name": room["label"],
                "spokenName": room["label"],
                "aliases": [],
                "type": room["type"],
                "floorId": floor_id,
                "nodeId": node["id"],
                "accessible": bool(room.get("accessible", True)),
            }
        )
    return places


def convert_beacons(entries: list[dict]) -> list[dict]:
    beacons = []
    for b in entries:
        beacons.append(
            {
                "beaconId": b["beaconId"],
                "floorId": str(b["floor"]),
                "x": float(b["coordinates"][0]),
                "y": float(b["coordinates"][1]),
                "txPower": float(b["txPower"]),
                "installHeightM": float(b["installHeight"]),
                "uuid": b["uuid"],
                "major": int(b["major"]),
                "minor": int(b["minor"]),
                # 部署元数据随包携带（schema 容忍 extra）
                "locationDesc": b.get("locationDesc"),
                "mountType": b.get("mountType"),
                "semanticTag": b.get("semanticTag"),
            }
        )
    return beacons


def prune_unreachable(
    floors: dict[str, dict], cross_floor: list[dict]
) -> tuple[dict[str, dict], list[dict], list[str]]:
    """保留跨层全图的最大连通分量，剔除孤立/不可达节点及其边（v9 遗留数据缺陷）。

    返回（过滤后的 floors, 过滤后的跨层边, 被剔除的节点 id）。
    """
    adjacency: dict[str, list[str]] = {}

    def link(a: str, b: str) -> None:
        adjacency.setdefault(a, []).append(b)
        adjacency.setdefault(b, []).append(a)

    for floor in floors.values():
        for edge in floor["topology"]["edges"]:
            link(edge["from"], edge["to"])
    for edge in cross_floor:
        link(edge["from"], edge["to"])

    # BFS 求最大连通分量
    visited: set[str] = set()
    largest: set[str] = set()
    all_nodes = list(adjacency)
    for start in all_nodes:
        if start in visited:
            continue
        component = {start}
        queue = [start]
        while queue:
            current = queue.pop()
            for neighbor in adjacency.get(current, []):
                if neighbor not in component:
                    component.add(neighbor)
                    queue.append(neighbor)
        visited |= component
        if len(component) > len(largest):
            largest = component

    removed = [nid for nid in all_nodes if nid not in largest]
    pruned_floors = {}
    for floor_id, floor in floors.items():
        pruned_floors[floor_id] = {
            **floor,
            "topology": {
                "nodes": [n for n in floor["topology"]["nodes"] if n["id"] in largest],
                "edges": [
                    e
                    for e in floor["topology"]["edges"]
                    if e["from"] in largest and e["to"] in largest
                ],
            },
        }
    pruned_cross = [e for e in cross_floor if e["from"] in largest and e["to"] in largest]
    return pruned_floors, pruned_cross, removed


def promote_articulation_rooms(
    floors: dict[str, dict], cross_floor: list[dict]
) -> list[str]:
    """把充当「必经穿堂」的房间节点提升为 intersection（保留房间语义字段）。

    v9 拓扑存在个别真实房间（如 F2 生物工作室）位于两翼走廊之间，是全图
    关节点：基线路由引擎规则 1（中间节点白名单）与规则 3（room→door→room
    禁穿透）都会拒绝穿房路径，导致另一翼不可达。导航语义上此类空间实为通道。
    """
    adjacency: dict[str, set[str]] = {}

    def link(a: str, b: str) -> None:
        adjacency.setdefault(a, set()).add(b)
        adjacency.setdefault(b, set()).add(a)

    all_nodes: dict[str, dict] = {}
    for floor in floors.values():
        for node in floor["topology"]["nodes"]:
            all_nodes[node["id"]] = node
            adjacency.setdefault(node["id"], set())
        for edge in floor["topology"]["edges"]:
            link(edge["from"], edge["to"])
    for edge in cross_floor:
        link(edge["from"], edge["to"])

    def component_size_without(blocked: str) -> int:
        start = next(nid for nid in adjacency if nid != blocked)
        seen = {start}
        queue = [start]
        while queue:
            current = queue.pop()
            for neighbor in adjacency.get(current, ()):
                if neighbor != blocked and neighbor not in seen:
                    seen.add(neighbor)
                    queue.append(neighbor)
        return len(seen)

    promoted: list[str] = []
    total = len(adjacency)
    for nid, node in all_nodes.items():
        if node.get("type") != "room":
            continue
        if component_size_without(nid) < total - 1:
            node["type"] = "intersection"
            node["promotedFromRoom"] = True  # 保留改造痕迹（extra）
            promoted.append(nid)
    return promoted


def build_package(geo: dict, beacon_entries: list[dict], map_version: str) -> tuple[dict, list[str]]:
    floors: dict[str, dict] = {}
    for floor_id, floor in geo["floors"].items():
        level = int(floor_id)
        nodes = [convert_node(n) for n in floor["topology"]["nodes"]]
        floors[floor_id] = {
            "displayName": CN_FLOOR.get(level, f"{level}楼"),
            "level": level,
            "topology": {"nodes": nodes, "edges": floor["topology"]["edges"]},
            "geometry": floor.get("geometry", {"walls": []}),
            "semantic": floor.get("semantic", {"rooms": []}),
        }

    cross_floor = [convert_cross_floor_edge(e) for e in geo.get("crossFloorEdges", [])]
    floors, cross_floor, removed = prune_unreachable(floors, cross_floor)
    promoted = promote_articulation_rooms(floors, cross_floor)

    places: list[dict] = []
    for floor_id, floor in floors.items():
        places.extend(build_places(floor_id, floor["semantic"]["rooms"], floor["topology"]["nodes"]))

    package = {
        "siteId": SITE_ID,
        "mapVersion": map_version,
        "publishedAt": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "venueName": geo.get("venueName"),
        "coordinateSystem": {
            "name": "local_meters",
            "unit": "m",
            "originDescription": (
                "场馆局部坐标，由 CAD 画布原点 "
                f"({geo['origin']['x']},{geo['origin']['y']})pt × 比例 {geo['scale']} 映射，"
                "x 东向 y 北向，单位米"
            ),
            "headingReference": "map_north",
        },
        # 保留 CAD 映射参数（extra），便于回溯坐标
        "cadOriginPt": geo.get("origin"),
        "cadScale": geo.get("scale"),
        "floors": floors,
        "crossFloorEdges": cross_floor,
        "places": places,
        "beacons": convert_beacons(beacon_entries),
    }
    return package, promoted


def _cross_dists(p1, p2, a, b):
    """线段 p1-p2 与墙线 a-b 是否真正相交；返回 (距 p1, 距 p2)，否则 None。"""
    dx, dy = b[0] - a[0], b[1] - a[1]
    ex, ey = p2[0] - p1[0], p2[1] - p1[1]
    s1 = dx * (p1[1] - a[1]) - dy * (p1[0] - a[0])
    s2 = dx * (p2[1] - a[1]) - dy * (p2[0] - a[0])
    if (s1 == 0 and s2 == 0) or s1 * s2 > 0:
        return None
    det = dx * ey - dy * ex
    if abs(det) < 1e-12:
        return None
    t = (dy * (p1[0] - a[0]) - dx * (p1[1] - a[1])) / det
    u = (ex * (a[1] - p1[1]) - ey * (a[0] - p1[0])) / det
    if not (-1e-9 <= u <= 1 + 1e-9) or not (0 <= t <= 1):
        return None
    seg_len = ((ex * ex + ey * ey) ** 0.5)
    return t * seg_len, (1 - t) * seg_len


def _pt_seg_dist(p, a, b):
    dx, dy = b[0] - a[0], b[1] - a[1]
    l2 = dx * dx + dy * dy
    if l2 < 1e-12:
        return ((p[0] - a[0]) ** 2 + (p[1] - a[1]) ** 2) ** 0.5
    t = ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / l2
    t = max(0.0, min(1.0, t))
    return ((p[0] - (a[0] + t * dx)) ** 2 + (p[1] - (a[1] + t * dy)) ** 2) ** 0.5


#: 公共空间边起点本体墙放行半径（门/出入口 1.5m、设施 2.5m、走廊 0.1m）
_START_RADIUS = {"doorway": 1.5, "facility_entrance": 1.5, "facility": 2.5,
                 "intersection": 0.1}


def verify_no_public_wall_crossing(pkg_dict: dict) -> None:
    """发布门槛（spec v0.2.6 / 决策 8）：公共空间拓扑边穿墙数必须为 0。

    公共空间边 = TI↔TI、TD↔TI、TEN↔TD/TI；TR↔TD 与 TF↔TD 为语义边不计。
    """
    for p in REPO_ROOT, BACKEND_DIR:
        if str(p) not in sys.path:
            sys.path.insert(0, str(p))
    from src.topology.route_rules import RouteGraph  # noqa: E402

    graph = RouteGraph(pkg_dict)
    public_types = {"intersection", "doorway", "facility_entrance"}
    # 穿堂房间提升为 intersection 后仍是"房间质心↔门"语义边，不计公共空间边
    promoted = {
        node["id"]
        for floor in (pkg_dict.get("floors") or {}).values()
        for node in (floor.get("topology") or {}).get("nodes") or []
        if node.get("promotedFromRoom")
    }
    bad = []
    for edge in graph.edges:
        a = graph.nodes.get(edge["from"])
        b = graph.nodes.get(edge["to"])
        if not a or not b or edge.get("crossFloor") or a["floor"] != b["floor"]:
            continue
        if edge["from"] in promoted or edge["to"] in promoted:
            continue
        types = {a["type"], b["type"]}
        if not types <= public_types:
            continue
        if types == {"doorway", "doorway"} or types == {"facility_entrance",
                                                        "facility_entrance"}:
            continue
        if not a["coords"] or not b["coords"]:
            continue
        radius = _START_RADIUS.get(a["type"], 0.1)
        minx = min(a["coords"][0], b["coords"][0])
        maxx = max(a["coords"][0], b["coords"][0])
        miny = min(a["coords"][1], b["coords"][1])
        maxy = max(a["coords"][1], b["coords"][1])
        for wl, wb, wf in zip(graph.wall_lines, graph.wall_bounds, graph.wall_floors):
            if wf != a["floor"]:
                continue
            if wb[0] > maxx or wb[2] < minx or wb[1] > maxy or wb[3] < miny:
                continue
            wa, wb_ = wl.coords[0], wl.coords[-1]
            hit = _cross_dists(a["coords"], b["coords"], wa, wb_)
            if not hit:
                continue
            if hit[0] <= radius or hit[1] <= 0.2:
                continue
            if _pt_seg_dist(a["coords"], wa, wb_) <= radius:
                continue
            bad.append((a["floor"], edge["id"], edge["from"], edge["to"],
                        round(edge.get("distance") or 0.0, 2)))
            break
    assert not bad, (
        f"公共空间拓扑边穿墙 {len(bad)} 条（必须为 0）：{bad[:5]}；"
        "请先运行 src/tools/fix_topology_walls.py 整改源 GeoJSON")


def verify(pkg_dict: dict) -> None:
    """用后端加载器与导航服务做发布前冒烟：schema 校验、地点节点存在、规划可达。"""
    for p in REPO_ROOT, BACKEND_DIR:
        if str(p) not in sys.path:
            sys.path.insert(0, str(p))

    from pathai_backend.map_package import load_map_package  # noqa: E402
    from pathai_backend.nav_service import PackageIndex, plan_route  # noqa: E402
    from pathai_backend.schemas.common import UserMode  # noqa: E402

    tmp = OUT_DIR / "_verify_tmp.json"
    tmp.write_text(json.dumps(pkg_dict, ensure_ascii=False), encoding="utf-8")
    try:
        pkg, _ = load_map_package(tmp)
    finally:
        tmp.unlink(missing_ok=True)

    index = PackageIndex(pkg)
    missing = [p.placeId for p in pkg.places if p.nodeId not in index.nodes]
    assert not missing, f"地点节点缺失：{missing[:5]}"
    assert len(pkg.beacons) > 0, "信标目录为空"

    # 路线规划冒烟：从一楼电梯设施节点出发（楼梯 blind 不可通行，不能做起点）
    elevator_id = next(
        node.id
        for node in pkg.floors["1"].topology.nodes
        if node.type in ("facility", "facility_entrance")
        and node.facilityType == "elevator"
    )
    origin = index.nodes[elevator_id]

    # 盲模式数据可达集：只按盲模式边约束（edge_allowed）做 BFS。
    # v9 部分区域（如 F2 东翼）仅经 staircase 平台（accessibilityLevel=999）
    # 连通，源数据本身判定盲不可达；此类目的地允许返回
    # ACCESSIBLE_ROUTE_NOT_FOUND，不视为转换失败。
    from src.topology.route_rules import RouteGraph  # noqa: E402

    graph = RouteGraph(pkg_dict)
    blind_adj: dict[str, list[str]] = {}
    for edge in graph.edges:
        if not graph.edge_allowed(edge, "blind"):
            continue
        blind_adj.setdefault(edge["from"], []).append(edge["to"])
        blind_adj.setdefault(edge["to"], []).append(edge["from"])
    blind_reachable = {elevator_id}
    stack = [elevator_id]
    while stack:
        current = stack.pop()
        for neighbor in blind_adj.get(current, ()):
            if neighbor not in blind_reachable:
                blind_reachable.add(neighbor)
                stack.append(neighbor)

    failures = []
    blind_inaccessible: list[str] = []
    for place in pkg.places:
        blind_ok_by_data = place.nodeId in blind_reachable
        for mode in (UserMode.NORMAL, UserMode.BLIND):
            try:
                route, _ = plan_route(pkg_dict, pkg, index, origin, place.placeId, mode)
                if not route.segments:
                    failures.append((place.placeId, mode.value, "空指令"))
            except Exception as err:  # noqa: BLE001
                if mode is UserMode.BLIND and not blind_ok_by_data:
                    blind_inaccessible.append(place.placeId)
                    continue
                failures.append((place.placeId, mode.value, repr(err)))
    assert not failures, f"路线规划失败 {len(failures)} 项，前 5：{failures[:5]}"
    if blind_inaccessible:
        print(
            f"提示：{len(blind_inaccessible)} 个地点在源数据中盲不可达"
            f"（楼梯平台 accessibilityLevel=999 隔断）：{sorted(set(blind_inaccessible))}"
        )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", default="2026-10-25.1", help="输出地图版本号")
    parser.add_argument("--verify-only", action="store_true", help="只校验不写文件")
    args = parser.parse_args()

    geo = json.loads(DEFAULT_GEOJSON.read_text(encoding="utf-8"))
    beacon_doc = json.loads(DEFAULT_BEACONS.read_text(encoding="utf-8"))
    pkg, promoted = build_package(geo, beacon_doc["beacons"], args.version)

    removed_total = sum(
        len(floor["topology"]["nodes"]) for floor in geo["floors"].values()
    ) - sum(len(f["topology"]["nodes"]) for f in pkg["floors"].values())
    print(
        f"转换完成：{len(pkg['floors'])} 层，"
        f"{sum(len(f['topology']['nodes']) for f in pkg['floors'].values())} 节点"
        f"（剔除不可达 {removed_total}，穿堂房间提升 {len(promoted)}：{promoted}），"
        f"{len(pkg['places'])} 地点，{len(pkg['beacons'])} 信标，"
        f"{len(pkg['crossFloorEdges'])} 跨层边"
    )

    verify(pkg)
    verify_no_public_wall_crossing(pkg)
    print("发布前校验通过：schema / 地点节点 / 双模式全目的地路线规划 / 公共空间边穿墙=0")

    if args.verify_only:
        return
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = OUT_DIR / f"{args.version}.json"
    out_path.write_text(json.dumps(pkg, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"已写入 {out_path}")


if __name__ == "__main__":
    main()
