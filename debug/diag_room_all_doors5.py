# -*- coding: utf-8 -*-
"""需求⑥诊断 v5（纯拓扑域，最稳健）：
门对房间的归属 = TD/TDX 节点坐标贴房间墙（boundary.distance < 0.6m）。
对每个非卫生间封闭房间：应有门 = 所有贴墙 swing/fire TD(归并 TDX→hub)；
检查 TR 是否连了所有应有门。opening 门单独列出（若房间仅有 opening 则需提拔）。
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

    # 每个 doorway 节点贴墙的封闭房间
    door_wall_rooms = {}   # door id -> set(rid)
    for nid, nn in door_nodes.items():
        pt = Point(nn["coordinates"])
        owners = [rid for rid in closed if rid in room_poly
                  and room_poly[rid].boundary.distance(pt) < WALL_TOL]
        if owners:
            door_wall_rooms[nid] = set(owners)

    print(f"\n===== F{fk} 纯拓扑域（TD坐标贴墙 tol={WALL_TOL}m） =====")
    n_missing = 0
    for rid in sorted(closed.keys()):
        trid = closed[rid]
        # 应有门: 贴墙该房间的 TD/TDX → hub（swing/fire）
        want = {}
        opening_only = []
        for nid, owners in door_wall_rooms.items():
            if rid not in owners: continue
            hid = tdx_hub.get(nid, nid)
            dt = nmap[nid].get("doorType")
            if dt in ("swing", "fire"):
                want.setdefault(hid, dt)
            elif dt == "opening":
                opening_only.append(hid)
        actual = room_edges.get(rid, set())
        miss = {td: dt for td, dt in want.items() if td not in actual}
        if miss:
            n_missing += 1
            print(f"\n{rid} TR={trid} 实际连边={sorted(actual)}")
            for td, dt in sorted(miss.items()):
                print(f"  ⚠️ 缺 {td} ({dt}) 贴墙源={[k for k,v in door_wall_rooms.items() if rid in v and tdx_hub.get(k,k)==td]}")
        # opening 门处理提示
        if opening_only and not want:
            print(f"\n{rid} TR={trid} ⚠️ 仅有 opening 门 {sorted(set(opening_only))}（无 swing/fire，需提拔）")
    print(f"\nF{fk}: 缺口房间 = {n_missing}/{len(closed)}")
