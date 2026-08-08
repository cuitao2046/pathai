# -*- coding: utf-8 -*-
"""验证卫生间可达、管井摘除、连通性"""
import json
import sys
from collections import defaultdict
from shapely.geometry import Point, Polygon

sys.path.insert(0, r"E:/code/pathai/src")
g = json.load(open("result/school_building_01_map_v9.geojson", encoding="utf-8"))

for fk in ["1", "2"]:
    fl = g["floors"][fk]
    nmap = {n["id"]: n for n in fl["topology"]["nodes"]}
    tmap = {n["id"]: n.get("type") for n in fl["topology"]["nodes"]}
    edges_by = defaultdict(list)
    for e in fl["topology"]["edges"]:
        edges_by[e["from"]].append(e)
        edges_by[e["to"]].append(e)

    # 卫生间 TR 连接
    toilets = [n["id"] for n in fl["topology"]["nodes"]
               if n.get("type") == "room"
               and any(Polygon(r["geometry"]["coordinates"][0]).contains(Point(n["coordinates"]))
                       for r in fl["geometry"]["rooms"]
                       if (r.get("properties") or {}).get("roomType") == "toilet")]
    reach = []
    for tr in toilets:
        nbrs = [e["to"] if e["from"] == tr else e["from"] for e in edges_by[tr]]
        reach.append((tr, [tmap.get(x) for x in nbrs]))
    print(f"F{fk} 卫生间 TR: {reach}")

    # 管井 TD 是否仍连走廊
    shaft_td = []
    for n in fl["topology"]["nodes"]:
        if n.get("type") != "doorway":
            continue
        pt = Point(n["coordinates"])
        for r in fl["geometry"]["rooms"]:
            if (r.get("properties") or {}).get("roomType") != "shaft":
                continue
            try:
                p = Polygon(r["geometry"]["coordinates"][0])
            except Exception:
                continue
            if p.exterior.distance(pt) < 0.8:
                nbrs = [tmap.get(e["to"] if e["from"] == n["id"] else e["from"])
                        for e in edges_by[n["id"]]]
                if "intersection" in nbrs:
                    shaft_td.append(n["id"])
                break
    print(f"  管井TD仍连走廊: {shaft_td}")

    # 连通性
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
    print(f"  全边分量 {len(comps)} 主{comps[0]} 其余{comps[1:6]}")
