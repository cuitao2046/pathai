# -*- coding: utf-8 -*-
"""T3 + T5: Walkable Polygon → 栅格化中轴 + 悬挂分支剪枝（性能优化版）。

主要加速点：
  1) 栅格化用 matplotlib Path.contains_points，避免逐像素 shapely.covers
  2) 按面积自适应分辨率（大门厅用更粗网格）
  3) 限制最大栅格像素数，防止 870㎡@0.05m 爆炸
  4) 大图优先 skeletonize（比 medial_axis 更快）
"""

from __future__ import annotations

import math
from typing import List, Optional, Sequence, Tuple

import numpy as np
from shapely.geometry import MultiPolygon, Polygon
from shapely.ops import unary_union

try:
    from skimage.morphology import medial_axis, skeletonize
except ImportError as e:  # pragma: no cover
    raise ImportError("需要 scikit-image：pip install scikit-image") from e

try:
    from matplotlib.path import Path as MplPath
    _HAS_MPL = True
except ImportError:
    _HAS_MPL = False


DEFAULT_RESOLUTION = 0.08
MAX_GRID_PIXELS = 400_000
DEFAULT_DANGLE_LEN_M = 0.5
DEFAULT_KEEP_NEAR_M = 0.6


def _poly_bounds(poly) -> Tuple[float, float, float, float]:
    minx, miny, maxx, maxy = poly.bounds
    return minx, miny, maxx, maxy


def adaptive_resolution(poly, base: float = DEFAULT_RESOLUTION) -> float:
    """面积/外接框越大，分辨率越粗。目标像素数 ≤ MAX_GRID_PIXELS。"""
    if poly is None or poly.is_empty:
        return base
    minx, miny, maxx, maxy = poly.bounds
    bw = max(maxx - minx, 1e-3)
    bh = max(maxy - miny, 1e-3)
    est = (bw / base) * (bh / base)
    if est <= MAX_GRID_PIXELS:
        return base
    scale = math.sqrt(est / MAX_GRID_PIXELS)
    return min(base * scale, 0.25)


def _rings_to_fill(poly):
    parts = []
    geoms = list(poly.geoms) if isinstance(poly, MultiPolygon) else [poly]
    for g in geoms:
        if g is None or g.is_empty or g.area < 1e-9:
            continue
        if g.geom_type != "Polygon":
            continue
        parts.append((np.asarray(g.exterior.coords, dtype=np.float64), 1))
        for ring in g.interiors:
            parts.append((np.asarray(ring.coords, dtype=np.float64), -1))
    return parts


def rasterize_polygon(
    poly,
    resolution: float = DEFAULT_RESOLUTION,
    pad: float = 0.3,
) -> Tuple[np.ndarray, float, float, float]:
    """快速栅格化：matplotlib Path.contains_points。"""
    if poly is None or poly.is_empty:
        return np.zeros((1, 1), dtype=bool), 0.0, 0.0, resolution

    if isinstance(poly, MultiPolygon):
        geoms = [g for g in poly.geoms if not g.is_empty and g.area > 1e-6]
        if not geoms:
            return np.zeros((1, 1), dtype=bool), 0.0, 0.0, resolution
        poly = unary_union(geoms)
        if poly.is_empty:
            return np.zeros((1, 1), dtype=bool), 0.0, 0.0, resolution

    resolution = adaptive_resolution(poly, base=resolution)

    minx, miny, maxx, maxy = _poly_bounds(poly)
    minx -= pad
    miny -= pad
    maxx += pad
    maxy += pad

    w = max(2, int(math.ceil((maxx - minx) / resolution)) + 1)
    h = max(2, int(math.ceil((maxy - miny) / resolution)) + 1)

    if w * h > MAX_GRID_PIXELS * 1.5:
        scale = math.sqrt((w * h) / MAX_GRID_PIXELS)
        resolution *= scale
        w = max(2, int(math.ceil((maxx - minx) / resolution)) + 1)
        h = max(2, int(math.ceil((maxy - miny) / resolution)) + 1)

    xs = minx + (np.arange(w) + 0.5) * resolution
    ys = miny + (np.arange(h) + 0.5) * resolution
    xx, yy = np.meshgrid(xs, ys)
    pts = np.column_stack([xx.ravel(), yy.ravel()])
    grid = np.zeros(h * w, dtype=bool)

    rings = _rings_to_fill(poly)
    if not rings:
        return grid.reshape(h, w), minx, miny, resolution

    if _HAS_MPL:
        for coords, sign in rings:
            if len(coords) < 3:
                continue
            path = MplPath(coords, closed=True)
            chunk = 65536
            for i in range(0, pts.shape[0], chunk):
                batch = pts[i : i + chunk]
                inside = path.contains_points(batch, radius=1e-9)
                if sign > 0:
                    grid[i : i + chunk] |= inside
                else:
                    grid[i : i + chunk] &= ~inside
    else:
        from shapely import points as shp_points
        chunk = 32768
        for i in range(0, pts.shape[0], chunk):
            batch = pts[i : i + chunk]
            mask = poly.covers(shp_points(batch))
            grid[i : i + chunk] = np.asarray(mask, dtype=bool)

    return grid.reshape(h, w), minx, miny, resolution


