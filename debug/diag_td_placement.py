# -*- coding: utf-8 -*-
import json, sys, math
from pathlib import Path
BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE / "src"))
from shapely.geometry import Polygon, Point, shape

geo = json.loads((BASE / "result" / "school_building_01_map_v9.geojson").read_text(encoding="utf-8"))

for fk, fd in geo["floors"].items():
    room_poly = {}
    for r in fd["geometry"].get("rooms", []):
        try:
            room_poly[r["id"]] = shape(r["geometry"])
        except Exception:
            pass
    # 房间 bbox 最大尺度，用于归一化阈值
    nodes = {n["id"]: n for n in fd["topology"]["nodes"]}
    far_td = 0
    near_td = 0
    for n in fd["topology"]["nodes"]:
        if n["type"] != "doorway":
            continue
        pt = Point(n["coordinates"])
        best = None
        for rid in (n.get("rooms") or []):
            poly = room_poly.get(rid)
            if poly:
                d = poly.distance(pt)
                if best is None or d < best:
                    best = d
        if best is None:
            continue
        if best > 1.0:
            far_td += 1
            if far_td <= 25:
                print(f"  F{fk} {n['id']} 距最近归属房间边界 {best:.2f}m rooms={n.get('rooms')} coord={tuple(round(x,2) for x in n['coordinates'])}")
        else:
            near_td += 1
    print(f"F{fk}: TD 贴近房间墙(<=1m)={near_td}, 远离房间(>1m)={far_td}")
