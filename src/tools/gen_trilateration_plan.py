#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
gen_trilateration_plan.py — 在当前「全楼层信标部署方案」(beacon_deployment_plan.json,
264 个, 全贴墙/门套) 基础上，针对三点定位(Trilateration)的硬约束「每点须同时被 >=3 个
信标稳定接收」做缺口填补，生成三点定位专用部署计划。

步骤:
  1. 读取 GeoJSON + 基础计划，对每层 walkable 区 1m 网格采样，射线穿墙算 RSSI，
     统计每点可见信标数(< 3 为缺口)。
  2. 贪心补点: 对每个缺口点用「距离模型」(房间内 LOS 近似 0 墙) 估计一个补点能覆盖的
     缺口数，反复选取覆盖最多的位置布放新信标，直到无缺口或达上限。
  3. 将补点吸附到最近墙线段(<=4m)以贴墙安装(遵循避免天花板部署原则)；若无处吸附则
     标 ceiling(极少)。
  4. 全部信标 txPower 统一 -10 dBm(三角定位要求功率一致)，写 augmented 计划。
  5. 用真实穿墙模型对 augmented 计划重仿真，输出最终 >=3 覆盖率。

输出:
  result/beacon_deployment_plan_trilateration.json  (264 + 补点)
  result/trilateration_coverage_analysis.json       (覆盖仿真报告, 含前后对比)

用法:
  python src/tools/gen_trilateration_plan.py
  python src/tools/gen_trilateration_plan.py --fill-cap 80 --grid 1.0
