#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
gen_beacon_plan.py — 依据 docs/07-信标部署方案.md，从 v9 楼层 GeoJSON 生成
「可施工 / 可运维优先」的蓝牙信标部署方案 JSON（新表台账 + 施工运维手册）。

设计原则（面向视障室内导航，兼顾施工可行性与运维成本）：
  - 决策节点优先 + 安全点加密：门口(重要房间) / 度≥3 交叉口 / 楼梯 / 电梯。
  - 可施工：信标坐标不要求钉死在拓扑点，而是在 ≤2.5m 内吸附到「可附着安装面」
    （结构柱 column / 门套 door_frame / 侧墙 wall）；无法吸附才用短吊杆天花
    （ceiling_pendant，有效高度≈3m）并标记为运维重点。
  - 可运维：安装高度墙装 2.2m / 天花 3.0m；楼梯·电梯强制墙装或门套；
    每台信标带 mountType + snapDist_m，电池可换（CR2477），年度抽检。
  - 防过密：交叉口方向标≤2 条且边≥4m 才加；2m 内不同语义合并；压缩纯天花阵列。
  - 排除：常闭防火门、内开且门后归属房间的常开防火门、纯基础设施门口、
    纯走廊连通门口、建筑出入口(户外设施)不部署。

输出 beacon_deployment_plan.json：
  - beacons[]：含 beaconId/uuid/major/minor/coordinates(施工坐标)/plannedCoordinates
    (规划坐标)/floor/locationDesc/mountType/installHeight/txPower/subType 等。
  - construction / procurement：内嵌施工原则与采购型号建议，形成自洽部署手册。
  - summary.byMount：统计墙装/柱/门套/天花数量，供施工与预算核算。

用法：
    python src/tools/gen_beacon_plan.py
    python src/tools/gen_beacon_plan.py --geo result/school_building_01_map_v9.geojson \
        --out result/beacon_deployment_plan.json
