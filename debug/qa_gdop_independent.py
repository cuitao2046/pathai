#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
QA 独立验证脚本 (严过关) — GDOP 退化判据升级 + R3 补点策略改造。
不 import src/tools/refine_beacon_placement.py 的任何函数, 从零重算:
  - 2D 三边测量 GDOP = sqrt(trace((H^T H)^-1)), 共线返回 999
  - 目标到三角形最近边距离 tri_out_dist (内=0)
  - RSSI 可见性模型: RSSI_REF_1M=-50, N=3.5, VISIBLE=-85, D_MAX=11.0, OFFSET=0.25
    WALL_ATTEN={brick:12,concrete:15,partition:8,glass:6,None:12}, 墙段 STRtree 加速
用法:
  python debug/qa_gdop_independent.py [--plan <json>] [--points <json>] [--self-test]
"""
import argparse
import json
import math
import sys
from collections import Counter

from shapely.geometry import LineString, Point
from shapely.strtree import STRtree

ROOT = "E:/code/pathai"
GEO = f"{ROOT}/result/school_building_01_map_v9.geojson"
DEFAULT_PLAN = f"{ROOT}/result/beacon_deployment_plan_trilateration_routes_refined.json"
DEFAULT_POINTS = f"{ROOT}/result/fingerprint_grid_routes.json"

RSSI_REF_1M = -50
N = 3.5
WALL_ATTEN = {"brick": 12, "concrete": 15, "partition": 8, "glass": 6, None: 12}
VISIBLE = -85
D_MAX = 11.0
OFFSET = 0.25
GDOP_MAX = 3.0
D_OUT_MAX = 1.0


# ---------- 独立重算: GDOP ----------
def gdop2d(p, b1, b2, b3):
    """2D 三边测量 GDOP = sqrt(trace((H^T H)^-1)); H = 3x2 方向余弦矩阵。
    理论: 等边三角+目标在中心 -> sqrt(4/3) ≈ 1.1547; 共线/重合 -> 999。"""
    a11 = a12 = a22 = 0.0
    for b in (b1, b2, b3):
        dx, dy = b[0] - p[0], b[1] - p[1]
        d = math.hypot(dx, dy)
        if d < 1e-6:
            return 999.0
        cx, cy = dx / d, dy / d
        a11 += cx * cx
        a12 += cx * cy
        a22 += cy * cy
    det = a11 * a22 - a12 * a12
    return math.sqrt((a11 + a22) / det) if det > 1e-12 else 999.0


def _sign_tri(p1, p2, p3):
    return (p1[0] - p3[0]) * (p2[1] - p3[1]) - (p2[0] - p3[0]) * (p1[1] - p3[1])


def in_triangle(p, a, b, c):
    d1, d2, d3 = _sign_tri(p, a, b), _sign_tri(p, b, c), _sign_tri(p, c, a)
    return not ((d1 < 0 or d2 < 0 or d3 < 0) and (d1 > 0 or d2 > 0 or d3 > 0))


def _dist_pt_seg(p, a, b):
    ax, ay = a
    bx, by = b
    px, py = p
    dx, dy = bx - ax, by - ay
    L2 = dx * dx + dy * dy
    t = 0 if L2 < 1e-12 else max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / L2))
    cx, cy = ax + t * dx, ay + t * dy
    return math.hypot(px - cx, py - cy)


def tri_out_dist(p, a, b, c):
    if in_triangle(p, a, b, c):
        return 0.0
    return min(_dist_pt_seg(p, a, b), _dist_pt_seg(p, b, c), _dist_pt_seg(p, c, a))


def is_degenerate(p, b1, b2, b3):
    return gdop2d(p, b1, b2, b3) > GDOP_MAX or tri_out_dist(p, b1, b2, b3) > D_OUT_MAX


# ---------- 独立重算: RSSI 可见性 ----------
def load_floor_geom(geo, fl):
    fg = geo["floors"][fl]["geometry"]
    segs, atten = [], []
    for w in fg.get("walls", []):
        c = w["geometry"]["coordinates"]
        if len(c) < 2:
            continue
        segs.append(LineString([(x, y) for x, y in c]))
        atten.append(WALL_ATTEN.get((w.get("properties") or {}).get("material"), 12))
    return segs, atten


def visible_ids(pt, beacons, segs, tree, atten):
    """返回点 pt 可见的信标索引集合 (与 refine 同口径, 独立实现)。"""
    out = set()
    x, y = pt
    for i, (bx, by) in enumerate(beacons):
        dx, dy = bx - x, by - y
        d = math.hypot(dx, dy)
        if d > D_MAX or d < 1e-6:
            continue
        ux, uy = dx / d, dy / d
        seg = LineString([(x, y), (bx - ux * OFFSET, by - uy * OFFSET)])
        cand = tree.query(seg)
        atten_sum = 0
        for j in cand:
            if seg.intersects(segs[j]):
                atten_sum += atten[j]
        rssi = RSSI_REF_1M - 10 * N * math.log10(d) - atten_sum
        if rssi > VISIBLE:
            out.add(i)
    return out


def analyze(plan_path, points_path, verbose=True):
    geo = json.load(open(GEO, encoding="utf-8"))
    plan = json.load(open(plan_path, encoding="utf-8"))
    fp = json.load(open(points_path, encoding="utf-8"))

    beacons_by_floor = {}
    for b in plan["beacons"]:
        beacons_by_floor.setdefault(str(b["floor"]), []).append(
            (b["coordinates"][0], b["coordinates"][1]))

    pts_by_floor = {}
    for fk_str, fdata in fp.get("floors", {}).items():
        pts_by_floor[fk_str] = [(p["coordinates"][0], p["coordinates"][1])
                                for p in fdata.get("points", [])]

    total_pts = 0
    ge3 = 0
    degen = 0
    gdop_hi = 0
    dout_hi = 0
    both_hi = 0
    details = {}  # point id -> (gdop, dout)

    for fl in ("1", "2"):
        segs, atten = load_floor_geom(geo, fl)
        tree = STRtree(segs)
        beacons = beacons_by_floor.get(fl, [])
        pts = pts_by_floor.get(fl, [])
        for i, pt in enumerate(pts):
            total_pts += 1
            vis = visible_ids(pt, beacons, segs, tree, atten)
            if len(vis) < 3:
                continue
            ge3 += 1
            top3 = sorted(vis, key=lambda k: math.hypot(
                beacons[k][0] - pt[0], beacons[k][1] - pt[1]))[:3]
            b0, b1, b2 = [beacons[k] for k in top3]
            g = gdop2d(pt, b0, b1, b2)
            dout = tri_out_dist(pt, b0, b1, b2)
            details[(fl, i)] = (g, dout)
            if g > GDOP_MAX:
                gdop_hi += 1
            if dout > D_OUT_MAX:
                dout_hi += 1
            if is_degenerate(pt, b0, b1, b2):
                degen += 1
                if g > GDOP_MAX and dout > D_OUT_MAX:
                    both_hi += 1

    pct_ge3 = round(100.0 * ge3 / total_pts, 2) if total_pts else 0.0
    if verbose:
        print(f"plan      : {plan_path}")
        print(f"points    : {points_path}  total={total_pts}")
        print(f"beacons   : F1={len(beacons_by_floor.get('1', []))} "
              f"F2={len(beacons_by_floor.get('2', []))} "
              f"total={sum(len(v) for v in beacons_by_floor.values())}")
        print(f"pct_ge3   : {pct_ge3}%  (ge3={ge3}/{total_pts})")
        print(f"degen     : {degen}   (GDOP>3: {gdop_hi}, dout>1m: {dout_hi}, both: {both_hi})")
        print(f"  disjoint check: gdop_hi + dout_hi - both_hi = "
              f"{gdop_hi + dout_hi - both_hi} vs degen={degen}")
    return {"total_pts": total_pts, "ge3": ge3, "pct_ge3": pct_ge3,
            "degen": degen, "gdop_hi": gdop_hi, "dout_hi": dout_hi,
            "both_hi": both_hi, "details": details}


def self_test():
    """数学自检: 等边三角中心 GDOP≈1.155; 共线 999; 外扩距离。"""
    import math as _m
    ok = True
    # 等边三角边长 L, 目标在质心
    L = 10.0
    a = (0.0, 0.0)
    b = (L, 0.0)
    c = (L / 2, L * _m.sqrt(3) / 2)
    cen = ((a[0] + b[0] + c[0]) / 3, (a[1] + b[1] + c[1]) / 3)
    g = gdop2d(cen, a, b, c)
    print(f"[selftest] equilateral center gdop = {g:.4f} (expect ~1.1547)")
    if abs(g - 1.1547005383792517) > 1e-6:
        ok = False
    # 共线
    g2 = gdop2d((0.5, 0.0), (0.0, 0.0), (1.0, 0.0), (2.0, 0.0))
    print(f"[selftest] collinear gdop = {g2} (expect 999)")
    if g2 != 999.0:
        ok = False
    # 目标在三角形内 -> dout 0
    d = tri_out_dist(cen, a, b, c)
    print(f"[selftest] inside dout = {d} (expect 0)")
    if d != 0.0:
        ok = False
    # 目标在三角形外 -> 最近边距离
    far = (L / 2, -3.0)
    d2 = tri_out_dist(far, a, b, c)
    print(f"[selftest] outside dout = {d2:.4f} (expect 3.0)")
    if abs(d2 - 3.0) > 1e-9:
        ok = False
    # 退化判定: 共线三角形 + 目标 → degenerate True
    degen = is_degenerate((0.5, 0.0), (0.0, 0.0), (1.0, 0.0), (2.0, 0.0))
    print(f"[selftest] collinear degenerate = {degen} (expect True)")
    if not degen:
        ok = False
    # 等边三角中心: 非退化
    degen2 = is_degenerate(cen, a, b, c)
    print(f"[selftest] equilateral degenerate = {degen2} (expect False)")
    if degen2:
        ok = False
    print("SELFTEST:", "PASS" if ok else "FAIL")
    return ok


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--plan", default=DEFAULT_PLAN)
    ap.add_argument("--points", default=DEFAULT_POINTS)
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args()
    if args.self_test:
        sys.exit(0 if self_test() else 1)
    analyze(args.plan, args.points)
