# -*- coding: utf-8 -*-
"""修复 TR→TD 归属：路径起点/终点为房间时，第一/倒数第二条边必须是对应房间的门。

对每个 TR（所在封闭房间 P）的所有 TD 邻居：
- TD 距 P 边界 <= 0.8m：OK（本房间门）
- TD 距 P 边界 > 0.8m（连了别的房间的门）：
  - 若 P 边界上有其他 TD（本房间门）→ 删除该错误边
  - 若 P 边界无任何 TD（本房间无门）→ 查 P 边界上是否有未连 TR 的 TD 可补连；
    否则记录保留（无法修复，楼梯间等特殊 case）
- 冗余楼梯间 TR（F2-TR-0024 只连楼梯间6门且与 TR-0038 重复）→ 删除边和节点
"""
import json
import math
import sys
from collections import defaultdict
from shapely.geometry import Point, Polygon

CLOSED = {"classroom", "lab", "office", "meeting", "toilet", "storage",
          "equipment", "library", "medical", "shaft", "room", "reception",
          "counseling", "staircase"}

BOUNDARY = 0.8


def main(path):
    geo = json.load(open(path, encoding="utf-8"))
    for fk, fl in (geo.get("floors") or {}).items():
        nlist = fl["topology"]["nodes"]
        elist = fl["topology"]["edges"]
        nmap = {n["id"]: n for n in nlist}
        edges_by = defaultdict(list)
        for e in elist:
            edges_by[e["from"]].append(e)
            edges_by[e["to"]].append(e)

        # 封闭房间多边形
        closed_rooms = []
        for r in fl["geometry"]["rooms"]:
            rt = (r.get("properties") or {}).get("roomType", "")
            if rt not in CLOSED:
                continue
            try:
                p = Polygon(r["geometry"]["coordinates"][0])
                if p.is_valid and p.area > 0.5:
                    closed_rooms.append((r["id"], rt,
                                         (r.get("properties") or {}).get("label", ""), p))
            except Exception:
                continue

        drop_edges = set()
        drop_nodes = set()
        fixed = []

        # TR 归属房间
        for n in nlist:
            if n.get("type") != "room":
                continue
            tr_id = n["id"]
            tr_pt = Point(n["coordinates"])
            room = next(((rid, rt, lab, p) for rid, rt, lab, p in closed_rooms
                         if p.contains(tr_pt)), None)
            if room is None:
                continue
            rid, rt, lab, p = room
            # 本房间边界上的所有 TD
            own_doors = [m2["id"] for m2 in nlist
                         if m2.get("type") == "doorway"
                         and p.exterior.distance(Point(m2["coordinates"])) <= BOUNDARY]
            # TR 的 TD 邻居
            for e in edges_by[tr_id]:
                td_id = e["to"] if e["from"] == tr_id else e["from"]
                td = nmap.get(td_id)
                if not td or td.get("type") != "doorway":
                    continue
                d = p.exterior.distance(Point(td["coordinates"]))
                if d <= BOUNDARY:
                    continue  # 本房间门 OK
                # TD 不在本房间边界 → 错误连接
                # 判断 TR 除当前错误 TD 外是否还有其他本房间门邻居
                other_own = [x for x in edges_by[tr_id]
                             if (x["to"] if x["from"] == tr_id else x["from"]) != td_id
                             and nmap.get(x["to"] if x["from"] == tr_id else x["from"])
                             and nmap[x["to"] if x["from"] == tr_id else x["from"]].get("type") == "doorway"
                             and p.exterior.distance(Point(nmap[
                                 x["to"] if x["from"] == tr_id else x["from"]]["coordinates"])) <= BOUNDARY]
                if other_own:
                    # TR 还有其他本房间门 → 删除错误边
                    drop_edges.add(e["id"])
                    fixed.append(f"删除 {e['id']} {tr_id}→{td_id}（{td_id}是别房间门，"
                                 f"距{rid}[{lab}] {d:.1f}m，TR 另有本房间门）")
                else:
                    # TR 无其他本房间门：尝试补连本房间边界上未连的 TD
                    candidates = [o for o in own_doors
                                  if not any((x["from"] == tr_id and x["to"] == o) or
                                             (x["from"] == o and x["to"] == tr_id)
                                             for x in edges_by[tr_id])]
                    if candidates:
                        # 已有本房间门但 TR 未连（补连）
                        td2 = candidates[0]
                        a = n["coordinates"]
                        b = nmap[td2]["coordinates"]
                        dd = math.hypot(a[0] - b[0], a[1] - b[1])
                        seq = max([int(x["id"].rsplit("-", 1)[-1]) for x in elist
                                   if x.get("id", "").rsplit("-", 1)[-1].isdigit()] + [900])
                        new_id = f"{fk}-TE-HC-{seq + 1:04d}"
                        elist.append({
                            "id": new_id, "from": tr_id, "to": td2,
                            "distance": round(dd, 3),
                            "estimatedTime": round(dd / 0.8, 2),
                            "accessibilityLevel": 0, "riskLevel": 0.5,
                            "walkable": True, "wheelchairAccessible": True,
                            "blindAccessible": True,
                        })
                        fixed.append(f"补连 {new_id} {tr_id}→{td2}（本房间门）")
                    else:
                        # 无法修复：删除错误边（保持数据正确，TR 孤立）
                        drop_edges.add(e["id"])
                        fixed.append(f"删除 {e['id']} {tr_id}→{td_id}（{rid}[{lab}] "
                                     f"无本房间门，TD距墙{d:.1f}m，删除后房间不可达）")

        # F2-TR-0024 冗余楼梯间 TR：只连 TD-0004（楼梯间6门），且楼梯间6有 TR-0038
        if fk == "2":
            tr24_edges = [e for e in edges_by.get("F2-TR-0024", [])]
            if tr24_edges and all(nmap.get(x).get("type") == "doorway" for x in
                                  [e["to"] if e["from"] == "F2-TR-0024" else e["from"]
                                   for e in tr24_edges]):
                for e in tr24_edges:
                    drop_edges.add(e["id"])
                drop_nodes.add("F2-TR-0024")
                fixed.append("删除冗余楼梯间 TR F2-TR-0024 及其边（楼梯间6 另有 TR-0038）")

        if drop_edges:
            elist = [e for e in elist if e["id"] not in drop_edges]
        if drop_nodes:
            nlist = [n for n in nlist if n["id"] not in drop_nodes]
        fl["topology"]["edges"] = elist
        fl["topology"]["nodes"] = nlist

        print(f"[F{fk}] 处理 {len(fixed)} 条:")
        for f in fixed:
            print(f"  {f}")

    json.dump(geo, open(path, "w", encoding="utf-8"),
              ensure_ascii=False, indent=2)
    print("已写回:", path)


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else
         "result/school_building_01_map_v9.geojson")
