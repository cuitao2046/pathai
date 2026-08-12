# -*- coding: utf-8 -*-
"""
骨架管线总装：Walkable → 中轴 → 剪枝 → 矢量化 → Junction/门投影 → 拓扑节点边。

供 parse_cad_pdf / topology 调用；输出与现有 GeoJSON topology 字段兼容。
"""

from __future__ import annotations

import math
from typing import Dict, List, Optional, Sequence, Tuple

import networkx as nx
from shapely.geometry import LineString, Point, Polygon, mapping, shape
from shapely.ops import unary_union

from .medial_axis import (
    DEFAULT_RESOLUTION,
    extract_medial_axis,
    prune_dangling_branches,
)
from .skeleton_vectorize import (
    graph_to_linestrings,
    nearest_graph_node,
    skeleton_to_graph,
)
from .junction_detector import (
    detect_junctions,
    simplify_degree2_paths,
    collapse_short_edges,
)
from .door_projector import project_doors_to_skeleton, project_points_to_skeleton
try:
    from src.topology import bridge_disconnected_components
except ImportError:
    bridge_disconnected_components = None  # type: ignore

# 全局常量统一来源（见 docs/code-review-2026-08-12.md D1-D4）
from src.common.constants import BLIND_WALK_SPEED, SCALE


def _merge_nearby_points(pts, radius_m=1.0):
    """贪心合并：距离 < radius 的点收成簇，取均值坐标。

    用于压缩栅格骨架上过密的 degree≥3 微交叉口，降低 TI 数量。
    """
    if not pts:
        return []
    used = [False] * len(pts)
    out = []
    r2 = radius_m * radius_m
    for i, (x, y) in enumerate(pts):
        if used[i]:
            continue
        sx, sy, n = float(x), float(y), 1
        used[i] = True
        for j in range(i + 1, len(pts)):
            if used[j]:
                continue
            dx = pts[j][0] - x
            dy = pts[j][1] - y
            if dx * dx + dy * dy <= r2:
                used[j] = True
                sx += pts[j][0]
                sy += pts[j][1]
                n += 1
        out.append((sx / n, sy / n))
    return out



def _contract_nearby_graph_nodes(G: nx.Graph, radius_m: float = 1.0) -> nx.Graph:
    """把距离 < radius 的 key 节点（degree≠2）收缩成超点，边权累加。

    比只合并坐标更安全：收缩后图连通性保持，TI 可与图节点 1:1 对齐。
    """
    if G.number_of_nodes() == 0:
        return G
    deg = dict(G.degree())
    keys = [n for n, d in deg.items() if d != 2]
    if len(keys) <= 1:
        return G

    # 并查集合并近邻 key 节点
    parent = {n: n for n in keys}

    def find(a):
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    r2 = radius_m * radius_m
    for i, a in enumerate(keys):
        ax, ay = G.nodes[a]["x"], G.nodes[a]["y"]
        for b in keys[i + 1:]:
            bx, by = G.nodes[b]["x"], G.nodes[b]["y"]
            if (ax - bx) ** 2 + (ay - by) ** 2 <= r2:
                union(a, b)

    # 代表点：簇内坐标均值；映射 old → rep
    clusters = {}
    for n in keys:
        r = find(n)
        clusters.setdefault(r, []).append(n)

    rep_of = {}
    H = nx.Graph()
    for members in clusters.values():
        sx = sum(G.nodes[m]["x"] for m in members) / len(members)
        sy = sum(G.nodes[m]["y"] for m in members) / len(members)
        rep = members[0]
        H.add_node(rep, x=sx, y=sy, row=G.nodes[rep].get("row", 0),
                   col=G.nodes[rep].get("col", 0))
        for m in members:
            rep_of[m] = rep

    # degree=2 中间点：映射到自身（简化图通常已无 degree=2，但兼容）
    for n in G.nodes():
        if n not in rep_of:
            rep_of[n] = n
            if n not in H:
                H.add_node(n, **G.nodes[n])

    for a, b, data in G.edges(data=True):
        ra, rb = rep_of[a], rep_of[b]
        if ra == rb:
            continue
        length = float(data.get("length") or 0.0)
        if H.has_edge(ra, rb):
            if length < H.edges[ra, rb].get("length", float("inf")):
                H.edges[ra, rb]["length"] = length
        else:
            H.add_edge(ra, rb, length=length)
    return H


def _obj_id(floor, abbr, seq):
    return f"F{floor}-{abbr}-{seq:04d}"


# 拓扑层合并聚集交叉口(TI)节点的距离阈值。
# 手绘骨架/栅格中轴在 doorway 附近、走廊转折处常产生 0.5~1.5m 的冗余微节点，
# 合并后可显著减少无意义拓扑节点，同时不影响真实长走廊交叉口。
TI_MERGE_RADIUS_M = 1.5


