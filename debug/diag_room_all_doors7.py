# -*- coding: utf-8 -*-
"""需求⑥ 最终权威核查：几何门(贴墙0.6m) → 就近TD(2.5m) → 房间是否已连。
对每个非卫生间封闭房间输出完整对照表，标出真实缺口。
"""
import json, math, sys
from pathlib import Path
from shapely.geometry import shape, Point

BASE = Path(__file__).resolve().parent.parent
geo = json.loads((BASE / "result" / "school_building_01_map_v9.geojson").read_text(encoding="utf-8"))

OPEN = {"corridor", "lobby", "activity", "atrium", "elevator_lobby", "stair_lobby", "staircase"}
WALL_TOL = 0.6
TD_TOL = 2.5

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

    print(f"\n===== F{fk} 几何门权威最终核查 =====")
    for rid in sorted(closed.keys()):
        trid = closed[rid]
        if rid not in room_poly: continue
        poly = room_poly[rid]
        rows = []
        for d in fd["geometry"].get("doors", []):
            pr = d.get("properties", {})
            coords = d.get("geometry", {}).get("coordinates")
            if not coords or pr.get("doorType") not in ("swing", "fire"): continue
            wd = poly.boundary.distance(Point(coords))
            if wd >= WALL_TOL: continue
            # 就近 TD（归一化 TDX→hub）
            best, bd = None, 1e9
            for nid, nn in door_nodes.items():
                c = nn["coordinates"]
                dist = math.hypot(coords[0]-c[0], coords[1]-c[1])
                if dist < bd: bd, best = dist, nid
            if best is None or bd >= TD_TOL: continue
            hid = tdx_hub.get(best, best)
            rows.append((d["id"], pr.get("doorType"), wd, bd, hid,
                         "已连" if hid in room_edges.get(rid, set()) else "缺口"))
        if not rows: continue
        print(f"\n{rid} TR={trid} 已连hub={sorted(room_edges.get(rid,set()))}")
        for gid, dt, wd, bd, hid, st in rows:
            print(f"  {gid} {dt} 距墙{wd:.2f}m 距TD{bd:.2f}m -> {hid} [{st}]")
