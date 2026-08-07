# -*- coding: utf-8 -*-
"""
空间分解：基于中轴骨架将可通行区域切割为近似矩形/梯形的独立几何块。

输入：skeleton lines（src/skeleton/pipeline 输出的简化中轴线列表）+ walkable polygon
输出：空间块列表 [{geometry, approx_shape, area_m2, aspect_ratio, ...}]
"""

from __future__ import annotations

import math
from typing import Dict, List, Optional, Sequence, Tuple

from shapely.geometry import LineString, Point, Polygon
from shapely.ops import unary_union


# ── geometry helpers ────────────────────────────────────────────────

def _perpendicular_cut_line(
    point: Tuple[float, float],
    tangent: Tuple[float, float],
    walkable_poly: Polygon,
    extend_m: float = 25.0,
) -> Optional[LineString]:
    """在 point 处沿 tangent 的垂直方向画一条穿过 walkable 的截线。

    返回：截线在 walkable_poly 内的那一段 LineString（2 端点）。
    若交点不足 2 或截线退化则返回 None。
    """
    dx, dy = tangent
    mag = math.hypot(dx, dy)
    if mag < 1e-9:
        return None
    # 垂直方向（逆时针 90°）
    px, py = -dy / mag, dx / mag
    p1 = Point(point[0] + px * extend_m, point[1] + py * extend_m)
    p2 = Point(point[0] - px * extend_m, point[1] - py * extend_m)
    cutter = LineString([p1, p2])
    inter = cutter.intersection(walkable_poly)
    if inter.is_empty:
        return None
    if inter.geom_type == "LineString":
        if len(list(inter.coords)) >= 2:
            return inter
        return None
    if inter.geom_type == "MultiLineString":
        # 取最长的一段（通常是穿过 walkable 的主段）
        best = max((g for g in inter.geoms if g.length > 1e-6),
                   key=lambda g: g.length, default=None)
        if best is not None and len(list(best.coords)) >= 2:
            return best
    # 兜底：GeometryCollection 或其他类型 → 取其中的线
    if hasattr(inter, "geoms"):
        lines = [g for g in inter.geoms
                 if g.geom_type == "LineString" and g.length > 1e-6]
        if lines:
            best = max(lines, key=lambda g: g.length)
            if len(list(best.coords)) >= 2:
                return best
    return None


