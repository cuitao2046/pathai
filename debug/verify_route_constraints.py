# -*- coding: utf-8 -*-
"""验证：无TR门、穿墙边、连通性、音乐教室路径"""
import json
import sys
import heapq
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src" / "tools"))
import fix_crossing_edges as m
from shapely.geometry import Point, LineString

g = json.load(open("result/school_building_01_map_v9.geojson", encoding="utf-8"))

for fk in ["1", "2"]:
    fl = g["floors"][fk]
    rooms = m.collect_rooms(fl)
    nmap = {n["id"]: n for n in fl["topology"]["nodes"]}
    tmap = {n["id"]: n.get("type") for n in fl["topology"]["nodes"]}
    edges_by = defaultdict(list)
    for e in fl["topology"]["edges"]:
        edges_by[e["from"]].append(e)
        edges_by[e["to"]].append(e)

    no_tr = [n["id"] for n in fl["topology"]["nodes"]
             if n.get("type") == "doorway"
             and not any(tmap.get(x) == "room" for x in
                         [e["to"] if e["from"] == n["id"] else e["from"]
                          for e in edges_by[n["id"]]])]

    bad = []
    for e in fl["topology"]["edges"]:
        a, b = nmap.get(e["from"]), nmap.get(e["to"])
        if not a or not b:
            continue
        seg = LineString([a["coordinates"], b["coordinates"]])
        for r, rt, l, p, nbuf in rooms:
            inter = seg.intersection(p.buffer(-0.06))
            if inter.is_empty:
                continue
            ta, tb = tmap[e["from"]], tmap[e["to"]]
            # TR→TD 自己的房间内部、TF→TD/TI 楼梯内部：允许
            tr_in_room = (ta == "room" and p.contains(Point(a["coordinates"]))) or \
                         (tb == "room" and p.contains(Point(b["coordinates"])))
            tf_in_room = (ta == "facility" and p.contains(Point(a["coordinates"]))) or \
                         (tb == "facility" and p.contains(Point(b["coordinates"])))
            if tr_in_room or (tf_in_room and rt == "staircase"):
                break
            pen = inter.length if hasattr(inter, "length") else 0
            bad.append((e["id"], e["from"], e["to"], l, round(pen, 2)))
            break

    adj = defaultdict(set)
    for e in fl["topology"]["edges"]:
        if e["from"] in nmap and e["to"] in nmap:
            adj[e["from"]].add(e["to"])
            adj[e["to"]].add(e["from"])
    seen = set()
    comps = []
    for nid in nmap:
        if nid in seen:
            continue
        c = []
        q = [nid]
        seen.add(nid)
        while q:
            u = q.pop()
            c.append(u)
            for v in adj.get(u, ()):
                if v not in seen:
                    seen.add(v)
                    q.append(v)
        comps.append(len(c))
    comps.sort(reverse=True)
    print(f"F{fk}: 无TR门 {len(no_tr)} | 穿墙边 {len(bad)} "
          f"| 全边分量 {len(comps)} 主{comps[0]} 其余{comps[1:5]}")
    for x in bad[:8]:
        print("   ", x)

# 音乐教室→组织办公路径（禁 room 中转 = 前端白名单逻辑）
fl = g["floors"]["1"]
nmap = {n["id"]: n for n in fl["topology"]["nodes"]}
tmap = {n["id"]: n.get("type") for n in fl["topology"]["nodes"]}
adj = {}
for e in fl["topology"]["edges"]:
    if e["from"] not in nmap or e["to"] not in nmap:
        continue
    d = e.get("distance", 0)
    adj.setdefault(e["from"], []).append((e["to"], d))
    adj.setdefault(e["to"], []).append((e["from"], d))


def dijkstra(src, dst):
    dist = {src: 0}
    prev = {src: None}
    pq = [(0, src)]
    while pq:
        d, u = heapq.heappop(pq)
        if u == dst:
            break
        if d > dist.get(u, 1e18):
            continue
        if u != src and tmap.get(u) == "room":
            continue
        for v, w in adj.get(u, []):
            nd = d + w
            if nd < dist.get(v, 1e18):
                dist[v] = nd
                prev[v] = u
                heapq.heappush(pq, (nd, v))
    if dst not in dist:
        return None
    path = []
    u = dst
    while u is not None:
        path.append(u)
        u = prev[u]
    path.reverse()
    return path, dist[dst]


path, d = dijkstra("F1-TR-0015", "F1-TR-0036")
print(f"\n音乐教室→组织办公: {len(path)} 节点 {d:.1f}m")
for i, nid in enumerate(path):
    n = nmap[nid]
    print(f"  {i:2d} {nid} [{n.get('type')}] ({n['coordinates'][0]:7.2f},{n['coordinates'][1]:7.2f})")
