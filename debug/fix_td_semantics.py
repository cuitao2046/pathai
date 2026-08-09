# -*- coding: utf-8 -*-
"""TD 语义重建（需求：不同房间的同类拓扑节点不能合并）。

权威真相 = 几何门坐标贴墙封闭房间(<0.6m)（不信任漂移的 rooms 字段）。
1. 每个 hub TD → 就近几何门(<2.5m) → 贴墙封闭房间并集 = 该 TD 权威 rooms
   （一扇门贴两房间墙 = 套间直通门，合法共享；不同房间的门不会因距离近合并）。
2. TD.rooms 重写为权威集（含开放空间标记保留原样，仅封闭房间权威化）。
3. 修剪 room↔door 边：只保留「房间 ∈ 该 TD 权威 rooms」的边（含经 TDX）。
4. 需求⑥补边：对每个贴墙封闭房间的 swing/fire 几何门，确保房间连到对应 hub。

用法: python debug/fix_td_semantics.py [geojson]
"""
from __future__ import annotations
import argparse, json, math, sys
from pathlib import Path
from shapely.geometry import shape, Point, LineString, Polygon
from shapely.strtree import STRtree

BASE = Path(__file__).resolve().parent.parent
WALL_TOL = 0.6
TD_TOL = 2.5
BLIND_WALK_SPEED = 0.8
OPENP = {"corridor", "lobby", "activity", "atrium", "elevator_lobby",
         "stair_lobby", "staircase", "infrastructure", "elevator_hall"}


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


