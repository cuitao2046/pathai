# -*- coding: utf-8 -*-
import json, sys
from pathlib import Path
from shapely.geometry import shape, Point, LineString, Polygon
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
    ex, ey = qx - px, qy - py
    det = dx * ey - dy * ex
    if abs(det) < 1e-12: return False
    u = (ex * (ay - py) - ey * (ax - px)) / det
    t = (dy * (px - ax) - dx * (py - ay)) / det
    return (-1e-9) <= t <= (1 + 1e-9) and (-1e-9) <= u <= (1 + 1e-9)


total_room_edges = 0
room_exit = 0          # 房间↔门 线段未完全落在房间多边形内（穿墙/出界）
room_wallcross = 0     # 房间↔门 真正「严格穿墙」（两端异侧且交点在线段内，排除端点落墙）
corr_edges = 0
corr_cross = 0         # 非房间↔门 边 严格穿墙
examples = []
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
    tree = STRtree(wall_list) if wall_list else None
    def qw(bbox, m=0.3):
        if tree is None: return []
        minx, miny, maxx, maxy = bbox
        box = Polygon([(minx-m,miny-m),(maxx+m,miny-m),(maxx+m,maxy+m),(minx-m,maxy+m)])
        return [wall_list[i] for i in tree.query(box)]
    for e in fd["topology"]["edges"]:
        a = nmap.get(e["from"]); b = nmap.get(e["to"])
        if not a or not b: continue
        types = {a["type"], b["type"]}
        if types == {"room", "doorway"}:
            total_room_edges += 1
            rn = a if a["type"] == "room" else b
            dn = b if a["type"] == "room" else a
            poly = room_poly.get(rn.get("roomId"))
            if poly is None: continue
            seg = LineString([rn["coordinates"], dn["coordinates"]])
            if not seg.covered_by(poly):
                room_exit += 1
            # 严格穿墙判定
            crossed = False
            for w in qw(seg.bounds):
                wc = w.coords
                if seg_crosses_wall_strict(rn["coordinates"], dn["coordinates"], wc[0], wc[-1]):
                    crossed = True
                    break
            if crossed:
                room_wallcross += 1
                if len(examples) < 12:
                    examples.append((fk, e["id"], rn.get("roomId"), dn["id"],
                                    round(seg.length,2)))
        else:
            corr_edges += 1
            if a["type"] in ("doorway","facility","facility_entrance") or \
               b["type"] in ("doorway","facility","facility_entrance"):
                continue
            seg = LineString([a["coordinates"], b["coordinates"]])
            for w in qw(seg.bounds):
                wc = w.coords
                if seg_crosses_wall_strict(a["coordinates"], b["coordinates"], wc[0], wc[-1]):
                    corr_cross += 1
                    break

print(f"房间↔门 边总数={total_room_edges}")
print(f"  未落在房间内(出界/穿墙)={room_exit}, 其中严格穿墙={room_wallcross}")
print(f"走廊类边(排除门/设施端点)={corr_edges}, 严格穿墙={corr_cross}")
print("房间边严格穿墙示例(前12):")
for t in examples:
    print("  ", t)

