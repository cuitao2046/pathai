# -*- coding: utf-8 -*-
"""分析 room<->opening 边，以及 TD 节点是否落在房间多边形外（导致穿墙）。"""
import json, sys, math
from collections import defaultdict
from pathlib import Path
BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE / "src"))
from shapely.geometry import Polygon, Point, shape

geo = json.loads((BASE / "result" / "school_building_01_map_v9.geojson").read_text(encoding="utf-8"))

# 房间多边形 & 门类型索引
room_poly = {}
room_doors_by_type = defaultdict(lambda: defaultdict(list))  # roomId -> type -> [doorIds]
td_nodes = {}
td_by_rid = defaultdict(list)
for fk, fd in geo["floors"].items():
    for r in fd["geometry"].get("rooms", []):
        try:
            poly = shape(r["geometry"])
            room_poly[r["id"]] = poly
        except Exception:
            pass
    for n in fd["topology"]["nodes"]:
        if n["type"] == "doorway":
            td_nodes[n["id"]] = n
            for rid in (n.get("rooms") or []):
                room_doors_by_type[rid][n.get("doorType")].append(n["id"])
    for e in fd["topology"]["edges"]:
        a = next((n for n in fd["topology"]["nodes"] if n["id"] == e["from"]), None)
        b = next((n for n in fd["topology"]["nodes"] if n["id"] == e["to"]), None)
        if not a or not b:
            continue
        if a["type"] == "room" and b["type"] == "doorway":
            td_by_rid[a.get("roomId")].append((e["id"], b["id"], b.get("doorType")))
        if b["type"] == "room" and a["type"] == "doorway":
            td_by_rid[b.get("roomId")].append((e["id"], a["id"], a.get("doorType")))

print("=== room<->opening 边（12 条）及其房间其它门 ===")
for rid, lst in td_by_rid.items():
    op_edges = [x for x in lst if x[2] == "opening"]
    if not op_edges:
        continue
    sw = len(room_doors_by_type[rid].get("swing", []))
    fr = len(room_doors_by_type[rid].get("fire", []))
    op = len(room_doors_by_type[rid].get("opening", []))
    print(f"  {rid}: opening边={len(op_edges)} (room->opening) | 该房间 swing={sw} fire={fr} opening={op} 全部门={room_doors_by_type[rid]}")

print("\n=== TD 节点是否落在「任一归属房间」多边形内 ===")
outside = 0
total_td = 0
for tid, n in td_nodes.items():
    total_td += 1
    c = n["coordinates"]
    pt = Point(c[0], c[1])
    inside_any = False
    for rid in (n.get("rooms") or []):
        poly = room_poly.get(rid)
        if poly and poly.contains(pt):
            inside_any = True
            break
    if not inside_any and (n.get("rooms")):
        outside += 1
        if outside <= 30:
            print(f"  {tid} 在归属房间多边形外: rooms={n.get('rooms')} coord={tuple(round(x,2) for x in c)}")
print(f"\n归属房间的 TD 节点共 {total_td}，其中落在「任一归属房间」多边形外: {outside}")
