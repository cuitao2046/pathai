# -*- coding: utf-8 -*-
"""对已有 GeoJSON 应用「房间节点边约束」（与 pipeline.py 生成逻辑一致）：

1. 门节点(TD)坐标回退到其所属房间的「最近墙体边界点」——即门开口处。
   这样 room↔door 边从房间质心连到本房间墙面，绝不会穿墙（不再投影到走廊骨架）。
2. 门洞(opening)重定向：非卫生间封闭房间若不以 swing/fire 作为出入口，将其仅有的
   opening 提拔为 swing，满足「房间只连普通门/防火门」规则且保持连通。
3. 非卫生间封闭房间的质心节点只连 swing/fire 门：过滤 room↔opening 拓扑边。
4. 重算所有边距离（TD 坐标已变）。

用法: python debug/fix_room_edges.py [geojson]
"""
from __future__ import annotations
import argparse, json, math, sys
from pathlib import Path
from collections import defaultdict
from shapely.geometry import shape, Point, MultiPolygon
from shapely.ops import unary_union

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE / "src"))
sys.path.insert(0, str(BASE / "src" / "skeleton"))
from skeleton.pipeline import _merge_nearby_ti_nodes, TI_MERGE_RADIUS_M

OPEN = {"corridor", "lobby", "activity", "atrium",
        "elevator_lobby", "stair_lobby"}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("geojson", nargs="?",
                    default=str(BASE / "result" / "school_building_01_map_v9.geojson"))
    args = ap.parse_args()
    p = Path(args.geojson)
    geo = json.loads(p.read_text(encoding="utf-8"))

    for fk in sorted(geo["floors"].keys(), key=lambda x: int(x)):
        fd = geo["floors"][fk]
        nodes = fd["topology"]["nodes"]
        edges = fd["topology"]["edges"]
        nmap = {n["id"]: n for n in nodes}

        # 房间多边形
        room_polys = {}
        for r in fd["geometry"].get("rooms", []):
            try:
                room_polys[r["id"]] = shape(r["geometry"])
            except Exception:
                pass

        # 1) TD 节点坐标 → 其归属房间并集边界的最近点（门开口）
        moved = 0
        for n in nodes:
            if n["type"] != "doorway":
                continue
            rms = [rid for rid in n.get("rooms", []) if rid in room_polys]
            if not rms:
                continue
            union = unary_union([room_polys[rid] for rid in rms])
            pt = Point(n["coordinates"])
            # 最近边界点
            bpt = union.boundary.interpolate(union.boundary.project(pt))
            n["coordinates"] = [round(bpt.x, 3), round(bpt.y, 3)]
            moved += 1

        # 2) opening 重定向
        room_type_by_id = {r["id"]: r.get("roomType")
                           for r in fd["geometry"].get("rooms", [])}
        room_has_sf = set()
        for n in nodes:
            if n["type"] == "doorway" and n.get("doorType") in ("swing", "fire"):
                for rid in n.get("rooms", []):
                    room_has_sf.add(rid)
        reclassified = 0
        for n in nodes:
            if n["type"] != "doorway" or n.get("doorType") != "opening":
                continue
            rms = n.get("rooms", [])
            if not rms:
                continue
            if all((room_type_by_id.get(rid) not in OPEN
                    and room_type_by_id.get(rid) != "toilet") for rid in rms) \
               and all(rid not in room_has_sf for rid in rms):
                n["doorType"] = "swing"
                n["label"] = "普通门"
                reclassified += 1

        # 3) 过滤 room↔opening 边（非卫生间房间）
        kept = []
        dropped = 0
        for e in edges:
            a = nmap.get(e["from"]); b = nmap.get(e["to"])
            if not a or not b:
                kept.append(e); continue
            if a["type"] == "room" and b["type"] == "doorway":
                room_n, td_n = a, b
            elif b["type"] == "room" and a["type"] == "doorway":
                room_n, td_n = b, a
            else:
                kept.append(e); continue
            if (room_n.get("roomType") != "toilet"
                    and td_n.get("doorType") == "opening"):
                dropped += 1
                continue
            kept.append(e)
        edges[:] = kept

        # 4) 重算边距离
        for e in edges:
            a = nmap.get(e["from"]); b = nmap.get(e["to"])
            if not a or not b:
                continue
            d = math.hypot(a["coordinates"][0] - b["coordinates"][0],
                           a["coordinates"][1] - b["coordinates"][1])
            e["distance"] = round(float(d), 2)
            e["estimatedTime"] = round(float(d) / 0.8, 1)

        # 5) 重新合并近邻 TI（TI 坐标未变，保持一致性）
        nodes, edges, _ = _merge_nearby_ti_nodes(nodes, edges, radius_m=TI_MERGE_RADIUS_M)
        fd["topology"]["nodes"] = nodes
        fd["topology"]["edges"] = edges
        print(f"F{fk}: TD→墙体开口={moved} opening→swing={reclassified} "
              f"room↔opening边删除={dropped}")

    p.write_text(json.dumps(geo, ensure_ascii=False, indent=2), encoding="utf-8")
    print("已写回:", p)


if __name__ == "__main__":
    main()
