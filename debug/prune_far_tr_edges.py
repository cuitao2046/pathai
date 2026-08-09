# -*- coding: utf-8 -*-
"""修剪 1:1 重构产生的过长穿墙 TR↔TD 边（来自 rooms 字段漂移）。

策略：
- 对每条 TR↔TD 边：若房间质心与门距离 > 8m 且边真正穿墙 → 删除
- 房间若因此失联（无任何 TD 边且不在开放空间），兜底连最近 TD

用法: python debug/prune_far_tr_edges.py [geojson]
"""
from __future__ import annotations
import argparse, json, math, sys
from pathlib import Path
from shapely.geometry import shape, Point

BASE = Path(__file__).resolve().parent.parent
MAX_M = 8.0
OPENP = {"corridor", "lobby", "activity", "atrium", "elevator_lobby",
         "stair_lobby", "staircase", "infrastructure", "elevator_hall"}


def side(px, py, ax, ay, bx, by):
    return (bx - ax) * (py - ay) - (by - ay) * (px - ax)


def seg_crosses(p1, p2, A, B):
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
    bak = p.with_name(p.stem + "_before_prune" + p.suffix)
    bak.write_text(json.dumps(geo, ensure_ascii=False), encoding="utf-8")
    print("备份 ->", bak)

    for fk in sorted(geo["floors"].keys(), key=lambda x: int(x)):
        fd = geo["floors"][fk]
        nodes = fd["topology"]["nodes"]
        edges = fd["topology"]["edges"]
        nmap = {n["id"]: n for n in nodes}
        room_poly = {r["id"]: shape(r["geometry"])
                     for r in fd["geometry"].get("rooms", [])}
        walls = [(tuple(w["geometry"]["coordinates"][0]),
                  tuple(w["geometry"]["coordinates"][-1]))
                 for w in fd["geometry"].get("walls", [])
                 if w.get("geometry", {}).get("type") == "LineString"]
        tds = [n for n in nodes if n["type"] == "doorway"]

        # 修剪：过长且穿墙的 TR↔TD
        keep, pruned = [], 0
        tr_kept_hubs = {}  # roomId -> set(hub TD ids)
        for e in edges:
            a, b = e["from"], e["to"]
            # 判定 TR↔TD：一边是 room 一边是 doorway
            types = (nmap.get(a, {}).get("type"), nmap.get(b, {}).get("type"))
            if not ("room" in types and "doorway" in types):
                keep.append(e); continue
            if a in nmap and nmap[a]["type"] == "room":
                tr_id, td_id = a, b
            else:
                tr_id, td_id = b, a
            tr_node = nmap[tr_id]
            td_node = nmap[td_id]
            # 距离检查
            d = e["distance"]
            # 穿墙检查
            cross = any(seg_crosses(tr_node["coordinates"], td_node["coordinates"], A, B)
                        for A, B in walls)
            if d > MAX_M and cross:
                pruned += 1
                continue
            keep.append(e)
            rid = tr_node.get("roomId")
            if rid:
                tr_kept_hubs.setdefault(rid, set()).add(td_id)

        # 兜底：因修剪失联的封闭房间 → 连最近 TD
        room_edges = {}
        for e in keep:
            a, b = nmap.get(e["from"], {}), nmap.get(e["to"], {})
            if a.get("type") == "room":
                room_edges.setdefault(a.get("roomId"), set()).add(e["to"])
            elif b.get("type") == "room":
                room_edges.setdefault(b.get("roomId"), set()).add(e["from"])
        edge_counter = max([int(e["id"].split("E")[1].split("-")[0]) for e in keep
                            if e["id"].startswith("E") and e["id"][1].isdigit()], default=0)
        def mk_edge(frm, to):
            nonlocal edge_counter
            c1 = nmap[frm]["coordinates"]; c2 = nmap[to]["coordinates"]
            d = round(math.hypot(c1[0]-c2[0], c1[1]-c2[1]), 2)
            edge_counter += 1
            return {"id": f"E{edge_counter:06d}", "from": frm, "to": to,
                    "distance": d, "estimatedTime": round(d/0.8, 1),
                    "accessibilityLevel": 0, "riskLevel": 0.5,
                    "walkable": True, "wheelchairAccessible": True,
                    "blindAccessible": True, "crossFloor": False}

        fallback = 0
        for n in nodes:
            if n["type"] != "room" or not n.get("roomId"):
                continue
            rid = n["roomId"]
            if room_type_of(fd, rid) in OPENP:
                continue
            if room_edges.get(rid):
                continue
            c = n["coordinates"]
            best, bd = None, 1e9
            for td in tds:
                dd = math.hypot(c[0]-td["coordinates"][0], c[1]-td["coordinates"][1])
                if dd < bd: bd, best = dd, td["id"]
            if best:
                keep.append(mk_edge(n["id"], best))
                # 同步：兜底房间加入 TD.rooms
                td_node = nmap[best]
                if rid not in (td_node.get("rooms") or []):
                    td_node["rooms"] = list(td_node.get("rooms") or []) + [rid]
                fallback += 1

        fd["topology"]["edges"] = keep
        print(f"F{fk}: 修剪穿墙长边={pruned} 兜底={fallback}")

    p.write_text(json.dumps(geo, ensure_ascii=False, indent=2), encoding="utf-8")
    print("已写回:", p)


if __name__ == "__main__":
    main()