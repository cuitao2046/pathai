# -*- coding: utf-8 -*-
import json, sys
from pathlib import Path
from shapely.geometry import shape, Point, LineString
BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE / "src"))
import route_rules as rr

geo = json.loads((BASE / "result" / "school_building_01_map_v9.geojson").read_text(encoding="utf-8"))
g = rr.RouteGraph(geo)

def seq(eid):
    return int(eid.rsplit("-", 1)[-1])

room_poly = {}
room_type = {}
for fk, fd in geo["floors"].items():
    for r in fd["geometry"].get("rooms", []):
        try:
            room_poly[r["id"]] = shape(r["geometry"])
            room_type[r["id"]] = r.get("roomType")
        except Exception:
            pass

# 墙体（用于找被穿的墙）
walls_by_floor = {}
for fk, fd in geo["floors"].items():
    wl = []
    for w in fd["geometry"].get("walls", []):
        gg = w.get("geometry", {})
        if gg.get("type") == "LineString":
            wl.append(LineString(gg["coordinates"]))
    walls_by_floor[fk] = wl

def seg_crosses_which_walls(ca, cb, fk):
    out = []
    ln = LineString([ca, cb])
    for w in walls_by_floor[fk]:
        if ln.crosses(w):
            out.append(w)
    return out

count = 0
for fk, fd in geo["floors"].items():
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
        count += 1
        if count > 4:
            break
        crossed = seg_crosses_which_walls(ca, cb, fk)
        cw = crossed[0] if crossed else None
        print(f"\n=== {fk} {rn['id']}({room_type.get(rms)}) centroid={tuple(round(x,2) for x in ca)} in_poly={poly.contains(Point(ca))} area={poly.area:.1f} npts={len(poly.exterior.coords)} ===")
        print(f"  TD={dn['id']} tdcoord={tuple(round(x,2) for x in cb)} doorType={dn.get('doorType')} rooms={dn.get('rooms')}")
        print(f"  polygon bbox x[{poly.bounds[0]:.1f},{poly.bounds[2]:.1f}] y[{poly.bounds[1]:.1f},{poly.bounds[3]:.1f}]")
        # 房间多边形顶点
        pts = list(poly.exterior.coords)
        print(f"  polygon pts: {[ (round(x,1),round(y,1)) for x,y in pts ]}")
        if cw is not None:
            cc = list(cw.coords)
            print(f"  被穿墙段: {[ (round(x,1),round(y,1)) for x,y in cc ]}")
    if count > 4:
        break
print(f"\n(total real-jump processed for dump: {count})")
