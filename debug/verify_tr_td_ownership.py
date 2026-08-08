# -*- coding: utf-8 -*-
"""验证 TR→TD 归属 + 连通性 + 孤立房间"""
import json
import sys
from collections import defaultdict
from shapely.geometry import Point, Polygon

g = json.load(open("result/school_building_01_map_v9.geojson", encoding="utf-8"))
CLOSED = {"classroom", "lab", "office", "meeting", "toilet", "storage",
          "equipment", "library", "medical", "shaft", "room", "reception",
          "counseling", "staircase"}

for fk in ["1", "2"]:
    fl = g["floors"][fk]
    nmap = {n["id"]: n for n in fl["topology"]["nodes"]}
    edges_by = defaultdict(list)
    for e in fl["topology"]["edges"]:
        edges_by[e["from"]].append(e)
        edges_by[e["to"]].append(e)

    tr_room = {}
    for r in fl["geometry"]["rooms"]:
        rt = (r.get("properties") or {}).get("roomType", "")
        if rt not in CLOSED:
            continue
        try:
            p = Polygon(r["geometry"]["coordinates"][0])
        except Exception:
            continue
        for n in fl["topology"]["nodes"]:
            if n.get("type") == "room" and p.contains(Point(n["coordinates"])):
                tr_room.setdefault(n["id"], (r["id"], rt, p))

    bad = []
    for tr_id, (rid, rt, p) in tr_room.items():
        for e in edges_by[tr_id]:
            td_id = e["to"] if e["from"] == tr_id else e["from"]
            if nmap[td_id].get("type") != "doorway":
                continue
            d = p.exterior.distance(Point(nmap[td_id]["coordinates"]))
            if d > 0.8:
                bad.append((e["id"], tr_id, td_id, rid, round(d, 2)))

    no_tr = [n["id"] for n in fl["topology"]["nodes"]
             if n.get("type") == "doorway"
             and not any(nmap[x].get("type") == "room" for x in
                         [e["to"] if e["from"] == n["id"] else e["from"]
                          for e in edges_by[n["id"]]])]

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

    iso_tr = [n["id"] for n in fl["topology"]["nodes"]
              if n.get("type") == "room" and not edges_by[n["id"]]]
    print(f"F{fk}: TR→TD超界 {len(bad)} | 无TR门 {len(no_tr)} "
          f"| 主分量{comps[0]}/共{len(comps)} | 孤立TR {iso_tr}")
    for b in bad:
        print("   ", b)