def _block_for_skeleton_segment(
    line: LineString,
    walkable_poly: Polygon,
    min_area_m2: float = 1.0,
    stats: dict = None,
) -> Optional[dict]:
    """围绕一条中轴段构建空间块。

    在中轴段两端各自生成垂直截线，两条截线的端点构成四边形，
    再用 walkable_poly 裁剪得到最终块。
    """
    if line.length < 0.3:
        if stats: stats["too_short"] += 1
        return None
    coords = list(line.coords)
    if len(coords) < 2:
        return None

    # 起 / 止点及方向
    u = coords[0]
    v = coords[-1]
    tang_u = (coords[1][0] - u[0], coords[1][1] - u[1])
    tang_v = (coords[-1][0] - coords[-2][0],
              coords[-1][1] - coords[-2][1])

    # 也用中点方向兜底（若端方向太短则参照整体）
    if math.hypot(*tang_u) < 0.1 * line.length:
        tang_u = (v[0] - u[0], v[1] - u[1])
    if math.hypot(*tang_v) < 0.1 * line.length:
        tang_v = (v[0] - u[0], v[1] - u[1])

    cut_u = _perpendicular_cut_line(u, tang_u, walkable_poly)
    cut_v = _perpendicular_cut_line(v, tang_v, walkable_poly)

    if cut_u is None or cut_v is None:
        if stats:
            if cut_u is None: stats["cut_u_none"] += 1
            if cut_v is None: stats["cut_v_none"] += 1
        # 一端截不出 → 尝试在骨架整体中点做单截线并取中线交点
        mid = Point(line.interpolate(line.length / 2.0, normalized=False))
        tang_mid = (v[0] - u[0], v[1] - u[1])
        # 用中点 + walkable 的包围盒内缩来兜底
        return _fallback_block(u, v, walkable_poly, min_area_m2)

    cu = list(cut_u.coords)
    cv = list(cut_v.coords)
    if len(cu) < 2 or len(cv) < 2:
        return None

    # 配对：最近端点法
    cu0, cu1 = (cu[0][0], cu[0][1]), (cu[1][0], cu[1][1])
    cv0, cv1 = (cv[0][0], cv[0][1]), (cv[1][0], cv[1][1])
    d00 = math.hypot(cu0[0] - cv0[0], cu0[1] - cv0[1])
    d01 = math.hypot(cu0[0] - cv1[0], cu0[1] - cv1[1])
    if d00 <= d01:
        ring = [cu0, cv0, cv1, cu1]
    else:
        ring = [cu0, cv1, cv0, cu1]
    ring.append(ring[0])  # close

    try:
        block = Polygon(ring)
        if not block.is_valid:
            block = block.buffer(0)
        if block.is_empty or not isinstance(block, Polygon):
            return None
    except Exception:
        return None

    # 裁剪到 walkable
    block = block.intersection(walkable_poly)
    if block.is_empty:
        if stats: stats["block_none"] += 1
        return None
    # 只保留最大组件（若切割成多块）
    if block.geom_type in ("MultiPolygon", "GeometryCollection"):
        parts = [g for g in block.geoms
                 if isinstance(g, Polygon) and not g.is_empty]
        if not parts:
            if stats: stats["block_none"] += 1
            return None
        block = max(parts, key=lambda p: p.area)

    if not isinstance(block, Polygon) or block.is_empty:
        if stats: stats["block_none"] += 1
        return None
    area = block.area
    if area < min_area_m2:
        if stats: stats["block_none"] += 1
        return None

    # 近似形状：用最小旋转矩形
    mbr = block.minimum_rotated_rectangle
    if mbr.is_empty:
        return None
    # 旋转矩形的两条边方向向量
    mbr_coords = list(mbr.exterior.coords)
    w1 = math.hypot(mbr_coords[1][0] - mbr_coords[0][0],
                    mbr_coords[1][1] - mbr_coords[0][1])
    w2 = math.hypot(mbr_coords[2][0] - mbr_coords[1][0],
                    mbr_coords[2][1] - mbr_coords[1][1])
    w_short = min(w1, w2)
    w_long = max(w1, w2)
    aspect = w_long / w_short if w_short > 1e-9 else 999.0
    approx_shape = "rect" if aspect < 2.5 else "trapezoid"

    return {
        "geometry": block,
        "approx_shape": approx_shape,
        "area_m2": block.area,
        "aspect_ratio": aspect,
        "width_m": w_short,
        "length_m": w_long,
        "skeleton_line": line,
    }


def _fallback_block(
    u: Tuple[float, float],
    v: Tuple[float, float],
    walkable_poly: Polygon,
    min_area_m2: float,
) -> Optional[dict]:
    """裁线失败时的兜底：直接用 uv 连线 + walkable 的局部裁剪。"""
    uv = LineString([u, v])
    buf = uv.buffer(2.0)  # 2m 缓冲，足够覆盖走廊宽度的一半
    clipped = buf.intersection(walkable_poly)
    if clipped.is_empty:
        return None
    if clipped.geom_type in ("MultiPolygon", "GeometryCollection"):
        parts = [g for g in clipped.geoms
                 if isinstance(g, Polygon) and not g.is_empty]
        clipped = max(parts, key=lambda p: p.area) if parts else None
    if clipped is None or not isinstance(clipped, Polygon) or clipped.is_empty:
        return None
    area = clipped.area
    if area < min_area_m2:
        return None
    return {
        "geometry": clipped,
        "approx_shape": "rect",  # 缓冲近似的默认
        "area_m2": area,
        "aspect_ratio": 1.0,
        "width_m": math.sqrt(area),
        "length_m": math.sqrt(area),
        "skeleton_line": uv,
    }


