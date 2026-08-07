# -*- coding: utf-8 -*-
"""
空间分解：基于中轴骨架将可通行区域切割为互不重叠、尽量贴边的近似矩形/梯形块。

设计目标（相对 v1 的改进）：
  1. **无重叠**：用交叉口/端点处的垂直截线对 walkable 做真剖分（partition），
     而非为每条骨架段独立生成可能互相覆盖的四边形。
  2. **尽量覆盖完整**：剖分后剩余碎片并入最近主块，或保留为小块（面积阈值以上）。
  3. **近似矩形/梯形**：每个块由「骨架段 + 两端截线」围成，锯齿墙用 buffer(0)
     与简化处理；形状用最小旋转矩形判定 rect / trapezoid / triangle。

输入：skeleton lines + walkable polygon（米制）
输出：空间块列表 [{geometry, approx_shape, area_m2, ...}]
"""

from __future__ import annotations

import math
from typing import Dict, List, Optional, Sequence, Tuple

from shapely.geometry import (
    GeometryCollection, LineString, MultiLineString, MultiPolygon, Point, Polygon,
)
from shapely.ops import linemerge, polygonize, split, unary_union


# ── geometry helpers ────────────────────────────────────────────────

def _as_polygons(geom) -> List[Polygon]:
    if geom is None or geom.is_empty:
        return []
    if isinstance(geom, Polygon):
        return [geom] if not geom.is_empty else []
    if isinstance(geom, MultiPolygon):
        return [g for g in geom.geoms if isinstance(g, Polygon) and not g.is_empty]
    if isinstance(geom, GeometryCollection):
        out = []
        for g in geom.geoms:
            out.extend(_as_polygons(g))
        return out
    return []


def _tangent_at_endpoint(
    line: LineString, at_start: bool
) -> Tuple[float, float]:
    coords = list(line.coords)
    if len(coords) < 2:
        return (1.0, 0.0)
    if at_start:
        a, b = coords[0], coords[1]
    else:
        a, b = coords[-2], coords[-1]
    dx, dy = b[0] - a[0], b[1] - a[1]
    if math.hypot(dx, dy) < 1e-9:
        dx, dy = coords[-1][0] - coords[0][0], coords[-1][1] - coords[0][1]
    return (dx, dy)


def _perpendicular_cut_line(
    point: Tuple[float, float],
    tangent: Tuple[float, float],
    walkable_poly: Polygon,
    extend_m: float = 30.0,
) -> Optional[LineString]:
    """在 point 处沿 tangent 垂向画一条穿过 walkable 的截线。"""
    dx, dy = tangent
    mag = math.hypot(dx, dy)
    if mag < 1e-9:
        return None
    px, py = -dy / mag, dx / mag
    p1 = (point[0] + px * extend_m, point[1] + py * extend_m)
    p2 = (point[0] - px * extend_m, point[1] - py * extend_m)
    cutter = LineString([p1, p2])
    inter = cutter.intersection(walkable_poly)
    if inter.is_empty:
        return None
    if inter.geom_type == "LineString":
        return inter if inter.length > 1e-4 else None
    if inter.geom_type == "MultiLineString":
        best = max((g for g in inter.geoms if g.length > 1e-4),
                   key=lambda g: g.length, default=None)
        return best
    if hasattr(inter, "geoms"):
        lines = [g for g in inter.geoms
                 if g.geom_type == "LineString" and g.length > 1e-4]
        if lines:
            return max(lines, key=lambda g: g.length)
    return None


