# -*- coding: utf-8 -*-
"""测试：用不同「房间节点中心」(质心 / representative_point / polylabel) 重排 TD 后，
房间↔门 严格穿墙数对比。非破坏性（只打印，不写 geojson）。"""
import json, sys, math
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


def sample_ring(ring, step):
    coords = list(ring.coords); pts = []
    for i in range(len(coords) - 1):
        x0, y0 = coords[i][0], coords[i][1]; x1, y1 = coords[i+1][0], coords[i+1][1]
        seglen = math.hypot(x1-x0, y1-y0)
        if seglen < 1e-9: continue
        n = max(1, int(seglen/step))
        for k in range(n+1):
            f = k/n; pts.append((x0+(x1-x0)*f, y0+(y1-y0)*f))
    return pts


def polylabel(poly, tolerance=0.1):
    """近似 pole of inaccessibility：网格扫描取「到边界最小距离」最大点。"""
    minx, miny, maxx, maxy = poly.bounds
    best = None; best_d = -1
    step = max(0.3, (maxx-minx + maxy-miny) / 60.0)
    yy = miny
    while yy <= maxy:
        xx = minx
        while xx <= maxx:
            p = Point(xx, yy)
            if poly.contains(p):
                d = poly.exterior.distance(p)
                for interior in poly.interiors:
                    d = min(d, interior.distance(p))
                if d > best_d:
                    best_d = d; best = (xx, yy)
            xx += step
        yy += step
    return best or (poly.centroid.x, poly.centroid.y)


def run(center_fn):
    total_room = 0; room_cross = 0; corr = 0; corr_cross = 0
    for fk, fd in geo["floors"].items():
        nodes = fd["topology"]["nodes"]
        edges = fd["topology"]["edges"]
        nmap = {n["id"]: dict(n) for n in nodes}
        room_poly = {}
        for r in fd["geometry"].get("rooms", []):
            try: room_poly[r["id"]] = shape(r["geometry"])
            except Exception: pass
        # 房间中心
        center = {}
        for rid, poly in room_poly.items():
            center[rid] = center_fn(poly)
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
        # TD 房间集合
        td_rooms = {}
        for e in edges:
            a = nmap.get(e["from"]); b = nmap.get(e["to"])
            if not a or not b: continue
            if a["type"] == "doorway" and b["type"] == "room": td_rooms.setdefault(a["id"], set()).add(b.get("roomId"))
            elif b["type"] == "doorway" and a["type"] == "room": td_rooms.setdefault(b["id"], set()).add(a.get("roomId"))
        for n in nodes:
            if n["type"] != "doorway": continue
            rset = set(td_rooms.get(n["id"], set()))
            for rid in (n.get("rooms") or []):
                if rid in center: rset.add(rid)
            rset = {rid for rid in rset if rid in room_poly and rid in center}
            if not rset: continue
            cands = {}
            for rid in rset:
                poly = room_poly[rid]
                for ring in [poly.exterior] + list(poly.interiors):
                    for pt in sample_ring(ring, 0.3):
                        cands[(round(pt[0],2), round(pt[1],2))] = pt
            cur = tuple(n["coordinates"])
            def is_valid(P):
                for rid in rset:
                    c = center[rid]; poly = room_poly[rid]
                    seg = LineString([c, P])
                    if not poly.covers(seg): return False
                    for w in qw(seg.bounds):
                        wc = w.coords
                        if seg_crosses_wall_strict(c, P, wc[0], wc[-1]): return False
                return True
            valid = []
            for P in cands.values():
                if is_valid(P): valid.append((math.hypot(P[0]-cur[0], P[1]-cur[1]), P))
            if valid:
                valid.sort(key=lambda x: x[0]); best = valid[0][1]
            else:
                best = cur
            nmap[n["id"]]["coordinates"] = [best[0], best[1]]
        # 验证
        for e in edges:
            a = nmap.get(e["from"]); b = nmap.get(e["to"])
            if not a or not b: continue
            if {a["type"], b["type"]} == {"room", "doorway"}:
                total_room += 1
                rn = a if a["type"] == "room" else b
                dn = b if a["type"] == "room" else a
                rid = rn.get("roomId")
                if rid not in room_poly: continue
                seg = LineString([rn["coordinates"], dn["coordinates"]])
                for w in qw(seg.bounds):
                    wc = w.coords
                    if seg_crosses_wall_strict(rn["coordinates"], dn["coordinates"], wc[0], wc[-1]):
                        room_cross += 1; break
            else:
                corr += 1
                if a["type"] in ("doorway","facility","facility_entrance") or b["type"] in ("doorway","facility","facility_entrance"): continue
                seg = LineString([a["coordinates"], b["coordinates"]])
                for w in qw(seg.bounds):
                    wc = w.coords
                    if seg_crosses_wall_strict(a["coordinates"], b["coordinates"], wc[0], wc[-1]):
                        corr_cross += 1; break
    return total_room, room_cross, corr, corr_cross


for name, fn in [("centroid", lambda p: (p.centroid.x, p.centroid.y)),
                 ("representative", lambda p: (p.representative_point().x, p.representative_point().y)),
                 ("polylabel", lambda p: polylabel(p))]:
    tr, rc, c, cc = run(fn)
    print(f"{name:14s}: 房间边={tr} 严格穿墙={rc} | 走廊边={c} 严格穿墙={cc}")
