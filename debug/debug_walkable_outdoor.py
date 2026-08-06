# -*- coding: utf-8 -*-
"""诊断：Walkable Polygon 是否包含户外(越出建筑外轮廓)区域。

用与渲染一致的 building_outline()（墙线栅格化+膨胀弥合门洞+外部泛洪）
提取建筑外轮廓，对每个公共空间 walkable 计算 越界面积 = walkable − outline。
"""
import importlib.util
import json
import sys
from pathlib import Path

from shapely.geometry import shape, Polygon
from shapely.ops import unary_union

ROOT = Path(__file__).resolve().parent.parent
GEOJSON = ROOT / "result" / "school_building_01_map_v9.geojson"

# 从 render_interactive.py 加载 building_outline（不执行 main）
spec = importlib.util.spec_from_file_location(
    "ri", ROOT / "src" / "render_interactive.py")
ri = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ri)

OPEN_TYPES = {"corridor", "lobby", "activity", "atrium",
              "elevator_lobby", "stair_lobby"}
MIN_OVERLAP_M2 = 0.5  # 越界面积阈值


def main():
    d = json.load(open(GEOJSON, encoding="utf-8"))
    total_bad = 0
    for fn, fl in d["floors"].items():
        geom = fl["geometry"]
        outlines = ri.building_outline(geom)
        if not outlines:
            print(f"[F{fn}] 无建筑外轮廓, 跳过")
            continue
        # 与渲染一致：仅保留最大块 ≥5%(或 ≥150m²)
        areas = [_area(p) for p in outlines]
        mx = max(areas)
        thr = max(150.0, 0.05 * mx)
        outlines = [p for p, a in zip(outlines, areas) if a >= thr]
        outline_union = unary_union([Polygon(p) for p in outlines])
        print(f"[F{fn}] 建筑外轮廓 {len(outlines)} 块 "
              f"(总面积 {outline_union.area:.0f}m², 最大块 {mx:.0f}m²)")

        bad = []
        for r in geom["rooms"]:
            p = r["properties"]
            if p.get("roomType") not in OPEN_TYPES:
                continue
            wp = p.get("walkablePolygon")
            if not wp:
                continue
            wpg = shape(wp)
            outside = wpg.difference(outline_union)
            oa = outside.area
            if oa > MIN_OVERLAP_M2:
                bad.append((r["id"], p["label"], p["roomType"],
                            round(wpg.area, 1), round(oa, 2)))
        if bad:
            total_bad += len(bad)
            print(f"  ⚠ 越界 walkable {len(bad)} 个:")
            for rid, lbl, rt, wa, oa in bad:
                print(f"    {rid} [{rt}] {lbl}: walkable {wa}m², "
                      f"越出建筑外轮廓 {oa}m²")
        else:
            print(f"  全部 walkable 在建筑外轮廓内 ✓")
    print(f"\n=== 越界总数: {total_bad} ===")


def _area(ring):
    """多边形外环面积（m²），ring 含闭合末点。"""
    xs = [p[0] for p in ring]
    ys = [p[1] for p in ring]
    return 0.5 * abs(sum(xs[i] * ys[i + 1] - xs[i + 1] * ys[i]
                         for i in range(len(ring) - 1)))


if __name__ == "__main__":
    main()
