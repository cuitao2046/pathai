# -*- coding: utf-8 -*-
"""孤儿门修复：所有封闭房间的门都必须附属到本房间 TR。

规则：
- 对每个无 TR 邻居的 doorway(TD)：
  - 找最近封闭房间(CLOSED，含 staircase)：
    - 距离 <= 4.0m → 吸附 TD 到该房间边界最近点 + 确保 TD→TR 边存在
      （房间无 TR 节点则创建，坐标=房间质心，type=room）
    - 距离 > 4.0m → 该门属于公共空间(门厅/走廊)，保持连接走廊 TI，
      不做 TR 附属（附属会产生穿墙长边）
- 所有受影响的边重算 distance/estimatedTime。
用法: python debug/fix_orphan_doors.py <geojson>
"""
import json
import math
import sys

from shapely.geometry import Point, Polygon, LineString
from shapely.ops import nearest_points

CLOSED = {
    "classroom", "lab", "office", "meeting", "toilet", "storage",
    "equipment", "library", "medical", "shaft", "staircase",
    "elevator_hall", "room", "reception", "counseling",
}

ADOPT_DIST = 4.0  # 吸附补连的最大距离


def load_rooms(fl):
    out = []
    for r in (fl.get("geometry") or {}).get("rooms") or []:
        pr = r.get("properties") or {}
        rt = pr.get("roomType") or ""
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
        out.append((r.get("id"), rt, pr.get("label") or "", p))
    return out


def main(path):
    geo = json.load(open(path, encoding="utf-8"))
    for fk, fl in (geo.get("floors") or {}).items():
        rooms = load_rooms(fl)
        closed = [(rid, rt, lab, p) for rid, rt, lab, p in rooms if rt in CLOSED]
        nlist = fl["topology"]["nodes"]
        elist = fl["topology"]["edges"]
        nmap = {n["id"]: n for n in nlist}
        tmap = {n["id"]: n.get("type") for n in nlist}
        from collections import defaultdict
        nbrs = defaultdict(set)
        for e in elist:
            if e["from"] in nmap and e["to"] in nmap:
                nbrs[e["from"]].add(e["to"])
                nbrs[e["to"]].add(e["from"])

        tr_by_room = {}
        for n in nlist:
            if n.get("type") == "room":
                for rid, rt, lab, p in closed:
                    if p.contains(Point(n["coordinates"])):
                        tr_by_room.setdefault(rid, []).append(n["id"])

        added_edges = []
        created_tr = []
        adopted = []
        kept_public = []
        seq = max([int(e["id"].split("-")[-1]) for e in elist
                   if e.get("id", "").endswith("-HC-0")
                   or e["id"].rsplit("-", 1)[-1].isdigit()] + [0]) if False else \
            max([int(e["id"].rsplit("-", 1)[-1]) for e in elist
                 if e.get("id", "").rsplit("-", 1)[-1].isdigit()] + [900])

        for n in nlist:
            if n.get("type") != "doorway":
                continue
            td_id = n["id"]
            if any(tmap.get(x) == "room" for x in nbrs.get(td_id, ())):
                continue  # 已有 TR 归属
            pt = Point(n["coordinates"])
            # 最近封闭房间
            best = None
            for rid, rt, lab, p in closed:
                d = p.exterior.distance(pt)
                if best is None or d < best[0]:
                    best = (d, rid, rt, lab, p)
            if best is None or best[0] > ADOPT_DIST:
                kept_public.append(td_id)
                continue
            d, rid, rt, lab, p = best
            # 吸附到房间边界
            np_pt = nearest_points(p.exterior, pt)[0]
            n["coordinates"] = [round(np_pt.x, 3), round(np_pt.y, 3)]
            # 确保房间有 TR
            tr_id = (tr_by_room.get(rid) or [None])[0]
            if tr_id is None:
                cx, cy = p.centroid.coords[0]
                tr_id = f"{fk}-TR-{int(rid.rsplit('-', 1)[-1]):04d}"
                nlist.append({
                    "id": tr_id, "type": "room", "floor": fk,
                    "coordinates": [round(cx, 3), round(cy, 3)],
                    "label": lab or rid, "public": False, "accessible": True,
                    "riskLevel": 0.5,
                })
                tr_by_room[rid] = [tr_id]
                created_tr.append(tr_id)
            # 补 TD→TR 边
            a = nmap[td_id]["coordinates"]
            b = nmap[tr_id]["coordinates"]
            dd = math.hypot(a[0] - b[0], a[1] - b[1])
            seq += 1
            eid = f"{fk}-TE-HC-{seq:04d}"
            elist.append({
                "id": eid, "from": td_id, "to": tr_id,
                "distance": round(dd, 3),
                "estimatedTime": round(dd / 0.8, 2),
                "accessibilityLevel": 0, "riskLevel": 0.5,
                "walkable": True, "wheelchairAccessible": True,
                "blindAccessible": True,
            })
            added_edges.append((eid, td_id, tr_id))
            adopted.append((td_id, rid, round(d, 2)))
            nbrs[td_id].add(tr_id)
            nbrs[tr_id].add(td_id)

        # 重算受影响边距离（TD 移动过的）
        moved_td = {x[0] for x in adopted}
        for e in elist:
            if e["from"] not in nmap or e["to"] not in nmap:
                continue
            if e["from"] not in moved_td and e["to"] not in moved_td:
                continue
            a = nmap[e["from"]]["coordinates"]
            b = nmap[e["to"]]["coordinates"]
            dd = math.hypot(a[0] - b[0], a[1] - b[1])
            e["distance"] = round(dd, 3)
            e["estimatedTime"] = round(dd / 0.8, 2)

        print(f"[F{fk}] 吸附补连 {len(adopted)} 个门（创建 TR {len(created_tr)} 个，"
              f"新增边 {len(added_edges)} 条）")
        for td, rid, d0 in adopted:
            print(f"    {td} → {rid}（原距 {d0:.1f}m）")
        print(f"[F{fk}] 保持公共空间门 {len(kept_public)} 个（距封闭房间 >{ADOPT_DIST}m）")
        if created_tr:
            print(f"[F{fk}] 新建 TR: {created_tr}")

    json.dump(geo, open(path, "w", encoding="utf-8"),
              ensure_ascii=False, indent=2)
    print("已写回:", path)


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else
         "result/school_building_01_map_v9.geojson")
