# -*- coding: utf-8 -*-
"""指纹采集网格生成（v2：设计层 / 采集层两层模型）。

设计层（design）：
  - 全场墙体包围盒左下角向下取偶米作为统一原点 O；
  - 2m 底网：可通行区内偶-偶格位；
  - 触发细分：交叉路口（唯一邻居 >=3）、门口（doorway + outward_door）、
    高风险（楼梯/电梯入口）命中 2m 格后切 1m；
  - 1m 格位键去重，优先级 路口 > 门口 > 高风险 > 普通，取消 0.6m 距离阈值；
  - 编号 FP-{f}-C{col:03d}R{row:03d}[-J/-D/-S]，坐标派生、增删点不重排。

采集层（collection）：
  - 读取地图包中实际部署信标，计算每个设计点的可见信标数/最近距离/预测 RSSI；
  - 同时给出两个口径：
      plan10m85: 10m 内 >=3 个信标且预测 RSSI >= -85dBm（规划模型 A=-50,n=3.5）
      geo15m:   15m 内 >=3 个信标
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

BASE_DIR = Path(__file__).resolve().parent.parent.parent
GEO_IN = str(BASE_DIR / "result" / "school_building_01_map_v9.geojson")
OUT_DEFAULT = str(BASE_DIR / "result" / "fingerprint_grid.json")
BEACON_PACKAGE = str(BASE_DIR / "backend" / "maps_data" / "school-building-01" / "2026-11-03.1.json")
ROUTES_DEFAULT = str(BASE_DIR / "result" / "beacon_routes.json")
GRID_VERSION = "10.0.0"

# ----------------------------- 设计层参数 -----------------------------
NORMAL_SPACING_M = 2.0
SPLIT_SPACING_M = 1.0
INTERSECTION_RADIUS_M = 1.0
DOOR_RADIUS_M = 1.0
RISK_RADIUS_M = 3.0
WALL_CLEARANCE_M = 0.3  # 仅用于统计贴墙比例；按用户拍板不做换点
LARGE_SPACING_M = 3.0
LARGE_AREA_MIN_M2 = 30.0

# ----------------------------- 采集层参数 -----------------------------
BEACON_RSSI_AT_1M_DBM = -50.0
BEACON_PATH_LOSS_N = 3.5
RSSI_THRESHOLD_DBM = -85.0
COLLECT_MIN_BEACONS = 3
PLAN_RADIUS_M = 10.0
GEO_RADIUS_M = 15.0

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


def coverage_flags(x, y, floor_beacons):
    """采集层覆盖判定：同时计算 plan10m85（默认）与 geo15m（备用）两个口径。"""
    d = [dist((x, y), (b["x"], b["y"])) for b in floor_beacons]
    vis10 = sum(1 for v in d if v <= PLAN_RADIUS_M)
    vis15 = sum(1 for v in d if v <= GEO_RADIUS_M)
    nearest = min(d) if d else None
    strongest = (max(BEACON_RSSI_AT_1M_DBM - 10 * BEACON_PATH_LOSS_N * math.log10(max(v, 0.1))
                     for v in d) if d else None)
    plan_ok = vis10 >= COLLECT_MIN_BEACONS and strongest is not None and strongest >= RSSI_THRESHOLD_DBM
    geo_ok = vis15 >= COLLECT_MIN_BEACONS
    return {
        "visible10m": vis10,
        "visible15m": vis15,
        "nearestBeaconM": round(nearest, 3) if nearest is not None else None,
        "strongestRssi": round(strongest, 1) if strongest is not None else None,
        "collectablePlan10m": bool(plan_ok),
        "collectableGeo15m": bool(geo_ok),
    }


def build_floor_grid(geo_floor, floor_no, origin, beacons, route_corridors):
    walk = build_walkable(geo_floor)
    if walk is None:
        return [], {"error": "no walkable regions"}, None
    walls = build_walls(geo_floor)
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
    for (u, v), c in kept.items():
        x, y = _coord(origin, u, v)
        cov = coverage_flags(x, y, floor_beacons)
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
    if not routes:
        return {}, []
    graph = _load_route_graph(geo)
    walk_by_floor = {fk: build_walkable(fl) for fk, fl in (geo.get("floors") or {}).items()}
    corridors = {}
    meta = []
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
        for a, b in zip(path, path[1:]):
            na, nb = graph.nodes.get(a), graph.nodes.get(b)
            if not na or not nb:
                continue
            ca, cb = na.get("coords"), nb.get("coords")
            if not ca or not cb or na.get("floor") != nb.get("floor"):
                continue
            lines.setdefault(str(na["floor"]), []).append(LineString([(ca[0], ca[1]), (cb[0], cb[1])]))
        for fk, ls in lines.items():
            walk = walk_by_floor.get(fk)
            if walk is None:
                continue
            poly = unary_union(ls).buffer(ROUTE_CORRIDOR_HALF_M).intersection(walk)
            if not poly.is_empty:
                corridors.setdefault(fk, []).append((tag, poly))
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
    return corridors, meta


def _load_route_graph(geo):
    import importlib.util
    if str(BASE_DIR) not in sys.path:
        sys.path.insert(0, str(BASE_DIR))
    topo_py = Path(__file__).resolve().parents[1] / "topology" / "route_rules.py"
    spec = importlib.util.spec_from_file_location("route_rules_fp", str(topo_py))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.RouteGraph(geo)


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
        })
    return out


def write_f2_collectable(points, floor_no, out_dir):
    rows = [p for p in points if p["floor"] == floor_no and (p["collectablePlan10m"] or p["collectableGeo15m"])]
    rows.sort(key=lambda p: (p["row"], p["col"]))
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    csv_path = out_dir / f"f{floor_no}_collectable_points.csv"
    md_path = out_dir / f"f{floor_no}_collectable_points.md"

    headers = ["fpId", "短号", "楼层", "x", "y", "类型",
               "可见信标数(10m)", "可见信标数(15m)", "最近信标距离m",
               "预测最强RSSI", "口径", "路线标签"]
    with csv_path.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(headers)
        for p in rows:
            criteria = []
            if p["collectablePlan10m"]:
                criteria.append("10m/-85/≥3")
            if p["collectableGeo15m"]:
                criteria.append("15m/≥3")
            w.writerow([
                p["fpId"], p["shortId"], p["floor"], p["x"], p["y"],
                REGION_TEXT.get(p["regionType"], p["regionType"]),
                p["visible10m"], p["visible15m"],
                "" if p["nearestBeaconM"] is None else f"{p['nearestBeaconM']:.2f}",
                "" if p["strongestRssi"] is None else f"{p['strongestRssi']:.1f}",
                "; ".join(criteria), ";".join(p["routeTags"]),
            ])

    lines = [
        f"# F{floor_no} 可采集点清单",
        "",
        f"> 设计层全量点中，满足 `10m/-85dBm/≥3` 或 `15m/≥3` 的点，共 {len(rows)} 个。",
        "> 口径列同时标出两个条件；`10m/-85/≥3` 为默认采集口径。",
        "",
        "| fpId | 短号 | 楼层 | x | y | 类型 | 可见(10m) | 可见(15m) | 最近信标m | 最强RSSI | 口径 | 路线 |",
        "|---|---|---:|---:|---:|---|---:|---:|---:|---:|---|---|",
    ]
    for p in rows:
        criteria = []
        if p["collectablePlan10m"]:
            criteria.append("10m/-85/≥3")
        if p["collectableGeo15m"]:
            criteria.append("15m/≥3")
        nearest = "" if p["nearestBeaconM"] is None else f"{p['nearestBeaconM']:.2f}"
        strongest = "" if p["strongestRssi"] is None else f"{p['strongestRssi']:.1f}"
        lines.append(
            f"| {p['fpId']} | {p['shortId']} | {p['floor']} | {p['x']} | {p['y']} | "
            f"{REGION_TEXT.get(p['regionType'], p['regionType'])} | {p['visible10m']} | {p['visible15m']} | "
            f"{nearest} | {strongest} | {'; '.join(criteria)} | {';'.join(p['routeTags'])} |"
        )
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return rows, csv_path, md_path


def main():
    ap = argparse.ArgumentParser(description="指纹采集网格生成（设计层/采集层）")
    ap.add_argument("geo", nargs="?", default=GEO_IN, help="输入 GeoJSON 路径")
    ap.add_argument("out", nargs="?", default=OUT_DEFAULT, help="输出 JSON 路径")
    ap.add_argument("--mode", default="full", choices=("full", "route"))
    ap.add_argument("--routes", default=ROUTES_DEFAULT, help="路线定义 JSON")
    ap.add_argument("--beacons", default=BEACON_PACKAGE, help="实际部署信标地图包")
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

    origin = even_origin(geo)
    beacons = load_beacons(args.beacons)
    routes = json.load(open(args.routes, encoding="utf-8")).get("routes", []) if Path(args.routes).exists() else []
    corridors, route_meta = collect_route_corridors(geo, routes)

    floors_out, summary = {}, {}
    total = {"total": 0, "base2m": 0, "split1m": 0, "collectablePlan10m": 0,
             "collectableGeo15m": 0, "routeTagged": 0, "routeCollectablePlan10m": 0}
    all_points = []
    for fk in sorted(geo["floors"].keys(), key=lambda x: int(x)):
        pts, stats, _walls = build_floor_grid(geo["floors"][fk], int(fk), origin, beacons, corridors)
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
            total[k] += stats.get(k, 0)
        print(f"  [F{fk}] 总={stats['total']} 2m={stats['base2m']} 1m={stats['split1m']} "
              f"可采10m={stats['collectablePlan10m']} 可采15m={stats['collectableGeo15m']} "
              f"路线点={stats['routeTagged']}")

    summary["total"] = total
    bx0, by0, bx1, by1 = wall_bbox(geo)
    out = {
        "schemaVersion": "fingerprint_grid_v2",
        "venueId": geo.get("venueId"),
        "venueName": geo.get("venueName"),
        "version": GRID_VERSION,
        "generator": "generate_fingerprint_grid.py",
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "mode": "full",
        "layers": {
            "design": "全楼部署假设：2m 底网 + 路口/门口/高风险 1m 细分，不做覆盖裁剪",
            "collection": "当前实际部署 61 枚信标（F1 52 / F2 9），按 plan10m85 与 geo15m 两个口径标记 collectable",
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
            "plan10m85": {"radiusM": PLAN_RADIUS_M, "minBeacons": COLLECT_MIN_BEACONS,
                          "rssiThresholdDbm": RSSI_THRESHOLD_DBM,
                          "rssiAt1mDbm": BEACON_RSSI_AT_1M_DBM, "pathLossN": BEACON_PATH_LOSS_N},
            "geo15m": {"radiusM": GEO_RADIUS_M, "minBeacons": COLLECT_MIN_BEACONS},
        },
        "beacons": beacons,
        "routes": route_meta,
        "summary": summary,
        "floors": floors_out,
    }
    Path(args.out).write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")

    f2_rows, csv_path, md_path = write_f2_collectable(all_points, 2, args.f2_out_dir)
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
