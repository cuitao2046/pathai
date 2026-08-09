# -*- coding: utf-8 -*-
"""需求⑥诊断 v4（客观判据）：
门的真实归属 = 门坐标所贴的房间墙（boundary.distance < 0.6m）。
对每个 swing/fire 几何门：列出其贴墙的封闭房间 → 就近 TD(hub) → 该房间 TR 是否已连。
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

    print(f"\n===== F{fk} 门贴墙归属（wall_tol={WALL_TOL}m） =====")
    wall_owners = {}   # geom door id -> [贴墙封闭房间]
    for d in fd["geometry"].get("doors", []):
        pr = d.get("properties", {})
        coords = d.get("geometry", {}).get("coordinates")
        if not coords: continue
        if pr.get("doorType") not in ("swing", "fire"): continue
        pt = Point(coords)
        owners = [rid for rid in closed if rid in room_poly
                  and room_poly[rid].boundary.distance(pt) < WALL_TOL]
        if owners:
            wall_owners[d["id"]] = owners

    # 缺口汇总：贴墙房间 → TD 是否连
    missing = {}
    for gid, owners in wall_owners.items():
        # 就近 TD（归一化）
        best, bd = None, 1e9
        gcoords = None
        for d in fd["geometry"].get("doors", []):
            if d["id"] == gid: gcoords = d["geometry"]["coordinates"]; break
        for nid, nn in door_nodes.items():
            c = nn["coordinates"]
            dist = math.hypot(gcoords[0]-c[0], gcoords[1]-c[1])
            if dist < bd: bd, best = dist, nid
        if best is None or bd >= 2.5: continue
        hid = tdx_hub.get(best, best)
        for rid in owners:
            if hid not in room_edges.get(rid, set()):
                missing.setdefault(rid, []).append((gid, hid, bd))
    n_rooms = len(missing)
    n_gaps = sum(len(v) for v in missing.values())
    print(f"贴墙归属：{len(wall_owners)} 扇几何门贴墙，涉及缺口房间={n_rooms} 缺口边={n_gaps}")
    for rid in sorted(missing):
        print(f"  {rid} TR={closed[rid]}:")
        for gid, hid, bd in missing[rid]:
            print(f"    缺 {hid} (几何门 {gid} 距TD {bd:.2f}m)")
    print(f"→ F{fk} 真实缺口（门贴墙但房间未连）：{n_gaps} 条边 / {n_rooms} 个房间")