def room_type_of(fd, rid):
    for r in fd["geometry"].get("rooms", []):
        if r["id"] == rid:
            return r.get("properties", {}).get("roomType") or r.get("type")
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("geojson", nargs="?",
                    default=str(BASE / "result" / "school_building_01_map_v9.geojson"))
    args = ap.parse_args()
    p = Path(args.geojson)
    geo = json.loads(p.read_text(encoding="utf-8"))
    bak = p.with_name(p.stem + "_before_semantics" + p.suffix)
    bak.write_text(json.dumps(geo, ensure_ascii=False), encoding="utf-8")
    print("备份 ->", bak)

    for fk in sorted(geo["floors"].keys(), key=lambda x: int(x)):
        fd = geo["floors"][fk]
        nodes = fd["topology"]["nodes"]
        edges = fd["topology"]["edges"]
        nmap = {n["id"]: n for n in nodes}

        room_poly = {}
        room_centroid = {}
        for r in fd["geometry"].get("rooms", []):
            try:
                room_poly[r["id"]] = shape(r["geometry"])
            except Exception: pass
        for n in nodes:
            if n["type"] == "room" and n.get("roomId"):
                room_centroid[n["roomId"]] = n["coordinates"]

        # 几何门：贴墙封闭房间 = 权威归属
        geo_doors = []
        for d in fd["geometry"].get("doors", []):
            pr = d.get("properties", {})
            coords = d.get("geometry", {}).get("coordinates")
            if not coords or pr.get("doorType") not in ("swing", "fire"):
                continue
            pt = Point(coords)
            wall = {rid for rid in room_poly
                    if room_type_of(fd, rid) not in OPENP
                    and room_poly[rid].boundary.distance(pt) < WALL_TOL}
            geo_doors.append({"id": d["id"], "doorType": pr.get("doorType"),
                              "coords": tuple(coords), "wall_rooms": wall})

        # TDX → hub
        tdx_hub = {}
        for e in edges:
            a, b = nmap.get(e["from"]), nmap.get(e["to"])
            if not a or not b: continue
            if a["type"] == "doorway" and b["type"] == "doorway":
                ta, tb = a["id"], b["id"]
                if "TDX" in ta and "TDX" not in tb: tdx_hub[ta] = tb
                elif "TDX" in tb and "TDX" not in ta: tdx_hub[tb] = ta

        # ---- Pass 1: hub TD 权威 rooms ----
        stats = {"hub_rebuild": 0, "hub_keep": 0, "edge_prune": 0, "edge_add": 0,
                 "tdx_drop": 0, "sub_new": 0}
        for n in nodes:
            if n["type"] != "doorway" or "TDX" in n["id"]:
                continue
            c = tuple(n["coordinates"])
            # 就近几何门（<TD_TOL），收集贴墙封闭房间并集
            truth = set()
            src = []
            for g in geo_doors:
                dd = math.hypot(c[0]-g["coords"][0], c[1]-g["coords"][1])
                if dd < TD_TOL:
                    truth |= g["wall_rooms"]
                    src.append(g["id"])
            if src:
                n["sourceDoorIds"] = sorted(set(src))
            closed_cur = {rid for rid in (n.get("rooms") or [])
                          if room_type_of(fd, rid) not in OPENP}
            if truth:
                # 重写：权威封闭房间 + 保留开放空间原样
                open_keep = [rid for rid in (n.get("rooms") or [])
                             if room_type_of(fd, rid) in OPENP]
                n["rooms"] = sorted(truth) + open_keep
                stats["hub_rebuild"] += 1
            else:
                stats["hub_keep"] += 1

        # ---- Pass 2: 修剪 room↔door 边（房间必须 ∈ hub 权威 rooms）----
        new_edges = []
        pruned = 0
        for e in edges:
            a, b = nmap.get(e["from"]), nmap.get(e["to"])
            if not a or not b:
                new_edges.append(e); continue
            rn = dn = None
            if a["type"] == "room" and b["type"] == "doorway": rn, dn = a, b
            elif b["type"] == "room" and a["type"] == "doorway": rn, dn = b, a
            if rn is None:
                new_edges.append(e); continue
            rid = rn.get("roomId")
            hid = tdx_hub.get(dn["id"], dn["id"])
            hub = nmap.get(hid)
            hub_rooms = set(hub.get("rooms") or []) if hub else set()
            # 开放空间房间（走廊等）不受约束；封闭房间必须 ∈ hub 权威 rooms
            rt = room_type_of(fd, rid)
            if rt in OPENP or rid in hub_rooms or hid == dn["id"] and hub is None:
                new_edges.append(e)
            else:
                pruned += 1
                stats["edge_prune"] += 1
        edges = new_edges
        fd["topology"]["edges"] = edges

        # ---- Pass 3: 需求⑥补边（贴墙几何门 → 房间 → hub）----
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

        door_nodes = {n["id"]: n for n in nodes if n["type"] == "doorway"}
        room_edges = {}
        for e in edges:
            a, b = nmap.get(e["from"]), nmap.get(e["to"])
            if not a or not b: continue
            rn = dn = None
            if a["type"] == "room" and b["type"] == "doorway": rn, dn = a, b
            elif b["type"] == "room" and a["type"] == "doorway": rn, dn = b, a
            if rn is None: continue
            hid = tdx_hub.get(dn["id"], dn["id"])
            room_edges.setdefault(rn.get("roomId"), set()).add(hid)

        sub_counter = max([int(n["id"].split("TDX-")[1]) for n in nodes if "TDX" in n["id"]],
                          default=0)
        edge_counter = max([int(e["id"].split("E")[1].split("-")[0]) for e in edges
                            if e["id"].startswith("E") and e["id"][1].isdigit()], default=0)

        def mk_edge(frm, to, acc=0, blind=True, wheel=True, rl=0.5):
            nonlocal edge_counter
            c1 = nmap[frm]["coordinates"]; c2 = nmap[to]["coordinates"]
            d = round(math.hypot(c1[0]-c2[0], c1[1]-c2[1]), 2)
            edge_counter += 1
            return {"id": f"E{edge_counter:06d}", "from": frm, "to": to,
                    "distance": d, "estimatedTime": round(d/BLIND_WALK_SPEED, 1),
                    "accessibilityLevel": acc, "riskLevel": rl,
                    "walkable": True, "wheelchairAccessible": wheel,
                    "blindAccessible": blind, "crossFloor": False}

        def ensure_tdx(rid, hub_id, hub_dt):
            nonlocal sub_counter
            for nid, nn in door_nodes.items():
                if "TDX" in nid and (nn.get("rooms") or [None])[0] == rid:
                    own_hub = tdx_hub.get(nid)
                    if own_hub is None or own_hub == hub_id:
                        return nid
            c = room_centroid[rid]
            poly = room_poly[rid]
            hub = nmap[hub_id]["coordinates"]
            cands = {}
            for ring in [poly.exterior] + list(poly.interiors):
                for pt in sample_ring(ring, 0.3):
                    cands[(round(pt[0],2), round(pt[1],2))] = pt
            best = None
            for P in cands.values():
                seg = LineString([c, P])
                cross = False
                for w in qw(seg.bounds):
                    wc = w.coords
                    if seg_crosses_wall_strict(c, P, wc[0], wc[-1]):
                        cross = True; break
                if cross: continue
                contained = poly.covers(seg)
                cost = (0 if contained else 1, math.hypot(P[0]-hub[0], P[1]-hub[1]))
                if best is None or cost < best[0]:
                    best = (cost, P)
            if best is None:
                bpt = poly.boundary.interpolate(poly.boundary.project(Point(hub)))
                best = ((1, 0), (bpt.x, bpt.y))
            P = best[1]
            dx_, dy_ = c[0]-P[0], c[1]-P[1]
            L = math.hypot(dx_, dy_)
            if L > 1e-6:
                P = (P[0] + dx_/L*0.1, P[1] + dy_/L*0.1)
            sub_counter += 1
            sub_id = f"{fk}-TDX-{sub_counter:04d}"
            sub = {"id": sub_id, "type": "doorway",
                   "coordinates": [round(P[0],3), round(P[1],3)],
                   "doorType": hub_dt, "rooms": [rid],
                   "label": nmap[hub_id].get("label")}
            nodes.append(sub)
            door_nodes[sub_id] = sub
            nmap[sub_id] = sub
            tdx_hub[sub_id] = hub_id
            stats["sub_new"] += 1
            return sub_id

        # 重新构建 tdx_hub（含新 TDX 登记）
        tdx_hub = {}
        for e in edges:
            a, b = nmap.get(e["from"]), nmap.get(e["to"])
            if not a or not b: continue
            if a["type"] == "doorway" and b["type"] == "doorway":
                ta, tb = a["id"], b["id"]
                if "TDX" in ta and "TDX" not in tb: tdx_hub[ta] = tb
                elif "TDX" in tb and "TDX" not in ta: tdx_hub[tb] = ta
        for nid, nn in list(door_nodes.items()):
            if "TDX" in nid and nid not in tdx_hub:
                pass

        # 补边：贴墙几何门 → 房间
        for g in geo_doors:
            if not g["wall_rooms"]:
                continue
            # 就近 hub
            best, bd = None, 1e9
            for nid, nn in door_nodes.items():
                if "TDX" in nid: continue
                cc = nn["coordinates"]
                dd = math.hypot(g["coords"][0]-cc[0], g["coords"][1]-cc[1])
                if dd < bd: bd, best = dd, nid
            if best is None or bd >= TD_TOL:
                continue
            hub_id = best
            hub = nmap[hub_id]
            hub_dt = hub.get("doorType", "swing")
            for rid in g["wall_rooms"]:
                if hub_id in room_edges.get(rid, set()):
                    continue
                subs_of_hub = [nid2 for nid2, h in tdx_hub.items() if h == hub_id]
                direct_ok = False
                if not subs_of_hub:
                    c = room_centroid[rid]
                    seg = LineString([c, hub["coordinates"]])
                    cross = False
                    for w in qw(seg.bounds):
                        wc = w.coords
                        if seg_crosses_wall_strict(c, hub["coordinates"], wc[0], wc[-1]):
                            cross = True; break
                    if not cross and room_poly[rid].covers(seg):
                        direct_ok = True
                trid = next(nn["id"] for nn in nodes
                            if nn["type"] == "room" and nn.get("roomId") == rid)
                if direct_ok:
                    edges.append(mk_edge(trid, hub_id,
                                         acc=2 if hub_dt == "fire" else 0,
                                         rl=5 if hub_dt == "fire" else 0.5))
                    stats["edge_add"] += 1
                else:
                    sub_id = ensure_tdx(rid, hub_id, hub_dt)
                    if not any((e["from"] == trid and e["to"] == sub_id) or
                               (e["to"] == trid and e["from"] == sub_id) for e in edges):
                        edges.append(mk_edge(trid, sub_id,
                                             acc=2 if hub_dt == "fire" else 0,
                                             rl=5 if hub_dt == "fire" else 0.5))
                    if not any((e["from"] == sub_id and e["to"] == hub_id) or
                               (e["to"] == sub_id and e["from"] == hub_id) for e in edges):
                        edges.append(mk_edge(sub_id, hub_id,
                                             acc=2 if hub_dt == "fire" else 0,
                                             rl=5 if hub_dt == "fire" else 0.5))
                    stats["edge_add"] += 1
                room_edges.setdefault(rid, set()).add(hub_id)
                if rid not in (hub.get("rooms") or []):
                    hub["rooms"] = list(hub.get("rooms") or []) + [rid]

        fd["topology"]["edges"] = edges
        print(f"F{fk}: hub重建={stats['hub_rebuild']} 保留={stats['hub_keep']} "
              f"边修剪={pruned} 补边={stats['edge_add']} 新TDX={stats['sub_new']}")

    p.write_text(json.dumps(geo, ensure_ascii=False, indent=2), encoding="utf-8")
    print("已写回:", p)


if __name__ == "__main__":
    main()