def _merge_nearby_ti_nodes(nodes: list, edges: list,
                           radius_m: float = TI_MERGE_RADIUS_M) -> tuple:
    """合并距离 < radius_m 的 intersection(TI) 拓扑节点。

    使用并查集按欧氏距离聚类；每个簇以 id 最小节点为代表，坐标取质心；
    所有与这些 TI 相连的边改接到代表节点，去除自环与重复边，并按新坐标
    重新计算距离/预估时间。返回 (new_nodes, new_edges, merge_cluster_count)。
    """
    ti_nodes = [n for n in nodes if n.get("type") == "intersection"]
    if len(ti_nodes) <= 1:
        return nodes, edges, 0

    parent = {n["id"]: n["id"] for n in ti_nodes}

    def find(a):
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    r2 = radius_m * radius_m
    for i in range(len(ti_nodes)):
        ax, ay = ti_nodes[i]["coordinates"]
        for j in range(i + 1, len(ti_nodes)):
            bx, by = ti_nodes[j]["coordinates"]
            if (ax - bx) ** 2 + (ay - by) ** 2 <= r2:
                union(ti_nodes[i]["id"], ti_nodes[j]["id"])

    clusters: Dict[str, List[dict]] = {}
    for n in ti_nodes:
        r = find(n["id"])
        clusters.setdefault(r, []).append(n)

    rep_of: Dict[str, str] = {}
    new_ti_nodes: List[dict] = []
    for rep_id, members in clusters.items():
        if len(members) == 1:
            new_ti_nodes.append(dict(members[0]))
            rep_of[members[0]["id"]] = members[0]["id"]
            continue
        mx = sum(m["coordinates"][0] for m in members) / len(members)
        my = sum(m["coordinates"][1] for m in members) / len(members)
        rep = min(members, key=lambda x: x["id"])
        new_n = dict(rep)
        new_n["coordinates"] = [round(mx, 3), round(my, 3)]
        new_ti_nodes.append(new_n)
        for m in members:
            rep_of[m["id"]] = rep["id"]

    # 重新编号 label，保持顺序
    new_ti_nodes.sort(key=lambda n: n["id"])
    for i, n in enumerate(new_ti_nodes):
        n["label"] = f"交叉口{i + 1}"

    non_ti = [dict(n) for n in nodes if n.get("type") != "intersection"]
    all_nodes = non_ti + new_ti_nodes
    coord_map = {n["id"]: n["coordinates"] for n in all_nodes}

    new_edges: List[dict] = []
    seen = set()

    def rewire(nid):
        return rep_of.get(nid, nid)

    for e in edges:
        a = rewire(e["from"])
        b = rewire(e["to"])
        if a == b:
            continue
        key = tuple(sorted((a, b)))
        if key in seen:
            continue
        seen.add(key)
        new_e = dict(e)
        new_e["from"] = a
        new_e["to"] = b
        ca, cb = coord_map[a], coord_map[b]
        new_dist = math.hypot(ca[0] - cb[0], ca[1] - cb[1])
        new_e["distance"] = round(float(new_dist), 2)
        new_e["estimatedTime"] = round(float(new_dist) / BLIND_WALK_SPEED, 1)
        new_edges.append(new_e)

    merge_count = sum(1 for c in clusters.values() if len(c) > 1)
    print(f"    [skeleton] 合并近邻 TI: 簇 {merge_count} 个, "
          f"TI {len(ti_nodes)} -> {len(new_ti_nodes)}")
    return all_nodes, new_edges, merge_count


def build_skeleton_for_walkables(
    walkable_polys_m: Sequence,  # list of Shapely polygons (meters)
    door_centers_m: Sequence[Tuple[float, float]],
    facility_centers_m: Optional[Sequence[Tuple[float, float]]] = None,
    resolution: float = DEFAULT_RESOLUTION,
) -> dict:
    """
    对一层所有 walkable 区域提取并合并骨架。

    性能：先把重叠/邻近的 walkable 合并，再逐块提取；单块自适应分辨率。
    """
    from shapely.ops import unary_union
    from shapely.geometry import MultiPolygon

    keep_pts = list(door_centers_m)
    if facility_centers_m:
        keep_pts.extend(facility_centers_m)

    # 过滤空几何，合并相交/贴近的碎片（减少中轴次数）
    valid = []
    for poly in walkable_polys_m:
        if poly is None or getattr(poly, "is_empty", True):
            continue
        if poly.area < 0.5:
            continue
        valid.append(poly)
    if not valid:
        return {
            "graph": nx.Graph(), "lines": [], "junctions": [],
            "terminals": [], "key_nodes": [], "raw_masks_meta": [], "empty": True,
        }

    try:
        # 软合并：先外扩 0.25m 填走廊缝隙，unary_union 后再内缩，
        # 把「几乎相连」的走道/门厅合成更少块（原 22→22 因缝隙未合并）。
        MERGE_GAP_M = 0.25
        buffered = [p.buffer(0).buffer(MERGE_GAP_M) for p in valid]
        merged = unary_union(buffered)
        if not merged.is_empty:
            merged = merged.buffer(-MERGE_GAP_M)
        if isinstance(merged, MultiPolygon):
            pieces = [g for g in merged.geoms if g.area >= 1.0]
        elif merged is None or merged.is_empty:
            pieces = valid
        else:
            pieces = [merged]
        # 若软合并后块数未减少且 valid 很多，退化为整层一次栅格
        # （单块可能很大，但只跑一次中轴，总时间通常更短）
        if len(pieces) >= max(8, len(valid) - 2) and len(valid) >= 8:
            try:
                whole = unary_union([p.buffer(0) for p in valid])
                if not whole.is_empty:
                    pieces = [whole]
            except Exception:
                pass
    except Exception:
        pieces = valid

    print(f"    [skeleton] walkable {len(valid)} → 合并后 {len(pieces)} 块, "
          f"base_res={resolution}m")

    all_graphs = []
    all_lines = []
    metas = []

    for wi, poly in enumerate(pieces):
        if poly is None or poly.is_empty:
            continue
        import time as _time
        t0 = _time.time()
        meta = extract_medial_axis(poly, resolution=resolution)
        metas.append(meta)
        if meta["empty"]:
            print(f"    [skeleton] 块{wi+1}/{len(pieces)} 空 "
                  f"(area={poly.area:.0f}m², {(_time.time()-t0)*1000:.0f}ms)")
            continue
        skel = prune_dangling_branches(
            meta["skeleton_mask"],
            keep_points_world=keep_pts,
            origin_x=meta["origin_x"],
            origin_y=meta["origin_y"],
            resolution=meta["resolution"],
        )
        G = skeleton_to_graph(
            skel, meta["origin_x"], meta["origin_y"], meta["resolution"]
        )
        if G.number_of_nodes() == 0:
            continue
        G2 = simplify_degree2_paths(G)
        # 收缩长度 <0.3m 的微段（相邻微交叉口对 / 微悬挂），降低短段比例，
        # 在简化图上操作不会误并长走廊。
        G2 = collapse_short_edges(G2, min_len_m=0.3)
        all_graphs.append(G2)
        all_lines.extend(graph_to_linestrings(G2, simplify_tol_m=0.12))
        print(f"    [skeleton] 块{wi+1}/{len(pieces)} area={poly.area:.0f}m² "
              f"res={meta['resolution']:.3f}m "
              f"nodes={G2.number_of_nodes()} "
              f"{(_time.time()-t0)*1000:.0f}ms")

    if not all_graphs:
        return {
            "graph": nx.Graph(),
            "lines": [],
            "junctions": [],
            "terminals": [],
            "key_nodes": [],
            "raw_masks_meta": metas,
            "empty": True,
        }

    # 合并多连通分量图
    Gm = nx.Graph()
    for g in all_graphs:
        # 节点 key 可能冲突（不同 walkable 的 row,col），加偏移前缀
        mapping_nodes = {}
        for n in g.nodes():
            new_n = (id(g), n) if isinstance(n, tuple) else (id(g), n)
            mapping_nodes[n] = new_n
            Gm.add_node(new_n, **g.nodes[n])
        for a, b, data in g.edges(data=True):
            Gm.add_edge(mapping_nodes[a], mapping_nodes[b], **data)

    # 在图上收缩邻近 key 节点（避免仅合并坐标导致 TI 与图边脱节）
    Gm = _contract_nearby_graph_nodes(Gm, radius_m=1.0)
    info = detect_junctions(Gm)
    junctions = [(x, y) for _, x, y in info["junctions"]]
    terminals = [(x, y) for _, x, y in info["terminals"]]
    # 附带：图节点坐标列表（用于 TI 与图严格对齐）
    key_nodes = [
        (n, data["x"], data["y"])
        for n, data in Gm.nodes(data=True)
        if Gm.degree(n) != 2
    ]

    return {
        "graph": Gm,
        "lines": all_lines,
        "junctions": junctions,
        "terminals": terminals,
        "key_nodes": key_nodes,  # (node_id, x, y)
        "raw_masks_meta": metas,
        "empty": False,
    }