def _shape_stats(poly: Polygon) -> dict:
    """最小旋转矩形 → 长宽比 / 近似形状。"""
    try:
        mbr = poly.minimum_rotated_rectangle
    except Exception:
        mbr = poly.envelope
    if mbr is None or mbr.is_empty:
        return {
            "approx_shape": "rect",
            "aspect_ratio": 1.0,
            "width_m": math.sqrt(max(poly.area, 0)),
            "length_m": math.sqrt(max(poly.area, 0)),
        }
    coords = list(mbr.exterior.coords)
    if len(coords) < 3:
        a = math.sqrt(max(poly.area, 0))
        return {"approx_shape": "rect", "aspect_ratio": 1.0,
                "width_m": a, "length_m": a}
    edges = []
    for i in range(len(coords) - 1):
        edges.append(math.hypot(coords[i + 1][0] - coords[i][0],
                                coords[i + 1][1] - coords[i][1]))
    # 矩形四边：取两个不同长度
    edges = sorted(set(round(e, 4) for e in edges if e > 1e-6))
    if not edges:
        a = math.sqrt(max(poly.area, 0))
        return {"approx_shape": "rect", "aspect_ratio": 1.0,
                "width_m": a, "length_m": a}
    # 用 exterior 连续两边
    coords = list(mbr.exterior.coords)
    w1 = math.hypot(coords[1][0] - coords[0][0], coords[1][1] - coords[0][1])
    w2 = math.hypot(coords[2][0] - coords[1][0], coords[2][1] - coords[1][1])
    w_short, w_long = min(w1, w2), max(w1, w2)
    aspect = w_long / w_short if w_short > 1e-9 else 999.0

    # 顶点数启发：简化后 ≈3 → triangle；否则按长宽比
    try:
        simplified = poly.simplify(0.25, preserve_topology=True)
        n_vert = len(list(simplified.exterior.coords)) - 1
    except Exception:
        n_vert = 4
    if n_vert <= 3:
        shape = "triangle"
    elif aspect < 2.8:
        shape = "rect"
    else:
        shape = "trapezoid"
    return {
        "approx_shape": shape,
        "aspect_ratio": aspect,
        "width_m": w_short,
        "length_m": w_long,
    }


def _endpoint_type(
    pt: Tuple[float, float],
    junctions: Sequence[Tuple[float, float]],
    terminals: Sequence[Tuple[float, float]],
    tol_m: float = 1.5,
) -> str:
    px, py = pt
    for jx, jy in junctions:
        if math.hypot(px - jx, py - jy) < tol_m:
            return "junction"
    for tx, ty in terminals:
        if math.hypot(px - tx, py - ty) < tol_m:
            return "terminal"
    return "mid"


def _collect_cut_points(
    skeleton_lines: Sequence[LineString],
    junctions: Sequence[Tuple[float, float]],
    terminals: Sequence[Tuple[float, float]],
    merge_tol_m: float = 0.6,
) -> List[Tuple[Tuple[float, float], Tuple[float, float]]]:
    """收集截点：(point, tangent)。同一位置的多条线方向合并为平均切向。"""
    raw: List[Tuple[Tuple[float, float], Tuple[float, float]]] = []
    for line in skeleton_lines:
        if not isinstance(line, LineString) or line.is_empty or line.length < 0.2:
            continue
        coords = list(line.coords)
        if len(coords) < 2:
            continue
        raw.append((coords[0], _tangent_at_endpoint(line, True)))
        raw.append((coords[-1], _tangent_at_endpoint(line, False)))

    # 也纳入显式 junctions/terminals（若骨架端点有遗漏）
    for jx, jy in junctions:
        raw.append(((jx, jy), (1.0, 0.0)))
    for tx, ty in terminals:
        raw.append(((tx, ty), (1.0, 0.0)))

    # 空间合并近点，累加切向
    clusters: List[dict] = []
    for pt, tang in raw:
        placed = False
        for c in clusters:
            if math.hypot(pt[0] - c["x"], pt[1] - c["y"]) <= merge_tol_m:
                n = c["n"]
                c["x"] = (c["x"] * n + pt[0]) / (n + 1)
                c["y"] = (c["y"] * n + pt[1]) / (n + 1)
                c["tx"] += tang[0]
                c["ty"] += tang[1]
                c["n"] = n + 1
                placed = True
                break
        if not placed:
            clusters.append({
                "x": pt[0], "y": pt[1],
                "tx": tang[0], "ty": tang[1], "n": 1,
            })

    out = []
    for c in clusters:
        tmag = math.hypot(c["tx"], c["ty"])
        if tmag < 1e-9:
            # 无可靠切向：跳过（避免乱切）；真要切可退化为轴对齐
            continue
        out.append(((c["x"], c["y"]), (c["tx"] / tmag, c["ty"] / tmag)))
    return out


