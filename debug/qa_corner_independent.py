#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""QA 独立角落检查: 用 refine 代码的『真墙角』定义(墙方向夹角>30°)复算
满配额 110 信标方案的信标距真墙角距离, 交叉核对 check_beacon_7rules.py [3]
(其用「任意墙段端点<0.3m」启发式, 会把门套/共线墙拼接点误判为墙角)。"""
import json
import math
from collections import defaultdict

import shapely
from shapely.geometry import Point
from shapely.strtree import STRtree

ROOT = "E:/code/pathai"
GEO = json.load(open(f"{ROOT}/result/school_building_01_map_v9.geojson", encoding="utf-8"))
PLAN = json.load(open(f"{ROOT}/result/beacon_deployment_plan_trilateration_routes_refined.json", encoding="utf-8"))

CORNER_ANGLE_DEG = 30.0
CORNER_CLEAR = 0.8


def true_corners(fg):
    vdir = defaultdict(list)
    for w in fg["walls"]:
        c = w["geometry"]["coordinates"]
        for i in range(len(c) - 1):
            a, b = c[i], c[i + 1]
            d = math.hypot(b[0] - a[0], b[1] - a[1])
            if d < 1e-9:
                continue
            ang = math.atan2(b[1] - a[1], b[0] - a[0])
            for pt in (a, b):
                vdir[(round(pt[0], 3), round(pt[1], 3))].append(ang)
    corners = []
    for (vx, vy), angs in vdir.items():
        if len(angs) < 2:
            continue
        mx = 0.0
        for i in range(len(angs)):
            for j in range(i + 1, len(angs)):
                diff = abs(((angs[i] - angs[j] + math.pi) % (2 * math.pi)) - math.pi)
                mx = max(mx, math.degrees(diff))
        if mx > CORNER_ANGLE_DEG:
            corners.append(Point(vx, vy))
    return corners


def on_column(p, fl):
    """独立复算: 是否挂柱(距某柱多边形 <= COL_MOUNT_TOL=0.06)。"""
    fg = GEO["floors"][fl]["geometry"]
    for c in fg.get("columns", []):
        if c.get("geometry", {}).get("type") != "Polygon":
            continue
        poly = json_load_poly(c["geometry"])
        if p.distance(poly) <= 0.06:
            return True
    return False


def json_load_poly(g):
    from shapely.geometry import Polygon
    return Polygon(g["coordinates"][0])


def main():
    by_floor = {}
    for b in PLAN["beacons"]:
        by_floor.setdefault(str(b["floor"]), []).append(b)

    flagged_7rules = ["BK-01-006", "BK-01-008", "BK-01-009", "BK-01-012", "BK-01-016",
                      "BK-01-019", "BK-01-021", "BK-01-022", "BK-01-025", "BK-01-026",
                      "BK-01-027", "BK-01-028", "BK-01-033", "BK-02-003", "BK-Q-F1-021"]

    print("== 每层真墙角数 ==")
    for fl in ("1", "2"):
        fg = GEO["floors"][fl]["geometry"]
        corners = true_corners(fg)
        print(f"  F{fl}: {len(corners)} 真墙角")

    print("\n== 110 信标距最近真墙角距离 (违规: <0.8m; 挂柱豁免) ==")
    viol = []
    for fl in ("1", "2"):
        fg = GEO["floors"][fl]["geometry"]
        corners = true_corners(fg)
        ctree = STRtree(corners) if corners else None
        for b in by_floor.get(fl, []):
            p = Point(b["coordinates"])
            if on_column(p, fl):
                continue  # 柱面挂载豁免
            if ctree is None:
                continue
            i = ctree.nearest(p)
            dc = p.distance(corners[i])
            if dc < CORNER_CLEAR:
                viol.append((b["beaconId"], fl, round(dc, 2)))
    print(f"  真墙角违规(<{CORNER_CLEAR}m): {len(viol)}")
    for v in viol:
        print("   ", v)

    print("\n== 7rules 标记的 17 个信标中, 真墙角距离 ==")
    for bid in flagged_7rules:
        found = None
        for fl in ("1", "2"):
            for b in by_floor.get(fl, []):
                if b["beaconId"] == bid:
                    found = (b, fl)
        if not found:
            continue
        b, fl = found
        fg = GEO["floors"][fl]["geometry"]
        corners = true_corners(fg)
        ctree = STRtree(corners) if corners else None
        p = Point(b["coordinates"])
        if ctree is None:
            print(f"  {bid}: no corners")
            continue
        i = ctree.nearest(p)
        print(f"  {bid}: 真墙角距={p.distance(corners[i]):.2f}m  "
              f"{'违规' if p.distance(corners[i]) < CORNER_CLEAR else 'OK(非真墙角)'}")

    print("\n== 全部新增 BK-Q-* 信标真墙角距离 ==")
    for fl in ("1", "2"):
        fg = GEO["floors"][fl]["geometry"]
        corners = true_corners(fg)
        ctree = STRtree(corners) if corners else None
        for b in by_floor.get(fl, []):
            if b["beaconId"].startswith("BK-Q-"):
                p = Point(b["coordinates"])
                i = ctree.nearest(p)
                dc = p.distance(corners[i])
                mark = "VIOL" if dc < CORNER_CLEAR else "ok"
                print(f"  {b['beaconId']} F{fl} 真墙角距={dc:.2f}m [{mark}]")


if __name__ == "__main__":
    main()
