# -*- coding: utf-8 -*-
"""验证 doorway 加入白名单后的可达性与穿墙"""
import json
import sys
import heapq
from collections import defaultdict

sys.path.insert(0, r"E:/code/pathai/src")
import fix_crossing_edges as m
from shapely.geometry import LineString, Point

g = json.load(open("result/school_building_01_map_v9.geojson", encoding="utf-8"))
fl = g["floors"]["1"]
rooms = m.collect_rooms(fl)
nmap = {n["id"]: n for n in fl["topology"]["nodes"]}
tmap = {n["id"]: n.get("type") for n in fl["topology"]["nodes"]}

MID = {"intersection", "facility", "facility_entrance", "doorway"}


def dijkstra(src, dst, mode="normal"):
    adj = {}
    for e in fl["topology"]["edges"]:
        if e["from"] not in nmap or e["to"] not in nmap:
            continue
        if mode == "blind":
            if e.get("blindAccessible") is False:
                continue
            if e.get("accessibilityLevel") == 999:
                continue
        d = e.get("distance", 0)
        adj.setdefault(e["from"], []).append((e["to"], d))
        adj.setdefault(e["to"], []).append((e["from"], d))
    dist = {src: 0}
    prev = {src: None}
    pq = [(0, src)]
    while pq:
        d, u = heapq.heappop(pq)
        if u == dst:
            break
        if d > dist.get(u, 1e18):
            continue
        if u != src and u != dst and tmap.get(u) not in MID:
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


for s, t in [("F1-TR-0015", "F1-TR-0036"),
             ("F1-TD-0017", "F1-TR-0036"),
             ("F1-TR-0015", "F1-TD-0065"),
             ("F1-TR-0018", "F1-TR-0036")]:
    for mode in ["normal", "blind"]:
        r = dijkstra(s, t, mode)
        print(f"{mode:6s} {s}→{t}: " +
              (f"{len(r[0])}节点 {r[1]:.1f}m" if r else "不可达"))

# 路径穿墙检查
r = dijkstra("F1-TR-0015", "F1-TR-0036")
CLOSED = {"classroom", "lab", "office", "meeting", "toilet", "storage",
          "equipment", "library", "medical", "shaft", "room", "reception",
          "counseling", "staircase"}
crooms = [(rid, rt, l, p) for rid, rt, l, p, _ in rooms if rt in CLOSED]
bad = 0
for i in range(len(r[0]) - 1):
    a = nmap[r[0][i]]["coordinates"]
    b = nmap[r[0][i + 1]]["coordinates"]
    seg = LineString([a, b])
    for rid, rt, l, p in crooms:
        if seg.intersects(p.buffer(-0.05)):
            ta, tb = tmap[r[0][i]], tmap[r[0][i + 1]]
            tr_ok = (ta == "room" and p.contains(Point(a))) or \
                    (tb == "room" and p.contains(Point(b)))
            tf_ok = rt == "staircase" and \
                    (ta in ("facility", "doorway") or tb in ("facility", "doorway"))
            if not (tr_ok or tf_ok):
                bad += 1
                print(f"  ⚠️ {r[0][i]}→{r[0][i+1]} 穿 {l}")
            break
print(f"路径穿墙(非房间内部/楼梯): {bad}")
print("路径:", [f"{nid}[{tmap.get(nid,'?')}]" for nid in r[0]])
