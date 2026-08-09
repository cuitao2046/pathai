# -*- coding: utf-8 -*-
import json, sys
from pathlib import Path
from shapely.geometry import shape, Point, LineString
from shapely.ops import unary_union
BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE / "src"))
import route_rules as rr

geo = json.loads((BASE / "result" / "school_building_01_map_v9.geojson").read_text(encoding="utf-8"))
g = rr.RouteGraph(geo)

def seq(eid):
    return int(eid.rsplit("-", 1)[-1])

total = 0
fixed_by_geom = 0
examples = []
room_poly_all = {}
for fk, fd in geo["floors"].items():
    for r in fd["geometry"].get("rooms", []):
        try:
            room_poly_all[r["id"]] = shape(r["geometry"])
        except Exception:
            pass

for fk, fd in geo["floors"].items():
    room_poly = {r["id"]: room_poly_all[r["id"]] for r in fd["geometry"].get("rooms", []) if r["id"] in room_poly_all}
    doors = {d["id"]: d for d in fd["geometry"].get("doors", [])}
    door_by_seq = {seq(d["id"]): d for d in doors.values()}

    for e in g.edges:
        a = g.nodes.get(e["from"]); b = g.nodes.get(e["to"])
        if not a or not b:
            continue
        if not ({a["type"], b["type"]} == {"room", "doorway"}):
            continue
        rn = a if a["type"] == "room" else b
        dn = b if a["type"] == "room" else a
        ca, cb = rn["coords"], dn["coords"]
        rms = rn.get("roomId"); poly = room_poly.get(rms)
        if poly is None:
            continue
        if not g._seg_crosses_any_wall(ca, cb):
            continue
        line = LineString([ca, cb])
        inter = line.intersection(poly)
        inside = inter.length if hasattr(inter, "length") else 0
        frac_out = max(0, line.length - inside) / line.length if line.length > 0 else 0
        if frac_out <= 0.5:
            continue
        total += 1
        # 用几何门坐标
        td_seq = seq(dn["id"])
        dgeom = door_by_seq.get(td_seq)
        dcoord = None
        if dgeom:
            gg = dgeom.get("geometry", {})
            if gg.get("type") == "Point":
                dcoord = gg["coordinates"]
            elif gg.get("type") == "LineString":
                c = gg["coordinates"]
                dcoord = [(c[0][0]+c[1][0])/2, (c[0][1]+c[1][1])/2]
        dcross = g._seg_crosses_any_wall(ca, dcoord) if dcoord else True
        if not dcross:
            fixed_by_geom += 1
        if len(examples) < 8:
            examples.append((fk, rn["id"], dn["id"],
                             tuple(round(x, 2) for x in cb),
                             poly.contains(Point(ca)),
                             tuple(round(x, 2) for x in dcoord) if dcoord else None,
                             dcross))
print(f"real-jump 总数={total}, 用几何门坐标后不穿墙={fixed_by_geom}")
print("示例 (fk, room, td, tdcoord, centroid_in_poly, doorgeom_coord, doorgeom_crosses):")
for t in examples:
    print("  ", t)
