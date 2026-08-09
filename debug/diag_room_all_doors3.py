# -*- coding: utf-8 -*-
"""需求⑥诊断 v3：归一化 TDX→hub 后比较；并核实几何门坐标是否真落在房间墙面上。
每个缺口打印：几何门坐标→房间墙距离、对应 TD 的 rooms 字段、doorType。
"""
import json, math, sys
from pathlib import Path
from shapely.geometry import shape, Point

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

    closed = {}
    for n in nodes:
        if n["type"] == "room" and n.get("roomId"):
            rt = room_type_of(fd, n["roomId"])
            if rt is not None and rt not in OPEN and rt != "toilet":
                closed[n["roomId"]] = n["id"]

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

    # 房间多边形
    room_poly = {}
    for r in fd["geometry"].get("rooms", []):
        try: room_poly[r["id"]] = shape(r["geometry"])
        except Exception: pass

    print(f"\n===== F{fk} 几何门权威口径（归一化 TDX→hub + 墙面核实） =====")
    n_missing = 0
    for rid in sorted(closed.keys()):
        trid = closed[rid]
        want = {}
        for d in fd["geometry"].get("doors", []):
            pr = d.get("properties", {})
            coords = d.get("geometry", {}).get("coordinates")
            if not coords: continue
            if pr.get("doorType") not in ("swing", "fire"): continue
            if rid not in (pr.get("rooms") or []): continue
            # 就近 doorway 节点（先归一化 TDX→hub）
            best, bd = None, 1e9
            for nid, nn in door_nodes.items():
                c = nn["coordinates"]
                dist = math.hypot(coords[0]-c[0], coords[1]-c[1])
                if dist < bd: bd, best = dist, nid
            if best is None or bd >= 2.5: continue
            hid = tdx_hub.get(best, best)
            want.setdefault(hid, []).append((d["id"], pr.get("doorType"), round(bd,2),
                                             round(room_poly[rid].boundary.distance(Point(coords)),2) if rid in room_poly else None))
        actual = room_edges.get(rid, set())
        miss = {td: v for td, v in want.items() if td not in actual}
        if miss:
            n_missing += 1
            print(f"\n{rid} TR={trid} 实际连边={sorted(actual)}")
            for td, lst in sorted(miss.items()):
                tdinfo = nmap.get(td, {})
                print(f"  ⚠️ 缺 {td} doorType={tdinfo.get('doorType')} rooms={tdinfo.get('rooms')}")
                for gid, dt, dd, wall in lst:
                    print(f"      几何门 {gid} {dt} 距离TD={dd}m 距房间墙面={wall}m")
    print(f"\nF{fk}: 缺口房间 = {n_missing}/{len(closed)}")