"""
from __future__ import annotations
import argparse, json, math
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

ROOT = Path(__file__).resolve().parents[2]          # .../pathai
DEFAULT_GEO = ROOT / "result" / "school_building_01_map_v9.geojson"
DEFAULT_OUT = ROOT / "result" / "beacon_deployment_plan.json"

IBEACON_UUID = "B9407F30-F5F8-466E-AFF9-25556B57FE6D"
DEFAULT_PARAMS = {
    "installHeightWall": 2.2,
    "installHeightCeiling": 3.0,
    "broadcastInterval": 300,
    "batteryModel": "CR2477",
    "expectedLifespanYears": 5,
    "txPowerBySemantic": {"door": -8, "intersection": -10, "stair": -12, "elevator": -12},
    "offsets": {"stairWarning": 3.5, "snapMax": 2.5},
    "consolidateDist": 2.0,
    "minJunctionDegree": 3,
}
IMPORTANT_ROOM_TYPES = {
    "classroom", "lab", "office", "meeting", "toilet", "library", "medical",
    "reception", "counseling", "activity", "lobby", "stair_lobby", "elevator_lobby",
    "staircase", "elevator_hall", "storage", "equipment", "room",
}
INFRA_ROOM_TYPES = {"infrastructure", "shaft", "pipe", "well", "duct"}
OPEN_TYPES = {"corridor", "lobby", "activity", "atrium", "elevator_lobby", "stair_lobby", "entrance", "accessible_entrance"}
_SEM_RANK = {"stair": 4, "elevator": 3, "door": 2, "intersection": 1}

def _dist(a, b):
    return math.hypot(float(a[0]) - float(b[0]), float(a[1]) - float(b[1]))

def _unit(dx, dy):
    L = math.hypot(dx, dy)
    return (0.0, 0.0) if L < 1e-9 else (dx / L, dy / L)

def _dir_label(dx, dy):
    return ("东" if dx >= 0 else "西") if abs(dx) >= abs(dy) else ("北" if dy >= 0 else "南")

def _round_coord(c, nd=3):
    return [round(float(c[0]), nd), round(float(c[1]), nd)]

def _quantize_opening(c, q=0.4):
    return (int(round(float(c[0]) / q)), int(round(float(c[1]) / q)))

def _poly_centroid(coords):
    ring = coords[0] if coords and isinstance(coords[0][0], (list, tuple)) else coords
    if not ring or len(ring) < 3:
        return None
    pts = ring[:-1] if len(ring) > 1 and ring[0] == ring[-1] else ring
    xs = [p[0] for p in pts]; ys = [p[1] for p in pts]
    return [sum(xs)/len(xs), sum(ys)/len(ys)] if xs else None

def _build_mount_index(fl):
    pts = []
    geom = fl.get("geometry") or {}
    for c in geom.get("columns") or []:
        g = c.get("geometry") or {}
        if g.get("type") == "Polygon":
            cen = _poly_centroid(g.get("coordinates"))
            if cen: pts.append((cen[0], cen[1], "column"))
        elif g.get("type") == "Point":
            xy = g.get("coordinates")
            if xy: pts.append((float(xy[0]), float(xy[1]), "column"))
    for d in geom.get("doors") or []:
        g = d.get("geometry") or {}
        if g.get("type") == "Point":
            xy = g["coordinates"]; pts.append((float(xy[0]), float(xy[1]), "door"))
        elif g.get("type") == "LineString":
            coords = g.get("coordinates") or []
            if len(coords) >= 2:
                pts.append(((coords[0][0]+coords[-1][0])/2, (coords[0][1]+coords[-1][1])/2, "door"))
        elif g.get("type") == "Polygon":
            cen = _poly_centroid(g.get("coordinates"))
            if cen: pts.append((cen[0], cen[1], "door"))
    for w in geom.get("walls") or []:
        coords = (w.get("geometry") or {}).get("coordinates") or []
        if len(coords) >= 2:
            pts.append((float(coords[0][0]), float(coords[0][1]), "wall"))
            pts.append((float(coords[-1][0]), float(coords[-1][1]), "wall"))
    for s in geom.get("stairs") or []:
        g = s.get("geometry") or {}
        if g.get("type") == "Polygon":
            ring = (g.get("coordinates") or [[]])[0]
            step = max(1, len(ring)//8) if ring else 1
            for i in range(0, len(ring), step):
                pts.append((float(ring[i][0]), float(ring[i][1]), "stair_wall"))
        elif g.get("type") == "LineString":
            for p in g.get("coordinates") or []:
                pts.append((float(p[0]), float(p[1]), "stair_wall"))
    return pts

def _snap_to_mount(x, y, mounts, max_d, prefer=None):
    prefer_set = set(prefer or [])
    best = None
    for mx, my, kind in mounts:
        d = math.hypot(mx - x, my - y)
        if d > max_d: continue
        score = d - (0.35 if kind in prefer_set else 0.0)
        if best is None or score < best[0]:
            best = (score, d, mx, my, kind)
    if best is None:
        return x, y, "ceiling_pendant", 0.0
    _, d, mx, my, kind = best
    mount = {"column": "column", "door": "door_frame", "wall": "wall", "stair_wall": "wall"}.get(kind, "wall")
    return mx, my, mount, d

def _index_floor(fl):
    rooms = fl.get("geometry", {}).get("rooms") or []
    doors = fl.get("geometry", {}).get("doors") or []
    nodes = fl.get("topology", {}).get("nodes") or []
    edges = fl.get("topology", {}).get("edges") or []
    risk = fl.get("accessibility", {}).get("riskNodes") or []
    elevs = fl.get("accessibility", {}).get("elevators") or []
    room_by_id = {}
    for r in rooms:
        rid = r.get("id") or (r.get("properties") or {}).get("roomId")
        if rid: room_by_id[rid] = r
    door_by_id = {d.get("id"): d for d in doors if d.get("id")}
    node_by_id = {n["id"]: n for n in nodes}
    adj = defaultdict(list)
    for e in edges:
        a, b = e.get("from"), e.get("to")
        if not a or not b: continue
        d = float(e.get("distance") or 0.0)
        if d <= 0 and a in node_by_id and b in node_by_id:
            d = _dist(node_by_id[a]["coordinates"], node_by_id[b]["coordinates"])
        adj[a].append((b, d)); adj[b].append((a, d))
    return {"rooms": rooms, "doors": doors, "nodes": nodes, "edges": edges, "risk": risk,
            "elevators": elevs, "room_by_id": room_by_id, "door_by_id": door_by_id,
            "node_by_id": node_by_id, "adj": adj, "mounts": _build_mount_index(fl)}

def _room_type(room_by_id, rid):
    r = room_by_id.get(rid)
    return ((r.get("properties") or {}).get("roomType") or "") if r else ""

def _should_keep_doorway(n, idx):
    rooms = n.get("rooms") or []
    door_type = (n.get("doorType") or "").lower()
    if door_type == "fire":
        is_open = None
        inward_into_room = False
        for sid in n.get("sourceDoorIds") or []:
            d = idx["door_by_id"].get(sid)
            if not d:
                continue
            p = d.get("properties") or {}
            if p.get("isNormallyOpen") is True:
                is_open = True
            # 门向内开且门后空间为房间(非走廊/开放空间) -> 门套侧墙落在房间内，不布
            if n.get("openDirection") == "inward":
                sir = p.get("swingIntoRoom")
                if sir and _room_type(idx["room_by_id"], sir) == "room":
                    inward_into_room = True
        if is_open is not True or inward_into_room:
            return False
    if not rooms: return True
    types = [_room_type(idx["room_by_id"], rid) for rid in rooms]
    if types and all(t in INFRA_ROOM_TYPES or t == "" for t in types): return False
    if any(t in IMPORTANT_ROOM_TYPES for t in types): return True
    if any(t in OPEN_TYPES for t in types) and any(t and t not in OPEN_TYPES and t not in INFRA_ROOM_TYPES for t in types):
        return True
    if types and all(t in OPEN_TYPES for t in types): return False
    return False

def collect_door_candidates(floor_no, idx, params):
    cands, seen = [], set()
    for n in idx["nodes"]:
        if n.get("type") != "doorway" or not _should_keep_doorway(n, idx): continue
        key = _quantize_opening(n["coordinates"])
        if key in seen: continue
        seen.add(key)
        x, y = n["coordinates"]
        cands.append({
            "semanticTag": "door", "subType": "base",
            "plannedCoordinates": [x, y], "coordinates": [x, y],
            "floor": floor_no,
            "locationDesc": f"{floor_no}F 门口（{n.get('label') or n['id']}）",
            "sourceNodeId": n["id"], "sourceNodeType": "doorway",
            "riskLevel": float(n.get("riskLevel") or 1.0),
            "adjacentRooms": n.get("rooms") or [],
            "mountType": "door_frame", "snapDist_m": 0.0, "priority": 40,
        })
    return cands

def collect_intersection_candidates(floor_no, idx, params):
    snap_max = float(params["offsets"]["snapMax"])
    min_deg = int(params["minJunctionDegree"])
    cands = []
    for n in idx["nodes"]:
        if n.get("type") != "intersection": continue
        cid = n["id"]
        useful = []
        for nid, ed in idx["adj"].get(cid) or []:
            other = idx["node_by_id"].get(nid)
            if other and other.get("type") != "room":
                useful.append((nid, ed, other))
        if len(useful) < min_deg: continue
        c0 = n["coordinates"]
        sx, sy, mount, sd = _snap_to_mount(c0[0], c0[1], idx["mounts"], snap_max, prefer=("column", "wall", "door"))
        label = n.get("label") or cid
        cands.append({
            "semanticTag": "intersection", "subType": "base",
            "plannedCoordinates": list(c0), "coordinates": [sx, sy],
            "floor": floor_no,
            "locationDesc": f"{floor_no}F 交叉口（{label}）" + ("" if mount != "ceiling_pendant" else " · 建议短吊杆"),
            "sourceNodeId": cid, "sourceNodeType": "intersection",
            "riskLevel": float(n.get("riskLevel") or 0.5),
            "mountType": mount, "snapDist_m": round(sd, 2),
            "priority": 30 if mount != "ceiling_pendant" else 15,
        })
        useful.sort(key=lambda t: -t[1])
        used_dirs = set()
        for nid, ed, other in useful[:4]:
            if len(used_dirs) >= 2 or ed < 4.0: continue
            dx = other["coordinates"][0] - c0[0]; dy = other["coordinates"][1] - c0[1]
            ux, uy = _unit(dx, dy); dlab = _dir_label(ux, uy)
            if dlab in used_dirs: continue
            used_dirs.add(dlab)
            step = min(2.0, max(1.0, ed * 0.35))
            px, py = c0[0] + ux * step, c0[1] + uy * step
            sx2, sy2, mount2, sd2 = _snap_to_mount(px, py, idx["mounts"], snap_max, prefer=("column", "wall", "door"))
            if mount2 == "ceiling_pendant" and mount != "ceiling_pendant":
                continue
            cands.append({
                "semanticTag": "intersection", "subType": "dir", "direction": dlab,
                "plannedCoordinates": [px, py], "coordinates": [sx2, sy2],
                "floor": floor_no,
                "locationDesc": f"{floor_no}F 交叉口（{label} · {dlab}向）",
                "sourceNodeId": cid, "sourceNodeType": "intersection",
                "riskLevel": 0.5, "mountType": mount2, "snapDist_m": round(sd2, 2),
                "priority": 22 if mount2 != "ceiling_pendant" else 10,
            })
    return cands

def collect_stair_candidates(floor_no, idx, params):
    warn = float(params["offsets"]["stairWarning"]); snap_max = float(params["offsets"]["snapMax"])
    cands = []
    for n in idx["nodes"]:
        if not (n.get("type") == "facility" and n.get("facilityType") == "staircase"): continue
        c0 = n["coordinates"]; label = n.get("label") or n["id"]
        sx, sy, mount, sd = _snap_to_mount(c0[0], c0[1], idx["mounts"], snap_max, prefer=("stair_wall", "wall", "column", "door"))
        if mount == "ceiling_pendant": mount = "wall"
        cands.append({
            "semanticTag": "stair", "subType": "base",
            "plannedCoordinates": list(c0), "coordinates": [sx, sy],
            "floor": floor_no,
            "locationDesc": f"{floor_no}F 楼梯入口（{label}）· 装前室/梯段侧墙",
            "sourceNodeId": n["id"], "sourceNodeType": "facility",
            "riskLevel": float(n.get("riskLevel") or 10.0),
            "mountType": mount, "snapDist_m": round(sd, 2), "priority": 55,
        })
        best = None
        for nid, ed in idx["adj"].get(n["id"]) or []:
            other = idx["node_by_id"].get(nid)
            if not other or other.get("type") not in ("doorway", "intersection"): continue
            d = _dist(c0, other["coordinates"])
            if best is None or d < best[0]: best = (d, other)
        if best is not None:
            other = best[1]
            ux, uy = _unit(other["coordinates"][0]-c0[0], other["coordinates"][1]-c0[1])
            if ux or uy:
                px, py = c0[0]+ux*warn, c0[1]+uy*warn
                sx2, sy2, mount2, sd2 = _snap_to_mount(px, py, idx["mounts"], snap_max, prefer=("wall", "column", "door", "stair_wall"))
                cands.append({
                    "semanticTag": "stair", "subType": "warning",
                    "plannedCoordinates": [px, py], "coordinates": [sx2, sy2],
                    "floor": floor_no,
                    "locationDesc": f"{floor_no}F 楼梯预警（{label} 前约 {warn:.0f}m）",
                    "sourceNodeId": n["id"], "sourceNodeType": "facility",
                    "riskLevel": 10.0, "mountType": mount2, "snapDist_m": round(sd2, 2), "priority": 50,
                })
    return cands

def collect_elevator_candidates(floor_no, idx, params):
    snap_max = float(params["offsets"]["snapMax"]); cands = []
    elev_nodes = [n for n in idx["nodes"] if n.get("type")=="facility" and n.get("facilityType")=="elevator"]
    for n in elev_nodes:
        c0 = n["coordinates"]; label = n.get("label") or n["id"]
        cands.append({
            "semanticTag": "elevator", "subType": "elevator_door",
            "plannedCoordinates": list(c0), "coordinates": list(c0),
            "floor": floor_no,
            "locationDesc": f"{floor_no}F 电梯口（{label}）· 门套或呼梯板旁",
            "sourceNodeId": n["id"], "sourceNodeType": "facility",
            "riskLevel": float(n.get("riskLevel") or 1.0),
            "mountType": "door_frame", "snapDist_m": 0.0, "priority": 45,
        })
    lobbies = []
    for r in idx["rooms"]:
        props = r.get("properties") or {}
        if props.get("roomType") == "elevator_lobby" and props.get("centroid"):
            lobbies.append(props["centroid"])
    if not lobbies and elev_nodes:
        used = [False]*len(elev_nodes)
        for i, n in enumerate(elev_nodes):
            if used[i]: continue
            cluster = [n]; used[i] = True
            for j in range(i+1, len(elev_nodes)):
                if not used[j] and _dist(n["coordinates"], elev_nodes[j]["coordinates"]) < 8:
                    cluster.append(elev_nodes[j]); used[j] = True
            lobbies.append([sum(x["coordinates"][0] for x in cluster)/len(cluster),
                            sum(x["coordinates"][1] for x in cluster)/len(cluster)])
    for cen in lobbies:
        sx, sy, mount, sd = _snap_to_mount(cen[0], cen[1], idx["mounts"], snap_max, prefer=("column", "wall"))
        cands.append({
            "semanticTag": "elevator", "subType": "hall_center",
            "plannedCoordinates": list(cen), "coordinates": [sx, sy],
            "floor": floor_no, "locationDesc": f"{floor_no}F 电梯厅 · 优先柱/侧墙",
            "sourceNodeId": None, "sourceNodeType": "elevator_lobby",
            "riskLevel": 1.0, "mountType": mount, "snapDist_m": round(sd, 2), "priority": 35,
        })
    return cands

def consolidate(cands, dist_m):
    ordered = sorted(cands, key=lambda c: (-c.get("priority", 0), c["semanticTag"]))
    kept = []
    for c in ordered:
        absorbed = False
        for k in kept:
            if k["floor"] != c["floor"] or _dist(k["coordinates"], c["coordinates"]) > dist_m:
                continue
            if _SEM_RANK.get(c["semanticTag"], 0) > _SEM_RANK.get(k["semanticTag"], 0):
                for field in ("semanticTag", "subType", "locationDesc", "sourceNodeId", "sourceNodeType",
                              "mountType", "direction", "plannedCoordinates"):
                    if c.get(field) is not None: k[field] = c[field]
                k["coordinates"] = list(c["coordinates"])
                k["riskLevel"] = max(float(k.get("riskLevel") or 0), float(c.get("riskLevel") or 0))
                if c.get("adjacentRooms"): k["adjacentRooms"] = c["adjacentRooms"]
            else:
                k["riskLevel"] = max(float(k.get("riskLevel") or 0), float(c.get("riskLevel") or 0))
                if c.get("adjacentRooms") and not k.get("adjacentRooms"):
                    k["adjacentRooms"] = c["adjacentRooms"]
                if k.get("mountType") == "ceiling_pendant" and c.get("mountType") != "ceiling_pendant":
                    k["mountType"] = c["mountType"]; k["coordinates"] = list(c["coordinates"])
            absorbed = True; break
        if not absorbed: kept.append(dict(c))
    return kept

def assign_ids(beacons, params, install_date):
    by_floor = defaultdict(list)
    for b in beacons: by_floor[int(b["floor"])].append(b)
    out, minor = [], 10100
    tx_map = params["txPowerBySemantic"]
    for fl in sorted(by_floor.keys()):
        seq = 0
        for b in by_floor[fl]:
            seq += 1; minor += 1
            sem = b["semanticTag"]; mount = b.get("mountType") or "wall"
            height = params["installHeightCeiling"] if mount == "ceiling_pendant" else params["installHeightWall"]
            item = {
                "beaconId": f"BK-{fl:02d}-{seq:03d}", "uuid": IBEACON_UUID,
                "major": fl, "minor": minor,
                "coordinates": _round_coord(b["coordinates"]),
                "plannedCoordinates": _round_coord(b.get("plannedCoordinates") or b["coordinates"]),
                "floor": fl, "locationDesc": b["locationDesc"], "mountType": mount,
                "installHeight": height, "txPower": tx_map.get(sem, -10),
                "broadcastInterval": params["broadcastInterval"],
                "batteryModel": params["batteryModel"],
                "expectedLifespan": params["expectedLifespanYears"],
                "semanticTag": sem, "installDate": install_date,
                "sourceNodeId": b.get("sourceNodeId"), "sourceNodeType": b.get("sourceNodeType"),
                "riskLevel": float(b.get("riskLevel") or 0.5), "snapDist_m": b.get("snapDist_m", 0),
            }
            if b.get("adjacentRooms"): item["adjacentRooms"] = b["adjacentRooms"]
            if b.get("subType"): item["subType"] = b["subType"]
            if b.get("direction"): item["direction"] = b["direction"]
            out.append(item)
    return out

PROCUREMENT = {
    "protocol": ["iBeacon", "Eddystone-UID"], "uuid": IBEACON_UUID,
    "recommendedModels": [
        {"role": "主力（门套/柱/侧墙）", "vendor": "Minew 深圳矿鑫", "model": "i10 / E8 壁挂型",
         "why": "可换 CR2477、iBeacon、SDK 成熟、批量价友好，适合校舍大规模部署",
         "battery": "CR2477，约 4–6 年（300ms 广播）", "ip": "IP65–IP67",
         "mount": "3M 胶 + 螺钉孔，壁挂/门套", "unitPriceCNY": "35–80（批量）",
         "qtyHint": "按 wall/door_frame/column 点数"},
        {"role": "主力备选", "vendor": "Feasycom 飞易通", "model": "FSC-BP108 / BP104 可换电池",
         "why": "续航与可编程性好，支持 iBeacon，国内供货稳定",
         "battery": "CR2477，约 5–8 年", "ip": "IP67", "mount": "壁挂/磁吸底座",
         "unitPriceCNY": "40–90", "qtyHint": "与 Minew 二选一统一型号"},
        {"role": "天花短吊杆（少量）", "vendor": "Minew / Feasycom", "model": "同系列 + 吊装支架/吸顶盒",
         "why": "仅无法靠柱靠墙的交叉口；有效高度约 3 m", "battery": "同壁挂，必须可换电",
         "ip": "IP65+", "mount": "短吊杆 0.5–1.0 m 或灯盘共杆", "unitPriceCNY": "信标+支架 60–120",
         "qtyHint": "summary.byMount.ceiling_pendant"},
        {"role": "楼梯高风险点（可选加固）", "vendor": "Minew", "model": "MBM01 / 工业外壳版",
         "why": "抗磕碰、IP67，适合楼梯间与学生活动密集区", "battery": "长续航可换电",
         "ip": "IP67", "mount": "侧墙螺钉固定", "unitPriceCNY": "80–150", "qtyHint": "stair 语义点"},
    ],
    "notRecommended": ["一次性不可换电池贴纸型", "仅 USB 供电型", "无品牌超低价模块"],
    "acceptanceTests": [
        "手机 App 在点位 3–5 m 内稳定收到对应 major/minor",
        "楼梯预警点：距梯段入口约 3–4 m 可触发语音",
        "吊装点：站立高度 RSSI 与墙装同量级（必要时 +2 dB txPower）",
    ],
}
CONSTRUCTION = {
    "principles": [
        "语义坐标可在 2.5 m 内平移到柱、门套、侧墙；以 mountType 与 coordinates 为施工依据",
        "安装高度：墙/门套/柱 2.0–2.2 m；天花吊装有效高度约 3.0 m（非贴 4 m+ 板底）",
        "禁止占用消防栓、疏散指示灯、弱电箱正面；可借侧缘",
        "楼梯、电梯口必须墙装或门套装，不得只靠走廊天花",
        "同一柱/门套多点合并后只装 1 台高优先级语义",
    ],
    "byMountType": {
        "door_frame": "门套侧或门楣下，避开闭门器；胶+螺钉，高度 2.0–2.2 m",
        "column": "结构柱朝向走廊一侧，避免被广告牌完全遮挡",
        "wall": "走廊侧墙、楼梯前室墙、电梯厅侧墙；距阴角 ≥0.3 m",
        "ceiling_pendant": "短吊杆或与灯具共杆，有效高度≈3 m；必须可换电池并登记检修周期",
    },
    "ops": [
        "每年抽检 20% 点位电量与广播；天花点优先巡检",
        "更换电池后保持同一 major/minor",
        "装修拆除时更新部署 JSON 的 status 字段",
    ],
}

def build_plan(geo, params=None):
    params = {**DEFAULT_PARAMS, **(params or {})}
    params["offsets"] = {**DEFAULT_PARAMS["offsets"], **(params.get("offsets") or {})}
    install_date = date.today().isoformat()
    all_cands = []
    for fk, fl in (geo.get("floors") or {}).items():
        floor_no = int(fk); idx = _index_floor(fl)
        all_cands.extend(collect_door_candidates(floor_no, idx, params))
        all_cands.extend(collect_intersection_candidates(floor_no, idx, params))
        all_cands.extend(collect_stair_candidates(floor_no, idx, params))
        all_cands.extend(collect_elevator_candidates(floor_no, idx, params))
    n_before = len(all_cands)
    merged = consolidate(all_cands, float(params["consolidateDist"]))
    beacons = assign_ids(merged, params, install_date)
    by_floor = Counter(str(b["floor"]) for b in beacons)
    by_sem = Counter(b["semanticTag"] for b in beacons)
    by_sub = Counter(f"{b['semanticTag']}/{b.get('subType') or 'base'}" for b in beacons)
    by_mount = Counter(b.get("mountType") or "unknown" for b in beacons)
    return {
        "schemaVersion": "1.1-constructable",
        "generatedBy": "gen_beacon_plan.py",
        "generatedAt": install_date,
        "venueId": geo.get("venueId"), "venueName": geo.get("venueName"),
        "sourceGeojson": "school_building_01_map_v9.geojson",
        "docRef": "docs/07-信标部署方案.md", "uuid": IBEACON_UUID,
        "defaultParams": params,
        "strategy": {
            "principle": "可施工优先：门套/柱/侧墙吸附；决策点与安全点覆盖；压缩纯天花阵列",
            "sources": "topology doorway / degree≥3 intersection / stair&elevator；吸附 columns/walls/doors",
            "simplifications": [
                "门口仅重要房间；防火门默认不布（常闭/内开归属房间不布）；同开口只 1 个",
                "交叉口仅度≥3；中心优先吸附结构柱；方向标最多 2 条且尽量靠墙",
                "楼梯入口+预警必须可墙装；电梯口门套装",
                "2 m 内不同语义合并；平移≤2.5 m 到可附着点",
                "无法吸附则 ceiling_pendant（短吊杆≈3 m），并计入运维重点",
            ],
            "stats": {"candidatesBeforeMerge": n_before, "afterMerge": len(beacons)},
        },
        "construction": CONSTRUCTION, "procurement": PROCUREMENT,
        "beacons": beacons,
        "summary": {
            "total": len(beacons),
            "byFloor": dict(sorted(by_floor.items())),
            "bySemantic": dict(by_sem), "bySubType": dict(by_sub), "byMount": dict(by_mount),
            "ceilingPendantCount": by_mount.get("ceiling_pendant", 0),
            "wallLikeCount": sum(by_mount.get(k, 0) for k in ("wall", "column", "door_frame")),
        },
    }

def main():
    ap = argparse.ArgumentParser(description="可施工/可运维 ROI 蓝牙信标部署方案生成器")
    ap.add_argument("--geo", default=str(DEFAULT_GEO), help="v9 楼层 GeoJSON 路径")
    ap.add_argument("--out", default=str(DEFAULT_OUT), help="输出信标部署方案 JSON")
    args = ap.parse_args()
    geo = json.loads(Path(args.geo).read_text(encoding="utf-8"))
    plan = build_plan(geo)
    Path(args.out).write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
    s = plan["summary"]
    print(f"venue: {plan['venueName']}")
    print(f"total: {s['total']}")
    print(f"  byFloor: {s['byFloor']}")
    print(f"  bySemantic: {s['bySemantic']}")
    print(f"  bySubType: {s['bySubType']}")
    print(f"  byMount: {s['byMount']}")
    print(f"  wall-like: {s['wallLikeCount']}  ceiling_pendant: {s['ceilingPendantCount']}")
    print(f"written: {args.out}")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
