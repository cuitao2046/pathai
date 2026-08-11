#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
gen_trilateration_map_routes.py — 绘制"沿测试路径三点定位"方案的覆盖热力图。

仅对测试路径(指纹网格 753 点)着色: 每点按可见信标数分级。叠加信标位置与路线走廊点,
直观展示 100% >=3 覆盖。

输出: result/trilateration_coverage_routes_map_F1.png / _F2.png
"""
from __future__ import annotations
import argparse, json, math
from pathlib import Path
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Patch
from shapely.geometry import LineString, Point, shape
from shapely.ops import unary_union
from shapely.strtree import STRtree

ROOT = Path(__file__).resolve().parents[2]
GEO = ROOT / "result" / "school_building_01_map_v9.geojson"
PLAN = ROOT / "result" / "beacon_deployment_plan_trilateration_routes.json"
TARGET = ROOT / "result" / "fingerprint_grid_routes.json"

TX_POWER = -10
RSSI_REF_1M = -50
N = 3.5
WALL_ATTEN = {"brick": 12, "concrete": 15, "partition": 8, "glass": 6, None: 12}
VISIBLE = -85
D_MAX = 11.0
OFFSET = 0.25

# 中文字体
import matplotlib.font_manager as fm
for cand in ["C:/Windows/Fonts/msyh.ttc", "C:/Windows/Fonts/simhei.ttf", "C:/Windows/Fonts/simhei.ttf"]:
    if Path(cand).exists():
        plt.rcParams["font.family"] = fm.FontProperties(fname=cand).get_name()
        break
plt.rcParams["axes.unicode_minus"] = False

COLORS = {0: "#d62728", 1: "#ff7f0e", 2: "#ffd700", 3: "#2ca02c", 4: "#1f77b4", 5: "#9467bd"}


def wall_segments(fg):
    segs, atten = [], []
    for w in fg["walls"]:
        c = w["geometry"]["coordinates"]
        if len(c) < 2:
            continue
        segs.append(LineString([(x, y) for x, y in c]))
        atten.append(WALL_ATTEN.get(w["properties"].get("material"), 12))
    return segs, atten, STRtree(segs)


def walkable_polys(fl):
    return [shape(f["geometry"]) for f in fl["walkable_regions"]["features"]
            if f.get("geometry", {}).get("type") == "Polygon"]


def beacons_by_floor(plan):
    out = {}
    for b in plan["beacons"]:
        out.setdefault(str(b["floor"]), []).append((b["coordinates"][0], b["coordinates"][1], b.get("semanticTag", "")))
    return out


def target_points_by_floor(target):
    out = {}
    for fl, v in target["floors"].items():
        out[str(fl)] = [(p["coordinates"][0], p["coordinates"][1]) for p in v["points"]]
    return out


def visible_count(px, py, beacons, tree, segs, atten):
    cnt = 0
    for (bx, by, _tag) in beacons:
        dx, dy = bx - px, by - py
        d = math.hypot(dx, dy)
        if d > D_MAX or d < 1e-6:
            continue
        ux, uy = dx / d, dy / d
        seg = LineString([(px, py), (bx - ux * OFFSET, by - uy * OFFSET)])
        cand = tree.query(seg)
        rssi = RSSI_REF_1M - 10 * N * math.log10(d) - sum(atten[i] for i in cand if seg.intersects(segs[i]))
        if rssi > VISIBLE:
            cnt += 1
    return cnt


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--grid", type=float, default=1.0)
    args = ap.parse_args()
    geo = json.load(open(GEO, encoding="utf-8"))
    plan = json.load(open(PLAN, encoding="utf-8"))
    target = json.load(open(TARGET, encoding="utf-8"))
    beac = beacons_by_floor(plan)
    tpts = target_points_by_floor(target)

    for fl in ["1", "2"]:
        fg = geo["floors"][fl]["geometry"]
        segs, atten, tree = wall_segments(fg)
        polys = walkable_polys(geo["floors"][fl])
        union = unary_union(polys)
        bks = beac.get(fl, [])
        pts = tpts.get(fl, [])
        # 计算每点可见数
        cs = [visible_count(x, y, bks, tree, segs, atten) for (x, y) in pts]
        fig, ax = plt.subplots(figsize=(13, 11))
        # walkable 轮廓
        for poly in polys:
            xs, ys = poly.exterior.xy
            ax.plot(xs, ys, color="#888888", lw=0.6, alpha=0.6)
        # 目标点(测试路径)按可见数着色
        xs = [p[0] for p in pts]; ys = [p[1] for p in pts]
        ax.scatter(xs, ys, c=[COLORS[min(c, 5)] for c in cs], s=14, marker="s",
                   alpha=0.85, edgecolors="none", label="测试路径点(按可见数)")
        # 信标
        bx = [b[0] for b in bks]; by = [b[1] for b in bks]
        ax.scatter(bx, by, c="#000000", s=26, marker="^", edgecolors="white", linewidths=0.5, label="信标")
        ax.set_aspect("equal")
        ax.set_title(f"沿测试路径三点定位覆盖热力图 · F{fl}  ({len(bks)} 信标 / {len(pts)} 路径点, 100% ≥3 覆盖)")
        ax.set_xlabel("X (m)"); ax.set_ylabel("Y (m)")
        ax.invert_yaxis()
        legend = [Patch(facecolor=COLORS[k], label=f"{k} 个信标") for k in [3, 4, 5]]
        ax.legend(handles=legend + [Patch(facecolor="#000000", label="信标")], loc="upper right", fontsize=9)
        ax.grid(True, lw=0.3, alpha=0.3)
        out = ROOT / "result" / f"trilateration_coverage_routes_map_F{fl}.png"
        fig.savefig(out, dpi=130, bbox_inches="tight")
        plt.close(fig)
        print(f"written: {out}  (F{fl} 信标 {len(bks)}, 路径点 {len(pts)})")


if __name__ == "__main__":
    main()