def _merge_nearby_doors(doors: list, max_dist_m: float = 0.8,
                        coords: Optional[Sequence] = None) -> list:
    """合并坐标距 < max_dist_m 的门为单个 doorway 节点（同一开口的摆弧/防火/门洞）。

    DISABLED: 用户明确约定「同一物理开口只允许一扇门」，禁止任何形式门合并
    （见 docs/设计决策记录.md ADR-01 门不合并、docs/项目迭代日志.md 门不合并铁律）。
    本函数不再被 build_skeleton_topology 调用，保留仅为历史参考；
    恢复使用前必须征得用户确认。

    同一物理开口常被识别为多条门记录（swing + fire + opening），其几何中心重合；
    也可能在投影到骨架后落到同一骨架点（同一房间多个邻近入口）。合并后 rooms 取并集、
    kind 取 fire 优先、width 取最大，避免拓扑层出现重叠/重复的 TD 节点
    （这也是渲染时「两个重叠的拓扑节点」的根因）。

    coords: 可选，预先算好的合并依据坐标（如投影后的最终坐标）；为 None 时退回用
    door 的 center_m。返回合并后的门列表，次序按簇首排列。
    """
    if not doors:
        return []
    if coords is None:
        coords = [tuple(dr.get("center_m") or (0.0, 0.0)) for dr in doors]
    used = [False] * len(doors)
    merged = []
    for i in range(len(doors)):
        if used[i]:
            continue
        ci = tuple(coords[i])
        cluster = [i]
        used[i] = True
        for j in range(i + 1, len(doors)):
            if used[j]:
                continue
            cj = tuple(coords[j])
            if math.hypot(ci[0] - cj[0], ci[1] - cj[1]) < max_dist_m:
                cluster.append(j)
                used[j] = True
        rooms_u = []
        for j in cluster:
            for rid in (doors[j].get("rooms") or []):
                if rid not in rooms_u:
                    rooms_u.append(rid)
        kinds = [doors[j].get("kind", "swing") for j in cluster]
        kind = "fire" if "fire" in kinds else (kinds[0] if kinds else "swing")
        width = max((doors[j].get("width_pt") or 0) for j in cluster)
        md = dict(doors[cluster[0]])
        md["center_m"] = list(ci)
        md["kind"] = kind
        md["width_pt"] = width
        md["rooms"] = rooms_u
        merged.append(md)
    return merged


