# -*- coding: utf-8 -*-
"""需求⑥：非卫生间封闭房间 TR 必须与房间「所有」swing/fire 门都有拓扑边。

修复方式（几何门贴墙为权威，保证不穿墙）：
1. 对每个 swing/fire 几何门，找其「贴墙(<0.6m)的封闭房间」集合。
2. 若该房间 TR 尚未连到该门对应 hub（TD 或经 TDX）：
   - 若 hub 已有多房间 TDX 结构（或门贴多房间墙）→ 为房间生成/复用 TDX 子节点，
     连 room→TDX→hub（TDX 落在房间墙上、质心可见不穿墙）。
   - 若 hub 是单房间门且质心→hub 直线不穿墙 → 直接连 room→hub。
3. 门类型纠偏：几何门是 swing/fire 而拓扑标 opening 时（类型漂移），提拔为几何门类型。
4. 同步更新 hub 的 rooms 字段（加入新房间），保持详情一致性。

用法: python debug/fix_room_all_doors.py [geojson]
"""
from __future__ import annotations
import argparse, json, math, sys
from pathlib import Path
from shapely.geometry import shape, Point, LineString, Polygon
from shapely.strtree import STRtree

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE / "src"))

OPEN = {"corridor", "lobby", "activity", "atrium", "elevator_lobby", "stair_lobby", "staircase"}
WALL_TOL = 0.6
TD_TOL = 2.5
BLIND_WALK_SPEED = 0.8


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
    bak = p.with_name(p.stem + "_before_alldoors" + p.suffix)
    bak.write_text(json.dumps(geo, ensure_ascii=False), encoding="utf-8")
    print("备份 ->", bak)

    for fk in sorted(geo["floors"].keys(), key=lambda x: int(x)):
        fd = geo["floors"][fk]
        nodes = fd["topology"]["nodes"]
        edges = fd["topology"]["edges"]
        nmap = {n["id"]: n for n in nodes}

        # 封闭房间（非卫生间）
        closed = {}
        for n in nodes:
            if n["type"] == "room" and n.get("roomId"):
                rt = room_type_of(fd, n["roomId"])
                if rt is not None and rt not in OPEN and rt != "toilet":
                    closed[n["roomId"]] = n["id"]

        room_poly = {}
        room_centroid = {}
        for r in fd["geometry"].get("rooms", []):
            try:
                room_poly[r["id"]] = shape(r["geometry"])
                room_centroid[r["id"]] = r["geometry"]["coordinates"][0] if False else None
            except Exception: pass
        for n in nodes:
            if n["type"] == "room" and n.get("roomId"):
                room_centroid[n["roomId"]] = n["coordinates"]

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
        # TDX → hub
        tdx_hub = {}
        for e in edges:
            a, b = nmap.get(e["from"]), nmap.get(e["to"])
            if not a or not b: continue
            if a["type"] == "doorway" and b["type"] == "doorway":
                ta, tb = a["id"], b["id"]
                if "TDX" in ta and "TDX" not in tb: tdx_hub[ta] = tb
                elif "TDX" in tb and "TDX" not in ta: tdx_hub[tb] = ta

        # 房间已连 hub 集合
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

        stats = {"edge_add": 0, "sub_new": 0, "sub_reuse": 0, "promote": 0}

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

        # 为某房间生成/复用 TDX（贴房间墙、质心可见）
        def ensure_tdx(rid, hub_id, hub_dt):
            nonlocal sub_counter
            # 已有该房间 TDX（任意位置）→ 复用；但若它已连其它 hub 则不能复用
            # （一个 TDX 只能归属一个 hub），需新建。
            for nid, nn in door_nodes.items():
                if "TDX" in nid and (nn.get("rooms") or [None])[0] == rid:
                    own_hub = tdx_hub.get(nid)
                    if own_hub is None or own_hub == hub_id:
                        return nid
            # 生成
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
            tdx_hub[sub_id] = hub_id   # 登记归属，避免复用逻辑误判
            stats["sub_new"] += 1
            return sub_id

        # 门类型纠偏：几何门 swing/fire 但拓扑 opening（类型漂移）。
        # 判据：拓扑门坐标就近(<TD_TOL)存在 swing/fire 几何门，且该几何门贴墙
        # 至少一个封闭房间 → 物理开口确为普通门/防火门，拓扑类型跟随几何。
        geo_swing_fire = []
        for d in fd["geometry"].get("doors", []):
            pr = d.get("properties", {})
            if pr.get("doorType") in ("swing", "fire"):
                geo_swing_fire.append((d["id"], pr.get("doorType"),
                                       tuple(d["geometry"]["coordinates"])))
        for nid, nn in door_nodes.items():
            if nn.get("doorType") != "opening":
                continue
            coords = nn["coordinates"]
            for gid, gdt, gc in geo_swing_fire:
                if math.hypot(coords[0]-gc[0], coords[1]-gc[1]) < TD_TOL:
                    # 该几何门是否贴墙封闭房间
                    pt = Point(gc)
                    touches_closed = any(rid in room_poly and
                                         room_poly[rid].boundary.distance(pt) < WALL_TOL
                                         for rid in closed)
                    if touches_closed:
                        nn["doorType"] = gdt
                        stats["promote"] += 1
                    break

        # 遍历几何门 → 贴墙封闭房间 → 补边
        for d in fd["geometry"].get("doors", []):
            pr = d.get("properties", {})
            coords = d.get("geometry", {}).get("coordinates")
            if not coords or pr.get("doorType") not in ("swing", "fire"):
                continue
            pt = Point(coords)
            owners = [rid for rid in closed if rid in room_poly
                      and room_poly[rid].boundary.distance(pt) < WALL_TOL]
            if not owners:
                continue
            # 就近 TD（归一化 TDX→hub）
            best, bd = None, 1e9
            for nid, nn in door_nodes.items():
                c = nn["coordinates"]
                dist = math.hypot(coords[0]-c[0], coords[1]-c[1])
                if dist < bd: bd, best = dist, nid
            if best is None or bd >= TD_TOL:
                continue
            hub_id = tdx_hub.get(best, best)
            hub = nmap[hub_id]
            hub_dt = hub.get("doorType", "swing")
            for rid in owners:
                if hub_id in room_edges.get(rid, set()):
                    continue
                # 单房间门（hub 无 TDX 且只服务此房间）→ 尝试直连
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
                if direct_ok:
                    trid = closed[rid]
                    edges.append(mk_edge(trid, hub_id,
                                         acc=2 if hub_dt == "fire" else 0,
                                         rl=5 if hub_dt == "fire" else 0.5))
                    stats["edge_add"] += 1
                else:
                    sub_id = ensure_tdx(rid, hub_id, hub_dt)
                    trid = closed[rid]
                    # 幂等：room→sub 边已存在则跳过
                    if not any((e["from"] == trid and e["to"] == sub_id) or
                               (e["to"] == trid and e["from"] == sub_id) for e in edges):
                        edges.append(mk_edge(trid, sub_id,
                                             acc=2 if hub_dt == "fire" else 0,
                                             rl=5 if hub_dt == "fire" else 0.5))
                    # sub→hub（若不存在）
                    if not any((e["from"] == sub_id and e["to"] == hub_id) or
                               (e["to"] == sub_id and e["from"] == hub_id) for e in edges):
                        edges.append(mk_edge(sub_id, hub_id,
                                             acc=2 if hub_dt == "fire" else 0,
                                             rl=5 if hub_dt == "fire" else 0.5))
                    stats["edge_add"] += 1
                room_edges.setdefault(rid, set()).add(hub_id)
                # hub rooms 字段同步
                if rid not in (hub.get("rooms") or []):
                    hub["rooms"] = list(hub.get("rooms") or []) + [rid]

        print(f"F{fk}: 补边={stats['edge_add']} 新TDX={stats['sub_new']} "
              f"复用TDX={stats['sub_reuse']} 类型提拔={stats['promote']}")

    p.write_text(json.dumps(geo, ensure_ascii=False, indent=2), encoding="utf-8")
    print("已写回:", p)


if __name__ == "__main__":
    main()