def _partition_by_cuts(
    walkable: Polygon,
    cut_lines: Sequence[LineString],
) -> List[Polygon]:
    """用截线集合剖分 walkable，返回互不重叠的多边形列表。"""
    if walkable is None or walkable.is_empty:
        return []
    # 合并截线
    valid = [c for c in cut_lines if c is not None and not c.is_empty and c.length > 1e-4]
    if not valid:
        return _as_polygons(walkable)

    blades = unary_union(valid)
    # split 可能因精度失败 → 退化为 polygonize
    try:
        pieces = split(walkable, blades)
        polys = _as_polygons(pieces)
        if polys:
            return polys
    except Exception:
        pass

    try:
        # polygonize：walkable 边界 + 截线
        boundary = walkable.boundary
        if boundary is None or boundary.is_empty:
            return _as_polygons(walkable)
        segs = []
        if boundary.geom_type == "LineString":
            segs.append(boundary)
        elif boundary.geom_type == "MultiLineString":
            segs.extend(list(boundary.geoms))
        segs.extend(valid)
        merged = unary_union(segs)
        faces = list(polygonize(merged))
        # 只保留落在 walkable 内的面
        polys = []
        for f in faces:
            if not isinstance(f, Polygon) or f.is_empty:
                continue
            inter = f.intersection(walkable)
            polys.extend(_as_polygons(inter))
        if polys:
            return polys
    except Exception:
        pass

    return _as_polygons(walkable)


def _nearest_line(
    poly: Polygon, lines: Sequence[LineString]
) -> Optional[LineString]:
    if not lines:
        return None
    c = poly.centroid
    best, best_d = None, float("inf")
    for ln in lines:
        if ln is None or ln.is_empty:
            continue
        d = ln.distance(c)
        if d < best_d:
            best_d, best = d, ln
    return best


def _coverage_repair(
    walkable: Polygon,
    blocks: List[Polygon],
    min_area_m2: float,
) -> List[Polygon]:
    """把 walkable 中未被覆盖的部分并入最近块，或作为新块。"""
    if not blocks:
        return _as_polygons(walkable)

    covered = unary_union(blocks)
    residual = walkable.difference(covered)
    scraps = _as_polygons(residual)
    if not scraps:
        return blocks

    result = [b.buffer(0) for b in blocks]
    for scrap in scraps:
        if scrap.area < min_area_m2 * 0.3:
            # 极小碎片：并入最近块
            c = scrap.centroid
            idx = min(range(len(result)),
                      key=lambda i: result[i].distance(c))
            try:
                merged = result[idx].union(scrap)
                parts = _as_polygons(merged)
                if parts:
                    result[idx] = max(parts, key=lambda p: p.area)
            except Exception:
                pass
            continue
        if scrap.area < min_area_m2:
            # 小碎片仍尝试合并
            c = scrap.centroid
            idx = min(range(len(result)),
                      key=lambda i: result[i].distance(c))
            if result[idx].distance(scrap) < 1.5:
                try:
                    merged = result[idx].union(scrap)
                    parts = _as_polygons(merged)
                    if parts:
                        result[idx] = max(parts, key=lambda p: p.area)
                        continue
                except Exception:
                    pass
        result.append(scrap)
    return [b for b in result if b is not None and not b.is_empty and b.area >= min_area_m2 * 0.5]


# ── public API ───────────────────────────────────────────────────────