def build_skeleton_topology(
    floor_no: int,
    rooms: list,
    doors: list,
    stairs: list,
    elevators: list,
    walkable_by_room_id: Optional[Dict[str, object]] = None,
    extra_nodes: Optional[list] = None,
    resolution: float = DEFAULT_RESOLUTION,
    obj_type: Optional[dict] = None,
    manual_skeleton: Optional[dict] = None,
) -> dict:
    """T8 替代逻辑：基于骨架生成 topology nodes/edges。

    rooms: parse_cad_pdf 的 rooms（含 roomType, centroid_m, id, polygon 可选）
    doors: [{center_m, kind, width_pt, rooms}, ...]
    stairs/elevators: GeoJSON-like features with properties.centroid
    walkable_by_room_id: room_id → Shapely walkable poly (meters)
    manual_skeleton: 可选，按楼层从「手绘骨架 JSON」传入
        {"ti_nodes":[...], "edges":[...], "skeleton_features":[...]}。
        若提供，则 TI 节点 / TI-TI 边 / 骨架线 直接取自 JSON，**跳过中轴提取**；
        TR/TD/TF/TEN 节点及挂接边仍按下方统一逻辑生成（门/设施挂到手动 TI）。
        若省略，则走自动中轴骨架生成。
    """
    if obj_type is None:
        obj_type = {
            "topo_room": "TR", "topo_doorway": "TD", "topo_intersection": "TI",
            "topo_facility": "TF", "topo_entrance": "TEN", "topo_edge": "TE",
        }

    OPEN = {"corridor", "lobby", "activity", "atrium",
            "elevator_lobby", "stair_lobby"}
    PUBLIC = {"toilet", "staircase", "elevator_hall", "corridor", "lobby",
              "entrance", "accessible_entrance", "atrium"}
    NON_ACC = {"staircase", "infrastructure"}

    # --- 收集 walkable ---
    walkables = []
    if walkable_by_room_id:
        for rid, wp in walkable_by_room_id.items():
            if wp is not None and not getattr(wp, "is_empty", True):
                walkables.append(wp)
    else:
        for r in rooms:
            if r.get("roomType") in OPEN:
                wp = r.get("walkable_poly_m") or r.get("walkable_poly_pt")
                # 若仍是 pt 坐标，调用方应先转换；这里尽量兼容已是 m 的
                if wp is not None and not getattr(wp, "is_empty", True):
                    walkables.append(wp)

    # 门不做合并（用户明确约定）：每扇门独立成 TD，禁止 _merge_nearby_doors 合并。
    # 原合并会把 0.8m 内的多扇门并为单个 TD 且 rooms 取并集，
    # 导致归属混叠（如 F2-TD-0010 出现 ['F2-CR-0042','F2-RM-0005']）——已禁用。
    td_doors = list(doors)

    door_centers = []
    for dr in td_doors:
        c = dr.get("center_m")
        if c is not None:
            door_centers.append((float(c[0]), float(c[1])))

    fac_centers = []
    for s in stairs:
        c = s.get("properties", {}).get("centroid") or s.get("centroid_m")
        if c:
            fac_centers.append((float(c[0]), float(c[1])))
    for e in elevators:
        c = e.get("properties", {}).get("centroid") or e.get("centroid_m")
        if c:
            fac_centers.append((float(c[0]), float(c[1])))

    # 手动骨架覆盖：本层 JSON 存在时，TI 节点 / TI-TI 边 / 骨架线 直接取自 JSON，
    # 跳过中轴提取（省去整层 walkable 栅格化）；TR/TD/TF/TEN 节点及挂接边仍统一生成。
    manual_ti, manual_ti_edges, manual_skel_features = [], [], []
    if manual_skeleton is not None:
        manual_ti = manual_skeleton.get("ti_nodes") or []
        manual_ti_edges = manual_skeleton.get("edges") or []
        manual_skel_features = manual_skeleton.get("skeleton_features") or []
        sk = {"graph": nx.Graph(), "lines": [], "junctions": [],
              "terminals": [], "key_nodes": [], "empty": True}
    else:
        sk = build_skeleton_for_walkables(
            walkables, door_centers, fac_centers, resolution=resolution
        )

    nodes = []
    edges = []
    # 手动骨架优先时，TI-TI 边沿用 JSON 的 F{floor}-TE-xxxx 编号；
    # 所有后续 add_edge 生成的边必须从 max_manual_seq 之后续号，
    # 否则与手动边 id 重复（同层 TE 编号冲突 → 前端 doorType 注入错乱）。
    _manual_max_seq = 0
    if manual_skeleton is not None:
        try:
            for _e in (manual_skeleton.get("edges") or []):
                _tail = str(_e.get("id") or "").rsplit("-", 1)[-1]
                try:
                    _manual_max_seq = max(_manual_max_seq, int(_tail))
                except (ValueError, TypeError):
                    pass
        except Exception:
            pass
    edge_seq = [_manual_max_seq]

    def add_edge(frm, to, distance, a_level=0, r_level=0.5,
                 wheel=True, blind=True):
        edge_seq[0] += 1
        edges.append({
            "id": _obj_id(floor_no, obj_type["topo_edge"], edge_seq[0]),
            "from": frm,
            "to": to,
            "distance": round(float(distance), 2),
            "estimatedTime": round(float(distance) / BLIND_WALK_SPEED, 1),
            "accessibilityLevel": a_level,
            "riskLevel": r_level,
            "walkable": True,
            "wheelchairAccessible": wheel,
            "blindAccessible": blind,
        })

    # ---------- TR: 封闭房间 ----------
    room_index = {}
    room_seq = 0
    room_by_id = {r["id"]: r for r in rooms}
    for r in rooms:
        if r.get("roomType") in OPEN:
            continue
        room_seq += 1
        rnid = _obj_id(floor_no, obj_type["topo_room"], room_seq)
        room_index[r["id"]] = rnid
        nodes.append({
            "id": rnid,
            "type": "room",
            "roomType": r["roomType"],
            "roomId": r["id"],
            "label": r.get("label", ""),
            "coordinates": list(r["centroid_m"]),
            "public": r["roomType"] in PUBLIC,
            "accessible": r["roomType"] not in NON_ACC,
        })

    # ---------- TI: 与简化骨架 key 节点 1:1（保证 TI↔TI 邻接 = 图边） ----------
    G = sk["graph"]
    ti_ids = []
    ti_of_gn = {}  # graph node → TI id
    # 优先用收缩后的 key_nodes；否则用 junctions+terminals
    key_list = sk.get("key_nodes") or []
    skel_lines = []

    if manual_skeleton is not None:
        # 手动骨架：TI 节点原样取用（type/coordinates/属性均保留）；
        # 无图结构 → TD↔TI 与门投影改走欧氏最近分支。
        for n in manual_ti:
            ti_ids.append(n["id"])
            nodes.append(dict(n))
        G = nx.Graph()
        ti_of_gn = {}
        skel_lines = [shape(f["geometry"]) for f in manual_skel_features]
    else:
        if not key_list and G.number_of_nodes():
            key_list = [
                (n, G.nodes[n]["x"], G.nodes[n]["y"])
                for n in G.nodes() if G.degree(n) != 2
            ]
        if not key_list:
            # 兜底：junctions / terminals 坐标
            for i, (x, y) in enumerate(sk["junctions"] or sk["terminals"][:8]):
                nid = _obj_id(floor_no, obj_type["topo_intersection"], i + 1)
                ti_ids.append(nid)
                nodes.append({
                    "id": nid,
                    "type": "intersection",
                    "roomType": "corridor",
                    "label": f"交叉口{i + 1}",
                    "coordinates": [round(x, 3), round(y, 3)],
                })
        else:
            for i, (gn, x, y) in enumerate(key_list):
                nid = _obj_id(floor_no, obj_type["topo_intersection"], i + 1)
                ti_ids.append(nid)
                ti_of_gn[gn] = nid
                nodes.append({
                    "id": nid,
                    "type": "intersection",
                    "roomType": "corridor",
                    "label": f"交叉口{i + 1}",
                    "coordinates": [round(float(x), 3), round(float(y), 3)],
                })
        skel_lines = sk["lines"]

    # ---------- TD: 门节点严格 1:1 对应每扇 door（需求⑧） ----------
    # 说明：每扇 geometry.door（含纯走廊门/门洞）都生成一个专属 TD 节点，
    # TD id 序号与 door id 序号一致（F1-D-0018 → F1-TD-0018），不合并、不投影。
    # TD 坐标 = 门坐标（真实开口处）；房间↔门边按贴墙/标注归属生成；
    # 走廊连通性由 TI↔TI 承担，TD 连最近 TI 接入路网。
    # （多房间共享门 room↔door 直连可能穿墙——已知限制，后续单独处理）
    td_doors = list(td_doors)

    # 门贴墙补全归属（需求⑥：房间必须与「所有」swing/fire 门都有边）
    # 门 rooms 字段来自 CAD 标签归属，偶有缺漏（一扇门漏标某房间）。
    # 以门坐标贴房间墙(<0.6m)为准补全：物理上开在该房间墙上的门，必为该房间的门。
    # 仅补封闭房间（roomType 非开放/非卫生间），且仅对 swing/fire 门补全。
    _WALL_TOL = 0.6
    _room_polys = {}
    for r in rooms:
        poly_pts = r.get("coords_m") or r.get("polygon_m")
        if poly_pts:
            try:
                _room_polys[r["id"]] = Polygon(poly_pts)
            except Exception:
                pass
    if _room_polys:
        _closed_ids = {rid for rid, r in room_by_id.items()
                       if r.get("roomType") not in OPEN and r.get("roomType") != "toilet"}
        for dr in td_doors:
            if dr.get("kind") not in ("swing", "fire"):
                continue
            dc = dr.get("center_m")
            if not dc:
                continue
            dpt = Point(dc)
            owned = set(dr.get("rooms") or [])
            for rid in _closed_ids:
                if rid in owned or rid not in _room_polys:
                    continue
                if _room_polys[rid].boundary.distance(dpt) < _WALL_TOL:
                    owned.add(rid)
            if len(owned) > len(dr.get("rooms") or []):
                dr["rooms"] = sorted(owned)

    # 门洞(opening)重定向：封闭房间(非卫生间)若不以 swing/fire 作为出入口，
    # 将其仅有的 opening 提拔为普通门(swing)。这样既能满足「房间质心节点只连
    # 普通门/防火门」的规则，又不至于让房间因无 swing/fire 门而失联。
    room_type_by_id = {r["id"]: r.get("roomType") for r in rooms}
    _room_has_sf = set()
    for dr in td_doors:
        if dr.get("kind") in ("swing", "fire"):
            for rid in dr.get("rooms", []):
                _room_has_sf.add(rid)

    def _is_closed_room(rid):
        rt = room_type_by_id.get(rid)
        return rt is not None and rt not in OPEN and rt != "toilet"

    for dr in td_doors:
        if dr.get("kind") != "opening":
            continue
        rms = dr.get("rooms", [])
        if not rms:
            continue
        # 仅当所有归属房间都是非卫生间封闭房间、且该门是其唯一出入口时提拔
        if all(_is_closed_room(rid) for rid in rms) and \
           all(rid not in _room_has_sf for rid in rms):
            dr["kind"] = "swing"

    door_node_ids = []  # 与 td_doors 对齐（1:1，每扇门都建 TD）
    for i, dr in enumerate(td_doors):
        # TD id 序号 = door id 序号（F1-D-0018 → F1-TD-0018）
        door_id = dr.get("id") or ""
        seq = door_id.split("-")[-1] if "-" in door_id else str(i + 1)
        nid = f"F{floor_no}-TD-{seq}"
        door_node_ids.append(nid)
        kind = dr.get("kind", "swing")
        coords = list(dr.get("center_m") or [0, 0])
        nodes.append({
            "id": nid,
            "type": "doorway",
            "label": {"swing": "普通门", "fire": "防火门",
                      "opening": "门洞"}.get(kind, "门"),
            "doorType": kind,
            "width_m": round(float(dr.get("width_pt", 0)) * SCALE, 3),
            "coordinates": [round(coords[0], 3), round(coords[1], 3)],
            "rooms": dr.get("rooms", []),
            "sourceDoorIds": [door_id] if door_id else None,
            # 指南 §3.2 开向：外开门门扇扫入走廊，视障风险更高
            "openDirection": dr.get("openDirection"),
            "hingeSide": dr.get("hingeSide"),
        })

    # ---------- TF: 设施 ----------
    fac_seq = 0
    fac_node_ids = []
    for i, s in enumerate(stairs):
        fac_seq += 1
        nid = _obj_id(floor_no, obj_type["topo_facility"], fac_seq)
        fac_node_ids.append(nid)
        c = s.get("properties", {}).get("centroid") or s.get("centroid_m") or [0, 0]
        nodes.append({
            "id": nid,
            "type": "facility",
            "facilityType": "staircase",
            "label": s.get("properties", {}).get("label", f"楼梯{floor_no}F-{i+1}"),
            "coordinates": [float(c[0]), float(c[1])],
            "blindAccessible": False,
            "wheelchairAccessible": False,
        })
    for i, e in enumerate(elevators):
        fac_seq += 1
        nid = _obj_id(floor_no, obj_type["topo_facility"], fac_seq)
        fac_node_ids.append(nid)
        c = e.get("properties", {}).get("centroid") or e.get("centroid_m") or [0, 0]
        nodes.append({
            "id": nid,
            "type": "facility",
            "facilityType": "elevator",
            "label": e.get("properties", {}).get("label", f"电梯{floor_no}F-{i+1}"),
            "coordinates": [float(c[0]), float(c[1])],
            "blindAccessible": True,
            "wheelchairAccessible": True,
        })

    # ---------- TEN ----------
    if extra_nodes:
        for i, en in enumerate(extra_nodes):
            nodes.append({
                "id": _obj_id(floor_no, obj_type["topo_entrance"], i + 1),
                "type": "facility_entrance",
                "facilityType": en.get("facilityType", "entrance"),
                "label": en.get("label", ""),
                "coordinates": list(en["coordinates"]),
                "blindAccessible": True,
                "wheelchairAccessible": True,
            })

    # ---------- 边 ----------
    # 1) TR ↔ TD
    tr_connected = {}  # room node id -> True
    for i, dr in enumerate(td_doors):
        dnid = door_node_ids[i]
        if dnid is None:
            continue
        dcoord = next(n["coordinates"] for n in nodes if n["id"] == dnid)
        kind = dr.get("kind", "swing")
        for rid in dr.get("rooms", []):
            rnid = room_index.get(rid)
            if rnid is None:
                continue
            r = room_by_id[rid]
            # 规则：非卫生间封闭房间，质心节点只连普通门/防火门；
            # 门洞(opening)不与该房间质心节点直连（其归属房间若为卫生间则允许）。
            if r.get("roomType") != "toilet" and kind == "opening":
                continue
            dist = math.hypot(
                dcoord[0] - r["centroid_m"][0],
                dcoord[1] - r["centroid_m"][1],
            )
            add_edge(rnid, dnid, dist,
                     a_level=2 if kind == "fire" else 0,
                     r_level=5 if kind == "fire" else 0.5)
            tr_connected[rnid] = True

    # 1b) 兜底：无任何门边的封闭房间（管井/无门楼梯间/无门卫生间等）
    #     连最近 TD 保连通（validate 要求每个 TR 有 TD 边；楼梯间是跨层枢纽须可达）。
    if door_node_ids:
        td_coords = {}
        for i, dnid in enumerate(door_node_ids):
            if dnid is None:
                continue
            td_coords[dnid] = next(
                n["coordinates"] for n in nodes if n["id"] == dnid)
        for r in rooms:
            rnid = room_index.get(r["id"])
            if rnid is None or rnid in tr_connected:
                continue
            if r.get("roomType") in OPEN:
                continue
            c = r["centroid_m"]
            best_td, best_d = None, float("inf")
            for dnid, dc in td_coords.items():
                d = math.hypot(c[0] - dc[0], c[1] - dc[1])
                if d < best_d:
                    best_d, best_td = d, dnid
            if best_td is not None:
                add_edge(rnid, best_td, best_d)
                tr_connected[rnid] = True
                # 同步：兜底房间加入 TD.rooms（route_rules 用 rooms 判定无门卫生间）
                td_node = next(n for n in nodes if n["id"] == best_td)
                if r["id"] not in (td_node.get("rooms") or []):
                    td_node["rooms"] = list(td_node.get("rooms") or []) + [r["id"]]

    # 2) TD ↔ 最近 TI（优先：最近图节点对应的 TI；否则欧氏最近 TI）
    for i, dnid in enumerate(door_node_ids):
        if dnid is None:
            continue
        dcoord = next(n["coordinates"] for n in nodes if n["id"] == dnid)
        if not ti_ids:
            continue
        best_ti, best_d = None, float("inf")
        if G.number_of_nodes() and ti_of_gn:
            gn, gd = nearest_graph_node(G, dcoord[0], dcoord[1])
            if gn is not None and gn in ti_of_gn:
                tid = ti_of_gn[gn]
                tc = next(n["coordinates"] for n in nodes if n["id"] == tid)
                best_d = math.hypot(dcoord[0] - tc[0], dcoord[1] - tc[1])
                best_ti = tid
            elif gn is not None:
                # 图节点不是 key：在图上找最近的已映射 TI
                try:
                    lengths = nx.single_source_dijkstra_path_length(
                        G, gn, cutoff=55.0, weight="length")
                    for g2, plen in lengths.items():
                        if g2 in ti_of_gn and plen < best_d:
                            best_d = plen + (gd or 0)
                            best_ti = ti_of_gn[g2]
                except Exception:
                    pass
        if best_ti is None:
            for tid in ti_ids:
                tc = next(n["coordinates"] for n in nodes if n["id"] == tid)
                d = math.hypot(dcoord[0] - tc[0], dcoord[1] - tc[1])
                if d < best_d:
                    best_d, best_ti = d, tid
        if best_ti is not None and best_d < 55.0:
            add_edge(dnid, best_ti, best_d)

    # 3) TI ↔ TI：图边 → TE（1:1，禁止 all-pairs）
    #    TI 已与 key 节点对齐，故每条骨架边直接对应一条 TI-TI 边，连通性=骨架连通性。
    G = sk["graph"]
    linked = set()
    n_adj = 0
    if manual_skeleton is not None:
        # 手动骨架：TI-TI 边直接取自 JSON（保留其 accessibility/risk 等属性）
        ti_id_set = set(ti_ids)
        max_manual_seq = 0
        for e in manual_ti_edges:
            a, b = e.get("from"), e.get("to")
            if a not in ti_id_set or b not in ti_id_set or a == b:
                continue
            key = tuple(sorted((a, b)))
            if key in linked:
                continue
            linked.add(key)
            try:
                seqn = int(e.get("id", "").rsplit("-", 1)[-1])
                max_manual_seq = max(max_manual_seq, seqn)
            except (ValueError, AttributeError, TypeError):
                pass
            edges.append({
                "id": e.get("id"),
                "from": a, "to": b,
                "distance": round(float(e.get("distance") or 0.0), 2),
                "estimatedTime": round(float(
                    e.get("estimatedTime") or
                    (e.get("distance") or 0.0) / BLIND_WALK_SPEED), 1),
                "accessibilityLevel": e.get("accessibilityLevel", 0),
                "riskLevel": e.get("riskLevel", 0.5),
                "walkable": e.get("walkable", True),
                "wheelchairAccessible": e.get("wheelchairAccessible", True),
                "blindAccessible": e.get("blindAccessible", True),
            })
            n_adj += 1
        # 推进编号，避免后续 add_edge 与手动边 ID 冲突
        edge_seq[0] = max(edge_seq[0], max_manual_seq)
    elif G.number_of_edges() and ti_of_gn:
        for ua, ub, edata in G.edges(data=True):
            ta = ti_of_gn.get(ua)
            tb = ti_of_gn.get(ub)
            if not ta or not tb or ta == tb:
                continue
            key = tuple(sorted((ta, tb)))
            if key in linked:
                continue
            linked.add(key)
            length = float(edata.get("length") or 0.0)
            if length <= 0:
                ca = next(n["coordinates"] for n in nodes if n["id"] == ta)
                cb = next(n["coordinates"] for n in nodes if n["id"] == tb)
                length = math.hypot(ca[0] - cb[0], ca[1] - cb[1])
            add_edge(ta, tb, length)
            n_adj += 1
    elif len(ti_ids) >= 2:
        # 无图：k=2 近邻兜底
        for i, tid in enumerate(ti_ids):
            ca = next(n["coordinates"] for n in nodes if n["id"] == tid)
            dists = []
            for j, tid2 in enumerate(ti_ids):
                if i == j:
                    continue
                cb = next(n["coordinates"] for n in nodes if n["id"] == tid2)
                dists.append((math.hypot(ca[0] - cb[0], ca[1] - cb[1]), tid2))
            dists.sort()
            for d, tid2 in dists[:2]:
                if d > 30:
                    break
                key = tuple(sorted((tid, tid2)))
                if key in linked:
                    continue
                linked.add(key)
                add_edge(tid, tid2, d)
                n_adj += 1
    print(f"    [skeleton] TI-TI 邻接边 {n_adj} 条 (TI={len(ti_ids)})")

    # 4) TF ↔ 最近 TD
    #    需求⑲：电梯节点(TF)不得连防火门(fire) TD——电梯井道门已由电梯门
    #    (elevator door)元素承载，防火门属前室/房间隔断，不接入电梯。
    for fn in [n for n in nodes if n["type"] == "facility"]:
        if not door_node_ids:
            break
        best_d, best_dn = float("inf"), None
        for dnid in door_node_ids:
            if dnid is None:
                continue
            dn_node = next((n for n in nodes if n["id"] == dnid), None)
            if dn_node is None:
                continue
            # 电梯不连防火门（需求⑲）
            if fn["facilityType"] == "elevator" and \
               dn_node.get("doorType") == "fire":
                continue
            dc = dn_node["coordinates"]
            d = math.hypot(fn["coordinates"][0] - dc[0],
                           fn["coordinates"][1] - dc[1])
            if d < best_d:
                best_d, best_dn = d, dnid
        if best_dn is not None:
            is_stair = fn["facilityType"] == "staircase"
            add_edge(fn["id"], best_dn, best_d,
                     a_level=999 if is_stair else 0,
                     r_level=10 if is_stair else 1,
                     wheel=not is_stair, blind=not is_stair)

    # 5) TEN ↔ 最近 TD
    for en in [n for n in nodes if n["type"] == "facility_entrance"]:
        if not door_node_ids:
            break
        best_d, best_dn = float("inf"), None
        for dnid in door_node_ids:
            if dnid is None:
                continue
            dc = next(n["coordinates"] for n in nodes if n["id"] == dnid)
            d = math.hypot(en["coordinates"][0] - dc[0],
                           en["coordinates"][1] - dc[1])
            if d < best_d:
                best_d, best_dn = d, dnid
        if best_dn is not None:
            add_edge(en["id"], best_dn, best_d, a_level=0, r_level=5)

    # 6) 全局合并聚集的 TI 节点（手绘/栅格骨架在 doorway、转折处产生的冗余微交叉口）
    nodes, edges, _ = _merge_nearby_ti_nodes(nodes, edges, radius_m=TI_MERGE_RADIUS_M)

    # skeleton GeoJSON features
    if manual_skeleton is not None:
        skel_features = [dict(f) for f in manual_skel_features]
    else:
        skel_features = []
        for i, line in enumerate(sk["lines"]):
            skel_features.append({
                "type": "Feature",
                "id": _obj_id(floor_no, "SK", i + 1),
                "geometry": mapping(line),
                "properties": {"type": "skeleton", "length_m": round(line.length, 2)},
            })

    # 6) 孤立节点兜底：TR/TD/TF/TEN/TI 度为 0 时挂到最近可达节点
    degree = {}
    for e in edges:
        degree[e["from"]] = degree.get(e["from"], 0) + 1
        degree[e["to"]] = degree.get(e["to"], 0) + 1
    nmap = {n["id"]: n for n in nodes}
    orphans = [n for n in nodes if degree.get(n["id"], 0) == 0]
    n_orphan_fixed = 0
    for n in orphans:
        nt = n.get("type")
        # 目标类型优先级
        if nt == "room":
            prefer = ("doorway",)
        elif nt == "doorway":
            prefer = ("intersection", "doorway", "facility")
        elif nt == "facility":
            prefer = ("doorway", "intersection")
        elif nt == "facility_entrance":
            prefer = ("doorway", "intersection")
        elif nt == "intersection":
            prefer = ("intersection", "doorway")
        else:
            prefer = ("intersection", "doorway")
        best, best_d = None, float("inf")
        for o in nodes:
            if o["id"] == n["id"]:
                continue
            if o.get("type") not in prefer:
                continue
            d = math.hypot(
                n["coordinates"][0] - o["coordinates"][0],
                n["coordinates"][1] - o["coordinates"][1],
            )
            # 房间→门 放宽到 25m；其它 20m
            lim = 25.0 if nt == "room" else 20.0
            if d < best_d and d <= lim:
                best_d, best = d, o
        if best is not None:
            a_level, r_level = 0, 0.5
            wheel = blind = True
            if n.get("facilityType") == "staircase" or best.get("facilityType") == "staircase":
                a_level, r_level, wheel, blind = 999, 10, False, False
            add_edge(n["id"], best["id"], best_d,
                     a_level=a_level, r_level=r_level, wheel=wheel, blind=blind)
            n_orphan_fixed += 1
    if n_orphan_fixed:
        print(f"    [skeleton] 孤立节点挂接 {n_orphan_fixed} 条")

    # 7) 骨架多连通分量：近距离 TI 软桥（非 all-pairs，每对分量最多 1 条）
    ti_nodes = [n for n in nodes if n.get("type") == "intersection"]
    if len(ti_nodes) >= 2:
        # 用当前边重建 TI 子图分量
        tset = {n["id"] for n in ti_nodes}
        adj = {tid: set() for tid in tset}
        for e in edges:
            a, b = e["from"], e["to"]
            if a in tset and b in tset:
                adj[a].add(b)
                adj[b].add(a)
        seen = set()
        comps = []
        for tid in tset:
            if tid in seen:
                continue
            st = [tid]
            seen.add(tid)
            c = {tid}
            while st:
                u = st.pop()
                for v in adj[u]:
                    if v not in seen:
                        seen.add(v)
                        c.add(v)
                        st.append(v)
            comps.append(c)
        if len(comps) > 1:
            comps.sort(key=len, reverse=True)
            n_soft = 0
            main = comps[0]
            for island in comps[1:]:
                best = None
                best_d = 25.0  # 仅桥接 ≤25m 的近邻分量（走廊缝隙）
                for a in island:
                    ca = nmap[a]["coordinates"]
                    for b in main:
                        cb = nmap[b]["coordinates"]
                        d = math.hypot(ca[0] - cb[0], ca[1] - cb[1])
                        if d < best_d:
                            best_d, best = d, (a, b)
                if best:
                    add_edge(best[0], best[1], best_d)
                    main = main | island
                    n_soft += 1
            if n_soft:
                print(f"    [skeleton] TI 分量软桥 {n_soft} 条 (分量 {len(comps)}→更少)")

    # 同层大孤岛补边（与质心拓扑一致）
    if bridge_disconnected_components is not None:
        edges = bridge_disconnected_components(
            floor_no, nodes, edges,
            min_island_nodes=5,   # 略降，收纳中等孤岛
            max_bridge_dist_m=120.0,
            bridges_per_island=2,
        )

    return {
        "nodes": nodes,
        "edges": edges,
        "skeleton_features": skel_features,
        "skeleton_meta": {
            "junction_count": len(manual_ti) if manual_skeleton is not None else len(sk["junctions"]),
            "terminal_count": 0 if manual_skeleton is not None else len(sk["terminals"]),
            "segment_count": len(manual_skel_features) if manual_skeleton is not None else len(sk["lines"]),
            "empty": False if manual_skeleton is not None else sk["empty"],
            "manual": manual_skeleton is not None,
        },
    }
