# -*- coding: utf-8 -*-
"""T3 + T5: Walkable Polygon → 栅格化中轴 + 悬挂分支剪枝。"""

from __future__ import annotations

import math
from typing import Iterable, List, Optional, Sequence, Tuple

import numpy as np
from shapely.geometry import LineString, MultiPolygon, Point, Polygon
from shapely.ops import unary_union

try:
    from skimage.morphology import medial_axis, skeletonize
except ImportError as e:  # pragma: no cover
    raise ImportError(
        "需要 scikit-image：pip install scikit-image"
    ) from e


# 默认分辨率 0.05 m/px（任务书 SKELETON_RESOLUTION）
DEFAULT_RESOLUTION = 0.05
# 悬挂剪枝：短毛刺长度阈值（米）
DEFAULT_DANGLE_LEN_M = 0.5
# 端点若距门/设施 ≤ 此值则保留分支
DEFAULT_KEEP_NEAR_M = 0.6


def _poly_bounds(poly) -> Tuple[float, float, float, float]:
    minx, miny, maxx, maxy = poly.bounds
    return minx, miny, maxx, maxy


def rasterize_polygon(
    poly,
    resolution: float = DEFAULT_RESOLUTION,
    pad: float = 0.5,
) -> Tuple[np.ndarray, float, float, float]:
    """
    将 Polygon/MultiPolygon 栅格化为二值图（True=内部）。

    Returns
    -------
    grid : (H, W) bool
    origin_x, origin_y : 网格左下角对应的世界坐标（米）
    resolution : 米/像素
    """
    if poly is None or poly.is_empty:
        return np.zeros((1, 1), dtype=bool), 0.0, 0.0, resolution

    if isinstance(poly, MultiPolygon):
        geoms = [g for g in poly.geoms if not g.is_empty and g.area > 1e-6]
        if not geoms:
            return np.zeros((1, 1), dtype=bool), 0.0, 0.0, resolution
        poly = unary_union(geoms)

    minx, miny, maxx, maxy = _poly_bounds(poly)
    minx -= pad
    miny -= pad
    maxx += pad
    maxy += pad

    w = max(2, int(math.ceil((maxx - minx) / resolution)) + 1)
    h = max(2, int(math.ceil((maxy - miny) / resolution)) + 1)

    # 采样网格中心点
    xs = minx + (np.arange(w) + 0.5) * resolution
    ys = miny + (np.arange(h) + 0.5) * resolution
    xx, yy = np.meshgrid(xs, ys)
    # shapely 2 prepared + vectorized covers 较慢于逐点；对走廊尺度用
    # contains 批量：构造 MultiPoint 可能内存大，分块处理
    grid = np.zeros((h, w), dtype=bool)
    chunk = 4096
    coords = np.column_stack([xx.ravel(), yy.ravel()])
    for i in range(0, coords.shape[0], chunk):
        batch = coords[i : i + chunk]
        from shapely import points as shp_points
        pts = shp_points(batch)
        # covers 含边界
        mask = poly.covers(pts)
        if hasattr(mask, "__len__"):
            grid.ravel()[i : i + chunk] = np.asarray(mask, dtype=bool)
        else:
            grid.ravel()[i : i + chunk] = bool(mask)

    return grid, minx, miny, resolution


def extract_medial_axis(
    walkable_poly,
    resolution: float = DEFAULT_RESOLUTION,
    method: str = "medial_axis",
) -> dict:
    """
    T3: 对单个 Walkable Polygon 提取中轴骨架。

    Parameters
    ----------
    walkable_poly : Shapely Polygon/MultiPolygon（米制）
    resolution : 栅格分辨率（米/像素）
    method : 'medial_axis' | 'skeletonize'

    Returns
    -------
    dict with keys:
      skeleton_mask : (H,W) bool
      origin_x, origin_y, resolution
      pixel_coords : list[(row, col)] 骨架像素
      empty : bool
    """
    grid, ox, oy, res = rasterize_polygon(walkable_poly, resolution=resolution)
    if not grid.any():
        return {
            "skeleton_mask": grid,
            "origin_x": ox,
            "origin_y": oy,
            "resolution": res,
            "pixel_coords": [],
            "empty": True,
        }

    if method == "skeletonize":
        skel = skeletonize(grid)
    else:
        # medial_axis 返回距离变换骨架，居中性更好
        skel = medial_axis(grid)

    rows, cols = np.where(skel)
    coords = list(zip(rows.tolist(), cols.tolist()))
    return {
        "skeleton_mask": skel.astype(bool),
        "origin_x": ox,
        "origin_y": oy,
        "resolution": res,
        "pixel_coords": coords,
        "empty": len(coords) == 0,
    }


