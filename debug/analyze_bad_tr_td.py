# -*- coding: utf-8 -*-
"""分析 11 条 TR→TD 连错门：TD 真实归属 + TR 其他门"""
import json
import sys
from collections import defaultdict
from shapely.geometry import Point, Polygon

g = json.load(open("result/school_building_01_map_v9.geojson", encoding="utf-8"))
CLOSED = {"classroom", "lab", "office", "meeting", "toilet", "storage",
          "equipment", "library", "medical", "shaft", "room", "reception",
          "counseling", "staircase"}

cases = [
    ("1", "F1-TE-0050", "F1-TR-0003", "F1-TD-0095"),
    ("1", "F1-TE-0347", "F1-TR-0017", "F1-TD-0009"),
    ("1", "1-TE-HC-1152", "F1-TR-0028", "F1-TD-0005"),
    ("1", "F1-TE-0048", "F1-TR-0032", "F1-TD-0093"),
    ("1", "1-TE-HC-1201", "F1-TR-0050", "F1-TD-0087"),
    ("2", "2-TE-HC-1086", "F2-TR-0005", "F2-TD-0010"),
    ("2", "F2-TE-0017", "F2-TR-0012", "F2-TD-0016"),
    ("2", "F2-TE-0001", "F2-TR-0017", "F2-TD-0001"),
    ("2", "F2-TE-0027", "F2-TR-0018", "F2-TD-0027"),
    ("2", "F2-TE-0201", "F2-TR-0024", "F2-TD-0004"),
    ("2", "F2-TE-0050", "F2-TR-0027", "F2-TD-0061"),
]

for fk, eid, tr_id, td_id in cases:
    fl = g["floors"][fk]
    nmap = {n["id"]: n for n in fl["topology"]["nodes"]}
    edges_by = defaultdict(list)
    for e in fl["topology"]["edges"]:
        edges_by[e["from"]].append(e)
        edges_by[e["to"]].append(e)
    # TR 所在房间
    tr_pt = Point(nmap[tr_id]["coordinates"])
    tr_room = None
    for r in fl["geometry"]["rooms"]:
        rt = (r.get("properties") or {}).get("roomType", "")
        if rt not in CLOSED:
            continue
        try:
            p = Polygon(r["geometry"]["coordinates"][0])
        except Exception:
            continue
        if p.contains(tr_pt):
            tr_room = (r["id"], rt, (r.get("properties") or {}).get("label", ""), p)
            break
    # TD 在哪些房间边界(≤0.8m)
    td_pt = Point(nmap[td_id]["coordinates"])
    td_on = []
    for r in fl["geometry"]["rooms"]:
        rt = (r.get("properties") or {}).get("roomType", "")
        if rt not in CLOSED:
            continue
        try:
            p = Polygon(r["geometry"]["coordinates"][0])
        except Exception:
            continue
        d = p.exterior.distance(td_pt)
        if d <= 0.8:
            td_on.append((r["id"], rt, (r.get("properties") or {}).get("label", ""), round(d, 2)))
    # TR 的其他门邻居
    other_doors = []
    for e in edges_by[tr_id]:
        o = e["to"] if e["from"] == tr_id else e["from"]
        if nmap[o].get("type") == "doorway" and o != td_id:
            other_doors.append(o)
    print(f"[F{fk}] {eid} {tr_id}→{td_id}")
    print(f"  TR 所在房间: {tr_room[0]}[{tr_room[1]}]{tr_room[2] if tr_room else '?'}" if tr_room else "  TR 不在任何封闭房间!")
    print(f"  TD 位置 ({nmap[td_id]['coordinates'][0]:.2f},{nmap[td_id]['coordinates'][1]:.2f}) 位于房间边界(≤0.8m): {td_on}")
    print(f"  TR 的其他门邻居: {other_doors}")
    print()
