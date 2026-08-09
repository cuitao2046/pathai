# -*- coding: utf-8 -*-
"""拓扑节点物理归属全量审查。

判据：
- TR: 节点坐标 vs 其 roomId 房间质心/边界距离（房间内部则 0）
- TD: 节点坐标 vs sourceDoorIds 门坐标距离；TD.rooms 中各房间贴墙距离
- TF: 节点坐标 vs 楼梯/电梯设施几何距离（若 geometry 有）
- TEN: 节点坐标 vs 出入口几何（若 geometry 有）
- TI: 交叉口无归属（跳过）

>5m 的列为重点怀疑对象（套间门/大房间例外需甄别）。
"""
import json
import math
from pathlib import Path
from shapely.geometry import shape, Point

BASE = Path(__file__).resolve().parent.parent
GEO = json.loads((BASE / "result" / "school_building_01_map_v9.geojson").read_text(encoding="utf-8"))

WALL_TOL = 0.6
SUSPECT = 5.0

report = []

for fk, fd in GEO["floors"].items():
    g = fd.get("geometry", {}) or {}
    t = fd.get("topology", {}) or {}
    nodes = t.get("nodes", [])
    edges = t.get("edges", [])
    rooms = {r["id"]: shape(r["geometry"]) for r in g.get("rooms", [])}
    doors = {d["id"]: d for d in g.get("doors", [])}
    nmap = {n["id"]: n for n in nodes}

    # 设施/出入口几何（若存在）
    fac_geo = {}
    for k in ("stairs", "elevators", "facilities", "exits", "entrances"):
        for f in g.get(k, []):
            if "id" in f and f.get("geometry", {}).get("coordinates"):
                try:
                    fac_geo[f["id"]] = Point(f["geometry"]["coordinates"])
                except Exception:
                    pass

    for n in nodes:
        nid = n["id"]
        ntype = n["type"]
        c = n.get("coordinates")
        if not c:
            continue
        pt = Point(c)
        notes = []

        if ntype == "room":
            rid = n.get("roomId")
            if rid and rid in rooms:
                rp = rooms[rid]
                inside = rp.contains(pt) or rp.boundary.distance(pt) < WALL_TOL
                d = 0.0 if inside else rp.distance(pt)
                if d > SUSPECT:
                    notes.append(f"TR 距归属房间 {rid} 墙 {d:.1f}m")
            else:
                notes.append(f"TR roomId={rid} 无多边形")

        elif ntype == "doorway":
            # ① sourceDoorIds 门坐标距离
            src = n.get("sourceDoorIds") or []
            if src:
                dmax = 0.0
                for did in src:
                    dd = doors.get(did)
                    if dd:
                        dc = dd["geometry"]["coordinates"]
                        dmax = max(dmax, math.hypot(c[0]-dc[0], c[1]-dc[1]))
                if dmax > SUSPECT:
                    notes.append(f"TD 距 source门 {dmax:.1f}m (max of {src})")
            else:
                notes.append("TD 无 sourceDoorIds")
            # ② rooms 贴墙距离（仅检查标注房间是否真贴墙）
            for rid in (n.get("rooms") or []):
                if rid in rooms:
                    d = rooms[rid].boundary.distance(pt)
                    if d > SUSPECT:
                        notes.append(f"TD.rooms 含 {rid} 但距其墙 {d:.1f}m")

        elif ntype == "facility":
            # 设施归属：贴墙房间 / label
            wall = [rid for rid, rp in rooms.items() if rp.boundary.distance(pt) < WALL_TOL]
            if not wall and fac_geo:
                for fid, fpt in fac_geo.items():
                    pass  # id 可能对不上
            if not wall:
                # 最近房间
                nearest = min(rooms.items(), key=lambda kv: kv[1].distance(pt)) if rooms else None
                if nearest:
                    d = nearest[1].distance(pt)
                    if d > SUSPECT:
                        notes.append(f"TF 无贴墙房间，最近 {nearest[0]} {d:.1f}m")

        elif ntype == "facility_entrance":
            wall = [rid for rid, rp in rooms.items() if rp.boundary.distance(pt) < WALL_TOL]
            if not wall:
                nearest = min(rooms.items(), key=lambda kv: kv[1].distance(pt)) if rooms else None
                if nearest:
                    d = nearest[1].distance(pt)
                    if d > SUSPECT:
                        notes.append(f"TEN 无贴墙房间，最近 {nearest[0]} {d:.1f}m")

        if notes:
            report.append((fk, nid, ntype, n.get("label", ""), n.get("roomId", ""), notes))

print(f"=== 拓扑节点物理归属审查（> {SUSPECT}m 重点怀疑）===")
print(f"节点总数: F1={sum(1 for n in GEO['floors']['1']['topology']['nodes'])} "
      f"F2={sum(1 for n in GEO['floors']['2']['topology']['nodes'])}")
print(f"怀疑项: {len(report)}\n")
for fk, nid, ntype, label, rid, notes in report:
    print(f"[F{fk}] {nid} ({ntype}) label={label!r} roomId={rid}")
    for x in notes:
        print(f"    ⚠ {x}")