def decompose_walkable_to_blocks(
    skeleton_lines: Sequence[LineString],
    walkable_polygon_m: Polygon,
    min_area_m2: float = 1.0,
    junctions: Sequence[Tuple[float, float]] = (),
    terminals: Sequence[Tuple[float, float]] = (),
    connectivity_tol_m: float = 1.5,
) -> List[dict]:
    """主入口：将可通行区域按中轴截线剖分为互不重叠的空间块。

    Parameters
    ----------
    skeleton_lines : list of LineString
        中轴线段（米）。
    walkable_polygon_m : Polygon | MultiPolygon
        可通行区域。
    min_area_m2 : float
        丢弃过小碎块。
    junctions / terminals :
        交叉口与端点，用于端点类型标注。

    Returns
    -------
    list of block dicts with geometry / approx_shape / area / endpoint_types
    """
    raw = walkable_polygon_m
    if raw is None or raw.is_empty:
        return []
    polys = _as_polygons(raw)
    if not polys:
        return []
    # 统一为一个几何（可能 MultiPolygon）
    walkable_u = unary_union([p.buffer(0) for p in polys])
    walkable_parts = _as_polygons(walkable_u)
    if not walkable_parts:
        return []

    lines = [ln for ln in skeleton_lines
             if isinstance(ln, LineString) and not ln.is_empty and ln.length >= 0.2]
    if not lines:
        # 无骨架：整块走道作为一个块
        out = []
        for p in walkable_parts:
            if p.area < min_area_m2:
                continue
            st = _shape_stats(p)
            out.append({
                "geometry": p,
                "approx_shape": st["approx_shape"],
                "area_m2": p.area,
                "aspect_ratio": st["aspect_ratio"],
                "width_m": st["width_m"],
                "length_m": st["length_m"],
                "skeleton_line": None,
                "endpoint_types": ("mid", "mid"),
            })
        return out

    cut_pts = _collect_cut_points(lines, junctions, terminals, merge_tol_m=0.6)

    all_blocks: List[Polygon] = []
    for part in walkable_parts:
        cuts = []
        for pt, tang in cut_pts:
            # 只在本组件附近做截
            if part.distance(Point(pt)) > 2.0 and not part.buffer(0.5).contains(Point(pt)):
                continue
            cl = _perpendicular_cut_line(pt, tang, part, extend_m=30.0)
            if cl is not None:
                cuts.append(cl)
        pieces = _partition_by_cuts(part, cuts)
        if not pieces:
            pieces = [part]
        # 覆盖修补
        pieces = _coverage_repair(part, pieces, min_area_m2)
        all_blocks.extend(pieces)

    # 去重叠（数值误差）：按面积从大到小，后到者减已占用区域
    all_blocks.sort(key=lambda p: p.area, reverse=True)
    used = None
    clean: List[Polygon] = []
    for b in all_blocks:
        if used is not None:
            try:
                b = b.difference(used)
            except Exception:
                pass
        for p in _as_polygons(b):
            if p.area >= min_area_m2 * 0.5:
                clean.append(p)
                used = p if used is None else unary_union([used, p])

    result = []
    for poly in clean:
        if poly.area < min_area_m2:
            continue
        ln = _nearest_line(poly, lines)
        st = _shape_stats(poly)
        if ln is not None and not ln.is_empty:
            coords = list(ln.coords)
            ep = (
                _endpoint_type(coords[0], junctions, terminals, connectivity_tol_m),
                _endpoint_type(coords[-1], junctions, terminals, connectivity_tol_m),
            )
        else:
            ep = ("mid", "mid")
        result.append({
            "geometry": poly,
            "approx_shape": st["approx_shape"],
            "area_m2": poly.area,
            "aspect_ratio": st["aspect_ratio"],
            "width_m": st["width_m"],
            "length_m": st["length_m"],
            "skeleton_line": ln,
            "endpoint_types": ep,
        })

    # 覆盖率日志
    try:
        cov = unary_union([b["geometry"] for b in result])
        cov_area = cov.area if cov and not cov.is_empty else 0.0
        total = walkable_u.area
        ratio = cov_area / total if total > 1e-6 else 0.0
        # 重叠检查（理论上接近 0）
        sum_a = sum(b["area_m2"] for b in result)
        overlap_ratio = max(0.0, (sum_a - cov_area) / total) if total > 1e-6 else 0.0
        print(f"    [decompose] 块={len(result)} 覆盖={ratio:.0%} "
              f"重叠≈{overlap_ratio:.1%} (截点={len(cut_pts)})")
    except Exception:
        print(f"    [decompose] 块={len(result)}")

    return result


def classify_block(
    block: dict,
    elevator_geoms_m: Sequence = (),
    stair_geoms_m: Sequence = (),
) -> str:
    """空间块功能分类（按建筑功能空间划分，不标注消防前室）。

    说明：电梯前室/楼梯前室属消防通道用途，本项目（视障室内导航）
    不需要该语义，统一按功能空间划分：
      - lobby（门厅/大厅）：大空间且方正（面积≥40m²、长宽比<2.2、宽≥3m）
      - passage（过道）：宽度<2.2m 的窄通道
      - corridor（走廊）：默认，标准走道

    elevator_geoms_m / stair_geoms_m 参数保留仅为兼容调用方，不再参与判定。
    """
    aspect = float(block.get("aspect_ratio") or 1.0)
    area = float(block.get("area_m2") or 0.0)
    width = float(block.get("width_m") or 0.0)

    # ── 1) 门厅 / 大厅：大空间 + 方正 ────────────────────
    if area >= 40.0 and aspect < 2.2 and (width <= 0 or width >= 3.0):
        return "lobby"

    # ── 2) 过道：窄通道（宽 < 2.2m）───────────────────────
    if width > 0 and width < 2.2:
        return "passage"

    # ── 3) 走廊：默认 ────────────────────────────────────
    return "corridor"
