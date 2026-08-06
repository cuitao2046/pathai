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
from .junction_detector import detect_junctions, simplify_degree2_paths
from .door_projector import project_doors_to_skeleton, project_points_to_skeleton
try:
    from topology import bridge_disconnected_components
except ImportError:
    bridge_disconnected_components = None  # type: ignore


BLIND_WALK_SPEED = 0.8


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
            "terminals": [], "raw_masks_meta": [], "empty": True,
        }

    try:
        # buffer(0) 修自交；unary_union 合并相接碎片
        merged = unary_union([p.buffer(0) for p in valid])
        if isinstance(merged, MultiPolygon):
            pieces = [g for g in merged.geoms if g.area >= 0.5]
        elif merged.is_empty:
            pieces = valid
        else:
            pieces = [merged]
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

    info = detect_junctions(Gm)
    junctions = [(x, y) for _, x, y in info["junctions"]]
    terminals = [(x, y) for _, x, y in info["terminals"]]

    return {
        "graph": Gm,
        "lines": all_lines,
        "junctions": junctions,
        "terminals": terminals,
        "raw_masks_meta": metas,
        "empty": False,
    }


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

    door_centers = []
    for dr in doors:
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

    # ---------- TI: 骨架交叉口 ----------
    ti_ids = []
    for i, (x, y) in enumerate(sk["junctions"]):
        nid = _obj_id(floor_no, obj_type["topo_intersection"], i + 1)
        ti_ids.append(nid)
        nodes.append({
            "id": nid,
            "type": "intersection",
            "roomType": "corridor",
            "label": f"交叉口{i + 1}",
            "coordinates": [round(x, 3), round(y, 3)],
        })
    # 无交叉口时，用 terminal / 骨架节点补 circulation 点
    if not ti_ids and sk["terminals"]:
        for i, (x, y) in enumerate(sk["terminals"][:8]):
            nid = _obj_id(floor_no, obj_type["topo_intersection"], i + 1)
            ti_ids.append(nid)
            nodes.append({
                "id": nid,
                "type": "intersection",
                "roomType": "corridor",
                "label": f"走廊节点{i + 1}",
                "coordinates": [round(x, 3), round(y, 3)],
            })

    # ---------- TD: 门投影到骨架 ----------
    door_node_ids = []
    door_projs = project_doors_to_skeleton(
        door_centers, sk["lines"], max_dist_m=10.0
    ) if sk["lines"] else []

    for i, dr in enumerate(doors):
        nid = _obj_id(floor_no, obj_type["topo_doorway"], i + 1)
        door_node_ids.append(nid)
        kind = dr.get("kind", "swing")
        # 优先投影点，否则门中心
        coords = list(dr.get("center_m") or [0, 0])
        if i < len(door_projs) and door_projs[i].get("projected"):
            coords = list(door_projs[i]["projected"])
        nodes.append({
            "id": nid,
            "type": "doorway",
            "label": {"swing": "普通门", "fire": "防火门",
                      "opening": "门洞"}.get(kind, "门"),
            "doorType": kind,
            "width_m": round(float(dr.get("width_pt", 0)) * 0.0529, 3),
            "coordinates": [round(coords[0], 3), round(coords[1], 3)],
            "rooms": dr.get("rooms", []),
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
    for i, dr in enumerate(doors):
        dnid = door_node_ids[i]
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

    # 2) TD ↔ 最近 TI（骨架接入）
    for i, dnid in enumerate(door_node_ids):
        dcoord = next(n["coordinates"] for n in nodes if n["id"] == dnid)
        if not ti_ids:
            continue
        best_ti, best_d = None, float("inf")
        for tid in ti_ids:
            tc = next(n["coordinates"] for n in nodes if n["id"] == tid)
            d = math.hypot(dcoord[0] - tc[0], dcoord[1] - tc[1])
            if d < best_d:
                best_d, best_ti = d, tid
        if best_ti is not None and best_d < 40.0:
            add_edge(dnid, best_ti, best_d)

    # 3) TI ↔ TI 沿骨架图测地距离（若图可用）
    G = sk["graph"]
    if G.number_of_nodes() >= 2 and len(ti_ids) >= 2:
        # 把 TI 坐标匹配到最近图节点
        ti_graph_nodes = []
        for tid in ti_ids:
            tc = next(n["coordinates"] for n in nodes if n["id"] == tid)
            gn, _ = nearest_graph_node(G, tc[0], tc[1])
            ti_graph_nodes.append((tid, gn))
        linked = set()
        for i in range(len(ti_graph_nodes)):
            for j in range(i + 1, len(ti_graph_nodes)):
                tid_a, ga = ti_graph_nodes[i]
                tid_b, gb = ti_graph_nodes[j]
                if ga is None or gb is None:
                    continue
                try:
                    path_len = nx.shortest_path_length(G, ga, gb, weight="length")
                except (nx.NetworkXNoPath, nx.NodeNotFound):
                    # 欧氏兜底
                    ca = next(n["coordinates"] for n in nodes if n["id"] == tid_a)
                    cb = next(n["coordinates"] for n in nodes if n["id"] == tid_b)
                    path_len = math.hypot(ca[0] - cb[0], ca[1] - cb[1])
                    if path_len > 50:
                        continue
                if path_len > 80:
                    continue
                key = (tid_a, tid_b)
                if key in linked:
                    continue
                linked.add(key)
                add_edge(tid_a, tid_b, path_len)
    elif len(ti_ids) >= 2:
        # 无图：近距离 TI 直连
        for i in range(len(ti_ids)):
            for j in range(i + 1, len(ti_ids)):
                ca = next(n["coordinates"] for n in nodes if n["id"] == ti_ids[i])
                cb = next(n["coordinates"] for n in nodes if n["id"] == ti_ids[j])
                d = math.hypot(ca[0] - cb[0], ca[1] - cb[1])
                if d <= 50:
                    add_edge(ti_ids[i], ti_ids[j], d)

    # 4) TF ↔ 最近 TD
    for fn in [n for n in nodes if n["type"] == "facility"]:
        if not door_node_ids:
            break
        best_d, best_dn = float("inf"), None
        for dnid in door_node_ids:
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

    # 同层大孤岛补边（与质心拓扑一致）
    if bridge_disconnected_components is not None:
        edges = bridge_disconnected_components(floor_no, nodes, edges)

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
