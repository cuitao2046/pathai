#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
从 指纹有效区间.svg 提取红色填充矩形(指纹有效采集空间)，
按楼层(svg_y=929 分界)拆分，转换为米制坐标，计算并集，输出 JSON。
"""
import re
import json
import math
from pathlib import Path

import shapely.geometry as geom
from shapely.ops import unary_union

SRC = Path(r"C:/Users/Administrator/Downloads/指纹有效区间.svg")
OUT = Path(r"E:/code/pathai/result/fingerprint_valid_region.json")

SCALE = 7.0          # 7 SVG 单位 = 1 米 (X/Y 各向同性，由坐标轴刻度确认)
ORIGIN_X_SVG = 636.6
ORIGIN_Y_1F = 265.0     # 1F 原点对应的 svg_y (metric y=0)
ORIGIN_Y_2F = 1194.0    # 2F 原点对应的 svg_y
FLOOR_DIVIDER = 929.0   # svg_y < 929 为 1F，否则为 2F

def mx(svg_x):
    return (svg_x - ORIGIN_X_SVG) / SCALE

def my(svg_y, axis_y):
    return (axis_y - svg_y) / SCALE

# ---- 1. 解析所有红色填充矩形 ----
text = SRC.read_text(encoding="utf-8")
rects = []
pat = re.compile(
    r'<rect fill="#ff0000"[^>]*?\bx="([\d.]+)"\s+y="([\d.]+)"\s+width="([\d.]+)"\s+height="([\d.]+)"[^>]*?/>'
)
for m in pat.finditer(text):
    x, y, w, h = (float(v) for v in m.groups())
    rects.append({"x": x, "y": y, "w": w, "h": h})

print(f"解析到红色矩形: {len(rects)} 个")

# ---- 2. 按楼层拆分 + 米制变换 ----
floors = {"1F": [], "2F": []}
for r in rects:
    axis_y = ORIGIN_Y_1F if r["y"] < FLOOR_DIVIDER else ORIGIN_Y_2F
    floor = "1F" if r["y"] < FLOOR_DIVIDER else "2F"
    minx, maxx = mx(r["x"]), mx(r["x"] + r["w"])
    # svg y 越小越靠上 => metric y 越大
    y_top = my(r["y"], axis_y)          # 较大
    y_bot = my(r["y"] + r["h"], axis_y)  # 较小
    floors[floor].append(
        {"svg": r, "box": (minx, y_bot, maxx, y_top), "floor": floor}
    )

# ---- 3. 每层并集 ----
def union_to_polys(items):
    boxes = [geom.box(*it["box"]) for it in items]
    u = unary_union(boxes)
    if u.geom_type == "Polygon":
        polys = [u]
    elif u.geom_type == "MultiPolygon":
        polys = list(u.geoms)
    else:
        polys = []
    out = []
    for p in polys:
        ext = [[round(c[0], 4), round(c[1], 4)] for c in p.exterior.coords]
        holes = [[[round(c[0], 4), round(c[1], 4)] for c in ring.coords] for ring in p.interiors]
        out.append({"exterior": ext, "holes": holes, "area_m2": round(p.area, 3)})
    return out, round(u.area, 3), round(u.length, 2)

result = {
    "meta": {
        "source": str(SRC),
        "description": "指纹有效采集空间 = SVG 中红色填充矩形(34个)的并集；按楼层拆分后分别求并集",
        "coordinate_system": "米制(原点在楼层左下角, X 向右、Y 向上为正)",
        "scale_svg_per_meter": SCALE,
        "origin_svg": {"x": ORIGIN_X_SVG, "y_1F": ORIGIN_Y_1F, "y_2F": ORIGIN_Y_2F},
        "floor_divider_svg_y": FLOOR_DIVIDER,
        "transform": "metric_x=(svg_x-636.6)/7; metric_y=(265-svg_y)/7 [1F]; metric_y=(1194-svg_y)/7 [2F]",
        "red_rect_count": len(rects),
    },
    "floors": {},
}

for fl in ("1F", "2F"):
    items = floors[fl]
    polys, area, perim = union_to_polys(items)
    result["floors"][fl] = {
        "red_rect_count": len(items),
        "union_area_m2": area,
        "union_perimeter_m": perim,
        "polygon_count": len(polys),
        "polygons": polys,
        "raw_rectangles_svg": [
            {"x": it["svg"]["x"], "y": it["svg"]["y"], "w": it["svg"]["w"], "h": it["svg"]["h"]}
            for it in items
        ],
    }
    print(f"{fl}: {len(items)} 矩形 -> {len(polys)} 个并集多边形, 面积 {area} m²")

OUT.parent.mkdir(parents=True, exist_ok=True)
OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
print(f"\n已写出: {OUT}")
