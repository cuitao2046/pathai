# -*- coding: utf-8 -*-
"""指纹采集网格生成（v2：设计层 / 采集层两层模型）。

设计层（design）：
  - 全场墙体包围盒左下角向下取偶米作为统一原点 O；
  - 2m 底网：可通行区内偶-偶格位；
  - 触发细分：交叉路口（唯一邻居 >=3）、门口（doorway + outward_door）、
    高风险（楼梯/电梯入口）命中 2m 格后切 1m；
  - 1m 格位键去重，优先级 路口 > 门口 > 高风险 > 普通，取消 0.6m 距离阈值；
  - 编号 FP-{f}-C{col:03d}R{row:03d}[-J/-D/-S]，坐标派生、增删点不重排。

采集层（collection，v11 覆盖口径）：
  - 读取地图包中实际部署信标（含 installHeightM），按 3D 斜距估算预测 RSSI；
  - RSSI = A - 10n·log10(d3) - Σ穿墙衰减 - 门数×2dB，
      A=-52dBm（E5 最低档 -12dBm @1m 推导），n=3.5，
      墙按材质分档 partition=8 / brick=12 / concrete=15 dB（未知按 partition），
      同一簇（0.45m 内多次求交）取最高档一次计费，交点 1.2m 内有门按门 2dB；
  - 两个口径：
      plan10m85: 预测 RSSI >= -85dBm 的信标 >=3（字段名保留兼容，10m 不再是硬门槛）
      geo15m:   3D 斜距 <=15m 的信标 >=3
  - 按测试路线（result/beacon_routes.json）给点打 routeTags。

输出：
  - result/fingerprint_grid.json（设计层全量 + 采集层标记）
  - result/f2_collectable_points.csv / .md（F2 可采集点清单）

用法：
    python generate_fingerprint_grid.py [geojson] [out_json]
    python generate_fingerprint_grid.py --mode route --routes result/beacon_routes.json
"""
import argparse
import csv
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path

from shapely.geometry import LineString, Point, shape
from shapely.ops import unary_union
from shapely.strtree import STRtree

BASE_DIR = Path(__file__).resolve().parent.parent.parent
GEO_IN = str(BASE_DIR / "result" / "school_building_01_map_v9.geojson")
OUT_DEFAULT = str(BASE_DIR / "result" / "fingerprint_grid.json")
BEACON_PACKAGE = str(BASE_DIR / "backend" / "maps_data" / "school-building-01" / "2026-11-08.2.json")
ROUTES_DEFAULT = str(BASE_DIR / "result" / "beacon_routes.json")
SIM_BEACONS = str(BASE_DIR / "result" / "simulated_beacons.json")
GRID_VERSION = "11.1.0"

# ----------------------------- 设计层参数 -----------------------------
NORMAL_SPACING_M = 2.0
SPLIT_SPACING_M = 1.0
INTERSECTION_RADIUS_M = 1.0
DOOR_RADIUS_M = 1.0
RISK_RADIUS_M = 3.0
WALL_CLEARANCE_M = 0.3  # 仅用于统计贴墙比例；按用户拍板不做换点
LARGE_SPACING_M = 3.0
LARGE_AREA_MIN_M2 = 30.0

# ----------------------------- 采集层参数（v11：3D 斜距 + 材质墙衰减） -----------------------------
BEACON_RSSI_AT_1M_DBM = -52.0    # E5 最低档 -12dBm @1m（厂商标称 -52，docs/07 §2.3）
BEACON_PATH_LOSS_N = 3.5
RSSI_THRESHOLD_DBM = -85.0
COLLECT_MIN_BEACONS = 3
PLAN_RADIUS_M = 10.0             # 仅作展示统计（visible10m），不再是硬门槛
GEO_RADIUS_M = 15.0
PHONE_HEIGHT_M = 1.2             # 手机持高（fit_location_params.PHONE_HEIGHT_M）
DEFAULT_INSTALL_HEIGHT_M = 2.5   # 台账缺省安装高（信标 installHeightM 全量=2.5）
WALL_ATTENUATION_DB = {"partition": 8.0, "brick": 12.0, "concrete": 15.0}
UNKNOWN_MATERIAL_DB = 8.0        # 材质缺失按最轻隔断档（保守偏乐观，现场标定校正）
DOOR_ATTENUATION_DB = 2.0
DOOR_PROXIMITY_M = 1.2           # 交点距门 <=1.2m 视为走门
CROSSING_CLUSTER_TOL_M = 0.45    # 同一墙体多次求交的聚类容差

REGION_PRIORITY = {"intersection": 1, "doorway": 2, "high_risk": 3, "normal": 4}
REGION_SUFFIX = {"intersection": "-J", "doorway": "-D", "high_risk": "-S", "normal": ""}
REGION_TEXT = {
    "normal": "普通",
    "intersection": "交叉路口",
    "doorway": "门口",
    "high_risk": "楼梯/电梯口",
}

FP_ID_RE = r"^FP-\d+-C\d{3}R\d{3}(-[JDS])?$"

# 路线模式参数（保留兼容；路线模式不是本轮设计层主路径）
ROUTE_FP_SPACING_M = 1.0
ROUTE_FP_SAFE_SPACING_M = 1.0
ROUTE_FP_SAFE_RADIUS_M = 3.0
ROUTE_CORRIDOR_HALF_M = 3.0


def dist(a, b):
    return math.hypot(a[0] - b[0], a[1] - b[1])


def build_walkable(geo_floor):
    feats = (geo_floor.get("walkable_regions") or {}).get("features", [])
    polys = []
    for f in feats:
        g = f.get("geometry")
        if not g:
            continue
        try:
            polys.append(shape(g))
        except Exception:
            continue
    if not polys:
        return None
    return unary_union(polys)


def build_walls(geo_floor):
    """墙体线串并集，用于 0.3m 贴墙替换。"""
    walls = []
    for f in (geo_floor.get("geometry") or {}).get("walls", []):
        g = f.get("geometry") or {}
        try:
            geom = shape(g)
        except Exception:
            continue
        if geom.is_empty:
            continue
        walls.append(geom)
    if not walls:
        return None
    return unary_union(walls)


