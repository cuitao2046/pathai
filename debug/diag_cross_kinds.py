# -*- coding: utf-8 -*-
import json, sys, math
from pathlib import Path
from shapely.geometry import shape, Point, LineString
from shapely.ops import unary_union

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE / "src"))
import route_rules as rr

geo = json.loads((BASE / "result" / "school_building_01_map_v9.geojson").read_text(encoding="utf-8"))
g = rr.RouteGraph(geo)

# 房间多边形并集（按 roomId）
room_poly = {}
for fk, fd in geo["floors"].items():
    for r in fd["geometry"].get("rooms", []):
        try:
            room_poly[r["id"]] = shape(r["geometry"])
        except Exception:
            pass

# 墙体 union（用于判断线段是否在墙内）
walls = []
for fk, fd in geo["floors"].items():
    for w in fd["geometry"].get("walls", []):
        gg = w.get("geometry", {})
        if gg.get("type") == "LineString":
            t = (w.get("properties") or {}).get("thickness") or 0.2
            walls.append(LineString(gg["coordinates"]).buffer(t/2 + 0.02))
wall_u = unary_union(walls) if walls else None

interior_clip = 0
real_jump = 0
examples_jump = []
for e in g.edges:
    a = g.nodes.get(e["from"]); b = g.nodes.get(e["to"])
    if not a or not b: continue
    if not ({a["type"], b["type"]} == {"room", "doorway"}):
        continue
    # 哪个是 room，哪个是 doorway
    rn = a if a["type"] == "room" else b
    dn = b if a["type"] == "room" else a
    ca, cb = rn["coords"], dn["coords"]
    rms = rn.get("roomId")
    poly = room_poly.get(rms)
    line = LineString([ca, cb])
    # 线段落在房间多边形外的比例
    if poly is None:
        continue
    inter = line.intersection(poly)
    try:
        inside_len = inter.length if hasattr(inter, "length") else 0
    except Exception:
        inside_len = 0
    outside_len = max(0.0, line.length - inside_len)
    frac_out = outside_len / line.length if line.length > 0 else 0
    # 是否真正穿墙（两端异侧）
    crosses = g._seg_crosses_any_wall(ca, cb)
    if not crosses:
        continue
    # 真·穿墙且线段大部分在房间外 → 视为真实跳跃；否则视为房间内墙clip
    if frac_out > 0.5 and crosses:
        real_jump += 1
        if len(examples_jump) < 15:
            examples_jump.append((e["id"], rms, round(line.length,2), round(frac_out,2)))
    else:
        interior_clip += 1

print(f"room↔door 穿墙边: 真实跳跃(>50%在房间外)={real_jump}, 房间内墙clip={interior_clip}")
print("真实跳跃示例:")
for t in examples_jump:
    print("  ", t)
