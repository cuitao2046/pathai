# -*- coding: utf-8 -*-
import json, sys
from pathlib import Path
from shapely.geometry import shape, LineString, Polygon
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

single = 0; multi = 0
single_examples = []
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
    # 每 TD 连接的房间数
    td_roomcount = {}
    for e in fd["topology"]["edges"]:
        a = nmap.get(e["from"]); b = nmap.get(e["to"])
        if not a or not b: continue
        if a["type"] == "doorway" and b["type"] == "room": td_roomcount.setdefault(a["id"], set()).add(b.get("roomId"))
        elif b["type"] == "doorway" and a["type"] == "room": td_roomcount.setdefault(b["id"], set()).add(a.get("roomId"))
    for e in fd["topology"]["edges"]:
        a = nmap.get(e["from"]); b = nmap.get(e["to"])
        if not a or not b: continue
        if not ({a["type"], b["type"]} == {"room", "doorway"}): continue
        rn = a if a["type"] == "room" else b
        dn = b if a["type"] == "room" else a
        rid = rn.get("roomId")
        if rid not in room_poly: continue
        seg = LineString([rn["coordinates"], dn["coordinates"]])
        cross = any(scw(rn["coordinates"], dn["coordinates"], w.coords[0], w.coords[-1]) for w in qw(seg.bounds))
        if not cross: continue
        cnt = len(td_roomcount.get(dn["id"], set()))
        if cnt <= 1:
            single += 1
            if len(single_examples) < 10:
                single_examples.append((fk, e["id"], rid, dn["id"], cnt))
        else:
            multi += 1

print(f"严格穿墙的房间边: 单房间门(TD只连≤1房间)={single}, 多房间门(TD连≥2房间)={multi}")
print("单房间门却穿墙示例(应为0, 若有则算法有bug):")
for t in single_examples:
    print("  ", t)
