#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""导出「骨架标注模板」SVG 底图（不含骨架层）。

用户拿到该 SVG 后，用矢量编辑器（Inkscape / Figma 等）或任意编辑器，
在走道区域用 **红色描边线（stroke:#e74c3c; stroke-width:4; fill:none）**
画出导航骨架线，保存为 debug/skeleton_manual_f{floor}.svg，
再由 import_manual_skeleton.py 解析成骨架代码。

模板包含：
  - 走道（walkable 区域）半透明绿色，方便识别哪里该画线
  - 封闭房间浅灰色
  - 门点（黄色圆点）、楼梯（蓝）、电梯（紫）
  - 坐标可逆：米制 ↔ SVG 的变换写死为
      sx = MARGIN_X + (cx - MINX) * SCALE
      sy = MARGIN_Y + (MAXY - cy) * SCALE
    导入时按同一公式反算回米制。

用法:
  python src/export_skeleton_template.py
  输出: result/skeleton_template_f1.svg, result/skeleton_template_f2.svg
"""
from __future__ import annotations

import json
import math
from pathlib import Path

from shapely.geometry import shape

BASE_DIR = Path(__file__).resolve().parent.parent
GEO_IN = str(BASE_DIR / "result" / "school_building_01_map_v9.geojson")
OUT_F1 = str(BASE_DIR / "result" / "skeleton_template_f1.svg")
OUT_F2 = str(BASE_DIR / "result" / "skeleton_template_f2.svg")

SCALE = 8.0        # 1m = 8px（比渲染页稍大，便于画线）
MARGIN_X = 60
MARGIN_Y = 60

OPEN_TYPES = {
    "corridor", "lobby", "activity", "atrium", "elevator_lobby", "stair_lobby",
    "entrance", "accessible_entrance",
}
CLOSED_TYPES = {
    "classroom", "lab", "office", "meeting", "toilet", "storage",
    "equipment", "library", "medical", "shaft", "staircase",
    "elevator_hall", "room", "reception", "counseling",
}


def poly_path(coords):
    """GeoJSON 多边形坐标 → SVG path d（含洞）。"""
    parts = []
    for ring in coords:
        d = "M " + " L ".join(f"{x:.2f},{y:.2f}" for x, y in ring) + " Z"
        parts.append(d)
    return " ".join(parts)


def build(fl, floor_no, out_path):
    geo_rooms = fl["geometry"]["rooms"]
    walk = fl.get("walkable_regions") or {}
    walk_feats = walk.get("features") if isinstance(walk, dict) else []
    doors = fl["geometry"]["doors"]
    stairs = fl["geometry"]["stairs"]
    elevators = fl["geometry"]["elevators"]

    # 米制范围（用房间 + walkable 的并集）
    minx = miny = 1e9
    maxx = maxy = -1e9
    for r in geo_rooms:
        b = shape(r["geometry"]).bounds
        minx = min(minx, b[0]); miny = min(miny, b[1])
        maxx = max(maxx, b[2]); maxy = max(maxy, b[3])
    for f in walk_feats:
        b = shape(f["geometry"]).bounds
        minx = min(minx, b[0]); miny = min(miny, b[1])
        maxx = max(maxx, b[2]); maxy = max(maxy, b[3])
    W = (maxx - minx) * SCALE + MARGIN_X * 2
    H = (maxy - miny) * SCALE + MARGIN_Y * 2

    def sx(cx): return MARGIN_X + (cx - minx) * SCALE
    def sy(cy): return MARGIN_Y + (maxy - cy) * SCALE

    svg = []
    svg.append(f'<svg xmlns="http://www.w3.org/2000/svg" width="{W:.0f}" height="{H:.0f}" '
               f'viewBox="0 0 {W:.0f} {H:.0f}">')
    svg.append('<rect width="100%" height="100%" fill="#ffffff"/>')
    svg.append(f'<text x="{MARGIN_X}" y="{MARGIN_Y - 18:.0f}" font-size="20" fill="#333">'
               f'F{floor_no} 骨架标注模板 — 请在走道区域用红色描边线画骨架（stroke:#e74c3c;stroke-width:4;fill:none）</text>')
    svg.append(f'<text x="{MARGIN_X}" y="{MARGIN_Y - 2:.0f}" font-size="14" fill="#888">'
               f'米制坐标范围 x:[{minx:.1f},{maxx:.1f}] y:[{miny:.1f},{maxy:.1f}]  |  '
               f'1m = {SCALE}px；导入时按 sx=MARGIN_X+(cx-MINX)*SCALE 反算</text>')

    # 走道（walkable）半透明绿
    for f in walk_feats:
        g = f.get("geometry") or {}
        if g.get("type") != "Polygon":
            continue
        coords = g["coordinates"]
        d = poly_path(coords)
        svg.append(f'<path d="{d}" fill="#a8e6cf" fill-opacity="0.45" stroke="none"/>')
    # 房间
    for r in geo_rooms:
        rt = (r.get("properties") or {}).get("roomType") or ""
        coords = r.get("geometry", {}).get("coordinates")
        if not coords:
            continue
        d = poly_path(coords)
        fill = "#ffffff"
        if rt in OPEN_TYPES:
            fill = "#dff0d8"
        elif rt in CLOSED_TYPES:
            fill = "#f0f0f0"
        label = (r.get("properties") or {}).get("label") or ""
        svg.append(f'<path d="{d}" fill="{fill}" stroke="#999" stroke-width="1.2"/>')
        if label:
            c = shape(r["geometry"]).centroid
            svg.append(f'<text x="{sx(c.x):.1f}" y="{sy(c.y):.1f}" font-size="10" '
                       f'fill="#666" text-anchor="middle">{label}</text>')
    # 门点
    for dr in doors:
        pr = dr.get("properties") or {}
        c = pr.get("center_m") or pr.get("centroid")
        if c is None:
            c = shape(dr["geometry"]).centroid
        try:
            cx_, cy_ = float(c[0]), float(c[1])
        except (TypeError, IndexError):
            c = shape(dr["geometry"]).centroid
            cx_, cy_ = c.x, c.y
        svg.append(f'<circle cx="{sx(cx_):.1f}" cy="{sy(cy_):.1f}" r="2.5" fill="#f1c40f"/>')
    # 楼梯
    for st in stairs:
        c = shape(st["geometry"]).centroid
        svg.append(f'<rect x="{sx(c.x)-6:.1f}" y="{sy(c.y)-6:.1f}" width="12" height="12" '
                   f'fill="#2980b9" opacity="0.7" rx="2"/>')
    # 电梯
    for ev in elevators:
        c = shape(ev["geometry"]).centroid
        svg.append(f'<rect x="{sx(c.x)-6:.1f}" y="{sy(c.y)-6:.1f}" width="12" height="12" '
                   f'fill="#8e44ad" opacity="0.7" rx="2"/>')

    svg.append('</svg>')
    Path(out_path).write_text("\n".join(svg), encoding="utf-8")
    print(f"已导出: {out_path}  ({W:.0f}×{H:.0f}px, 米制范围 x:[{minx:.1f},{maxx:.1f}] y:[{miny:.1f},{maxy:.1f}])")


def main():
    geo = json.loads(Path(GEO_IN).read_text(encoding="utf-8"))
    for fk in ("1", "2"):
        fl = geo["floors"][fk]
        build(fl, int(fk), OUT_F1 if fk == "1" else OUT_F2)


if __name__ == "__main__":
    main()
