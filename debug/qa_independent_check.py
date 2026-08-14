#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""QA 独立验证脚本：不调用被测脚本 detect_open_column_wraps.py 的任何函数，
仅用 shapely 直接重算 open_column_wraps.json 中的关键字段，逐项比对。

验证项:
  1. 开放柱 F1-C-0025 / F1-C-0099 / F2-C-0067:
     - 环形探测区开放度 openness = area(c.buffer(r) - c.buffer(0.05) ∩ O) / area(A)
     - 8 向射线落点(半径 r 处, 0.01 buffer)落入开放空间方向数
     - 包柱墙: 原始墙线到柱多边形距离 <= 0.15、单段长度 <= 2.0、中点象限
  2. 被拒柱 F1-C-0001 / F1-C-0002 / F1-C-0003:
     - 重算 openness, 确认 < 0.6 (openness_low 是否属实)
  3. 全量核对: 对所有开放柱逐个重算 openness 与报告值 diff (报告值四舍五入到 3 位)
"""
import json
import math
from collections import Counter

from shapely.geometry import Point, shape
from shapely.ops import unary_union

ROOT = "E:/code/pathai"
GEO = f"{ROOT}/result/school_building_01_map_v9.geojson"
REPORT = f"{ROOT}/result/open_column_wraps.json"

OPEN_TYPES = {"corridor", "lobby", "atrium", "elevator_lobby", "stair_lobby", "activity"}
R = 1.0
RAY_PROBE = 0.01
D_MAX = 0.15
L_MAX = 2.0
N_DIRS = 8

geo = json.load(open(GEO, encoding="utf-8"))
report = json.load(open(REPORT, encoding="utf-8"))

def build_O(fd):
    fg = fd.get("geometry", {})
    open_polys = []
    for f in fg.get("rooms", []):
        pr = f.get("properties", {})
        rtype = pr.get("type") or pr.get("roomType")
        if rtype in OPEN_TYPES:
            g = shape(f["geometry"])
            if not g.is_empty:
                open_polys.append(g)
    O = unary_union(open_polys) if open_polys else None
    wr = fd.get("walkable_regions")
    if wr and wr.get("features"):
        wr_polys = [shape(f["geometry"]) for f in wr["features"]
                    if f.get("geometry", {}).get("type") == "Polygon"]
        if wr_polys:
            wr_union = unary_union(wr_polys)
            O = unary_union([O, wr_union]) if O is not None else wr_union
    return O

def quadrant_of(px, py, x, y):
    ang = math.degrees(math.atan2(y - py, x - px)) % 360.0
    return int(ang // 90)

def annulus_openness(c, O):
    annulus = c.buffer(R).difference(c.buffer(0.05))
    a_a = annulus.area
    if a_a <= 1e-12:
        return 0.0, 0, 0.0, annulus
    a_o = annulus.intersection(O).area if O is not None else 0.0
    return a_o / a_a, 0, 0.0, annulus

def ray_nopen(p, O):
    n = 0
    for k in range(N_DIRS):
        ang = math.radians(k * 360.0 / N_DIRS)
        pt = Point(p.x + R * math.cos(ang), p.y + R * math.sin(ang))
        if O is not None and O.intersects(pt.buffer(RAY_PROBE)):
            n += 1
    return n

def main():
    print("========== 1. 开放柱独立重算比对 ==========")
    check_ids = [("1", "F1-C-0025"), ("1", "F1-C-0099"), ("2", "F2-C-0067")]
    all_ok = True
    for fl, cid in check_ids:
        fd = geo["floors"][fl]
        fg = fd.get("geometry", {})
        O = build_O(fd)
        col = next(f for f in fg["columns"] if f.get("id") == cid)
        c = shape(col["geometry"])
        p = c.centroid
        annulus = c.buffer(R).difference(c.buffer(0.05))
        a_a = annulus.area
        a_o = annulus.intersection(O).area
        openness = a_o / a_a
        n_open = ray_nopen(p, O)
        rep_col = next(x for x in report["floors"][fl]["openColumns"] if x["id"] == cid)
        d_open = abs(openness - rep_col["openness"])
        d_n = n_open - rep_col["nOpen"]
        status = "OK" if (d_open < 0.002 and d_n == 0) else "MISMATCH"
        if status != "OK":
            all_ok = False
        print(f"[{status}] {cid}: 独立 openness={openness:.6f} vs 报告 {rep_col['openness']} "
              f"(diff={d_open:.6f}) | 独立 nOpen={n_open} vs 报告 {rep_col['nOpen']} | "
              f"centroid=({p.x:.3f},{p.y:.3f}) 报告 {rep_col['centroid']}")
        # 独立算包柱墙
        walls = [shape(f["geometry"]) for f in fg["walls"] if f.get("geometry", {}).get("type") == "LineString"]
        wids = [f.get("id") for f in fg["walls"] if f.get("geometry", {}).get("type") == "LineString"]
        my_wrap = []
        for w, wid in zip(walls, wids):
            if w.length > L_MAX:
                continue
            d = w.distance(c)
            if d > D_MAX:
                continue
            mid = w.interpolate(w.length / 2)
            q = quadrant_of(p.x, p.y, mid.x, mid.y)
            my_wrap.append((wid, d, w.length, q))
        # 报告里的包柱墙（可能含 mergedFrom 的合并段，这里只比 id 是否都覆盖）
        rep_wall_ids = {e["id"] for e in rep_col["wrapWalls"]}
        my_wall_ids = {wid for wid, _, _, _ in my_wrap}
        # 报告的 id 是去重后代表段 id（簇内最长段），原墙 id 若被合并则不在 rep 中；
        # 这里检查: 报告所有 id 都确实存在且 dist<=dmax
        extra = my_wall_ids - rep_wall_ids
        missing = rep_wall_ids - my_wall_ids
        print(f"   独立贴柱墙数={len(my_wrap)} 报告包柱墙数={len(rep_col['wrapWalls'])} "
              f"| 报告有而独立无={sorted(missing)} | 独立有而报告无={sorted(extra)}")
        for wid, d, ln, q in sorted(my_wrap)[:8]:
            print(f"     {wid}: dist={d:.4f} len={ln:.3f} q={q}")

    print("\n========== 2. 被拒柱独立重算 ==========")
    for fl, cid in [("1", "F1-C-0001"), ("1", "F1-C-0002"), ("1", "F1-C-0003")]:
        fd = geo["floors"][fl]
        fg = fd.get("geometry", {})
        O = build_O(fd)
        col = next(f for f in fg["columns"] if f.get("id") == cid)
        c = shape(col["geometry"])
        p = c.centroid
        annulus = c.buffer(R).difference(c.buffer(0.05))
        a_a = annulus.area
        a_o = annulus.intersection(O).area
        openness = a_o / a_a
        n_open = ray_nopen(p, O)
        rep_rej = next((x for x in report["floors"][fl]["rejectedColumns"] if x["id"] == cid), None)
        print(f"F1 {cid}: 独立 openness={openness:.4f} nOpen={n_open} "
              f"报告 reason={rep_rej['reason'] if rep_rej else 'NOT IN REPORT'}")

    print("\n========== 3. 全量开放柱 openness 核对 ==========")
    diffs = []
    for fl, fd in geo["floors"].items():
        O = build_O(fd)
        fg = fd.get("geometry", {})
        for rep_col in report["floors"][fl]["openColumns"]:
            cid = rep_col["id"]
            col = next(f for f in fg["columns"] if f.get("id") == cid)
            c = shape(col["geometry"])
            p = c.centroid
            annulus = c.buffer(R).difference(c.buffer(0.05))
            a_a = annulus.area
            a_o = annulus.intersection(O).area
            openness = a_o / a_a
            diff = abs(openness - rep_col["openness"])
            diffs.append((cid, openness, rep_col["openness"], diff))
    maxdiff = max(d[3] for d in diffs)
    print(f"开放柱 {len(diffs)} 个, openness 最大 diff={maxdiff:.6f} "
          f"({'OK(<0.002)' if maxdiff < 0.002 else 'MISMATCH'})")
    for cid, o, ro, d in diffs:
        if d >= 0.002:
            print(f"   {cid}: 独立={o:.6f} 报告={ro} diff={d:.6f}")
            all_ok = False

    print("\n========== 4. 被拒理由分布 ==========")
    reasons = Counter()
    for fl in report["floors"]:
        for r in report["floors"][fl]["rejectedColumns"]:
            reasons[r["reason"]] += 1
    print(dict(reasons))

    print("\n========== 结论 ==========")
    print("ALL_MATCH" if all_ok and maxdiff < 0.002 else "HAS_MISMATCH")

if __name__ == "__main__":
    main()