def extract_medial_axis(
    walkable_poly,
    resolution: float = DEFAULT_RESOLUTION,
    method: str = "auto",
) -> dict:
    """T3: Walkable → 中轴。method=auto 时大图用 skeletonize。"""
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

    n_pix = int(grid.sum())
    if method == "auto":
        method = "skeletonize" if n_pix > 30_000 else "medial_axis"

    if method == "skeletonize":
        skel = skeletonize(grid)
    else:
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
    """T5: 悬挂分支剪枝。"""
    skel = skeleton_mask.astype(bool).copy()
    h, w = skel.shape
    keep_px: List[Tuple[int, int]] = []
    r_keep = max(1, int(keep_near_m / max(resolution, 1e-6)))
    if keep_points_world:
        for x, y in keep_points_world:
            r, c = world_to_pixel(x, y, origin_x, origin_y, resolution, h, w)
            keep_px.append((r, c))

    try:
        from scipy import ndimage
        use_scipy = True
    except ImportError:
        use_scipy = False

    def _degree(mask: np.ndarray) -> np.ndarray:
        kernel = np.ones((3, 3), dtype=np.uint8)
        deg = ndimage.convolve(mask.astype(np.uint8), kernel, mode="constant")
        deg = deg.astype(np.int16)
        deg[mask] -= 1
        deg[~mask] = 0
        return deg

    max_iter = 200
    max_px = max(1, int(max_dangle_len_m / max(resolution, 1e-6)))

    for _ in range(max_iter):
        if use_scipy:
            deg = _degree(skel)
            ends_r, ends_c = np.where(skel & (deg == 1))
            ends = list(zip(ends_r.tolist(), ends_c.tolist()))
        else:
            ends = []
            rs, cs = np.where(skel)
            for r, c in zip(rs.tolist(), cs.tolist()):
                d = sum(1 for rr, cc in _neighbors8(r, c, h, w) if skel[rr, cc])
                if d == 1:
                    ends.append((r, c))

        if not ends:
            break

        changed = False
        for r, c in ends:
            near = any(
                abs(kr - r) <= r_keep and abs(kc - c) <= r_keep
                for kr, kc in keep_px
            )
            if near:
                continue

            path = [(r, c)]
            nbrs = [(rr, cc) for rr, cc in _neighbors8(r, c, h, w) if skel[rr, cc]]
            if not nbrs:
                skel[r, c] = False
                changed = True
                continue
            cr, cc_ = nbrs[0]
            visited = {(r, c)}
            length_px = 1
            while length_px <= max_px + 2:
                path.append((cr, cc_))
                visited.add((cr, cc_))
                nexts = [
                    (rr, cc2)
                    for rr, cc2 in _neighbors8(cr, cc_, h, w)
                    if skel[rr, cc2] and (rr, cc2) not in visited
                ]
                n_all = sum(
                    1 for rr, cc2 in _neighbors8(cr, cc_, h, w) if skel[rr, cc2]
                )
                if n_all != 2 or not nexts:
                    break
                length_px += 1
                cr, cc_ = nexts[0]

            if length_px <= max_px:
                if any(
                    abs(pr_ - kr) <= r_keep and abs(pc_ - kc) <= r_keep
                    for pr_, pc_ in path
                    for kr, kc in keep_px
                ):
                    continue
                for pr_, pc_ in path[:-1]:
                    skel[pr_, pc_] = False
                changed = True

        if not changed:
            break

    return skel