def wall_bbox(geo):
    xs, ys = [], []
    for fl in (geo.get("floors") or {}).values():
        for f in (fl.get("geometry") or {}).get("walls", []):
            g = shape(f.get("geometry") or {})
            if g.is_empty:
                continue
            minx, miny, maxx, maxy = g.bounds
            xs.extend([minx, maxx])
            ys.extend([miny, maxy])
    if not xs:
        for fl in (geo.get("floors") or {}).values():
            w = build_walkable(fl)
            if w is not None:
                minx, miny, maxx, maxy = w.bounds
                xs.extend([minx, maxx])
                ys.extend([miny, maxy])
    if not xs:
        raise ValueError("地图包缺少墙体/可通行区，无法确定原点")
    return min(xs), min(ys), max(xs), max(ys)


def even_origin(geo):
    minx, miny, _, _ = wall_bbox(geo)
    return math.floor(minx / 2.0) * 2, math.floor(miny / 2.0) * 2


def grid_points_in_polygon(poly, spacing, origin=None):
    """按 spacing 生成规则网格点；origin 给定时从原点起算（整数米格位）。"""
    if origin is None:
        minx, miny, maxx, maxy = poly.bounds
    else:
        minx, miny = origin
        _, _, maxx, maxy = poly.bounds
    pts = []
    x = minx
    while x <= maxx + 1e-9:
        y = miny
        while y <= maxy + 1e-9:
            p = Point(x, y)
            if poly.contains(p) or poly.distance(p) < 1e-6:
                pts.append((x, y))
            y += spacing
        x += spacing
    return pts


def _inside(poly, x, y):
    p = Point(x, y)
    return poly.contains(p) or poly.distance(p) < 1e-6


def _cell_poly(origin, i, j):
    ox, oy = origin
    return shape({
        "type": "Polygon",
        "coordinates": [[
            (ox + 2 * i, oy + 2 * j),
            (ox + 2 * i + 2, oy + 2 * j),
            (ox + 2 * i + 2, oy + 2 * j + 2),
            (ox + 2 * i, oy + 2 * j + 2),
            (ox + 2 * i, oy + 2 * j),
        ]],
    })


def _cells_near(anchor, radius, walk, origin):
    ox, oy = origin
    minx, miny, maxx, maxy = walk.bounds
    i0 = math.floor((minx - ox) / 2) - 2
    i1 = math.ceil((maxx - ox) / 2) + 2
    j0 = math.floor((miny - oy) / 2) - 2
    j1 = math.ceil((maxy - oy) / 2) + 2
    out = set()
    p = Point(anchor[0], anchor[1])
    for i in range(i0, i1 + 1):
        for j in range(j0, j1 + 1):
            cell = _cell_poly(origin, i, j)
            if cell.distance(p) > radius + 1e-9:
                continue
            if cell.intersection(walk).is_empty:
                continue
            out.add((i, j))
    return out


def _split_points(origin, i, j, walk):
    """一个 2m 格切 1m 后的新增点（中心 + 四边中点；四角已是 2m 底网点）。"""
    ox, oy = origin
    cands = [
        (ox + 2 * i + 1, oy + 2 * j + 1),
        (ox + 2 * i + 1, oy + 2 * j),
        (ox + 2 * i + 1, oy + 2 * j + 2),
        (ox + 2 * i, oy + 2 * j + 1),
        (ox + 2 * i + 2, oy + 2 * j + 1),
    ]
    return [(x, y) for (x, y) in cands if _inside(walk, x, y)]


def _walk_degree(geo_floor):
    deg = {}
    for e in (geo_floor.get("topology") or {}).get("edges", []):
        if not (e.get("walkable") and e.get("blindAccessible", True)):
            continue
        a, b = e.get("from"), e.get("to")
        if a is None or b is None or a == b:
            continue
        deg.setdefault(a, set()).add(b)
        deg.setdefault(b, set()).add(a)
    return {k: len(v) for k, v in deg.items()}


def collect_triggers(geo_floor):
    """返回三类触发点；riskNodes 按 type 分流，不再一律 stair_entrance。"""
    out = []  # dict(kind, priority, x, y, node_id, node_type, radius)
    nodes = (geo_floor.get("topology") or {}).get("nodes", [])
    degree = _walk_degree(geo_floor)
    for n in nodes:
        t = n.get("type")
        c = n.get("coordinates")
        if not c or len(c) < 2:
            continue
        x, y = float(c[0]), float(c[1])
        if t == "intersection" and degree.get(n.get("id"), 0) >= 3:
            out.append({"kind": "intersection", "priority": 1, "x": x, "y": y,
                        "node_id": n.get("id"), "node_type": "intersection",
                        "radius": INTERSECTION_RADIUS_M})
        elif t == "doorway":
            out.append({"kind": "doorway", "priority": 2, "x": x, "y": y,
                        "node_id": n.get("id"), "node_type": "doorway",
                        "radius": DOOR_RADIUS_M})
        elif t in ("facility", "room"):
            rt = n.get("roomType") or n.get("facilityType") or ""
            if rt in ("staircase", "elevator"):
                out.append({"kind": "high_risk", "priority": 3, "x": x, "y": y,
                            "node_id": n.get("id"), "node_type": rt,
                            "radius": RISK_RADIUS_M})
    for rn in (geo_floor.get("accessibility") or {}).get("riskNodes", []):
        c = rn.get("coordinates")
        if not c or len(c) < 2:
            continue
        x, y = float(c[0]), float(c[1])
        rt = str(rn.get("type") or "")
        if rt == "outward_door":
            # 外门是门口类，1m 半径；绝不能当楼梯口用 3m
            out.append({"kind": "doorway", "priority": 2, "x": x, "y": y,
                        "node_id": rn.get("id"), "node_type": "outward_door",
                        "radius": DOOR_RADIUS_M})
        elif rt in ("stair_entrance", "elevator_entrance"):
            out.append({"kind": "high_risk", "priority": 3, "x": x, "y": y,
                        "node_id": rn.get("id"), "node_type": rt,
                        "radius": RISK_RADIUS_M})
    return out


def _snap(origin, x, y):
    return int(round(x - origin[0])), int(round(y - origin[1]))


def _coord(origin, u, v):
    return origin[0] + u, origin[1] + v


def _wall_distance(walls, x, y):
    if walls is None:
        return math.inf
    return walls.distance(Point(x, y))


