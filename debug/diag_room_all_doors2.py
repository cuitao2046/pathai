# -*- coding: utf-8 -*-
"""需求⑥诊断 v2：以 geometry.doors(properties.rooms/doorType) 为权威「房间应有门」来源，
按坐标就近映射几何门 → 拓扑 doorway 节点(TD/TDX)，检查非卫生间封闭房间是否对所有 swing/fire 门都有边。
同时对比「拓扑 hub rooms 字段口径」以发现 rooms 字段不全导致的缺口。
"""
import json, math, sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
geo = json.loads((BASE / "result" / "school_building_01_map_v9.geojson").read_text(encoding="utf-8"))

OPEN = {"corridor", "lobby", "activity", "atrium", "elevator_lobby", "stair_lobby", "staircase"}

def room_type_of(fd, rid):
    for r in fd["geometry"].get("rooms", []):
        if r["id"] == rid:
            return r.get("properties", {}).get("roomType") or r.get("type")
    return None

for fk in sorted(geo["floors"].keys(), key=lambda x: int(x)):
    fd = geo["floors"][fk]
    nodes = fd["topology"]["nodes"]
    edges = fd["topology"]["edges"]
    nmap = {n["id"]: n for n in nodes}

    # 封闭房间
    closed = {}
    for n in nodes:
        if n["type"] == "room" and n.get("roomId"):
            rt = room_type_of(fd, n["roomId"])
            if rt is not None and rt not in OPEN and rt != "toilet":
                closed[n["roomId"]] = n["id"]

    # 拓扑 doorway 节点坐标
    door_nodes = {n["id"]: n for n in nodes if n["type"] == "doorway"}

    # 几何门(权威) -> 就近 TD
    geo_doors = []
    for d in fd["geometry"].get("doors", []):
        pr = d.get("properties", {})
        coords = d.get("geometry", {}).get("coordinates")
        if not coords: continue
        geo_doors.append({
            "id": d["id"], "doorType": pr.get("doorType"),
            "rooms": set(pr.get("rooms") or []), "coords": tuple(coords),
        })

    # TD 与 TDX 的从属: TDX -> hub
    tdx_hub = {}
    for e in edges:
        a, b = nmap.get(e["from"]), nmap.get(e["to"])
        if not a or not b: continue
        if a["type"] == "doorway" and b["type"] == "doorway":
            ta, tb = a["id"], b["id"]
            if "TDX" in ta and "TDX" not in tb: tdx_hub[ta] = tb
            elif "TDX" in tb and "TDX" not in ta: tdx_hub[tb] = ta

    # 实际连边: roomId -> set(hub or TD)
    room_edges = {}
    for e in edges:
        a, b = nmap.get(e["from"]), nmap.get(e["to"])
        if not a or not b: continue
        rn = dn = None
        if a["type"] == "room" and b["type"] == "doorway": rn, dn = a, b
        elif b["type"] == "room" and a["type"] == "doorway": rn, dn = b, a
        if rn is None: continue
        rid = rn.get("roomId")
        hid = tdx_hub.get(dn["id"], dn["id"])
        room_edges.setdefault(rid, set()).add(hid)

    # 对每个几何门: 就近 TD(容差 2.5m)；记录 geom door -> td, td -> [rooms]
    geo_map = []  # (geom_id, doorType, rooms, td_id)
    for g in geo_doors:
        best, bd = None, 1e9
        for nid, nn in door_nodes.items():
            c = nn["coordinates"]
            dist = math.hypot(g["coords"][0]-c[0], g["coords"][1]-c[1])
            if dist < bd: bd, best = dist, nid
        if best and bd < 2.5:
            geo_map.append((g["id"], g["doorType"], g["rooms"], best, round(bd,2)))

    print(f"\n===== F{fk} 几何门权威口径 =====")
    n_missing = 0
    for rid in sorted(closed.keys()):
        trid = closed[rid]
        # 几何应有门: 该房间的 swing/fire 几何门 -> 就近 TD
        want = {}
        for gid, dt, rms, td, dd in geo_map:
            if rid in rms and dt in ("swing", "fire"):
                want.setdefault(td, []).append((gid, dd))
        actual = room_edges.get(rid, set())
        miss = {td: v for td, v in want.items() if td not in actual}
        if miss:
            n_missing += 1
            print(f"\n{rid} TR={trid} 实际连边TD={sorted(actual)}")
            for td, lst in sorted(miss.items()):
                print(f"  ⚠️ 缺 {td} (几何门 {[(a,b) for a,b in lst]}) doorType={nmap[td].get('doorType')}")
    print(f"F{fk}: 几何口径缺口房间 = {n_missing}/{len(closed)}")
