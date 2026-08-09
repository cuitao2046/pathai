# -*- coding: utf-8 -*-
"""需求⑥ 最终核实：5 个缺口门的几何细节 + 质心连线穿墙判定，决定补边方式（直连 or TDX）。
"""
import json, math, sys
from pathlib import Path
from shapely.geometry import shape, Point, LineString
from shapely.strtree import STRtree

BASE = Path(__file__).resolve().parent.parent
geo = json.loads((BASE / "result" / "school_building_01_map_v9.geojson").read_text(encoding="utf-8"))

def side(px, py, ax, ay, bx, by):
    return (bx - ax) * (py - ay) - (by - ay) * (px - ax)

def seg_crosses_wall_strict(p1, p2, A, B):
    ax, ay = A[0], A[1]; bx, by = B[0], B[1]
    px, py = p1[0], p1[1]; qx, qy = p2[0], p2[1]
    dx, dy = bx - ax, by - ay
    s1 = side(px, py, ax, ay, bx, by); s2 = side(qx, qy, ax, ay, bx, by)
    if s1 == 0 and s2 == 0: return False
    if s1 == 0 or s2 == 0: return False
    if s1 * s2 > 0: return False
    if abs(dx) < 1e-12 and abs(dy) < 1e-12: return False
    ex, ey = qx - px, qy - py; det = dx * ey - dy * ex
    if abs(det) < 1e-12: return False
    u = (ex * (ay - py) - ey * (ax - px)) / det
    t = (dy * (px - ax) - dx * (py - ay)) / det
    return (-1e-9) <= t <= (1 + 1e-9) and (-1e-9) <= u <= (1 + 1e-9)

# (floor, room, hub, geom_door)
GAPS = [("1", "F1-RM-0016", "F1-TD-0018", "F1-D-0019"),
        ("1", "F1-RM-0022", "F1-TD-0048", "F1-D-0001"),
        ("1", "F1-RM-0024", "F1-TD-0048", "F1-D-0063"),
        ("2", "F2-RM-0014", "F2-TD-0020", "F2-D-0057"),
        ("2", "F2-RM-0031", "F2-TD-0015", "F2-D-0019")]

for fk, rid, hub, gid in GAPS:
    fd = geo["floors"][fk]
    nodes = fd["topology"]["nodes"]
    edges = fd["topology"]["edges"]
    nmap = {n["id"]: n for n in nodes}
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
    tree = STRtree(wall_list) if wall_list else None
    def qw(bbox, m=0.3):
        if tree is None: return []
        minx, miny, maxx, maxy = bbox
        box = Polygon([(minx-m,miny-m),(maxx+m,miny-m),(maxx+m,maxy+m),(minx-m,maxy+m)])
        return [wall_list[i] for i in tree.query(box)]
    from shapely.geometry import Polygon

    tr = next(n for n in nodes if n["type"] == "room" and n.get("roomId") == rid)
    tc = tr["coordinates"]
    hn = nmap[hub]
    hc = hn["coordinates"]
    poly = room_poly.get(rid)
    # 几何门坐标
    gd = next(d for d in fd["geometry"].get("doors", []) if d["id"] == gid)
    gc = gd["geometry"]["coordinates"]
    # 门贴墙距离
    wall_d = poly.boundary.distance(Point(gc)) if poly else None
    # 质心→TD 直线穿墙
    seg = LineString([tc, hc])
    cross = False
    for w in qw(seg.bounds):
        wc = w.coords
        if seg_crosses_wall_strict(tc, hc, wc[0], wc[-1]):
            cross = True; break
    contained = poly.covers(seg) if poly else False
    print(f"{rid} TR={tr['id']} -> {hub}(dt={hn.get('doorType')})")
    print(f"  几何门 {gid} dt={gd.get('properties',{}).get('doorType')} 坐标={gc} 距房间墙={wall_d:.2f}m")
    print(f"  TR质心={tc} TD坐标={hc} 距离={math.hypot(tc[0]-hc[0],tc[1]-hc[1]):.2f}m")
    print(f"  质心→TD 线段: 严格穿墙={cross} 落在房间内={contained}")
    # TD 贴墙房间
    tpt = Point(hc)
    tdf = [(r2, round(p2.boundary.distance(tpt), 2)) for r2, p2 in room_poly.items()
           if p2.boundary.distance(tpt) < 0.6]
    print(f"  TD贴墙房间={tdf}")
    # hub 是否已拆 TDX
    subs = [n2["id"] for n2 in nodes if n2["type"] == "doorway" and "TDX" in n2["id"]
            and (n2.get("rooms") or [None])[0] == rid
            and any((e["from"] == hub and e["to"] == n2["id"]) or (e["to"] == hub and e["from"] == n2["id"]) for e in edges)]
    print(f"  该房间已有TDX={subs}")
    print()
