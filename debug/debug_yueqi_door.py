# -*- coding: utf-8 -*-
from pathlib import Path
"""查乐器存放室附近的门归属情况"""
import json
import math

geo = json.load(open(str(Path(__file__).resolve().parent.parent / "result" / "school_building_01_map_v8.geojson"),
                     encoding="utf-8"))
f1 = geo["floors"]["1"]["geometry"]
rooms = {r["id"]: r for r in f1["rooms"]}
SCALE = geo["scale"]
OX, OY = geo["origin"]["x"], geo["origin"]["y"]


def pt2m(p):
    return ((p[0] - OX) * SCALE, (OY - p[1]) * SCALE)


# 乐器存放室标签位置 pt (1177,986) -> 米
tx, ty = pt2m((1177, 986))
print("乐器存放室中心(m):", round(tx, 1), round(ty, 1))
for d in f1["doors"]:
    x, y = d["geometry"]["coordinates"]
    dist = math.hypot(x - tx, y - ty)
    if dist < 4.0:
        rooms_info = [(rid, rooms[rid]["properties"]["label"] if rid in rooms else "?")
                      for rid in d["properties"]["rooms"]]
        print(f"  {d['id']} @({x:.1f},{y:.1f}) d={dist:.2f}m "
              f"w={d['properties']['width_m']} rooms={rooms_info}")
# 乐器存放室多边形范围
for r in f1["rooms"]:
    if r["properties"]["label"] == "乐器存放室":
        xs = [c[0] for c in r["geometry"]["coordinates"][0]]
        ys = [c[1] for c in r["geometry"]["coordinates"][0]]
        print("乐器存放室多边形 bbox(m):",
              (round(min(xs), 1), round(min(ys), 1)),
              (round(max(xs), 1), round(max(ys), 1)),
              "顶点数", len(xs))
