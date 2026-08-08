#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""后处理：把「穿过封闭房间」的拓扑边改为「绕行」（贴走道、避开房间），而非删除。

为什么不能删：删除穿墙边会切断连通性。原图 F2 两翼仅靠穿过生物观察室的
桥边(F2-TE-0214/0215)相连，删掉后整层碎成 6 个分量、主分量从 178→60。

做法：
  1. 仅处理两端都在房间之外的公共边（TI-TI / TEN-TD 等）。门口边(TD 端点)保留
     ——那是房间自己的出入口，合法。
  2. 穿墙公共边：用「走道可见图」重算 A→B 路径，绕开所有房间（路径必在
     walkable 内 → 不穿墙）。端点不变 → 连通性不变。
  3. 可见图候选点 = {A,B} ∪ 被穿房间外环顶点 ∪ 走道外环在 A-B 条带内顶点；
     边合法 ⟺ 不与任何封闭房间内部相交且落在走道内。Dijkstra 取最短。

用法:
  python src/fix_crossing_edges.py result/school_building_01_map_v9.geojson
  python src/fix_crossing_edges.py in.geojson -o out.geojson
"""
from __future__ import annotations

import argparse
import json
import math
import sys
import heapq
from collections import defaultdict, deque
from pathlib import Path

from shapely.geometry import LineString, Polygon, MultiPolygon, Point, shape, mapping
from shapely.ops import unary_union

OPEN = {
    "corridor", "lobby", "activity", "atrium", "elevator_lobby", "stair_lobby",
    "entrance", "accessible_entrance",
}
CLOSED = {
    "classroom", "lab", "office", "meeting", "toilet", "storage",
    "equipment", "library", "medical", "shaft", "staircase",
    "elevator_hall", "room", "reception", "counseling",
}
WALK_SPEED_M_S = 0.8
TOL = 0.08  # 容差（m）


def collect_rooms(fl):
    """[(id, rt, label, poly, nbuf)]  nbuf=内部缓冲(用于判定穿入内部)。"""
    out = []
    for r in fl.get("geometry", {}).get("rooms") or []:
        rt = (r.get("properties") or {}).get("roomType") or ""
        if rt in OPEN or rt not in CLOSED:
            continue
        coords = r.get("geometry", {}).get("coordinates")
        if not coords or len(coords[0]) < 3:
            continue
        try:
            p = Polygon(coords[0])
            if p.is_valid and p.area > 0.5:
                out.append((r.get("id"), rt,
                            (r.get("properties") or {}).get("label", ""),
                            p, p.buffer(-TOL)))
        except Exception:
            continue
    return out


def collect_walkable(fl):
    polys = []
    wr = fl.get("walkable_regions") or {}
    feats = wr.get("features") if isinstance(wr, dict) else None
    if not feats:
        return None, None, []
    for f in feats:
        g = f.get("geometry") or {}
        if g.get("type") != "Polygon":
            continue
        c = g.get("coordinates")
        if not c or len(c[0]) < 3:
            continue
        try:
            p = Polygon(c[0])
            if p.is_valid and p.area > 1.0:
                polys.append(p)
        except Exception:
            continue
    if not polys:
        return None, None, []
    try:
        W = unary_union(polys)
    except Exception:
        return None, None, []
    verts = []
    for g in (W.geoms if isinstance(W, MultiPolygon) else [W]):
        verts.extend([(x, y) for x, y in g.exterior.coords])
    return W, W.buffer(TOL), verts


def clip_skeleton(fl, W):
    """把骨架 LineString 裁到 walkable 内（去掉穿入封闭房间的部分）。就地修改。"""
    if W is None:
        return 0
    sk = fl.get("skeleton")
    if not sk or "features" not in sk:
        return 0
    new_feats = []
    n_clip = 0
    for feat in sk["features"]:
        g = feat.get("geometry") or {}
        if g.get("type") != "LineString":
            new_feats.append(feat)
            continue
        try:
            line = shape(feat["geometry"])
        except Exception:
            new_feats.append(feat)
            continue
        inter = line.intersection(W)
        if inter.is_empty:
            n_clip += 1
            continue
        if inter.geom_type == "LineString":
            feat["geometry"] = mapping(inter)
            new_feats.append(feat)
        elif inter.geom_type == "MultiLineString":
            # 拆成多条，保留最长一段为主，其余也保留
            for i, part in enumerate(inter.geoms):
                if i == 0:
                    feat["geometry"] = mapping(part)
                    new_feats.append(feat)
                else:
                    nf = dict(feat)
                    nf["id"] = f"{feat.get('id','SK')}_p{i}"
                    nf["geometry"] = mapping(part)
                    new_feats.append(nf)
            n_clip += 1
        else:
            new_feats.append(feat)
    fl["skeleton"]["features"] = new_feats
    return n_clip


def edge_crosses_room(A, B, rooms):
    seg = LineString([A, B])
    if seg.length < TOL:
        return None
    for rid, rt, lab, poly, nbuf in rooms:
        if seg.intersects(nbuf):
            return (rid, rt, lab, poly)
    return None


def _dist_seg(p, A, B):
    ax, ay = A; bx, by = B
    vx, vy = bx - ax, by - ay
    wx, wy = p[0] - ax, p[1] - ay
    c1 = vx * wx + vy * wy
    if c1 <= 0:
        return math.hypot(wx, wy)
    c2 = vx * vx + vy * vy
    if c2 <= c1:
        return math.hypot(p[0] - bx, p[1] - by)
    t = c1 / c2
    return math.hypot(p[0] - (ax + t * vx), p[1] - (ay + t * vy))


def reroute_via_walkable(A, B, W_buf, walk_verts, rooms):
    """可见图求 A→B 绕开所有房间的路径，返回中间 waypoint(不含 A,B)。失败返回 None。"""
    # 候选点：A,B + 被穿房间在框内的外环顶点 + 走道外环在 A-B 条带内顶点
    minx = min(A[0], B[0]) - 5.0
    maxx = max(A[0], B[0]) + 5.0
    miny = min(A[1], B[1]) - 5.0
    maxy = max(A[1], B[1]) + 5.0

    def in_box(p):
        return minx <= p[0] <= maxx and miny <= p[1] <= maxy

    # 候选点：{A,B, kind='end'} + 房间外环顶点(kind='raw') +
    # 房间外环顶点向外偏移 0.4m(kind='off',) + 走道条带顶点(kind='walk')。
    # Dijkstra 对 raw 端点加 +1.0m 惩罚，使路径优先走走廊中线（off）而非贴墙。
    cand = []
    kinds = []  # 与 cand 一一对应：'end'/'raw'/'off'/'walk'
    cand.append(tuple(A)); kinds.append("end")
    cand.append(tuple(B)); kinds.append("end")
    seg = LineString([A, B])
    ROOM_OFFSET_M = 0.4
    for rid, rt, lab, poly, nbuf in rooms:
        if not seg.intersects(nbuf):
            continue
        cx, cy = poly.centroid.coords[0]
        for x, y in poly.exterior.coords:
            if not in_box((x, y)):
                continue
            cand.append((x, y)); kinds.append("raw")
            # 外环顶点向外偏移（远离房间形心）= 走廊侧候选
            dx, dy = x - cx, y - cy
            L = math.hypot(dx, dy) or 1.0
            cand.append((x + dx / L * ROOM_OFFSET_M,
                         y + dy / L * ROOM_OFFSET_M))
            kinds.append("off")
    for v in walk_verts:
        if not in_box(v):
            continue
        if _dist_seg(v, A, B) <= 3.0:
            cand.append(v); kinds.append("walk")
    # 去重
    uniq_p, uniq_k, seen = [], [], set()
    for p, k in zip(cand, kinds):
        key = (round(p[0], 3), round(p[1], 3))
        if key in seen:
            continue
        seen.add(key); uniq_p.append(p); uniq_k.append(k)
    cand = uniq_p; kinds = uniq_k
    n = len(cand)
    if n < 2:
        return None
    # 预筛边
    adj = defaultdict(list)
    RAW_PENALTY = 1.0
    for i in range(n):
        for j in range(i + 1, n):
            u, v = cand[i], cand[j]
            s = LineString([u, v])
            if W_buf is not None and not s.intersects(W_buf):
                continue
            ok = True
            for _, _, _, _, nbuf in rooms:
                if s.intersects(nbuf):
                    ok = False
                    break
            if not ok:
                continue
            d = math.hypot(u[0] - v[0], u[1] - v[1])
            if kinds[i] == "raw":
                d += RAW_PENALTY
            if kinds[j] == "raw":
                d += RAW_PENALTY
            adj[i].append((j, d))
            adj[j].append((i, d))
    # Dijkstra 0->1
    src, dst = 0, 1
    dist = {i: float("inf") for i in range(n)}
    prev = {i: -1 for i in range(n)}
    dist[src] = 0
    pq = [(0.0, src)]
    while pq:
        d, u = heapq.heappop(pq)
        if u == dst:
            break
        if d > dist[u]:
            continue
        for v, w in adj[u]:
            if dist[u] + w < dist[v]:
                dist[v] = dist[u] + w
                prev[v] = u
                heapq.heappush(pq, (dist[v], v))
    if dist[dst] == float("inf"):
        return None
    path = []
    cur = dst
    while cur != -1:
        path.append(cand[cur])
        cur = prev[cur]
    path.reverse()
    if len(path) < 2:
        return None
    return [list(p) for p in path[1:-1]]


def smooth_into_corridor(wps, W, rooms, ideal_offset=0.5):
    """把贴墙 waypoint 推到走道中线方向（远离房间外墙），让路径不再贴墙。

    可见图绕行的 waypoint 默认落在被绕房间的外环顶点（=墙角），渲染上看就像
    路线沿墙走。这里把每个 waypoint 沿「远离房间形心」方向外推 ideal_offset，
    若新点不在走道内则递减 (0.4→0.3→0.2→0.15→0.1)，仍失败则保留原 waypoint。
    """
    if not wps:
        return wps
    out = []
    for wp in wps:
        pt = Point(wp)
        # 找最近的封闭房间（绕的就是它）
        nearest, nd = None, float("inf")
        for _, _, _, poly, _ in rooms:
            d = poly.exterior.distance(pt)
            if d < nd:
                nd, nearest = d, poly
        if nearest is None or nd > 0.6:
            out.append(wp); continue
        cx, cy = nearest.centroid.coords[0]
        dx, dy = wp[0] - cx, wp[1] - cy
        L = math.hypot(dx, dy) or 1.0
        nx, ny = dx / L, dy / L
        chosen = wp
        for off in (ideal_offset, 0.4, 0.3, 0.2, 0.15, 0.1):
            cand = (wp[0] + nx * off, wp[1] + ny * off)
            if W is None or W.buffer(-0.05).contains(Point(cand)):
                chosen = list(cand); break
        out.append(chosen)
    return out


def component_count(fl, active_only=False):
    nodes = fl.get("topology", {}).get("nodes") or []
    edges = fl.get("topology", {}).get("edges") or []
    nmap = {n["id"]: n for n in nodes}
    adj = defaultdict(set)
    for e in edges:
        a, b = e.get("from"), e.get("to")
        if a is None or b is None:
            continue
        if active_only:
            if not e.get("walkable") or not e.get("blindAccessible"):
                continue
            if (e.get("accessibilityLevel") or 0) >= 900:
                continue
        if a not in nmap or b not in nmap:
            continue
        adj[a].add(b); adj[b].add(a)
    all_ids = set(nmap.keys())
    seen = set(); comps = []
    for nid in all_ids:
        if nid in seen:
            continue
        q = deque([nid]); seen.add(nid); comp = []
        while q:
            u = q.popleft(); comp.append(u)
            for v in adj.get(u, ()):
                if v not in seen:
                    seen.add(v); q.append(v)
        comps.append(len(comp))
    return len(comps), max(comps) if comps else 0


def process_floor(fl, floor_no):
    rooms = collect_rooms(fl)
    W, W_buf, walk_verts = collect_walkable(fl)
    nodes = fl.get("topology", {}).get("nodes") or []
    edges = fl.get("topology", {}).get("edges") or []
    nmap = {n["id"]: n for n in nodes}

    n_clip = clip_skeleton(fl, W)

    new_edges = []
    new_nodes = []
    rerouted, kept_td, kept, failed = 0, 0, 0, 0
    seq = max([int(e["id"].split("-")[-1]) for e in edges
               if e.get("id", "").startswith(f"{floor_no}-TE-")] + [0])
    wi = 0

    for e in edges:
        a, b = nmap.get(e["from"]), nmap.get(e["to"])
        if not a or not b:
            new_edges.append(e); kept += 1; continue
        if a.get("type") == "room" or b.get("type") == "room":
            new_edges.append(e); kept += 1; continue
        hit = edge_crosses_room(a["coordinates"], b["coordinates"], rooms)
        if not hit:
            new_edges.append(e); kept += 1; continue
        if a.get("type") == "doorway" or b.get("type") == "doorway":
            new_edges.append(e); kept_td += 1; continue
        wps = reroute_via_walkable(a["coordinates"], b["coordinates"],
                                   W_buf, walk_verts, rooms)
        if not wps:
            new_edges.append(e); failed += 1; continue
        # 把贴墙 waypoint 推到走道中线附近（远离房间外墙），避免路线「沿墙走」
        wps = smooth_into_corridor(wps, W, rooms)
        chain = [a["coordinates"]] + wps + [b["coordinates"]]
        prev = e["from"]
        for k, wp in enumerate(wps):
            wi += 1
            nid = f"{floor_no}-TWI-{wi:03d}"
            new_nodes.append({
                "id": nid, "type": "intersection", "floor": floor_no,
                "coordinates": [round(wp[0], 3), round(wp[1], 3)],
                "public": True, "accessible": True,
                "riskLevel": e.get("riskLevel", 1),
            })
            d = math.hypot(chain[k][0] - wp[0], chain[k][1] - wp[1])
            seq += 1
            new_edges.append(_mk_edge(seq, prev, nid, d, e))
            prev = nid
        d = math.hypot(chain[-2][0] - b["coordinates"][0],
                       chain[-2][1] - b["coordinates"][1])
        seq += 1
        new_edges.append(_mk_edge(seq, prev, e["to"], d, e))
        rerouted += 1

    fl.setdefault("topology", {})["edges"] = new_edges
    fl["topology"]["nodes"] = nodes + new_nodes
    return len(edges), len(new_edges), rerouted, kept_td, failed, n_clip


def _mk_edge(seq, frm, to, dist, src):
    prefix = src["id"].split("-")[0]
    return {
        "id": f"{prefix}-TE-{seq:03d}",
        "from": frm, "to": to,
        "distance": round(dist, 3),
        "estimatedTime": round(dist / WALK_SPEED_M_S, 2),
        "accessibilityLevel": src.get("accessibilityLevel", 0),
        "riskLevel": src.get("riskLevel", 1),
        "walkable": src.get("walkable", True),
        "wheelchairAccessible": src.get("wheelchairAccessible", True),
        "blindAccessible": src.get("blindAccessible", True),
    }


def main():
    ap = argparse.ArgumentParser(description="将穿墙拓扑边改为绕行（保连通）")
    ap.add_argument("input")
    ap.add_argument("-o", "--output")
    args = ap.parse_args()
    path = Path(args.input)
    geo = json.loads(path.read_text(encoding="utf-8"))

    for fk, fl in (geo.get("floors") or {}).items():
        fn = int(fk)
        total, kept_edges, rerouted, kept_td, failed, n_clip = process_floor(fl, fn)
        c_all, main_all = component_count(fl, False)
        c_act, main_act = component_count(fl, True)
        print(f"[F{fk}] 边 {total} → {kept_edges}（绕行 {rerouted}，门口边保留 {kept_td}"
              f"，绕行失败保留 {failed}）；骨架裁切 {n_clip} 段")
        print(f"      全边连通分量 {c_all}（主 {main_all}）  可导航连通分量 {c_act}（主 {main_act}）")

    out = Path(args.output) if args.output else path
    out.write_text(json.dumps(geo, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"已写入: {out}")


if __name__ == "__main__":
    sys.exit(main())