# ── public API ───────────────────────────────────────────────────────

def decompose_walkable_to_blocks(
    skeleton_lines: Sequence[LineString],
    walkable_polygon_m: Polygon,
    min_area_m2: float = 1.0,
) -> List[dict]:
    """主入口：将可通行区域按中轴分解为独立几何块。

    Parameters
    ----------
    skeleton_lines : list of LineString
        src/skeleton/pipeline.build_skeleton_for_walkables 返回的 "lines"。
    walkable_polygon_m : Polygon
        统一的可通行区域多边形（米坐标）。
    min_area_m2 : float
        丢弃面积过小的碎块（默认 1 m²）。

    Returns
    -------
    list of {
        "geometry": Polygon,       # 空间块的几何（米坐标）
        "approx_shape": "rect" | "trapezoid",
        "area_m2": float,
        "aspect_ratio": float,
        "width_m": float,
        "length_m": float,
        "skeleton_line": LineString,
    }
    """
    blocks = []
    stats = {"total": 0, "too_short": 0, "cut_u_none": 0, "cut_v_none": 0,
             "block_none": 0, "block_ok": 0}

    # 支持 Polygon 或 MultiPolygon
    raw = walkable_polygon_m
    if raw.is_empty:
        return blocks
    if raw.geom_type == "MultiPolygon":
        polys = [g for g in raw.geoms
                 if isinstance(g, Polygon) and not g.is_empty]
    elif raw.geom_type == "Polygon":
        polys = [raw] if not raw.is_empty else []
    else:
        return blocks
    polys = [p.buffer(0) for p in polys if not p.is_empty]
    if not polys:
        return blocks

    # 为每条骨架线找所在的多边形组件
    for line in skeleton_lines:
        if not isinstance(line, LineString) or line.is_empty:
            continue
        stats["total"] += 1
        mid_pt = Point(line.interpolate(line.length / 2, normalized=True))
        best_poly = None
        for p in polys:
            if p.contains(mid_pt) or p.buffer(0.1).contains(mid_pt):
                best_poly = p
                break
        if best_poly is None:
            best_poly = min(polys, key=lambda p: p.exterior.distance(mid_pt))
        bk = _block_for_skeleton_segment(line, best_poly, min_area_m2, stats)
        if bk:
            blocks.append(bk)
            stats["block_ok"] += 1
    if blocks:
        print(f"    [decompose] {len(blocks)}/{stats['total']} 分解成功 "
              f"(跳过短边:{stats['too_short']})")
    return blocks


def classify_block(block: dict,
                   elevator_geoms_m: Sequence = (),
                   stair_geoms_m: Sequence = (),
                   ) -> str:
    """根据几何特征和井道关系标注空间块用途。

    分类规则（v1，参考诊断报告的改进方向）：
    - 长宽比 > 3.0 且两端连通     → corridor (走道)
    - 距电梯 < 1.5m、袋形           → elevator_lobby
    - 距楼梯 < 3.0m、袋形           → stair_lobby
    - 面积 > 50m²、长宽比 < 2.0    → lobby (门厅/大厅)
    - 默认                         → corridor
    """
    geo = block["geometry"]
    aspect = block["aspect_ratio"]
    area = block["area_m2"]

    # 距电梯 / 楼梯最近距离
    d_elev = None
    d_stair = None
    for g in elevator_geoms_m:
        d = geo.exterior.distance(g) if geo.exterior else float("inf")
        if d_elev is None or d < d_elev:
            d_elev = d
    for g in stair_geoms_m:
        d = geo.exterior.distance(g) if geo.exterior else float("inf")
        if d_stair is None or d < d_stair:
            d_stair = d

    # 袋形判定（简化：最近井道距 < 阈值）
    if d_elev is not None and d_elev < 1.5 and area <= 150.0:
        return "elevator_lobby"
    if d_stair is not None and d_stair < 3.0 and area <= 60.0:
        return "stair_lobby"

    # 大厅/门厅
    if area > 50.0 and aspect < 2.0:
        return "lobby"

    return "corridor"