def build_wall_model(geo_floor):
    """构建墙体/门求交模型，供覆盖判定计算穿墙衰减。

    - 墙体取地图包 geometry.walls（LineString + properties.material）；
    - 门取 geometry.doors（Point），交点 DOOR_PROXIMITY_M 内有门按门衰减计。
    返回 None 表示该层无墙体，穿墙衰减按 0 处理。
    """
    walls, lines = [], []
    for f in (geo_floor.get("geometry") or {}).get("walls", []):
        g = f.get("geometry") or {}
        if g.get("type") != "LineString":
            continue
        try:
            geom = shape(g)
        except Exception:
            continue
        if geom.is_empty or geom.length < 1e-9:
            continue
        walls.append(f)
        lines.append(geom)
    if not walls:
        return None
    doors = []
    for d in (geo_floor.get("geometry") or {}).get("doors", []):
        g = d.get("geometry") or {}
        try:
            geom = shape(g)
        except Exception:
            continue
        if geom.is_empty or geom.geom_type != "Point":
            continue
        doors.append(geom)
    return {
        "walls": walls,
        "lines": lines,
        "tree": STRtree(lines),
        "doors": doors,
        "door_tree": STRtree(doors) if doors else None,
    }


def _wall_att_db(wall_feature):
    mat = ((wall_feature.get("properties") or {}).get("material"))
    return WALL_ATTENUATION_DB.get(mat, UNKNOWN_MATERIAL_DB)


def wall_attenuation_db(a, b, model):
    """a→b 线段的穿墙衰减（dB）：0.45m 簇内取最高材质档，门邻近按 2dB。"""
    if model is None:
        return 0.0
    seg = LineString([a, b])
    hits = []
    for h in model["tree"].query(seg):
        i = int(h)
        inter = seg.intersection(model["lines"][i])
        if inter.is_empty:
            continue
        pts = [inter] if inter.geom_type == "Point" else (
            list(inter.geoms) if inter.geom_type == "MultiPoint" else [])
        for ip in pts:
            hits.append((seg.project(ip), i))
    if not hits:
        return 0.0
    hits.sort()
    clusters = []
    for t, i in hits:
        if clusters and t - clusters[-1][0] <= CROSSING_CLUSTER_TOL_M:
            clusters[-1][1].append(i)
        else:
            clusters.append([t, [i]])
    att = 0.0
    for t, idxs in clusters:
        if (model["door_tree"] is not None
                and len(model["door_tree"].query(seg.interpolate(t),
                                                 predicate="dwithin",
                                                 distance=DOOR_PROXIMITY_M)) > 0):
            att += DOOR_ATTENUATION_DB
        else:
            att += max(_wall_att_db(model["walls"][i]) for i in idxs)
    return att


def _rssi_at(x, y, b, wall_model, a=BEACON_RSSI_AT_1M_DBM, n=BEACON_PATH_LOSS_N):
    """单枚信标在 (x,y) 处的 3D 含墙预测 RSSI；a/n 可注入标定实测值。"""
    dxy = dist((x, y), (b["x"], b["y"]))
    dz = float(b.get("installHeightM") or DEFAULT_INSTALL_HEIGHT_M) - PHONE_HEIGHT_M
    d3 = math.hypot(dxy, dz)
    return (float(a)
            - 10 * float(n) * math.log10(max(d3, 0.3))
            - wall_attenuation_db((x, y), (b["x"], b["y"]), wall_model))


def coverage_flags(x, y, floor_beacons, wall_model=None,
                   a=BEACON_RSSI_AT_1M_DBM, n=BEACON_PATH_LOSS_N):
    """采集层覆盖判定（v11 口径：3D 斜距 + 材质墙衰减 + 门 2dB）。

    - plan10m85：预测 RSSI>=-85dBm 的信标 >=3（字段名保留兼容；10m 半径不再是硬门槛，
      -85dBm 在 A=-52/n=3.5 下自由空间最远约 15.9m，半径约束已隐含）；
    - geo15m：3D 斜距 <=15m 的信标 >=3（备用几何口径）；
    - visible10m 展示口径 = 3D 斜距 <=10m 且 RSSI 达标。
    """
    vis10 = vis15 = rssi_ok = 0
    nearest = None
    strongest = None
    for b in floor_beacons:
        dxy = dist((x, y), (b["x"], b["y"]))
        dz = float(b.get("installHeightM") or DEFAULT_INSTALL_HEIGHT_M) - PHONE_HEIGHT_M
        d3 = math.hypot(dxy, dz)
        rssi = _rssi_at(x, y, b, wall_model, a=a, n=n)
        if nearest is None or dxy < nearest:
            nearest = dxy
        if strongest is None or rssi > strongest:
            strongest = rssi
        if rssi >= RSSI_THRESHOLD_DBM:
            rssi_ok += 1
            if d3 <= PLAN_RADIUS_M:
                vis10 += 1
        if d3 <= GEO_RADIUS_M:
            vis15 += 1
    plan_ok = rssi_ok >= COLLECT_MIN_BEACONS
    geo_ok = vis15 >= COLLECT_MIN_BEACONS
    return {
        "visible10m": vis10,
        "visible15m": vis15,
        "nearestBeaconM": round(nearest, 3) if nearest is not None else None,
        "strongestRssi": round(strongest, 1) if strongest is not None else None,
        "okBeacons": rssi_ok,
        "collectablePlan10m": bool(plan_ok),
        "collectableGeo15m": bool(geo_ok),
    }


def coverage_with_sim(base_ok, x, y, floor_sim, wall_model,
                      a=BEACON_RSSI_AT_1M_DBM, n=BEACON_PATH_LOSS_N):
    """补装仿真口径：在真实部署基线之上叠加虚拟信标（不改 collectable 语义）。

    返回 collectableWithSim 与 simAddedBy（使该点达标的虚拟信标，按 RSSI 从强到弱，
    只列出真正补齐所缺信标数的最小集合）。
    """
    if base_ok >= COLLECT_MIN_BEACONS or not floor_sim:
        return {"collectableWithSim": base_ok >= COLLECT_MIN_BEACONS, "simAddedBy": [], "okWithSim": base_ok}
    hits = []
    for b in floor_sim:
        r = _rssi_at(x, y, b, wall_model, a=a, n=n)
        if r >= RSSI_THRESHOLD_DBM:
            hits.append((r, b["beaconId"]))
    hits.sort(key=lambda z: -z[0])
    need = COLLECT_MIN_BEACONS - base_ok
    ok = len(hits) >= need
    return {
        "collectableWithSim": bool(ok),
        "simAddedBy": [bid for _, bid in hits[:need]] if ok else [],
        "okWithSim": base_ok + len(hits),
    }


