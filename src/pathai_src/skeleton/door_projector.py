# -*- coding: utf-8 -*-
"""T7: 门中心正交投影到骨架段（STRtree 加速）。"""

from __future__ import annotations

import math
from typing import Dict, List, Optional, Sequence, Tuple

from shapely.geometry import LineString, Point
from shapely.strtree import STRtree


def project_points_to_skeleton(
    points: Sequence[Tuple[float, float]],
    skeleton_lines: Sequence[LineString],
    max_dist_m: float = 8.0,
) -> List[dict]:
    """
    将一组点投影到最近骨架段。

    Returns
    -------
    list of {
      "src": (x,y),
      "projected": (x,y) | None,
      "distance": float,
      "segment_index": int | None,
      "t": float,  # 沿段参数 0..1
    }
    """
    if not skeleton_lines:
        return [
            {"src": p, "projected": None, "distance": float("inf"),
             "segment_index": None, "t": None}
            for p in points
        ]

    tree = STRtree(list(skeleton_lines))
    # shapely 2: tree.query returns indices
    results = []
    for p in points:
        pt = Point(p[0], p[1])
        # 候选：缓冲查询
        cand_idx = tree.query(pt.buffer(max_dist_m))
        best = None
        best_d = float("inf")
        best_proj = None
        best_t = None
        best_i = None
        for i in cand_idx:
            seg = skeleton_lines[int(i)]
            # 最近点
            proj = seg.interpolate(seg.project(pt))
            d = pt.distance(proj)
            if d < best_d:
                best_d = d
                best_proj = (proj.x, proj.y)
                best_i = int(i)
                L = seg.length
                best_t = (seg.project(pt) / L) if L > 1e-9 else 0.0
        if best_proj is not None and best_d <= max_dist_m:
            results.append({
                "src": p,
                "projected": best_proj,
                "distance": best_d,
                "segment_index": best_i,
                "t": best_t,
            })
        else:
            # 全局最近兜底
            for i, seg in enumerate(skeleton_lines):
                proj = seg.interpolate(seg.project(pt))
                d = pt.distance(proj)
                if d < best_d:
                    best_d = d
                    best_proj = (proj.x, proj.y)
                    best_i = i
                    L = seg.length
                    best_t = (seg.project(pt) / L) if L > 1e-9 else 0.0
            results.append({
                "src": p,
                "projected": best_proj,
                "distance": best_d,
                "segment_index": best_i,
                "t": best_t,
            })
    return results


def project_doors_to_skeleton(
    door_centers: Sequence[Tuple[float, float]],
    skeleton_lines: Sequence[LineString],
    max_dist_m: float = 8.0,
) -> List[dict]:
    """
    T7: 门中心 → 骨架正交投影 → TD 节点候选。

    每个门返回:
      door_index, projected (x,y), distance, segment_index
    """
    projs = project_points_to_skeleton(door_centers, skeleton_lines, max_dist_m)
    out = []
    for i, pr in enumerate(projs):
        out.append({
            "door_index": i,
            "src": pr["src"],
            "projected": pr["projected"],
            "distance": pr["distance"],
            "segment_index": pr["segment_index"],
            "t": pr["t"],
        })
    return out


def split_segment_at_point(seg: LineString, point: Tuple[float, float],
                           tol: float = 0.05) -> List[LineString]:
    """在投影点处分割线段（若点靠近端点则不切）。"""
    pt = Point(point)
    d0 = pt.distance(Point(seg.coords[0]))
    d1 = pt.distance(Point(seg.coords[-1]))
    if d0 <= tol or d1 <= tol:
        return [seg]
    from shapely.ops import split as shp_split
    # 用极短切线切开
    cutter = pt.buffer(1e-6).boundary
    try:
        parts = list(shp_split(seg, cutter).geoms)
        lines = [p for p in parts if isinstance(p, LineString) and p.length > 1e-6]
        return lines if lines else [seg]
    except Exception:
        return [seg]
