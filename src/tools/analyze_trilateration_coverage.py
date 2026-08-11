#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
analyze_trilateration_coverage.py — 评估「全楼层信标部署方案」对三点定位(Trilateration)
的覆盖充分性：每个室内可行走点必须同时被 >=3 个信标稳定接收(RSSI > 阈值)。

方法：
  - 读取 beacon_deployment_plan.json(全楼 264 个) 与 school_building_01_map_v9.geojson。
  - 对每层 walkable_regions 的并集做 1m 网格采样(仅保留落在可行走区内的点)。
  - 对每个采样点，对每个同层信标：
      * 信标发射功率统一视为 TxPower=-10 dBm(三角定位要求功率一致)。
      * RSSI_ref@1m = -50 dBm (由 -10dBm 输出经 2.4GHz 自由空间 1m 损耗≈40dB 推出)。
      * 路径损耗: RSSI = RSSI_ref - 10*n*log10(d), n=3.5(室内)。
      * 穿墙衰减: 射线与每层墙体相交一次累加材质对应 dB(brick 12 / concrete 15 /
        partition 8 / 无标注 12 / glass 6)。
      * 信标坐标向采样点偏移 0.25m(天线在墙面内侧,不把安装墙算作穿透)。
      * 可见判定: RSSI > -85 dBm。
  - 统计每点可见信标数直方图，输出 >=3 覆盖占比、每层明细、最差缺口位置。

用法:
    python src/tools/analyze_trilateration_coverage.py
    python src/tools/analyze_trilateration_coverage.py --grid 1.0 --threshold -85
"""
from __future__ import annotations
import argparse, json, math
from collections import Counter
from pathlib import Path
from shapely.geometry import LineString, Point, shape
from shapely.ops import unary_union
from shapely.strtree import STRtree

ROOT = Path(__file__).resolve().parents[2]
GEO = ROOT / "result" / "school_building_01_map_v9.geojson"
PLAN = ROOT / "result" / "beacon_deployment_plan.json"

# ---- 无线模型参数 ----
TX_POWER = -10          # dBm，统一(三角定位要求一致)
RSSI_REF_1M = -50      # dBm，TxPower=-10 时 1m 参考(RSSI_ref = TX - FSPL@1m, FSPL≈40dB)
N = 3.5                # 室内路径损耗指数
WALL_ATTEN = {         # 每穿透一面墙的附加衰减 dB
    "brick": 12, "concrete": 15, "partition": 8, "glass": 6, None: 12,
}
VISIBLE = -85          # dBm 稳定可检测阈值
D_MAX = 11.0           # m，超出此距离即使 0 墙也不可见，跳过射线
OFFSET = 0.25          # m，信标向采样点偏移(天线在墙内侧)


def load_data():
    geo = json.load(open(GEO, encoding="utf-8"))
    plan = json.load(open(PLAN, encoding="utf-8"))
    return geo, plan


def wall_segments(floor_geo):
    segs, atten = [], []
    for w in floor_geo["walls"]:
        coords = w["geometry"]["coordinates"]
        if len(coords) < 2:
            continue
        ls = LineString([(x, y) for x, y in coords])
        a = WALL_ATTEN.get(w["properties"].get("material"), 12)
        segs.append(ls)
        atten.append(a)
    tree = STRtree(segs)
    return segs, atten, tree


def walkable_union(floor):
    polys = []
    for f in floor["walkable_regions"]["features"]:
        g = f.get("geometry")
        if g and g.get("type") == "Polygon":
            polys.append(shape(g))
    return unary_union(polys)


def beacons_by_floor(plan):
    out = {}
    for b in plan["beacons"]:
        out.setdefault(str(b["floor"]), []).append((b["coordinates"][0], b["coordinates"][1]))
    return out


def analyze_floor(floor_no, floor_geo, beacons, grid, tree, segs, atten, union, threshold):
    minx, miny, maxx, maxy = union.bounds
    pts = []
    g = grid
    x = minx
    while x <= maxx:
        y = miny
        while y <= maxy:
            p = Point(x, y)
            if union.contains(p):
                pts.append((x, y))
            y += g
        x += g
    hist = Counter()
    weak = []  # (x,y,count)
    total = len(pts)
    n_beac = len(beacons)
    for (px, py) in pts:
        visible = 0
        for (bx, by) in beacons:
            dx, dy = bx - px, by - py
            d = math.hypot(dx, dy)
            if d > D_MAX or d < 1e-6:
                continue
            # 信标向采样点偏移 0.25m(墙内侧)
            ux, uy = dx / d, dy / d
            ox, oy = bx - ux * OFFSET, by - uy * OFFSET
            seg = LineString([(px, py), (ox, oy)])
            # 查询附近墙体
            cand = tree.query(seg)
            walls = 0
            for idx in cand:
                if seg.intersects(segs[idx]):
                    walls += 1
            rssi = RSSI_REF_1M - 10 * N * math.log10(d) - sum(atten[i] for i in cand if seg.intersects(segs[i]))
            if rssi > threshold:
                visible += 1
        hist[min(visible, 5)] += 1
        if visible < 3:
            weak.append((round(px, 1), round(py, 1), visible))
    return {"floor": floor_no, "samples": total, "hist": dict(hist), "weak": weak, "beacons": n_beac}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--grid", type=float, default=1.0)
    ap.add_argument("--threshold", type=float, default=VISIBLE)
    args = ap.parse_args()

    geo, plan = load_data()
    beac = beacons_by_floor(plan)
    report = {"params": {"txPower": TX_POWER, "rssiRef1m": RSSI_REF_1M, "n": N,
                          "visibleThreshold": args.threshold, "dMax": D_MAX, "grid": args.grid},
              "floors": []}
    for fl in ["1", "2"]:
        fg = geo["floors"][fl]["geometry"]
        segs, atten, tree = wall_segments(fg)
        union = walkable_union(geo["floors"][fl])
        r = analyze_floor(fl, fg, beac.get(fl, []), args.grid, tree, segs, atten, union, args.threshold)
        area = union.area
        ge3 = sum(c for k, c in r["hist"].items() if k >= 3)
        r["walkable_area_m2"] = round(area, 1)
        r["pct_ge3"] = round(100.0 * ge3 / r["samples"], 2) if r["samples"] else 0
        report["floors"].append(r)
        print(f"F{fl}: 信标 {r['beacons']} | 可行走面积 {area:.0f}m² | 采样 {r['samples']} 点 | "
              f">=3 覆盖 {r['pct_ge3']}%")
        print(f"   可见数直方图 0/1/2/3/4/5+: {r['hist']}")
        print(f"   缺口(<3)点数: {len(r['weak'])}")
    out = ROOT / "result" / "trilateration_coverage_analysis.json"
    json.dump(report, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print("\n报告已写入:", out)


if __name__ == "__main__":
    main()
