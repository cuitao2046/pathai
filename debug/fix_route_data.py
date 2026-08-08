# -*- coding: utf-8 -*-
"""修复路径穿墙的三类数据问题：
A. TR→TD 错误挂接（音乐教室孤儿门）：TD 离本房间边界 0.5~6m → 吸附到边界最近点；
    >6m → 删除该 TR→TD 边。吸附后重算所有以该 TD 为端点的边。
B. 越界 TI（不在 walkable 内，如楼梯间内部的 1-TI-022）：吸附到 walkable 最近点，
    重算该 TI 所有边。
C. 骨架线穿墙复核。
用法: python debug/fix_route_data.py result/school_building_01_map_v9.geojson
"""
import json
import math
import sys

from shapely.geometry import Point, LineString
from shapely.ops import nearest_points

CLOSED = {
    "classroom", "lab", "office", "meeting", "toilet", "storage",
    "equipment", "library", "medical", "shaft", "staircase",
    "elevator_hall", "room", "reception", "counseling",
}


def collect_rooms(fl):
    rooms = []
    for r in (fl.get("geometry") or {}).get("rooms") or []:
        pr = r.get("properties") or {}
        g = r.get("geometry") or {}
        if g.get("type") != "Polygon":
            continue
        c = g.get("coordinates")
        if not c or len(c[0]) < 3:
            continue
        try:
            p = Polygon(c[0])
        except Exception:
            continue
        if not p.is_valid or p.area < 0.5:
            continue
        rooms.append((r.get("id"), pr.get("roomType") or "",
                      pr.get("label") or "", p))
    return rooms


def collect_walkable(fl):
    polys = []
    wr = fl.get("walkable_regions") or {}
    feats = wr.get("features") if isinstance(wr, dict) else None
    if feats:
        for f in feats:
            g = f.get("geometry") or {}
            if g.get("type") != "Polygon":
                continue
            c = g.get("coordinates")
            if not c or len(c[0]) < 3:
                continue
            try:
                p = Polygon(c[0])
                if p.is_valid and p.area > 1.0:
                    polys.append(p)
            except Exception:
                continue
    if not polys:
        return None
    from shapely.ops import unary_union
    try:
        return unary_union(polys)
    except Exception:
        return None


def main(path):
    geo = json.load(open(path, encoding="utf-8"))
    for fk, fl in (geo.get("floors") or {}).items():
        rooms = collect_rooms(fl)
        W = collect_walkable(fl)
        nodes = fl.get("topology") or {}
        nlist = nodes.get("nodes") or []
        elist = nodes.get("edges") or []
        nmap = {n["id"]: n for n in nlist}

        moved_td = set()   # TD id -> 新坐标
        removed_tr_td = []  # (edge_id, tr, td, reason)
        moved_ti = {}

        # ---------- A. TR→TD 挂接修复 ----------
        for n in nlist:
            if n.get("type") != "room":
                continue
            tr_id = n["id"]
            # TR 所在房间多边形
            tr_pt = Point(n["coordinates"])
            room = None
            for rid, rt, lab, p in rooms:
                if p.contains(tr_pt):
                    room = (rid, rt, p)
                    break
            if room is None:
                continue
            rid, rt, p = room
            # TR 的所有 TD 邻居
            for e in elist:
                if e["from"] == tr_id or e["to"] == tr_id:
                    td_id = e["to"] if e["from"] == tr_id else e["from"]
                    tn = nmap.get(td_id)
                    if not tn or tn.get("type") != "doorway":
                        continue
                    td_pt = Point(tn["coordinates"])
                    d = td_pt.distance(p.exterior)
                    if d <= 0.5:
                        continue  # 正常门口
                    if d <= 6.0:
                        # 吸附到房间边界最近点
                        np_pt = nearest_points(p.exterior, td_pt)[0]
                        tn["coordinates"] = [round(np_pt.x, 3), round(np_pt.y, 3)]
                        moved_td.add(td_id)
                        print(f"[F{fk}] {td_id} 吸附到 {rid} 边界 "
                              f"({np_pt.x:.2f},{np_pt.y:.2f})，原距 {d:.2f}m")
                    else:
                        removed_tr_td.append((e["id"], tr_id, td_id, d))
                        print(f"[F{fk}] 删除 {e['id']} ({tr_id}→{td_id})，"
                              f"TD 距 {rid} 边界 {d:.1f}m (>6m)")

        # 删除 >6m 的错误 TR→TD 边
        if removed_tr_td:
            drop = {x[0] for x in removed_tr_td}
            elist = [e for e in elist if e["id"] not in drop]

        # ---------- B. 越界 TI 吸附到 walkable ----------
        if W is not None:
            for n in nlist:
                if n.get("type") != "intersection":
                    continue
                pt = Point(n["coordinates"])
                if W.contains(pt) or W.buffer(-0.02).contains(pt):
                    continue
                # 若在封闭房间内部则吸附；在 walkable 外的缝里也吸附
                np_pt = nearest_points(W, pt)[0]
                if pt.distance(np_pt) < 0.15:
                    continue
                n["coordinates"] = [round(np_pt.x, 3), round(np_pt.y, 3)]
                moved_ti[n["id"]] = (pt.distance(np_pt), (np_pt.x, np_pt.y))
                print(f"[F{fk}] TI {n['id']} 吸附到 walkable "
                      f"({np_pt.x:.2f},{np_pt.y:.2f})，移 {pt.distance(np_pt):.2f}m")

        # ---------- 重算受影响边距离 ----------
        changed = moved_td | set(moved_ti.keys())
        for e in elist:
            if e["from"] not in nmap or e["to"] not in nmap:
                continue
            if e["from"] not in changed and e["to"] not in changed:
                continue
            a = nmap[e["from"]]["coordinates"]
            b = nmap[e["to"]]["coordinates"]
            d = math.hypot(a[0] - b[0], a[1] - b[1])
            e["distance"] = round(d, 3)
            e["estimatedTime"] = round(d / 0.8, 2)

        nodes["nodes"] = nlist
        nodes["edges"] = elist

        # ---------- C. 骨架线穿墙复核 ----------
        from shapely.geometry import shape as sh_shape
        skel_bad = []
        for f in (fl.get("skeleton") or {}).get("features") or []:
            g = f.get("geometry") or {}
            if g.get("type") != "LineString":
                continue
            line = sh_shape(g)
            for rid, rt, lab, p in rooms:
                if line.intersects(p.buffer(-0.08)):
                    skel_bad.append((f.get("id"), rid, rt, lab))
                    break
        print(f"[F{fk}] 骨架穿墙: {skel_bad if skel_bad else '无'}")

    out = path
    json.dump(geo, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print("已写回:", out)


if __name__ == "__main__":
    from shapely.geometry import Polygon
    main(sys.argv[1] if len(sys.argv) > 1 else
         "result/school_building_01_map_v9.geojson")