"""
from __future__ import annotations
import argparse, json, math
from pathlib import Path
from shapely.geometry import LineString, Point, shape
from shapely.ops import unary_union
from shapely.strtree import STRtree

ROOT = Path(__file__).resolve().parents[2]
GEO = ROOT / "result" / "school_building_01_map_v9.geojson"
PLAN = ROOT / "result" / "beacon_deployment_plan.json"

TX_POWER = -10
RSSI_REF_1M = -50
N = 3.5
WALL_ATTEN = {"brick": 12, "concrete": 15, "partition": 8, "glass": 6, None: 12}
VISIBLE = -85
D_MAX = 11.0
OFFSET = 0.25
FILL_R = 8.0      # 贪心补点覆盖半径(距离模型, 房间内近似 0 墙)


def load():
    return json.load(open(GEO, encoding="utf-8")), json.load(open(PLAN, encoding="utf-8"))


def wall_segments(fg):
    segs, atten = [], []
    for w in fg["walls"]:
        c = w["geometry"]["coordinates"]
        if len(c) < 2:
            continue
        segs.append(LineString([(x, y) for x, y in c]))
        atten.append(WALL_ATTEN.get(w["properties"].get("material"), 12))
    return segs, atten, STRtree(segs)


def walkable_union(fl):
    polys = [shape(f["geometry"]) for f in fl["walkable_regions"]["features"]
             if f.get("geometry", {}).get("type") == "Polygon"]
    return unary_union(polys)


def beacons_by_floor(plan):
    out = {}
    for b in plan["beacons"]:
        out.setdefault(str(b["floor"]), []).append((b["coordinates"][0], b["coordinates"][1]))
    return out


def sample_points(union, grid):
    minx, miny, maxx, maxy = union.bounds
    pts = []
    x = minx
    while x <= maxx:
        y = miny
        while y <= maxy:
            if union.contains(Point(x, y)):
                pts.append((x, y))
            y += grid
        x += grid
    return pts


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


def weak_points(pts, beacons, tree, segs, atten):
    weak = []
    for (x, y) in pts:
        if visible_count(x, y, beacons, tree, segs, atten) < 3:
            weak.append((x, y))
    return weak


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
    """将补点吸附到最近墙线段(<=4m)，返回 (snapped_coord, found)。"""
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
    ap.add_argument("--grid", type=float, default=1.0)
    ap.add_argument("--fill-cap", type=int, default=120)
    args = ap.parse_args()

    geo, plan = load()
    base_beac = beacons_by_floor(plan)
    max_minor = max(b.get("minor", 0) for b in plan["beacons"])

    aug_plan = json.loads(json.dumps(plan))  # deep copy
    # 统一功率
    for b in aug_plan["beacons"]:
        b["txPower"] = TX_POWER

    summary = {"params": {"txPower": TX_POWER, "visibleThreshold": VISIBLE, "fillRadius": FILL_R},
               "floors": [], "added": 0}
    new_minor = max_minor
    added_total = 0

    for fl in ["1", "2"]:
        fg = geo["floors"][fl]["geometry"]
        segs, atten, tree = wall_segments(fg)
        union = walkable_union(geo["floors"][fl])
        beacons = list(base_beac.get(fl, []))
        pts = sample_points(union, args.grid)
        # 计算每个采样点的基础可见数(穿墙模型)
        pts_counts = [[x, y, visible_count(x, y, beacons, tree, segs, atten)] for (x, y) in pts]
        before = [p for p in pts_counts if p[2] < 3]
        # 贪心补点(跟踪实时可见数, 直到每个点 >=3)
        added = greedy_fill_counts(pts_counts, args.fill_cap, FILL_R)
        # 补点保持房间内部坐标(保证 LOS 可视)，贴房间内墙/高处书架/支架 2.2m，非天花板
        seq = 1
        for (cx, cy) in added:
            new_minor += 1
            beacons.append((cx, cy))
            entry = {
                "beaconId": f"BK-T-F{fl}-{seq:03d}",
                "uuid": plan["uuid"],
                "major": int(fl),
                "minor": new_minor,
                "coordinates": [cx, cy],
                "plannedCoordinates": [cx, cy],
                "floor": int(fl),
                "locationDesc": "房间内部补点(三点定位覆盖, 建议贴房间内墙/高处书架/支架 2.2m, 非天花板)",
                "mountType": "interior",
                "installHeight": 2.2,
                "txPower": TX_POWER,
                "broadcastInterval": 300,
                "batteryModel": "CR2477",
                "expectedLifespan": 5,
                "semanticTag": "trilateration_fill",
                "sourceNodeId": None,
                "sourceNodeType": "interior_fill",
                "riskLevel": "low",
                "snapDist_m": 0.0,
                "subType": "fill",
            }
            aug_plan["beacons"].append(entry)
            seq += 1
        # 重仿真(augmented)
        after = weak_points(pts, beacons, tree, segs, atten)
        hist = {}
        for (x, y) in pts:
            c = visible_count(x, y, beacons, tree, segs, atten)
            hist[min(c, 5)] = hist.get(min(c, 5), 0) + 1
        ge3 = sum(v for k, v in hist.items() if k >= 3)
        summary["floors"].append({
            "floor": fl, "samples": len(pts), "walkable_area_m2": round(union.area, 1),
            "base_beacons": len(base_beac.get(fl, [])),
            "added_beacons": len(added),
            "total_beacons": len(beacons),
            "weak_before": len(before), "weak_after": len(after),
            "pct_ge3_before": round(100.0 * (len(pts) - len(before)) / len(pts), 2),
            "pct_ge3_after": round(100.0 * ge3 / len(pts), 2),
            "hist_after": hist,
        })
        added_total += len(added)
        print(f"F{fl}: 基础 {len(base_beac.get(fl,[]))} + 补点 {len(added)} = {len(beacons)} | "
              f">=3 覆盖 {summary['floors'][-1]['pct_ge3_before']}% -> "
              f"{summary['floors'][-1]['pct_ge3_after']}% | 剩余缺口 {len(after)}")

    summary["added"] = added_total
    summary["total_beacons"] = len(aug_plan["beacons"])

    out_plan = ROOT / "result" / "beacon_deployment_plan_trilateration.json"
    json.dump(aug_plan, open(out_plan, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    json.dump(summary, open(ROOT / "result" / "trilateration_coverage_analysis.json", "w", encoding="utf-8"),
               ensure_ascii=False, indent=2)
    print(f"\n总信标数: {summary['total_beacons']} (基础 264 + 补点 {added_total})")
    print("计划已写入:", out_plan)


if __name__ == "__main__":
    main()
