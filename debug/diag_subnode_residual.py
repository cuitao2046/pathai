# -*- coding: utf-8 -*-
import json, sys, math
from pathlib import Path
from shapely.geometry import shape, Point, LineString, Polygon
from shapely.strtree import STRtree
BASE = Path(__file__).resolve().parent.parent
geo = json.loads((BASE / "result" / "school_building_01_map_v9.geojson").read_text(encoding="utf-8"))

def side(px, py, ax, ay, bx, by):
    return (bx - ax) * (py - ay) - (by - ay) * (px - ax)
def scw(p1, p2, A, B):
    ax, ay = A[0], A[1]; bx, by = B[0], B[1]
    px, py = p1[0], p1[1]; qx, qy = p2[0], p2[1]
    dx, dy = bx - ax, by - ay
    s1 = side(px, py, ax, ay, bx, by); s2 = side(qx, qy, ax, ay, bx, by)
    if s1 == 0 and s2 == 0: return False
    if s1 == 0 or s2 == 0: return False
    if s1 * s2 > 0: return False
    if abs(dx) < 1e-12 and abs(dy) < 1e-12: return False
    ex, ey = qx - px, qy - py; det = dx*ey - dy*ex
    if abs(det) < 1e-12: return False
    u = (ex*(ay-py)-ey*(ax-px))/det; t = (dy*(px-ax)-dx*(py-ay))/det
    return (-1e-9) <= t <= (1+1e-9) and (-1e-9) <= u <= (1+1e-9)

irreducible = 0; fixable = 0; total_cross = 0
for fk, fd in geo["floors"].items():
    nmap = {n["id"]: n for n in fd["topology"]["nodes"]}
    room_poly = {}
    for r in fd["geometry"].get("rooms", []):
        try: room_poly[r["id"]] = shape(r["geometry"])
        except Exception: pass
    wall_list = []
    for w in fd["geometry"].get("walls", []):
        g = w.get("geometry", {})
        if g.get("type") == "LineString":
            ls = LineString(g["coordinates"])
            if ls.length > 1e-6: wall_list.append(ls)
    tree = STRtree(wall_list)
    def qw(bbox, m=0.3):
        minx, miny, maxx, maxy = bbox
        box = Polygon([(minx-m,miny-m),(maxx+m,miny-m),(maxx+m,maxy+m),(minx-m,maxy+m)])
        return [wall_list[i] for i in tree.query(box)]
    for e in fd["topology"]["edges"]:
        a = nmap.get(e["from"]); b = nmap.get(e["to"])
        if not a or not b: continue
        if {a["type"], b["type"]} != {"room", "doorway"}: continue
        rn = a if a["type"]=="room" else b
        dn = b if a["type"]=="room" else a
        rid = rn.get("roomId")
        if rid not in room_poly: continue
        seg = LineString([rn["coordinates"], dn["coordinates"]])
        if not any(scw(rn["coordinates"], dn["coordinates"], w.coords[0], w.coords[-1]) for w in qw(seg.bounds)):
            continue
        total_cross += 1
        c = rn["coordinates"]; poly = room_poly[rid]
        ring = poly.exterior; coords = list(ring.coords); nc=0; tot=0
        for i in range(len(coords)-1):
            x0,y0=coords[i]; x1,y1=coords[i+1]; L=math.hypot(x1-x0,y1-y0)
            if L<1e-9: continue
            n=max(1,int(L/0.3))
            for k in range(n+1):
                f=k/n; pt=(x0+(x1-x0)*f, y0+(y1-y0)*f); tot+=1
                s2=LineString([c, pt])
                if not any(scw(c, pt, w.coords[0], w.coords[-1]) for w in qw(s2.bounds)): nc+=1
        if nc == 0:
            irreducible += 1
        else:
            fixable += 1
            print(f"  FIXABLE[{fk}] {e['id']} {rid} 非穿墙候选={nc}/{tot}")

print(f"严格穿墙房间边总数={total_cross}, 不可约(0候选)={irreducible}, 可修(有候选但漏选)={fixable}")
