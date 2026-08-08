# -*- coding: utf-8 -*-
"""
骨架管线总装：Walkable → 中轴 → 剪枝 → 矢量化 → Junction/门投影 → 拓扑节点边。

供 parse_cad_pdf / topology 调用；输出与现有 GeoJSON topology 字段兼容。
"""

from __future__ import annotations

import math
from typing import Dict, List, Optional, Sequence, Tuple

import networkx as nx
from shapely.geometry import LineString, Point, mapping
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
    from topology import bridge_disconnected_components
except ImportError:
    bridge_disconnected_components = None  # type: ignore


BLIND_WALK_SPEED = 0.8


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
) -> dict:
    """
    T8 替代逻辑：基于骨架生成 topology nodes/edges。

    rooms: parse_cad_pdf 的 rooms（含 roomType, centroid_m, id, polygon 可选）
    doors: [{center_m, kind, width_pt, rooms}, ...]
    stairs/elevators: GeoJSON-like features with properties.centroid
    walkable_by_room_id: room_id → Shapely walkable poly (meters)
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
    NON_ACC = {"staircase", "shaft"}

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

    # 合并同开口门（同一物理开口的摆弧/防火/门洞），避免拓扑层重叠 TD 节点
    MERGE_DIST_M = 0.8
    td_doors = _merge_nearby_doors(doors, MERGE_DIST_M)

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

    sk = build_skeleton_for_walkables(
        walkables, door_centers, fac_centers, resolution=resolution
    )

    nodes = []
    edges = []
    edge_seq = [0]

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

    # ---------- TD: 门投影到骨架 + 二次合并（投影重合的门） ----------
    door_projs = project_doors_to_skeleton(
        door_centers, sk["lines"], max_dist_m=10.0
    ) if sk["lines"] else []
    # 二次合并：以投影后最终坐标为依据（阈值 1.0m），吸收「不同门但投影到同一
    # 骨架点」造成的重叠 TD 节点（如同一房间多个邻近入口）。合并时 center_m
    # 被改写为最终投影坐标，下方直接采用。
    _final = []
    for i, dr in enumerate(td_doors):
        c = list(dr.get("center_m") or [0, 0])
        if i < len(door_projs) and door_projs[i].get("projected"):
            c = list(door_projs[i]["projected"])
        _final.append(tuple(c))
    td_doors = _merge_nearby_doors(td_doors, 1.0, coords=_final)

    door_node_ids = []  # 与 td_doors 对齐，被跳过的门记为 None
    for i, dr in enumerate(td_doors):
        # 该门是否连到至少一个封闭房间（TR）；无房间归属则跳过，
        # 避免生成悬空 corridor-only 门节点（走廊连通性由 TI↔TI 边承担）。
        has_room = any(rid in room_index for rid in dr.get("rooms", []))
        if not has_room:
            door_node_ids.append(None)
            continue
        nid = _obj_id(floor_no, obj_type["topo_doorway"], len(door_node_ids) + 1)
        door_node_ids.append(nid)
        kind = dr.get("kind", "swing")
        coords = list(dr.get("center_m") or [0, 0])  # 已为最终（投影）坐标
        nodes.append({
            "id": nid,
            "type": "doorway",
            "label": {"swing": "普通门", "fire": "防火门",
                      "opening": "门洞"}.get(kind, "门"),
            "doorType": kind,
            "width_m": round(float(dr.get("width_pt", 0)) * 0.0529, 3),
            "coordinates": [round(coords[0], 3), round(coords[1], 3)],
            "rooms": dr.get("rooms", []),
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
    for i, dr in enumerate(td_doors):
        dnid = door_node_ids[i]
        if dnid is None:
            continue
        dcoord = next(n["coordinates"] for n in nodes if n["id"] == dnid)
        for rid in dr.get("rooms", []):
            rnid = room_index.get(rid)
            if rnid is None:
                continue
            r = room_by_id[rid]
            dist = math.hypot(
                dcoord[0] - r["centroid_m"][0],
                dcoord[1] - r["centroid_m"][1],
            )
            add_edge(rnid, dnid, dist,
                     a_level=2 if dr.get("kind") == "fire" else 0,
                     r_level=5 if dr.get("kind") == "fire" else 0.5)

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
    if G.number_of_edges() and ti_of_gn:
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
    for fn in [n for n in nodes if n["type"] == "facility"]:
        if not door_node_ids:
            break
        best_d, best_dn = float("inf"), None
        for dnid in door_node_ids:
            if dnid is None:
                continue
            dc = next(n["coordinates"] for n in nodes if n["id"] == dnid)
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

    # skeleton GeoJSON features
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
            "junction_count": len(sk["junctions"]),
            "terminal_count": len(sk["terminals"]),
            "segment_count": len(sk["lines"]),
            "empty": sk["empty"],
        },
    }
