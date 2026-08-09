# -*- coding: utf-8 -*-
"""需求⑥ v5 缺口逐个核实：对每个缺口输出 TDX.rooms、贴墙距离、房间是否已连 hub 的其它 TDX。
"""
import json, math, sys
from pathlib import Path
from shapely.geometry import shape, Point

BASE = Path(__file__).resolve().parent.parent
geo = json.loads((BASE / "result" / "school_building_01_map_v9.geojson").read_text(encoding="utf-8"))

OPEN = {"corridor", "lobby", "activity", "atrium", "elevator_lobby", "stair_lobby", "staircase"}
WALL_TOL = 0.6

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

    closed = {}
    for n in nodes:
        if n["type"] == "room" and n.get("roomId"):
            rt = room_type_of(fd, n["roomId"])
            if rt is not None and rt not in OPEN and rt != "toilet":
                closed[n["roomId"]] = n["id"]

    room_poly = {}
    for r in fd["geometry"].get("rooms", []):
        try: room_poly[r["id"]] = shape(r["geometry"])
        except Exception: pass

    door_nodes = {n["id"]: n for n in nodes if n["type"] == "doorway"}
    tdx_hub = {}
    for e in edges:
        a, b = nmap.get(e["from"]), nmap.get(e["to"])
        if not a or not b: continue
        if a["type"] == "doorway" and b["type"] == "doorway":
            ta, tb = a["id"], b["id"]
            if "TDX" in ta and "TDX" not in tb: tdx_hub[ta] = tb
            elif "TDX" in tb and "TDX" not in ta: tdx_hub[tb] = ta

    # TDX 的 rooms 字段（其归属房间）
    tdx_room = {nid: (nn.get("rooms") or [])[0] for nid, nn in door_nodes.items() if "TDX" in nid}

    room_edges = {}
    for e in edges:
        a, b = nmap.get(e["from"]), nmap.get(e["to"])
        if not a or not b: continue
        rn = dn = None
        if a["type"] == "room" and b["type"] == "doorway": rn, dn = a, b
        elif b["type"] == "room" and a["type"] == "doorway": rn, dn = b, a
        if rn is None: continue
        hid = tdx_hub.get(dn["id"], dn["id"])
        room_edges.setdefault(rn.get("roomId"), set()).add(hid)

    print(f"\n===== F{fk} 缺口核实 =====")
    for rid in sorted(closed.keys()):
        trid = closed[rid]
        want = {}
        for nid, nn in door_nodes.items():
            pt = Point(nn["coordinates"])
            if rid not in room_poly: continue
            d = room_poly[rid].boundary.distance(pt)
            if d >= WALL_TOL: continue
            hid = tdx_hub.get(nid, nid)
            dt = nn.get("doorType")
            if dt in ("swing", "fire"):
                want.setdefault(hid, []).append((nid, dt, round(d, 2),
                                                 tdx_room.get(nid, "-")))
        actual = room_edges.get(rid, set())
        miss = {td: v for td, v in want.items() if td not in actual}
        if not miss: continue
        print(f"\n{rid} TR={trid} 已连={sorted(actual)}")
        for td, lst in sorted(miss.items()):
            for nid, dt, d, own in lst:
                kind = "TDX" if "TDX" in nid else "TD "
                print(f"  ⚠️ 缺 hub={td} 贴墙源={nid}({kind}) dt={dt} 距墙={d}m TDX归属房间={own}")