def build_floor_grid(geo_floor, floor_no, origin, beacons, route_corridors, sim_beacons=None,
                     a=BEACON_RSSI_AT_1M_DBM, n=BEACON_PATH_LOSS_N):
    walk = build_walkable(geo_floor)
    if walk is None:
        return [], {"error": "no walkable regions"}, None
    walls = build_walls(geo_floor)
    wall_model = build_wall_model(geo_floor)
    cands = []

    # ---- A. 2m 底网（偶-偶）----
    base_pts = grid_points_in_polygon(walk, NORMAL_SPACING_M, origin)
    for (x, y) in base_pts:
        u, v = _snap(origin, x, y)
        cands.append({
            "x": x, "y": y, "u": u, "v": v,
            "region": "normal", "priority": 4, "source": "base_2m", "tier": 2,
            "triggers": [],
        })

    # ---- B. 触发细分 ----
    triggers = collect_triggers(geo_floor)
    trigger_stats = {
        k: {"anchors": 0, "cells": set(), "keys": set()}
        for k in ("intersection", "doorway", "high_risk")
    }
    for t in triggers:
        st = trigger_stats[t["kind"]]
        st["anchors"] += 1
        cells = _cells_near((t["x"], t["y"]), t["radius"], walk, origin)
        st["cells"] |= cells
        for (i, j) in cells:
            for (x, y) in _split_points(origin, i, j, walk):
                u, v = _snap(origin, x, y)
                st["keys"].add((u, v))
                cands.append({
                    "x": x, "y": y, "u": u, "v": v,
                    "region": t["kind"], "priority": t["priority"],
                    "source": "split_1m", "tier": 1,
                    "triggers": [{"id": t["node_id"], "type": t["node_type"],
                                  "kind": t["kind"]}],
                })

    # ---- C. 格位键去重（不做贴墙换点，按用户拍板保留原格位）----
    kept = {}
    for c in sorted(cands, key=lambda z: (z["priority"], z["tier"], z["u"], z["v"])):
        key = (c["u"], c["v"])
        prev = kept.get(key)
        if prev is None:
            kept[key] = c
            continue
        # 合并触发来源；保留更高优先级（排序保证 prev 已不低于当前，仅补元数据）
        seen = {(x.get("id"), x.get("kind")) for x in prev["triggers"]}
        for tr in c["triggers"]:
            if (tr.get("id"), tr.get("kind")) not in seen:
                prev["triggers"].append(tr)
        if c["priority"] < prev["priority"]:
            prev["region"] = c["region"]
            prev["priority"] = c["priority"]

    # ---- D. 采集层标记 + 路线标签 ----
    points = []
    floor_beacons = [b for b in beacons if str(b.get("floorId")) == str(floor_no)]
    floor_sim = [b for b in (sim_beacons or []) if str(b.get("floorId")) == str(floor_no)]
    for (u, v), c in kept.items():
        x, y = _coord(origin, u, v)
        cov = coverage_flags(x, y, floor_beacons, wall_model, a=a, n=n)
        sim_cov = coverage_with_sim(cov["okBeacons"], x, y, floor_sim, wall_model, a=a, n=n)
        tags = []
        for tag, poly in route_corridors.get(str(floor_no), []):
            if poly.contains(Point(x, y)) or poly.distance(Point(x, y)) < 1e-6:
                tags.append(tag)
        suffix = REGION_SUFFIX[c["region"]]
        fp_id = f"FP-{floor_no}-C{u:03d}R{v:03d}{suffix}"
        points.append({
            "fpId": fp_id,
            "id": fp_id,
            "shortId": f"C{u:03d}R{v:03d}{suffix}",
            "floor": floor_no,
            "coordinates": [x, y],
            "x": x,
            "y": y,
            "col": u,
            "row": v,
            "regionType": c["region"],
            "priority": c["priority"],
            "source": c["source"],
            "tier": c["tier"],
            "isBase2m": c["tier"] == 2,
            "nearNodeId": (c["triggers"][0]["id"] if c["triggers"] else None),
            "nearNodeType": (c["triggers"][0]["type"] if c["triggers"] else None),
            "triggers": c["triggers"],
            # 采集层
            "collectable": cov["collectablePlan10m"],
            "collectablePlan10m": cov["collectablePlan10m"],
            "collectableGeo15m": cov["collectableGeo15m"],
            "criteria": (["plan10m85"] if cov["collectablePlan10m"] else [])
                        + (["geo15m"] if cov["collectableGeo15m"] else []),
            "visible10m": cov["visible10m"],
            "visible15m": cov["visible15m"],
            "nearestBeaconM": cov["nearestBeaconM"],
            "strongestRssi": cov["strongestRssi"],
            # 补装仿真口径（不改 collectable 语义；虚拟信标未安装）
            "collectableWithSim": sim_cov["collectableWithSim"],
            "simAdded": bool(sim_cov["collectableWithSim"] and not cov["collectablePlan10m"]),
            "simAddedBy": sim_cov["simAddedBy"],
            "routeTags": tags,
        })
    points.sort(key=lambda p: (p["row"], p["col"]))

    # ---- E. 统计 ----
    nn = _nn_stats(points)
    by_region = {}
    for p in points:
        by_region[p["regionType"]] = by_region.get(p["regionType"], 0) + 1
    stats = {
        "total": len(points),
        "base2m": sum(1 for p in points if p["isBase2m"]),
        "split1m": sum(1 for p in points if not p["isBase2m"]),
        "byRegion": by_region,
        "byTier": {"2m": sum(1 for p in points if p["tier"] == 2), "1m": sum(1 for p in points if p["tier"] == 1)},
        "triggers": {
            k: {"anchors": v["anchors"], "cells": len(v["cells"]), "keys": len(v["keys"])}
            for k, v in trigger_stats.items()
        },
        "wallClearanceMoved": 0,
        "wallClearanceEnabled": False,
        "wallUnder03m": sum(1 for p in points if _wall_distance(walls, p["x"], p["y"]) < WALL_CLEARANCE_M),
        "collectable": sum(1 for p in points if p["collectable"]),
        "collectablePlan10m": sum(1 for p in points if p["collectablePlan10m"]),
        "collectableGeo15m": sum(1 for p in points if p["collectableGeo15m"]),
        "routeTagged": sum(1 for p in points if p["routeTags"]),
        "routeCollectablePlan10m": sum(1 for p in points if p["routeTags"] and p["collectablePlan10m"]),
        "collectableWithSim": sum(1 for p in points if p["collectableWithSim"]),
        "routeCollectableWithSim": sum(1 for p in points if p["routeTags"] and p["collectableWithSim"]),
        "simAdded": sum(1 for p in points if p["simAdded"]),
        "simBeaconAdds": {
            b["beaconId"]: sum(1 for p in points if b["beaconId"] in p["simAddedBy"])
            for b in floor_sim
        },
        "nearestNeighbor": nn,
    }
    return points, stats, walls