def pixel_to_world(row: int, col: int, origin_x: float, origin_y: float,
                   resolution: float) -> Tuple[float, float]:
    """像素中心 → 世界坐标（米）。"""
    return (
        origin_x + (col + 0.5) * resolution,
        origin_y + (row + 0.5) * resolution,
    )


def world_to_pixel(x: float, y: float, origin_x: float, origin_y: float,
                   resolution: float, h: int, w: int) -> Tuple[int, int]:
    col = int((x - origin_x) / resolution)
    row = int((y - origin_y) / resolution)
    col = max(0, min(w - 1, col))
    row = max(0, min(h - 1, row))
    return row, col


def _neighbors8(r: int, c: int, h: int, w: int):
    for dr in (-1, 0, 1):
        for dc in (-1, 0, 1):
            if dr == 0 and dc == 0:
                continue
            rr, cc = r + dr, c + dc
            if 0 <= rr < h and 0 <= cc < w:
                yield rr, cc


def prune_dangling_branches(
    skeleton_mask: np.ndarray,
    keep_points_world: Optional[Sequence[Tuple[float, float]]] = None,
    origin_x: float = 0.0,
    origin_y: float = 0.0,
    resolution: float = DEFAULT_RESOLUTION,
    max_dangle_len_m: float = DEFAULT_DANGLE_LEN_M,
    keep_near_m: float = DEFAULT_KEEP_NEAR_M,
) -> np.ndarray:
    """
    T5: 悬挂分支剪枝。

    迭代移除：
      - degree=1 的端点，且
      - 该端点不靠近任何 keep_points（门/出入口/设施），或
      - 从端点沿分支长度 < max_dangle_len_m

    通往门口的分支即使较短也保留。
    """
    skel = skeleton_mask.astype(bool).copy()
    h, w = skel.shape
    keep_px: List[Tuple[int, int]] = []
    if keep_points_world:
        for x, y in keep_points_world:
            r, c = world_to_pixel(x, y, origin_x, origin_y, resolution, h, w)
            keep_px.append((r, c))

    def degree_map(mask):
        deg = np.zeros(mask.shape, dtype=np.int8)
        rs, cs = np.where(mask)
        for r, c in zip(rs, cs):
            d = 0
            for rr, cc in _neighbors8(r, c, h, w):
                if mask[rr, cc]:
                    d += 1
            deg[r, c] = d
        return deg

    def near_keep(r, c) -> bool:
        for kr, kc in keep_px:
            if abs(kr - r) + abs(kc - c) <= max(1, int(keep_near_m / resolution)):
                return True
            # 欧氏
            if math.hypot(kr - r, kc - c) * resolution <= keep_near_m:
                return True
        return False

    changed = True
    max_iter = 500
    it = 0
    while changed and it < max_iter:
        it += 1
        changed = False
        deg = degree_map(skel)
        ends = list(zip(*np.where((skel) & (deg == 1))))
        for r, c in ends:
            if near_keep(r, c):
                continue
            # 沿唯一邻居走，量长度
            path = [(r, c)]
            pr, pc = r, c
            # 找唯一邻居
            nbrs = [(rr, cc) for rr, cc in _neighbors8(r, c, h, w) if skel[rr, cc]]
            if not nbrs:
                skel[r, c] = False
                changed = True
                continue
            cr, cc_ = nbrs[0]
            length_m = resolution
            visited = {(r, c)}
            while True:
                path.append((cr, cc_))
                visited.add((cr, cc_))
                deg_c = 0
                nexts = []
                for rr, cc2 in _neighbors8(cr, cc_, h, w):
                    if skel[rr, cc2] and (rr, cc2) not in visited:
                        nexts.append((rr, cc2))
                        deg_c += 1
                # 到达交叉口或另一端点则停
                n_all = sum(
                    1 for rr, cc2 in _neighbors8(cr, cc_, h, w) if skel[rr, cc2]
                )
                if n_all != 2 or not nexts:
                    break
                length_m += resolution
                cr, cc_ = nexts[0]
                if length_m > max_dangle_len_m * 2:
                    break
            if length_m <= max_dangle_len_m and not any(
                near_keep(pr_, pc_) for pr_, pc_ in path
            ):
                for pr_, pc_ in path[:-1]:  # 保留连接点
                    skel[pr_, pc_] = False
                changed = True

    return skel
