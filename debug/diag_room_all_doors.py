# -*- coding: utf-8 -*-
"""需求⑥诊断：非卫生间封闭房间 TR 应与该房间所有 swing/fire 门都有边。
统计每个房间：应有门(拓扑 TD rooms 字段 / 几何门 rooms 字段) vs 实际连边门(room 经 TDX 或直达 hub)。
"""
import json, sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
geo = json.loads((BASE / "result" / "school_building_01_map_v9.geojson").read_text(encoding="utf-8"))

OPEN = {"corridor", "lobby", "activity", "atrium", "elevator_lobby", "stair_lobby", "staircase"}

def room_type_of(fd, rid):
    """semantic.rooms 权威 roomType（type 字段）"""
    for r in fd["geometry"].get("rooms", []):
        if r["id"] == rid:
            return r.get("properties", {}).get("roomType") or r.get("type")
    return None

for fk in sorted(geo["floors"].keys(), key=lambda x: int(x)):
    fd = geo["floors"][fk]
    nodes = fd["topology"]["nodes"]
    edges = fd["topology"]["edges"]
    nmap = {n["id"]: n for n in nodes}

    rooms = {}
    for n in nodes:
        if n["type"] == "room" and n.get("roomId"):
            rt = room_type_of(fd, n["roomId"])
            rooms[n["roomId"]] = {"node": n["id"], "roomType": rt,
                                  "closed": rt is not None and rt not in OPEN and rt != "toilet"}

    # 拓扑门: hub = doorway 非 TDX；TDX 的 rooms 单房间
    hubs = {}   # id -> (doorType, rooms set)
    tdxs = {}   # id -> (hub_id(由 -b 边推导), roomId, doorType)
    for n in nodes:
        if n["type"] != "doorway":
            continue
        if "TDX" in n["id"]:
            tdxs[n["id"]] = {"hub": None, "roomId": (n.get("rooms") or [None])[0],
                             "doorType": n.get("doorType")}
        else:
            hubs[n["id"]] = {"doorType": n.get("doorType"),
                             "rooms": set(n.get("rooms") or [])}
    # TDX -> hub 通过 room? 实际是 TDX 有 -b 边连 hub；hub 的 rooms 含 roomId
    for e in edges:
        a, b = nmap.get(e["from"]), nmap.get(e["to"])
        if not a or not b: continue
        if a["type"] == "doorway" and b["type"] == "doorway":
            ta, tb = a["id"], b["id"]
            if "TDX" in ta and "TDX" not in tb:
                tdxs[ta]["hub"] = tb
            elif "TDX" in tb and "TDX" not in ta:
                tdxs[tb]["hub"] = ta

    # 实际连边: room -> TDX -> hub；room -> hub 直达
    room_edges = {}   # roomId -> set(hub_id)
    for e in edges:
        a, b = nmap.get(e["from"]), nmap.get(e["to"])
        if not a or not b: continue
        rn = dn = None
        if a["type"] == "room" and b["type"] == "doorway":
            rn, dn = a, b
        elif b["type"] == "room" and a["type"] == "doorway":
            rn, dn = b, a
        if rn is None: continue
        rid = rn.get("roomId")
        room_edges.setdefault(rid, set())
        if "TDX" in dn["id"]:
            h = tdxs[dn["id"]]["hub"]
            if h: room_edges[rid].add(h)
        else:
            room_edges[rid].add(dn["id"])

    # 几何门: rooms 含该房间
    geo_doors = {}
    for d in fd["geometry"].get("doors", []):
        rms = set(d.get("rooms") or [])
        geo_doors[d["id"]] = {"doorType": d.get("doorType"), "rooms": rms}

    print(f"\n===== F{fk} 非卫生间封闭房间：应有门 vs 实际连边 =====")
    n_missing = 0
    for rid, info in sorted(rooms.items()):
        if not info["closed"]:
            continue
        trid = info["node"]
        # 应有门(拓扑): hub rooms 含 rid 且 swing/fire
        have_topo = sorted(h for h, d in hubs.items()
                           if rid in d["rooms"] and d["doorType"] in ("swing", "fire"))
        # 应有门(几何): geometry.doors rooms 含 rid 且 swing/fire
        have_geo = sorted(g for g, d in geo_doors.items()
                          if rid in d["rooms"] and d["doorType"] in ("swing", "fire"))
        actual = sorted(room_edges.get(rid, set()))
        # 缺失 = 拓扑应有门 - 实际连边
        missing_topo = [h for h in have_topo if h not in actual]
        missing_geo = [g for g in have_geo if g not in actual]
        if missing_topo or missing_geo:
            n_missing += 1
            print(f"\n{rid} ({info['roomType']}) TR={trid}")
            print(f"  拓扑应有门({len(have_topo)}): {have_topo}")
            print(f"  几何应有门({len(have_geo)}): {missing_geo and '见下' or '无'}")
            if have_geo:
                print(f"    几何门明细: {[(g, geo_doors[g]['doorType']) for g in have_geo]}")
            print(f"  实际连边门({len(actual)}): {actual}")
            print(f"  ⚠️ 缺失(拓扑): {missing_topo}")
            if missing_geo:
                print(f"  ⚠️ 缺失(几何, 未在拓扑应有门中): {missing_geo}")
    print(f"\nF{fk}: 有缺口的房间数 = {n_missing} / 封闭房间总数 {sum(1 for r in rooms.values() if r['closed'])}")
