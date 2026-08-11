#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
gen_trilateration_plan_routes.py — 仅针对"选定导航测试路径"的三点定位(Trilateration)
轻量部署方案。

与全楼层三点定位方案(gen_trilateration_plan.py, 324 个)不同，本方案只要求测试路径
(即指纹网格的 753 个路线点 = 两条测试路线的走廊缓冲覆盖区)上每点同时被 >=3 个信标
稳定接收，因此信标数量远少于全楼方案。

步骤:
  1. 读取 GeoJSON + 基础路线计划(beacon_deployment_plan_routes.json, 48 个) +
     指纹网格路线点(result/fingerprint_grid_routes.json, 753 点) 作为覆盖目标。
  2. 对每层目标点射线穿墙算 RSSI，统计每点可见信标数(< 3 为缺口)。
  3. 贪心补点(距离模型 + 实时计数跟踪)，将补点吸附到最近墙(走廊墙, 贴墙安装)。
  4. 全部信标 txPower 统一 -10 dBm，写 augmented 计划。
  5. 用真实穿墙模型重仿真，输出最终 >=3 覆盖率。

物理模型与 gen_trilateration_plan.py 完全一致，保证可比性。

输出:
  result/beacon_deployment_plan_trilateration_routes.json
  result/trilateration_coverage_routes_analysis.json

用法:
  python src/tools/gen_trilateration_plan_routes.py
  python src/tools/gen_trilateration_plan_routes.py --fill-cap 120 --grid 1.0
