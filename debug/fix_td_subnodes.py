# -*- coding: utf-8 -*-
"""需求⑤(c) 彻底修复：对「一扇门服务于多个房间」的情况，门节点不再用单个共享点，
而是为每个归属房间生成一枚「门子节点」(doorway sub-node)，落在「该房间自身墙面」上、
且从房间质心可见(不穿墙/优先落在房间多边形内)。

拓扑改为：  room_r ── sub_r ── hub(TD，保留原有走廊/设施边)
- sub_r 在 r 的墙面，room_r→sub_r 必然不穿墙；
- sub_r→hub 为门口通道(两枚节点都在门口附近，短线不穿墙)；
- hub 保留原 TD 的全部 TD↔TI / TD↔TF 走廊连接，公共空间连通性不变。

单房间门(TD 只服务 1 个房间) 已无穿墙，保持不变。

用法: python debug/fix_td_subnodes.py [geojson]
"""
from __future__ import annotations
import argparse, json, math, sys
from pathlib import Path
from shapely.geometry import shape, Point, LineString, Polygon
from shapely.strtree import STRtree

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE / "src"))


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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("geojson", nargs="?",
                    default=str(BASE / "result" / "school_building_01_map_v9.geojson"))
    args = ap.parse_args()
    p = Path(args.geojson)
    geo = json.loads(p.read_text(encoding="utf-8"))
    bak = p.with_name(p.stem + "_before_subnode" + p.suffix)
    bak.write_text(json.dumps(geo, ensure_ascii=False), encoding="utf-8")
    print("备份 ->", bak)

    sub_counter = 0
    edge_counter = 0
    for fk in sorted(geo["floors"].keys(), key=lambda x: int(x)):
        fd = geo["floors"][fk]
        nodes = fd["topology"]["nodes"]
        edges = fd["topology"]["edges"]
        nmap = {n["id"]: n for n in nodes}

        room_poly = {}
        room_centroid = {}
        for n in nodes:
            if n["type"] == "room" and n.get("roomId"):
                room_centroid[n["roomId"]] = n["coordinates"]
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

        # 每扇门连接的房间（边 + td.rooms 并集）
        td_rooms = {}
        for e in edges:
            a = nmap.get(e["from"]); b = nmap.get(e["to"])
            if not a or not b: continue
            if a["type"] == "doorway" and b["type"] == "room":
                td_rooms.setdefault(a["id"], set()).add(b.get("roomId"))
            elif b["type"] == "doorway" and a["type"] == "room":
                td_rooms.setdefault(b["id"], set()).add(a.get("roomId"))

        # ---- Pass 1: 为每个多房间门生成子节点，记录 hub -> {room: sub_id} ----
        hub_subs = {}     # hub id -> {room_id: sub_node_dict}
        extra_nodes = []
        stats = {"split": 0, "single": 0}
        for n in nodes:
            if n["type"] != "doorway":
                continue
            rset = set(td_rooms.get(n["id"], set()))
            for rid in (n.get("rooms") or []):
                if rid in room_centroid: rset.add(rid)
            rset = {rid for rid in rset if rid in room_poly and rid in room_centroid}
            if len(rset) <= 1:
                stats["single"] += 1
                continue
            stats["split"] += 1
            hub = tuple(n["coordinates"])
            subs = {}
            for rid in sorted(rset):
                c = room_centroid[rid]
                poly = room_poly[rid]
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
                    cost = (0 if contained else 1,
                            math.hypot(P[0]-hub[0], P[1]-hub[1]))
                    if best is None or cost < best[0]:
                        best = (cost, P)
                if best is None:
                    bpt = poly.boundary.interpolate(poly.boundary.project(Point(hub)))
                    best = ((1, 0), (bpt.x, bpt.y))
                P = best[1]
                # 轻微内移避免与 hub 重合（门口通道段）
                dx_, dy_ = c[0]-P[0], c[1]-P[1]
                L = math.hypot(dx_, dy_)
                if L > 1e-6:
                    P = (P[0] + dx_/L*0.1, P[1] + dy_/L*0.1)
                sub_counter += 1
                sub_id = f"{fk}-TDX-{sub_counter:04d}"
                sub = {
                    "id": sub_id, "type": "doorway",
                    "coordinates": [round(P[0],3), round(P[1],3)],
                    "doorType": n.get("doorType"),
                    "rooms": [rid],
                    "label": n.get("label"),
                }
                extra_nodes.append(sub)
                subs[rid] = sub_id
            hub_subs[n["id"]] = (hub, subs)

        # 子节点也纳入 nmap（Pass 2 需要查坐标）
        for sn in extra_nodes:
            nmap[sn["id"]] = sn

        # ---- Pass 2: 重写边 ----
        new_edges = []
        split_hub_ids = set(hub_subs.keys())
        for e in edges:
            a = nmap.get(e["from"]); b = nmap.get(e["to"])
            if not a or not b:
                new_edges.append(e); continue
            # 找到涉及「被拆分 hub」且另一端是房间的边
            hub_id = None
            if a["id"] in split_hub_ids and b["type"] == "room":
                hub_id = a["id"]; room_node = b
            elif b["id"] in split_hub_ids and a["type"] == "room":
                hub_id = b["id"]; room_node = a
            if hub_id is None:
                new_edges.append(e); continue
            rid = room_node.get("roomId")
            hub, subs = hub_subs[hub_id]
            if rid not in subs:
                new_edges.append(e); continue
            sub_id = subs[rid]
            # 继承原边可达性属性
            acc = e.get("accessibilityLevel", 0)
            blind = e.get("blindAccessible", True)
            wheel = e.get("wheelchairAccessible", True)
            d1 = round(math.hypot(room_node["coordinates"][0] - nmap[sub_id]["coordinates"][0],
                                  room_node["coordinates"][1] - nmap[sub_id]["coordinates"][1]), 2)
            edge_counter += 1
            new_edges.append({
                "id": f"E{edge_counter:06d}-a", "from": room_node["id"],
                "to": sub_id, "distance": d1, "estimatedTime": round(d1/0.8, 1),
                "accessibilityLevel": acc, "blindAccessible": blind,
                "wheelchairAccessible": wheel, "crossFloor": False,
            })
            d2 = round(math.hypot(nmap[sub_id]["coordinates"][0] - hub[0],
                                  nmap[sub_id]["coordinates"][1] - hub[1]), 2)
            edge_counter += 1
            new_edges.append({
                "id": f"E{edge_counter:06d}-b", "from": sub_id, "to": hub_id,
                "distance": d2, "estimatedTime": round(d2/0.8, 1),
                "accessibilityLevel": acc, "blindAccessible": blind,
                "wheelchairAccessible": wheel, "crossFloor": False,
            })

        fd["topology"]["nodes"] = nodes + extra_nodes
        fd["topology"]["edges"] = new_edges
        print(f"F{fk}: 多房间门拆分={stats['split']} 单房间门保留={stats['single']} "
              f"新增子节点={len(extra_nodes)}")

    p.write_text(json.dumps(geo, ensure_ascii=False, indent=2), encoding="utf-8")
    print("已写回:", p)


if __name__ == "__main__":
    main()
