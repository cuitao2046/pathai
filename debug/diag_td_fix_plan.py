# -*- coding: utf-8 -*-
"""需求：不同房间的同类拓扑节点不能合并。
修复规划：以「几何门贴墙房间(<0.6m)」为权威真相重建 TD 语义。
对每个 TD（含 TDX），对比其 sourceDoorIds 对应几何门的贴墙房间并集：
- 污染房间 = TD.rooms 含但本门不贴墙的封闭房间
- 判定：该污染房间是否有「自己的独立门 TD」（贴该房间墙的几何门 → 就近 hub，且该 hub 不混其他房间）
  有 → 摘除（房间走自己的门）
  无 → 需补建独立 TD（该房间的门漏建/漂移）
"""
import json, math, sys
from pathlib import Path
from shapely.geometry import shape, Point

BASE = Path(__file__).resolve().parent.parent
geo = json.loads((BASE / "result" / "school_building_01_map_v9.geojson").read_text(encoding="utf-8"))
WALL_TOL = 0.6

for fk in sorted(geo["floors"].keys(), key=lambda x: int(x)):
    fd = geo["floors"][fk]
    nodes = fd["topology"]["nodes"]
    edges = fd["topology"]["edges"]
    nmap = {n["id"]: n for n in nodes}
    room_poly = {}
    for r in fd["geometry"].get("rooms", []):
        try: room_poly[r["id"]] = shape(r["geometry"])
        except Exception: pass
    dmap = {d["id"]: d for d in fd["geometry"].get("doors", [])}

    def wall_rooms(coords):
        pt = Point(coords)
        return {rid for rid in room_poly if room_poly[rid].boundary.distance(pt) < WALL_TOL}

    tdx_hub = {}
    for e in edges:
        a, b = nmap.get(e["from"]), nmap.get(e["to"])
        if not a or not b: continue
        if a["type"] == "doorway" and b["type"] == "doorway":
            ta, tb = a["id"], b["id"]
            if "TDX" in ta and "TDX" not in tb: tdx_hub[ta] = tb
            elif "TDX" in tb and "TDX" not in ta: tdx_hub[tb] = ta

    print(f"\n===== F{fk} 修复规划 =====")
    plan = []
    for n in nodes:
        if n["type"] != "doorway":
            continue
        is_tdx = "TDX" in n["id"]
        hub_id = tdx_hub.get(n["id"], n["id"])
        src = n.get("sourceDoorIds") or []
        truth = set()
        for sid in src:
            d = dmap.get(sid)
            if d: truth |= wall_rooms(d["geometry"]["coordinates"])
        cur = set(n.get("rooms") or [])
        poll = cur - truth
        if not poll:
            continue
        for rid in sorted(poll):
            rp = room_poly.get(rid)
            if rp is None:
                plan.append((n["id"], rid, "无多边形", None))
                continue
            # 贴该房间墙的几何门 → 就近 hub
            own_doors = [(did, round(rp.boundary.distance(Point(d["geometry"]["coordinates"])), 2))
                         for did, d in dmap.items()
                         if rp.boundary.distance(Point(d["geometry"]["coordinates"])) < WALL_TOL]
            hubs = set()
            for did, _ in own_doors:
                c = dmap[did]["geometry"]["coordinates"]
                best, bd = None, 1e9
                for nn in nodes:
                    if nn["type"] != "doorway": continue
                    dd = math.hypot(nn["coordinates"][0]-c[0], nn["coordinates"][1]-c[1])
                    if dd < bd: bd, best = dd, nn["id"]
                if best:
                    hubs.add(tdx_hub.get(best, best))
            plan.append((n["id"], rid, "有独立门" if hubs else "无独立门", sorted(hubs)))
    for nid, rid, verdict, hubs in plan:
        print(f"  {nid} 污染房间={rid}: {verdict} {hubs if hubs else ''}")