"""
from __future__ import annotations
import argparse, json, math
from pathlib import Path
from shapely.geometry import LineString, Point, shape
from shapely.ops import unary_union
from shapely.strtree import STRtree

ROOT = Path(__file__).resolve().parents[2]
GEO = ROOT / "result" / "school_building_01_map_v9.geojson"
BASE_PLAN = ROOT / "result" / "beacon_deployment_plan_routes.json"
TARGET = ROOT / "result" / "fingerprint_grid_routes.json"

TX_POWER = -10
RSSI_REF_1M = -50
N = 3.5
WALL_ATTEN = {"brick": 12, "concrete": 15, "partition": 8, "glass": 6, None: 12}
VISIBLE = -85
D_MAX = 11.0
OFFSET = 0.25
FILL_R = 8.0      # 贪心补点覆盖半径(距离模型, 走廊内近似 0 墙)


def load():
    return (json.load(open(GEO, encoding="utf-8")),
            json.load(open(BASE_PLAN, encoding="utf-8")),
            json.load(open(TARGET, encoding="utf-8")))


def wall_segments(fg):
    segs, atten = [], []
    for w in fg["walls"]:
        c = w["geometry"]["coordinates"]
        if len(c) < 2:
            continue
        segs.append(LineString([(x, y) for x, y in c]))
        atten.append(WALL_ATTEN.get(w["properties"].get("material"), 12))
    return segs, atten, STRtree(segs)


def beacons_by_floor(plan):
    out = {}
    for b in plan["beacons"]:
        out.setdefault(str(b["floor"]), []).append((b["coordinates"][0], b["coordinates"][1]))
    return out


def target_points_by_floor(target):
    out = {}
    for fl, v in target["floors"].items():
        pts = []
        for p in v["points"]:
            if "coordinates" in p:
                pts.append((p["coordinates"][0], p["coordinates"][1]))
            elif "x" in p:
                pts.append((p["x"], p["y"]))
        out[str(fl)] = pts
    return out


def visible_count(px, py, beacons, tree, segs, atten):
    cnt = 0
    for (bx, by) in beacons:
        dx, dy = bx - px, by - py
        d = math.hypot(dx, dy)
        if d > D_MAX or d < 1e-6:
            continue
        ux, uy = dx / d, dy / d
        seg = LineString([(px, py), (bx - ux * OFFSET, by - uy * OFFSET)])
        cand = tree.query(seg)
        walls = 0
        for i in cand:
            if seg.intersects(segs[i]):
                walls += 1
        rssi = RSSI_REF_1M - 10 * N * math.log10(d) - sum(atten[i] for i in cand if seg.intersects(segs[i]))
        if rssi > VISIBLE:
            cnt += 1
    return cnt


def greedy_fill_counts(pts_counts, cap, R):
    """跟踪每个缺口点的实时可见数, 反复选取能为最多缺口点 +1 的位置布放补点,
    直到所有点都 >=3 或达上限。pts_counts: [[x,y,count], ...] (in-place 更新)。"""
    added = []
    weak = [p for p in pts_counts if p[2] < 3]
    while weak and len(added) < cap:
        best = None
        bestcov = 0
        for p in weak:
            cx, cy = p[0], p[1]
            cov = 0
            for q in weak:
                if math.hypot(q[0] - cx, q[1] - cy) <= R:
                    cov += 1
            if cov > bestcov:
                bestcov = cov
                best = (cx, cy)
        if best is None or bestcov == 0:
            break
        added.append(best)
        bx, by = best
        for q in weak:
            if math.hypot(q[0] - bx, q[1] - by) <= R:
                q[2] += 1
        weak = [p for p in weak if p[2] < 3]
    return added


def snap_to_wall(pt, segs):
    p = Point(pt)
    best = None
    bestd = 4.0
    for s in segs:
        d = p.distance(s)
        if d < bestd:
            bestd = d
            best = s
    if best is None:
        return pt, False
    proj = best.interpolate(best.project(p))
    return (proj.x, proj.y), True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--grid", type=float, default=1.0, help="仅用于报告, 目标点已固定为指纹网格 1m")
    ap.add_argument("--fill-cap", type=int, default=120)
    args = ap.parse_args()

    geo, base_plan, target = load()
    base_beac = beacons_by_floor(base_plan)
    target_pts = target_points_by_floor(target)
    max_minor = max(b.get("minor", 0) for b in base_plan["beacons"])

    aug_plan = json.loads(json.dumps(base_plan))  # deep copy
    for b in aug_plan["beacons"]:
        b["txPower"] = TX_POWER
        b["semanticTag"] = "trilateration_route_base"

    summary = {"params": {"txPower": TX_POWER, "visibleThreshold": VISIBLE, "fillRadius": FILL_R,
                          "basePlan": str(BASE_PLAN.name), "target": str(TARGET.name)},
               "floors": [], "added": 0}
    new_minor = max_minor
    added_total = 0

    for fl in ["1", "2"]:
        fg = geo["floors"][fl]["geometry"]
        segs, atten, tree = wall_segments(fg)
        beacons = list(base_beac.get(fl, []))
        pts = target_pts.get(fl, [])
        pts_counts = [[x, y, visible_count(x, y, beacons, tree, segs, atten)] for (x, y) in pts]
        before = [p for p in pts_counts if p[2] < 3]
        added = greedy_fill_counts(pts_counts, args.fill_cap, FILL_R)
        seq = 1
        for (cx, cy) in added:
            snapped, found = snap_to_wall((cx, cy), segs)
            new_minor += 1
            beacons.append(snapped)
            entry = {
                "beaconId": f"BK-TR-F{fl}-{seq:03d}",
                "uuid": base_plan["uuid"],
                "major": int(fl),
                "minor": new_minor,
                "coordinates": [snapped[0], snapped[1]],
                "plannedCoordinates": [snapped[0], snapped[1]],
                "floor": int(fl),
                "locationDesc": "测试路径补点(三点定位覆盖, 贴走廊墙安装 2.2m, 非天花板)"
                               + ("" if found else " / 无墙可吸附-需评估"),
                "mountType": "wall" if found else "ceiling",
                "installHeight": 2.2 if found else 3.0,
                "txPower": TX_POWER,
                "broadcastInterval": 300,
                "batteryModel": "CR2477",
                "expectedLifespan": 5,
                "semanticTag": "trilateration_route_fill",
                "sourceNodeId": None,
                "sourceNodeType": "route_fill",
                "riskLevel": "low",
                "snapDist_m": round(math.hypot(snapped[0] - cx, snapped[1] - cy), 2),
                "subType": "fill",
            }
            aug_plan["beacons"].append(entry)
            seq += 1
        # 重仿真
        after = [p for p in pts_counts if p[2] < 3]
        hist = {}
        for (x, y) in pts:
            c = visible_count(x, y, beacons, tree, segs, atten)
            hist[min(c, 5)] = hist.get(min(c, 5), 0) + 1
        ge3 = sum(v for k, v in hist.items() if k >= 3)
        summary["floors"].append({
            "floor": fl, "samples": len(pts),
            "base_beacons": len(base_beac.get(fl, [])),
            "added_beacons": len(added),
            "total_beacons": len(beacons),
            "weak_before": len(before), "weak_after": len(after),
            "pct_ge3_before": round(100.0 * (len(pts) - len(before)) / len(pts), 2) if pts else 0,
            "pct_ge3_after": round(100.0 * ge3 / len(pts), 2) if pts else 0,
            "hist_after": hist,
        })
        added_total += len(added)
        print(f"F{fl}: 基础 {len(base_beac.get(fl,[]))} + 补点 {len(added)} = {len(beacons)} | "
              f">=3 覆盖 {summary['floors'][-1]['pct_ge3_before']}% -> "
              f"{summary['floors'][-1]['pct_ge3_after']}% | 剩余缺口 {len(after)}")

    summary["added"] = added_total
    summary["total_beacons"] = len(aug_plan["beacons"])

    out_plan = ROOT / "result" / "beacon_deployment_plan_trilateration_routes.json"
    json.dump(aug_plan, open(out_plan, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    json.dump(summary, open(ROOT / "result" / "trilateration_coverage_routes_analysis.json", "w", encoding="utf-8"),
               ensure_ascii=False, indent=2)
    print(f"\n总信标数: {summary['total_beacons']} (基础 {len(base_plan['beacons'])} + 补点 {added_total})")
    print("计划已写入:", out_plan)


if __name__ == "__main__":
    main()