def _nn_stats(points):
    if len(points) < 2:
        return {"min": None, "median": None, "max": None, "gt2m": 0}
    ds = []
    for i, p in enumerate(points):
        best = math.inf
        for j, q in enumerate(points):
            if i == j:
                continue
            best = min(best, dist((p["x"], p["y"]), (q["x"], q["y"])))
        ds.append(best)
    ds.sort()
    return {
        "min": round(ds[0], 3),
        "median": round(ds[len(ds) // 2], 3),
        "max": round(ds[-1], 3),
        "gt2m": sum(1 for d in ds if d > 2.0 + 1e-6),
    }


def collect_route_corridors(geo, routes):
    """返回 (corridors, meta, route_geom)。

    route_geom: {tag: {"label": str, "segments": {floor: [segment...]}}}，
    线段按路径顺序累计里程（跨层转移不产生线段，里程在下一层续算）。
    segment = {from, to, a, b, c0, lengthM}。
    """
    if not routes:
        return {}, [], {}
    graph = _load_route_graph(geo)
    walk_by_floor = {fk: build_walkable(fl) for fk, fl in (geo.get("floors") or {}).items()}
    corridors = {}
    meta = []
    route_geom = {}
    for idx, rt in enumerate(routes, 1):
        start, end = rt["start"], rt["end"]
        mode = rt.get("mode", "normal")
        res = graph.generate_route(start, end, mode)
        tag = f"R{idx}"
        if not res.get("reachable"):
            print(f"  [warn] 路线不可达：{start} -> {end}", file=sys.stderr)
            continue
        path = res["path"]
        lines = {}
        seg_by_floor: dict[str, list[dict]] = {}
        cum = 0.0
        for a, b in zip(path, path[1:]):
            na, nb = graph.nodes.get(a), graph.nodes.get(b)
            if not na or not nb:
                continue
            ca, cb = na.get("coords"), nb.get("coords")
            if not ca or not cb or na.get("floor") != nb.get("floor"):
                continue
            length = math.hypot(cb[0] - ca[0], cb[1] - ca[1])
            seg_by_floor.setdefault(str(na["floor"]), []).append({
                "from": a, "to": b, "a": ca, "b": cb, "c0": cum, "lengthM": length,
            })
            lines.setdefault(str(na["floor"]), []).append(LineString([(ca[0], ca[1]), (cb[0], cb[1])]))
            cum += length
        for fk, ls in lines.items():
            walk = walk_by_floor.get(fk)
            if walk is None:
                continue
            poly = unary_union(ls).buffer(ROUTE_CORRIDOR_HALF_M).intersection(walk)
            if not poly.is_empty:
                corridors.setdefault(fk, []).append((tag, poly))
        route_geom[tag] = {"label": rt.get("label", f"{start} -> {end}"), "segments": seg_by_floor}
        meta.append({
            "tag": tag,
            "label": rt.get("label", f"{start} -> {end}"),
            "start": start, "end": end, "mode": mode,
            "distance_m": res.get("distance"),
            "nodes": len(path),
            "crossFloor": res.get("cross_floor"),
            "usedElevator": res.get("used_elevator"),
            "usedStair": res.get("used_stair"),
        })
    return corridors, meta, route_geom


def _load_route_graph(geo):
    import importlib.util
    if str(BASE_DIR) not in sys.path:
        sys.path.insert(0, str(BASE_DIR))
    topo_py = Path(__file__).resolve().parents[1] / "topology" / "route_rules.py"
    spec = importlib.util.spec_from_file_location("route_rules_fp", str(topo_py))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.RouteGraph(geo)


def _floor_beacon_count(beacons, floor_no):
    return sum(1 for b in beacons if str(b.get("floorId")) == str(floor_no))


def load_beacons(path=BEACON_PACKAGE):
    with open(path, encoding="utf-8") as f:
        pkg = json.load(f)
    out = []
    for b in pkg.get("beacons", []):
        out.append({
            "beaconId": b.get("beaconId"),
            "floorId": str(b.get("floorId")),
            "x": float(b["x"]),
            "y": float(b["y"]),
            "installHeightM": float(b.get("installHeightM") or DEFAULT_INSTALL_HEIGHT_M),
        })
    return out


def load_sim_beacons(path=SIM_BEACONS):
    """读取补装/虚拟信标（仅仿真，不进 result/ble_deployment.json 部署计划）。"""
    p = Path(path) if path else None
    if p is None or not p.exists():
        return []
    with p.open(encoding="utf-8") as f:
        data = json.load(f)
    out = []
    for b in data.get("beacons", []):
        out.append({
            "beaconId": b.get("simId") or b.get("beaconId"),
            "floorId": str(b.get("floorId")),
            "x": float(b["x"]),
            "y": float(b["y"]),
            "installHeightM": float(b.get("installHeightM") or DEFAULT_INSTALL_HEIGHT_M),
            "simulated": True,
            "status": b.get("status") or "not_installed",
            "label": b.get("label"),
        })
    return out


def project_on_route(x, y, segments):
    """把点投影到本层路线折线，返回 {distanceM, mileageM, segIndex, from, to}。

    mileageM 为本层局部累计里程（从该层路线首个线段起点算起）。
    """
    if not segments:
        return None
    base = segments[0]["c0"]
    best = None
    for idx, s in enumerate(segments):
        ax, ay = s["a"]
        bx, by = s["b"]
        dx, dy = bx - ax, by - ay
        l2 = dx * dx + dy * dy
        t = 0.0 if l2 <= 1e-12 else max(0.0, min(1.0, ((x - ax) * dx + (y - ay) * dy) / l2))
        px, py = ax + t * dx, ay + t * dy
        d = math.hypot(x - px, y - py)
        mileage = s["c0"] - base + t * s["lengthM"]
        if best is None or d < best["distanceM"]:
            best = {"distanceM": d, "mileageM": mileage, "segIndex": idx,
                    "from": s["from"], "to": s["to"], "segStartM": s["c0"] - base,
                    "segEndM": s["c0"] - base + s["lengthM"]}
    return best


def order_route_points(rows, floor_no, route_geom):
    """按路线行进顺序排序：路线点在前（按本层累计里程），非路线点在后（按坐标序）。"""
    routed, plain = [], []
    for p in rows:
        best = None
        for tag in p["routeTags"]:
            geom = route_geom.get(tag)
            if not geom:
                continue
            proj = project_on_route(p["x"], p["y"], geom["segments"].get(str(floor_no), []))
            if proj is None:
                continue
            if best is None or proj["distanceM"] < best[0]["distanceM"]:
                best = (proj, tag)
        if best is None:
            plain.append(p)
        else:
            routed.append((best[1], best[0], p))
    routed.sort(key=lambda t: (t[0], t[1]["segIndex"], t[1]["mileageM"], t[2]["row"], t[2]["col"]))
    plain.sort(key=lambda p: (p["row"], p["col"]))
    return routed, plain


def write_f2_collectable(points, floor_no, out_dir, route_geom=None):
    route_geom = route_geom or {}
    rows = [p for p in points if p["floor"] == floor_no and (p["collectablePlan10m"] or p["collectableGeo15m"])]
    routed, plain = order_route_points(rows, floor_no, route_geom)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    csv_path = out_dir / f"f{floor_no}_collectable_points.csv"
    md_path = out_dir / f"f{floor_no}_collectable_points.md"

    headers = ["序号", "fpId", "短号", "楼层", "x", "y", "类型",
               "可见信标数(10m)", "可见信标数(15m)", "最近信标距离m",
               "预测最强RSSI", "口径", "路线标签", "累计里程(m)"]

    def row_values(seq, p, mileage):
        criteria = []
        if p["collectablePlan10m"]:
            criteria.append("RSSI≥-85/≥3(3D含墙)")
        if p["collectableGeo15m"]:
            criteria.append("15m/≥3(3D)")
        return [
            seq, p["fpId"], p["shortId"], p["floor"], p["x"], p["y"],
            REGION_TEXT.get(p["regionType"], p["regionType"]),
            p["visible10m"], p["visible15m"],
            "" if p["nearestBeaconM"] is None else f"{p['nearestBeaconM']:.2f}",
            "" if p["strongestRssi"] is None else f"{p['strongestRssi']:.1f}",
            "; ".join(criteria), ";".join(p["routeTags"]),
            "" if mileage is None else f"{mileage:.2f}",
        ]

    ordered = [(tag, proj, p) for tag, proj, p in routed] + [(None, None, p) for p in plain]
    with csv_path.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(headers)
        for seq, (tag, proj, p) in enumerate(ordered, 1):
            w.writerow(row_values(seq, p, None if proj is None else proj["mileageM"]))

    lines = [
        f"# F{floor_no} 可采集点清单",
        "",
        f"> 设计层全量点中，满足 `RSSI≥-85dBm/≥3信标`（3D 斜距+材质墙衰减）或 `15m/≥3`（3D 斜距）的点，共 {len(rows)} 个。",
        "> 口径列同时标出两个条件；`RSSI≥-85/≥3(3D含墙)` 为默认采集口径（v11：A=-52dBm，墙 partition/brick/concrete=8/12/15dB，门 2dB）。",
        "> 路线点按投影到路线中心线的**本层累计里程**排序（从本层路线入口起算），非路线点列在最后。",
    ]

    def md_row(seq, p, mileage):
        v = row_values(seq, p, mileage)
        seq, fp, short, fl, x, y, typ, v10, v15, near, rssi, crit, tags, mil = v
        return (f"| {seq} | {fp} | {short} | {fl} | {x} | {y} | {typ} | {v10} | {v15} | "
                f"{near} | {rssi} | {crit} | {tags} | {mil} |")

    seq = 0
    # 路线分段小标题（按折线段聚合）
    groups: dict[tuple[str, int], list] = {}
    for tag, proj, p in routed:
        groups.setdefault((tag, proj["segIndex"]), []).append((proj, p))
    for (tag, seg_index), items in sorted(groups.items(), key=lambda kv: (kv[0][0], kv[0][1])):
        first = items[0][0]
        label = route_geom.get(tag, {}).get("label", tag)
        lines += [
            "",
            f"### {tag} · {label} · 第{seg_index + 1}折线段 {first['from']} → {first['to']}"
            f"（累计里程 {first['segStartM']:.1f}–{first['segEndM']:.1f}m）",
            "",
            "| 序号 | fpId | 短号 | 楼层 | x | y | 类型 | 可见(10m) | 可见(15m) | 最近信标m | 最强RSSI | 口径 | 路线 | 累计里程(m) |",
            "|---:|---|---|---:|---:|---:|---|---:|---:|---:|---:|---|---|---:|",
        ]
        for proj, p in sorted(items, key=lambda t: (t[0]["mileageM"], t[1]["row"], t[1]["col"])):
            seq += 1
            lines.append(md_row(seq, p, proj["mileageM"]))

    if plain:
        lines += [
            "",
            "### 非路线可采集点（按坐标序）",
            "",
            "| 序号 | fpId | 短号 | 楼层 | x | y | 类型 | 可见(10m) | 可见(15m) | 最近信标m | 最强RSSI | 口径 | 路线 | 累计里程(m) |",
            "|---:|---|---|---:|---:|---:|---|---:|---:|---:|---:|---|---|---:|",
        ]
        for p in plain:
            seq += 1
            lines.append(md_row(seq, p, None))

    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return rows, csv_path, md_path


def generate_full_grid(geo, beacons, sim_beacons, routes, *, beacons_label="",
                       a=BEACON_RSSI_AT_1M_DBM, n=BEACON_PATH_LOSS_N,
                       grid_version=GRID_VERSION, progress=True):
    """可直接被服务端复用的整包生成入口（CLI 行为保持不变）。

    返回 (out_doc, all_points, route_geom)；不写文件、不写 F2 清单。
    a/n 为标定实测值时可覆盖默认常量；墙/门衰减与 -85dBm/≥3 口径保持工程常量。
    """
    origin = even_origin(geo)
    corridors, route_meta, route_geom = collect_route_corridors(geo, routes)

    floors_out, summary = {}, {}
    total = {"total": 0, "base2m": 0, "split1m": 0, "collectablePlan10m": 0,
             "collectableGeo15m": 0, "routeTagged": 0, "routeCollectablePlan10m": 0,
             "collectableWithSim": 0, "routeCollectableWithSim": 0, "simAdded": 0,
             "simBeaconAdds": {}}
    all_points = []
    for fk in sorted(geo["floors"].keys(), key=lambda x: int(x)):
        pts, stats, _walls = build_floor_grid(geo["floors"][fk], int(fk), origin, beacons, corridors,
                                              sim_beacons, a=a, n=n)
        floors_out[fk] = {
            "floor": int(fk),
            "parameters": {
                "normalSpacingM": NORMAL_SPACING_M, "splitSpacingM": SPLIT_SPACING_M,
                "intersectionRadiusM": INTERSECTION_RADIUS_M, "doorRadiusM": DOOR_RADIUS_M,
                "riskRadiusM": RISK_RADIUS_M, "wallClearanceM": WALL_CLEARANCE_M,
            },
            "points": pts,
        }
        summary[fk] = stats
        all_points.extend(pts)
        for k in total:
            if k == "simBeaconAdds":
                for bid, cnt in (stats.get("simBeaconAdds") or {}).items():
                    total["simBeaconAdds"][bid] = total["simBeaconAdds"].get(bid, 0) + cnt
            else:
                total[k] += stats.get(k, 0)
        if progress:
            print(f"  [F{fk}] 总={stats['total']} 2m={stats['base2m']} 1m={stats['split1m']} "
                  f"可采10m={stats['collectablePlan10m']} 可采15m={stats['collectableGeo15m']} "
                  f"路线点={stats['routeTagged']} 含补装={stats['collectableWithSim']}")

    summary["total"] = total
    bx0, by0, bx1, by1 = wall_bbox(geo)
    out = {
        "schemaVersion": "fingerprint_grid_v2",
        "venueId": geo.get("venueId"),
        "venueName": geo.get("venueName"),
        "version": grid_version,
        "generator": "generate_fingerprint_grid.py",
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "mode": "full",
        "layers": {
            "design": "全楼部署假设：2m 底网 + 路口/门口/高风险 1m 细分，不做覆盖裁剪",
            "collection": f"当前实际部署 {len(beacons)} 枚信标"
                          f"（F1 {_floor_beacon_count(beacons, 1)} / F2 {_floor_beacon_count(beacons, 2)}）"
                          f"，地图包 {beacons_label}，"
                          "按 plan10m85（3D 斜距+材质墙衰减 RSSI>=-85 且 >=3）与 geo15m（3D 斜距 15m 且 >=3）两个口径标记 collectable"
                          + (f"；另含 {len(sim_beacons)} 枚未安装补装信标的仿真口径 collectableWithSim" if sim_beacons else ""),
        },
        "origin": {
            "x": origin[0], "y": origin[1],
            "rule": "venue_wall_bbox_lower_left_floor_even",
            "wallBbox": [round(bx0, 3), round(by0, 3), round(bx1, 3), round(by1, 3)],
        },
        "parameters": {
            "normalSpacingM": NORMAL_SPACING_M, "splitSpacingM": SPLIT_SPACING_M,
            "intersectionRadiusM": INTERSECTION_RADIUS_M, "doorRadiusM": DOOR_RADIUS_M,
            "riskRadiusM": RISK_RADIUS_M, "wallClearanceM": WALL_CLEARANCE_M,
            "dedup": "lattice_key_1m",
            "fpIdPattern": FP_ID_RE,
            "priority": ["intersection", "doorway", "high_risk", "normal"],
            "largeSpacingM": LARGE_SPACING_M, "largeAreaMinM2": LARGE_AREA_MIN_M2,
        },
        "coverage": {
            "plan10m85": {
                "minBeacons": COLLECT_MIN_BEACONS,
                "rssiThresholdDbm": RSSI_THRESHOLD_DBM,
                "rssiAt1mDbm": float(a),
                "pathLossN": float(n),
                "distanceModel": "3d_slant",
                "phoneHeightM": PHONE_HEIGHT_M,
                "installHeightMDefault": DEFAULT_INSTALL_HEIGHT_M,
                "wallAttenuationDb": WALL_ATTENUATION_DB,
                "unknownWallMaterialDb": UNKNOWN_MATERIAL_DB,
                "doorAttenuationDb": DOOR_ATTENUATION_DB,
                "doorProximityM": DOOR_PROXIMITY_M,
                "crossingClusterTolM": CROSSING_CLUSTER_TOL_M,
                "radiusMLegacy": PLAN_RADIUS_M,
                "note": "v11 起由『10m 几何半径 + 无墙 RSSI(A=-50)』升级为『3D 斜距 + 材质墙衰减 + 门 2dB 的 RSSI>=-85 信标数>=3』；"
                        "字段名 plan10m85/collectablePlan10m 保留以兼容客户端，10m 半径不再是硬门槛",
            },
            "geo15m": {"radiusM": GEO_RADIUS_M, "minBeacons": COLLECT_MIN_BEACONS,
                       "distanceModel": "3d_slant"},
            "withSim": {
                "note": "补装仿真口径：真实部署 + simulatedBeacons 叠加后的 RSSI，字段 collectableWithSim；"
                        "不改 collectable/collectablePlan10m 语义（v11 真实口径基线）",
                "simBeaconCount": len(sim_beacons),
                "simBeaconIds": [b["beaconId"] for b in sim_beacons],
            },
        },
        "beacons": beacons,
        "simulatedBeacons": {
            "policy": "未安装·仅仿真：不进 result/ble_deployment.json 部署计划，不参与 collectable 判定",
            "source": Path(beacons_label).name if beacons_label else None,
            "beacons": sim_beacons,
        },
        "routes": route_meta,
        "summary": summary,
        "floors": floors_out,
    }
    return out, all_points, route_geom


def main():
    ap = argparse.ArgumentParser(description="指纹采集网格生成（设计层/采集层）")
    ap.add_argument("geo", nargs="?", default=GEO_IN, help="输入 GeoJSON 路径")
    ap.add_argument("out", nargs="?", default=OUT_DEFAULT, help="输出 JSON 路径")
    ap.add_argument("--mode", default="full", choices=("full", "route"))
    ap.add_argument("--routes", default=ROUTES_DEFAULT, help="路线定义 JSON")
    ap.add_argument("--beacons", default=BEACON_PACKAGE, help="实际部署信标地图包")
    ap.add_argument("--sim-beacons", default=SIM_BEACONS, help="补装/虚拟信标 JSON（仅仿真）")
    ap.add_argument("--f2-out-dir", default=str(BASE_DIR / "result"))
    args = ap.parse_args()

    geo = json.load(open(args.geo, encoding="utf-8"))
    if args.mode == "route":
        routes = json.load(open(args.routes, encoding="utf-8")).get("routes", [])
        floors_out, summary, route_meta, total_all = generate_route_mode(geo, routes, spacing=ROUTE_FP_SPACING_M)
        out = {
            "venueId": geo.get("venueId"), "venueName": geo.get("venueName"),
            "version": GRID_VERSION, "generator": "generate_fingerprint_grid.py",
            "generatedAt": datetime.now(timezone.utc).isoformat(), "mode": "route",
            "parameters": {"normalSpacingM": ROUTE_FP_SPACING_M, "safeSpacingM": ROUTE_FP_SAFE_SPACING_M,
                           "safeRadiusM": ROUTE_FP_SAFE_RADIUS_M},
            "routes": route_meta, "summary": summary, "floors": floors_out,
        }
        Path(args.out).write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"总计 {total_all} 个路线采集点 → {args.out}")
        return

    beacons = load_beacons(args.beacons)
    sim_beacons = load_sim_beacons(args.sim_beacons)
    routes = json.load(open(args.routes, encoding="utf-8")).get("routes", []) if Path(args.routes).exists() else []
    out, all_points, route_geom = generate_full_grid(
        geo, beacons, sim_beacons, routes,
        beacons_label=Path(args.beacons).stem,
    )
    total = out["summary"]["total"]
    Path(args.out).write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")

    f2_rows, csv_path, md_path = write_f2_collectable(all_points, 2, args.f2_out_dir, route_geom)
    print(f"\n设计层 {total['total']} 点 → {args.out}")
    print(f"可采集：10m/-85/≥3 = {total['collectablePlan10m']}；15m/≥3 = {total['collectableGeo15m']}")
    print(f"F2 可采集清单 {len(f2_rows)} 行 → {csv_path} / {md_path}")


# --------------------------- 路线模式（兼容保留） ---------------------------
def generate_route_mode(geo, routes, spacing=ROUTE_FP_SPACING_M,
                        safe_spacing=ROUTE_FP_SAFE_SPACING_M, safe_radius=ROUTE_FP_SAFE_RADIUS_M):
    graph = _load_route_graph(geo)
    walk_by_floor = {int(fk): build_walkable(fl) for fk, fl in (geo.get("floors") or {}).items()}
    route_lines, stair_elev_nodes, route_meta = {}, [], []
    for rt in routes:
        start, end, mode = rt["start"], rt["end"], rt.get("mode", "normal")
        res = graph.generate_route(start, end, mode)
        if not res.get("reachable"):
            print(f"  [warn] 路线不可达，跳过：{start} -> {end}")
            continue
        path = res["path"]
        route_meta.append({"label": rt.get("label", f"{start} -> {end}"), "start": start, "end": end,
                           "mode": mode, "distance_m": res["distance"], "nodes": len(path),
                           "crossFloor": res["cross_floor"], "usedElevator": res["used_elevator"],
                           "usedStair": res["used_stair"]})
        seq = []
        for nid in path:
            n = graph.nodes.get(nid)
            if not n or not n.get("coords") or len(n.get("coords")) < 2:
                continue
            seq.append((n["coords"][0], n["coords"][1], n.get("type"),
                        n.get("roomType") or n.get("facilityType") or "", n.get("floor")))
        for (x0, y0, _, _, f0), (x1, y1, _, _, f1) in zip(seq, seq[1:]):
            if f0 == f1:
                route_lines.setdefault(int(f0), []).append(LineString([(x0, y0), (x1, y1)]))
        for (x, y, nt, sub, f) in seq:
            if nt in ("facility", "room") and sub in ("staircase", "elevator"):
                stair_elev_nodes.append((x, y, int(f)))
    candidates = []
    for fk, lines in route_lines.items():
        walk = walk_by_floor.get(int(fk))
        if walk is None:
            continue
        route_walkable = unary_union(lines).buffer(ROUTE_CORRIDOR_HALF_M).intersection(walk)
        if route_walkable.is_empty:
            continue
        for (gx, gy) in grid_points_in_polygon(route_walkable, spacing):
            candidates.append((gx, gy, int(fk), "normal", 3))
    for (x, y, f) in stair_elev_nodes:
        walk = walk_by_floor.get(int(f))
        if walk is None:
            continue
        sub_poly = Point(x, y).buffer(safe_radius).intersection(walk)
        if sub_poly.is_empty:
            continue
        for (gx, gy) in grid_points_in_polygon(sub_poly, safe_spacing):
            candidates.append((gx, gy, int(f), "high_risk", 1))
    kept = {}
    for c in sorted(candidates, key=lambda z: (z[4], z[2], z[0], z[1])):
        key = (c[2], round(c[0]), round(c[1]))
        if key not in kept:
            kept[key] = c
    floors_out, summary = {}, {}
    for fk in sorted({c[2] for c in kept.values()}):
        fpts = [c for c in kept.values() if c[2] == fk]
        fpts.sort(key=lambda c: (-c[1], c[0]))
        points = []
        for i, c in enumerate(fpts, 1):
            points.append({"id": f"FP-{fk}-R{i:03d}", "fpId": f"FP-{fk}-R{i:03d}",
                           "shortId": f"R{i:03d}", "floor": fk,
                           "coordinates": [round(c[0], 3), round(c[1], 3)],
                           "x": round(c[0], 3), "y": round(c[1], 3),
                           "regionType": c[3], "priority": c[4], "source": "route",
                           "isBase2m": False, "collectable": False,
                           "collectablePlan10m": False, "collectableGeo15m": False,
                           "routeTags": [], "nearNodeId": None, "nearNodeType": None})
        floors_out[str(fk)] = {"floor": fk, "parameters": {"mode": "route", "spacingM": spacing},
                               "points": points}
        summary[str(fk)] = {"total": len(points)}
    return floors_out, summary, route_meta, sum(s["total"] for s in summary.values())


if __name__ == "__main__":
    main()
