# -*- coding: utf-8 -*-
"""卫生间/管井导航规则：
1. 卫生间例外：卫生间 TR 可直接"穿墙"连最近走廊 TI（卫生间门被丢弃，
   无门时允许 TR→TI 直连，作为导航例外）
2. 管井/水井（shaft）不导航：管井 TD 的走廊连接（TD→TI/TWI）删除，
   路径不经过管井；管井 TR 保持孤立
"""
import json
import math
import sys
from collections import defaultdict
from shapely.geometry import Point, Polygon

TOILET = {"toilet"}
SHAFT = {"shaft"}


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

        # walkable 内的走廊 TI
        wr = fl.get("walkable_regions") or {}
        W = None
        feats = wr.get("features") if isinstance(wr, dict) else None
        if feats:
            from shapely.ops import unary_union
            polys = []
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
            if polys:
                W = unary_union(polys)
        corridor_tis = []
        if W is not None:
            for n in nlist:
                if n.get("type") == "intersection" and W.contains(Point(n["coordinates"])):
                    corridor_tis.append(n["id"])

        added = []
        dropped = []

        # 2) 管井 TD 摘除走廊连接（先摘除：依赖管井门的卫生间后续才能正确补边）
        for n in nlist:
            if n.get("type") != "doorway":
                continue
            pt = Point(n["coordinates"])
            is_shaft_door = False
            for r in fl["geometry"]["rooms"]:
                rt = (r.get("properties") or {}).get("roomType", "")
                try:
                    p = Polygon(r["geometry"]["coordinates"][0])
                except Exception:
                    continue
                if rt in SHAFT and p.exterior.distance(pt) < 0.8:
                    is_shaft_door = True
                    break
            if not is_shaft_door:
                continue
            td_id = n["id"]
            # 豁免：TD 连了楼梯设施(TF)（楼梯间门，即使贴管井也是楼梯的）
            # 注意：不能只凭"距 staircase 多边形近"豁免（TD-0066 之类
            # 距楼梯间 0.58m 但实际是管井门，连的是管井 TR）。
            is_stair_door = any(
                nmap.get(e["to"] if e["from"] == td_id else e["from"], {}).get("type") == "facility"
                for e in edges_by[td_id])
            if is_stair_door:
                continue
            for e in edges_by[td_id]:
                o = e["to"] if e["from"] == td_id else e["from"]
                ot = nmap.get(o, {}).get("type")
                if ot in ("intersection", "facility_entrance"):
                    dropped.append((e["id"], td_id, o))

        drop_ids = {x[0] for x in dropped}
        elist = [e for e in elist if e["id"] not in drop_ids]
        edges_by = defaultdict(list)
        for e in elist:
            edges_by[e["from"]].append(e)
            edges_by[e["to"]].append(e)
        fl["topology"]["edges"] = elist

        # 1) 卫生间 TR 补穿墙直连（在管井摘除之后）
        for n in nlist:
            if n.get("type") != "room":
                continue
            pt = Point(n["coordinates"])
            toilet_room = None
            for r in fl["geometry"]["rooms"]:
                rt = (r.get("properties") or {}).get("roomType", "")
                if rt not in TOILET:
                    continue
                try:
                    p = Polygon(r["geometry"]["coordinates"][0])
                except Exception:
                    continue
                if p.contains(pt):
                    toilet_room = p
                    break
            if toilet_room is None:
                continue
            tr_id = n["id"]
            # 检查是否已有走廊可达（TD 邻居且 TD 连 TI，或已有 TI 邻居）
            nbrs = [e["to"] if e["from"] == tr_id else e["from"]
                    for e in edges_by[tr_id]]
            has_corridor = False
            for o in nbrs:
                on = nmap.get(o, {})
                if on.get("type") == "intersection":
                    has_corridor = True
                    break
                if on.get("type") == "doorway":
                    for e2 in edges_by[o]:
                        o2 = e2["to"] if e2["from"] == o else e2["from"]
                        if nmap.get(o2, {}).get("type") == "intersection":
                            has_corridor = True
                            break
                if has_corridor:
                    break
            if has_corridor:
                continue
            # 无走廊连接 → 补 TR→最近走廊 TI（穿墙例外）
            if not corridor_tis:
                continue
            best = None
            for ti in corridor_tis:
                d = math.hypot(n["coordinates"][0] - nmap[ti]["coordinates"][0],
                               n["coordinates"][1] - nmap[ti]["coordinates"][1])
                if best is None or d < best[0]:
                    best = (d, ti)
            d, ti = best
            seq = max([int(x["id"].rsplit("-", 1)[-1]) for x in elist
                       if x.get("id", "").rsplit("-", 1)[-1].isdigit()] + [900])
            eid = f"{fk}-TE-HC-{seq + 1:04d}"
            elist.append({
                "id": eid, "from": tr_id, "to": ti,
                "distance": round(d, 3),
                "estimatedTime": round(d / 0.8, 2),
                "accessibilityLevel": 0, "riskLevel": 0.5,
                "walkable": True, "wheelchairAccessible": True,
                "blindAccessible": True,
                "note": "toilet_wall_exception",
            })
            edges_by[tr_id].append(elist[-1])
            added.append((eid, tr_id, ti, round(d, 1)))

        # 2) 管井 TD 摘除走廊连接（已在上方执行）

        print(f"[F{fk}] 卫生间穿墙直连 {len(added)} 条:")
        for eid, tr, ti, d in added:
            print(f"    {eid} {tr}→{ti}（{d}m）")
        print(f"[F{fk}] 管井门走廊连接摘除 {len(dropped)} 条:")
        for eid, td, o in dropped:
            print(f"    {eid} {td}→{o}")

    json.dump(geo, open(path, "w", encoding="utf-8"),
              ensure_ascii=False, indent=2)
    print("已写回:", path)


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else
         "result/school_building_01_map_v9.geojson")
