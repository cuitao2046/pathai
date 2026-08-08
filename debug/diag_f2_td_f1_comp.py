# -*- coding: utf-8 -*-
"""排查 F2 管井 TD 未摘除 + F1 主分量下降"""
import json
import sys
from collections import defaultdict
from shapely.geometry import Point, Polygon

g = json.load(open("result/school_building_01_map_v9.geojson", encoding="utf-8"))

# F2 管井 TD
fl = g["floors"]["2"]
nmap = {n["id"]: n for n in fl["topology"]["nodes"]}
tmap = {n["id"]: n.get("type") for n in fl["topology"]["nodes"]}
edges_by = defaultdict(list)
for e in fl["topology"]["edges"]:
    edges_by[e["from"]].append(e)
    edges_by[e["to"]].append(e)

print("=== F2 管井 TD 详情 ===")
for td_id in ["F2-TD-0066", "F2-TD-0074", "F2-TD-0075"]:
    n = nmap[td_id]
    pt = Point(n["coordinates"])
    near = []
    for r in fl["geometry"]["rooms"]:
        rt = (r.get("properties") or {}).get("roomType", "")
        try:
            p = Polygon(r["geometry"]["coordinates"][0])
        except Exception:
            continue
        d = p.exterior.distance(pt)
        if d < 1.0:
            near.append((r["id"], rt, round(d, 2)))
    nbrs = [(e["id"], e["to"] if e["from"] == td_id else e["from"],
             tmap.get(e["to"] if e["from"] == td_id else e["from"], "?"))
            for e in edges_by[td_id]]
    print(f"{td_id} ({n['coordinates'][0]:.1f},{n['coordinates'][1]:.1f})")
    print(f"  附近房间: {near}")
    print(f"  连接: {nbrs}")

# F1 主分量下降：找孤立小分量节点
fl1 = g["floors"]["1"]
nmap1 = {n["id"]: n for n in fl1["topology"]["nodes"]}
tmap1 = {n["id"]: n.get("type") for n in fl1["topology"]["nodes"]}
edges_by1 = defaultdict(list)
for e in fl1["topology"]["edges"]:
    edges_by1[e["from"]].append(e)
    edges_by1[e["to"]].append(e)
adj = defaultdict(set)
for e in fl1["topology"]["edges"]:
    if e["from"] in nmap1 and e["to"] in nmap1:
        adj[e["from"]].add(e["to"])
        adj[e["to"]].add(e["from"])
seen = set()
comps = []
for nid in nmap1:
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
    comps.append(c)
comps.sort(key=len, reverse=True)
main = set(comps[0])
print("\n=== F1 主分量外的节点 ===")
for c in comps[1:]:
    if len(c) > 1:
        print(f"小分量 {len(c)}: {[(x, tmap1.get(x)) for x in c]}")
    else:
        nid = c[0]
        n = nmap1[nid]
        print(f"孤立 {nid} [{tmap1.get(nid)}] ({n['coordinates'][0]:.1f},{n['coordinates'][1]:.1f})")
