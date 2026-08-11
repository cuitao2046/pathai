#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
gen_trilateration_map.py — 渲染三点定位全楼层部署的覆盖热力图。

对每层 walkable 区按 grid 采样，逐点射线穿墙算可见信标数，绘制:
  - 浅灰: 可行走区
  - 背景散点: 按可见信标数着色(红=0/1 -> 黄 -> 绿=>=3)
  - 黑 x: 基础信标(贴墙/门套)   蓝 o: 房间内补点
  - 红圈: 剩余缺口点(可见 <3)

输出: result/trilateration_coverage_map_F1.png / _F2.png
"""
from __future__ import annotations
import argparse, json, math
from pathlib import Path
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.collections import LineCollection
from shapely.geometry import LineString, Point, shape
from shapely.ops import unary_union
from shapely.strtree import STRtree

ROOT = Path(__file__).resolve().parents[2]
GEO = ROOT / "result" / "school_building_01_map_v9.geojson"
PLAN = ROOT / "result" / "beacon_deployment_plan_trilateration.json"

import matplotlib.font_manager as fm
for _fp in ["C:/Windows/Fonts/msyh.ttc", "C:/Windows/Fonts/simhei.ttf"]:
    if Path(_fp).exists():
        fm.fontManager.addfont(_fp)
        matplotlib.rcParams["font.family"] = fm.FontProperties(fname=_fp).get_name()
        break
matplotlib.rcParams["axes.unicode_minus"] = False

TX_POWER = -10
RSSI_REF_1M = -50
N = 3.5
WALL_ATTEN = {"brick": 12, "concrete": 15, "partition": 8, "glass": 6, None: 12}
VISIBLE = -85
D_MAX = 11.0
OFFSET = 0.25


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
        walls = sum(1 for i in cand if seg.intersects(segs[i]))
        rssi = RSSI_REF_1M - 10 * N * math.log10(d) - sum(atten[i] for i in cand if seg.intersects(segs[i]))
        if rssi > VISIBLE:
            cnt += 1
    return cnt


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--grid", type=float, default=1.5)
    args = ap.parse_args()
    geo = json.load(open(GEO, encoding="utf-8"))
    plan = json.load(open(PLAN, encoding="utf-8"))
    for fl in ["1", "2"]:
        fg = geo["floors"][fl]["geometry"]
        segs, atten, tree = wall_segments(fg)
        union = walkable_union(geo["floors"][fl])
        base, fill = [], []
        for b in plan["beacons"]:
            if str(b["floor"]) != fl:
                continue
            (fill if b.get("subType") == "fill" else base).append((b["coordinates"][0], b["coordinates"][1]))
        minx, miny, maxx, maxy = union.bounds
        xs, ys, cs = [], [], []
        weak = []
        g = args.grid
        x = minx
        while x <= maxx:
            y = miny
            while y <= maxy:
                if union.contains(Point(x, y)):
                    c = visible_count(x, y, base + fill, tree, segs, atten)
                    xs.append(x); ys.append(y); cs.append(c)
                    if c < 3:
                        weak.append((x, y))
                y += g
            x += g
        fig, ax = plt.subplots(figsize=(11, 8))
        # walkable outline
        if union.geom_type == "Polygon":
            px, py = union.exterior.xy
            ax.plot(px, py, color="#999", lw=0.6)
        else:
            for g0 in union.geoms:
                px, py = g0.exterior.xy
                ax.plot(px, py, color="#999", lw=0.6)
        sc = ax.scatter(xs, ys, c=cs, cmap="RdYlGn", vmin=0, vmax=5, s=8, alpha=0.8)
        if base:
            bx, by = zip(*base)
            ax.scatter(bx, by, marker="x", c="black", s=18, label=f"基础 {len(base)}")
        if fill:
            fx, fy = zip(*fill)
            ax.scatter(fx, fy, marker="o", c="blue", s=14, label=f"补点 {len(fill)}")
        if weak:
            wx, wy = zip(*weak)
            ax.scatter(wx, wy, facecolors="none", edgecolors="red", s=26, linewidths=0.8, label=f"缺口 {len(weak)}")
        cb = fig.colorbar(sc, ax=ax)
        cb.set_label("可见信标数")
        ax.set_title(f"三点定位覆盖热力图  F{fl}  (信标 {len(base)+len(fill)} · ≥3 覆盖仿真)")
        ax.set_aspect("equal")
        ax.legend(loc="upper right")
        ax.set_xlabel("x (m)"); ax.set_ylabel("y (m)")
        out = ROOT / "result" / f"trilateration_coverage_map_F{fl}.png"
        fig.savefig(out, dpi=130, bbox_inches="tight")
        plt.close(fig)
        print(f"F{fl}: 信标 {len(base)+len(fill)} | 缺口 {len(weak)} | 图 -> {out}")


if __name__ == "__main__":
    main()
