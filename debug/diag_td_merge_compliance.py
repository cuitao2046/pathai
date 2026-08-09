# -*- coding: utf-8 -*-
"""TD 语义合并合规性诊断：
权威判据 = 几何门坐标贴墙房间(<0.6m)（几何真相，不信任 rooms 字段）。
对每个 TD：sourceDoorIds 对应几何门的贴墙房间并集 = 该 TD 应有 rooms；
对照 TD.rooms 找「污染」(含非本门贴墙房间) 与「缺失」(贴墙房间未连)。
"""
import json, math, sys
from pathlib import Path
from shapely.geometry import shape, Point

BASE = Path(__file__).resolve().parent.parent
geo = json.loads((BASE / "result" / "school_building_01_map_v9.geojson").read_text(encoding="utf-8"))
OPEN = {"corridor", "lobby", "activity", "atrium", "elevator_lobby", "stair_lobby", "staircase"}
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

    # 几何门贴墙封闭房间（真相）
    def wall_rooms(coords):
        pt = Point(coords)
        return {rid for rid in room_poly if room_poly[rid].boundary.distance(pt) < WALL_TOL}

    # TDX→hub
    tdx_hub = {}
    for e in edges:
        a, b = nmap.get(e["from"]), nmap.get(e["to"])
        if not a or not b: continue
        if a["type"] == "doorway" and b["type"] == "doorway":
            ta, tb = a["id"], b["id"]
            if "TDX" in ta and "TDX" not in tb: tdx_hub[ta] = tb
            elif "TDX" in tb and "TDX" not in ta: tdx_hub[tb] = ta

    print(f"\n===== F{fk} TD 合并合规性 =====")
    n_poll = n_miss = 0
    for n in nodes:
        if n["type"] != "doorway" or "TDX" in n["id"]:
            continue
        src = n.get("sourceDoorIds") or []
        # 真相：source 几何门贴墙房间并集
        truth = set()
        for sid in src:
            d = dmap.get(sid)
            if d:
                truth |= wall_rooms(d["geometry"]["coordinates"])
        cur = set(n.get("rooms") or [])
        # 只比较封闭房间（开放空间/卫生间不算在 TR 连接范围，但 rooms 字段可能含）
        poll = cur - truth          # 污染：TD.rooms 含非本门贴墙房间
        miss = truth - cur          # 缺失：本门贴墙房间未在 TD.rooms
        if poll or miss:
            flag = "污染" if poll else ""
            flag += ("+缺失" if miss else "")
            if n.get("sourceDoorIds") is None:
                flag += "(无source)"
            print(f"  {n['id']} dt={n.get('doorType')} source={src}")
            print(f"    TD.rooms={sorted(cur)} 贴墙真相={sorted(truth)}")
            print(f"    {flag}: 污染={sorted(poll)} 缺失={sorted(miss)}")
            if poll: n_poll += 1
            if miss: n_miss += 1
    print(f"→ F{fk}: 污染 TD={n_poll} 缺失 TD={n_miss}")
