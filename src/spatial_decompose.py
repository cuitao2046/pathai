# -*- coding: utf-8 -*-
"""
空间分解 v2：基于骨架连接度 + 统一分界线的 walkable 切分。

v1 问题：每条骨架段独立做垂直截线，相邻段在 junction 产生不同的截线
→ block 重叠、覆盖不全、形状非矩形/三角形。
v2 方案：1) 聚类骨架端点识别 junction/terminal；
2) junction 处用角平分线作为公共分界、terminal 处用垂直截线；
3) 所有分界线 + walkable 外边界 → polygonize → 无重叠、完全覆盖。

输入：skeleton lines（src/skeleton/pipeline 输出的简化中轴线列表）+ walkable polygon
输出：空间块列表 [{geometry: Polygon, approx_shape, area_m2, aspect_ratio, ...}]
"""

from __future__ import annotations

import math
import collections
from typing import Dict, List, Optional, Sequence, Set, Tuple

from shapely.geometry import LineString, Point, Polygon
from shapely.ops import unary_union, polygonize


# ── helper: 端点聚类 ──────────────────────────────────────────────────

def _canonical_pt(pt: Tuple[float, float],
                  tol: float = 1.5) -> Tuple[float, float]:
    """将点量化到 tol 网格上用于端点去重聚类。"""
    return (round(pt[0] / tol) * tol, round(pt[1] / tol) * tol)


def _build_endpoint_clusters(
    skeleton_lines: Sequence[LineString],
    tol_m: float = 1.5,
) -> Dict[Tuple[float, float], List[Tuple[LineString, int, Tuple[float, float]]]]:
    """对骨架段端点聚类。

    返回 {canonical_pt: [(line, endpoint_pos, real_pt), ...]}
    endpoint_pos: 0 = 起点, -1 = 终点。
    每个 cluster 内是一组共享同一 canonical 点的骨架段端点。
    """
    groups = collections.defaultdict(list)
    for line in skeleton_lines:
        if not isinstance(line, LineString) or line.is_empty:
            continue
        coords = list(line.coords)
        if len(coords) < 2:
            continue
        s, e = (coords[0][0], coords[0][1]), (coords[-1][0], coords[-1][1])
        cs = _canonical_pt(s, tol_m)
        ce = _canonical_pt(e, tol_m)
        groups[cs].append((line, 0, s))     # 起点
        groups[ce].append((line, -1, e))  # 终点
    return groups


# ── cut-line generation ───────────────────────────────────────────────

def _segment_direction_away_from(
    line: LineString,
    pos: int,       # 0 = 起点, -1 = 终点
) -> Optional[Tuple[float, float]]:
    """从 line 的指定端点出发、沿骨架 OUTWARD 的方向（单位向量）。"""
    coords = list(line.coords)
    if len(coords) < 2:
        return None
    if pos == 0:
        dx, dy = coords[0][0] - coords[1][0], coords[0][1] - coords[1][1]
    else:
        dx, dy = coords[-1][0] - coords[-2][0], coords[-1][1] - coords[-2][1]
    mag = math.hypot(dx, dy)
    if mag < 1e-9:
        return None
    return (dx / mag, dy / mag)


def _bisector(d1: Tuple[float, float],
              d2: Tuple[float, float]) -> Optional[Tuple[float, float]]:
    """两个方向向量的角平分线方向（单位向量）。

    若 d1 + d2 ≈ 0（反向），则改用 d1 旋转 90° 的垂直方向。
    """
    bx, by = d1[0] + d2[0], d1[1] + d2[1]
    mag = math.hypot(bx, by)
    if mag < 1e-9:
        # 反向：用 d1 的垂直方向
        bx, by = -d1[1], d1[0]
        mag = math.hypot(bx, by)
    if mag < 1e-9:
        return None
    return (bx / mag, by / mag)


def _long_line_through(pt: Tuple[float, float],
                       direction: Tuple[float, float],
                       length: float = 200.0) -> LineString:
    """过 pt 沿 direction 方向(±)做长线段，用于后续与 walkable boundary 求交。"""
    dx, dy = direction
    return LineString([
        (pt[0] + dx * length, pt[1] + dy * length),
        (pt[0] - dx * length, pt[1] - dy * length),
    ])


def generate_cut_lines(
    skeleton_lines: Sequence[LineString],
    tol_m: float = 1.5,
    max_extend: float = 200.0,
) -> List[LineString]:
    """生成所有分界线。

    - junction（≥2 条骨架段汇聚）：角平分线
    - terminal（仅 1 条骨架段）：该段 endpoint 的垂直截线
    """
    cuts: List[LineString] = []
    clusters = _build_endpoint_clusters(skeleton_lines, tol_m)

    for canonical_pt, members in clusters.items():
        # canonical_pt 是聚类网格点；取各真实端点的均值作为 junction 坐标
        pts = [m[2] for m in members]
        jx = sum(p[0] for p in pts) / len(pts)
        jy = sum(p[1] for p in pts) / len(pts)

        # 收集所有从该点向外辐射的骨架方向
        dirs: List[Tuple[float, float]] = []
        line_set: Set[int] = set()
        for line, pos, real_pt in members:
            lid = id(line)
            if lid in line_set:
                continue
            line_set.add(lid)
            d = _segment_direction_away_from(line, pos)
            if d is not None:
                dirs.append(d)

        if len(dirs) < 2:
            # terminal：仅 1 个方向 → 做垂直截线
            if dirs:
                d = dirs[0]
                perp = (-d[1], d[0])
                cuts.append(_long_line_through((jx, jy), perp, max_extend))
            continue

        # junction：按角度排序 → 相邻对之间做角平分线
        dirs.sort(key=lambda d: math.atan2(d[1], d[0]))
        n = len(dirs)
        for i in range(n):
            d1 = dirs[i]
            d2 = dirs[(i + 1) % n]
            b = _bisector(d1, d2)
            if b is not None:
                cuts.append(_long_line_through((jx, jy), b, max_extend))

    return cuts


# ── polygonize + assignment ───────────────────────────────────────────

def _polygonize_partition(
    walkable_poly: Polygon,
    cut_lines: Sequence[LineString],
) -> List[Polygon]:
    """用分界线 + walkable 边界做 polygonize，得到无重叠、完全覆盖的子多边形。"""
    if walkable_poly.is_empty:
        return []

    # 组合 walkable 外边界 + 所有分界线
    all_lines = [walkable_poly.exterior]
    all_lines.extend(cut_lines)

    merged = unary_union(all_lines)
    candidates = list(polygonize(merged))

    # 仅保留重心在 walkable 内的多边形（剔除外部多边形和洞）
    result = []
    for c in candidates:
        if not isinstance(c, Polygon) or c.is_empty:
            continue
        centroid = c.centroid
        if walkable_poly.contains(centroid) or walkable_poly.buffer(1e-6).contains(centroid):
            result.append(c)
    return result


def _assign_polygons_to_segments(
    polygons: List[Polygon],
    skeleton_lines: Sequence[LineString],
) -> Dict[int, List[Polygon]]:
    """将子多边形按最近骨架段归类。

    返回 {line_index_in_skeleton_lines: [polygons]}。
    """
    assignment: Dict[int, List[Polygon]] = collections.defaultdict(list)
    for poly in polygons:
        centroid = poly.centroid
        best_i = None
        best_d = float("inf")
        for i, line in enumerate(skeleton_lines):
            if not isinstance(line, LineString) or line.is_empty:
                continue
            d = line.distance(centroid)
            if d < best_d:
                best_d = d
                best_i = i
        if best_i is not None:
            assignment[best_i].append(poly)
    return assignment


def _build_blocks_for_lines(
    assignment: Dict[int, List[Polygon]],
    skeleton_lines: Sequence[LineString],
    min_area_m2: float,
    junction_points: Set[Tuple[float, float]],
    terminal_points: Set[Tuple[float, float]],
    tol_m: float = 1.5,
) -> List[dict]:
    """对已归类的子多边形做合并 + 几何元数据提取，返回 block 列表。"""
    blocks: List[dict] = []
    for seg_idx, polys in assignment.items():
        if seg_idx >= len(skeleton_lines) or not polys:
            continue
        line = skeleton_lines[seg_idx]
        if not isinstance(line, LineString) or line.is_empty:
            continue

        merged = unary_union(polys)
        if merged.is_empty:
            continue
        merged = merged.buffer(0)  # 消除 sliver / 微小自交
        if merged.is_empty:
            continue
        if merged.geom_type == "Polygon":
            geoms = [merged]
        elif hasattr(merged, "geoms"):
            geoms = [g for g in merged.geoms
                     if isinstance(g, Polygon) and not g.is_empty
                     and g.area >= min_area_m2]
        else:
            continue

        for g in geoms:
            if g.area < min_area_m2:
                continue

            # 近似形状
            mbr = g.minimum_rotated_rectangle
            if mbr.is_empty:
                continue
            mbr_coords = list(mbr.exterior.coords)
            w1 = math.hypot(mbr_coords[1][0] - mbr_coords[0][0],
                            mbr_coords[1][1] - mbr_coords[0][1])
            w2 = math.hypot(mbr_coords[2][0] - mbr_coords[1][0],
                            mbr_coords[2][1] - mbr_coords[1][1])
            w_short = min(w1, w2)
            w_long = max(w1, w2)
            aspect = w_long / w_short if w_short > 1e-9 else 999.0
            approx_shape = "rect" if aspect < 2.5 else "trapezoid"

            # 端点类型
            coords = list(line.coords)
            ep_start = _canonical_pt((coords[0][0], coords[0][1]), tol_m)
            ep_end = _canonical_pt((coords[-1][0], coords[-1][1]), tol_m)
            et0 = ("junction" if ep_start in junction_points else
                   ("terminal" if ep_start in terminal_points else "mid"))
            et1 = ("junction" if ep_end in junction_points else
                   ("terminal" if ep_end in terminal_points else "mid"))

            blocks.append({
                "geometry": g,
                "approx_shape": approx_shape,
                "area_m2": g.area,
                "aspect_ratio": aspect,
                "width_m": w_short,
                "length_m": w_long,
                "skeleton_line": line,
                "endpoint_types": (et0, et1),
            })

    return blocks


# ── public API ─────────────────────────────────────────────────────────

def _lines_touching_poly(
    skeleton_lines: Sequence[LineString],
    poly: Polygon,
) -> List[LineString]:
    """筛选与指定多边形相交/内含的骨架线。"""
    result: List[LineString] = []
    for line in skeleton_lines:
        if not isinstance(line, LineString) or line.is_empty:
            continue
        if poly.contains(line) or poly.intersects(line):
            result.append(line)
    return result


def decompose_walkable_to_blocks(
    skeleton_lines: Sequence[LineString],
    walkable_polygon_m: Polygon,
    min_area_m2: float = 1.0,
    junctions: Sequence[Tuple[float, float]] = (),
    terminals: Sequence[Tuple[float, float]] = (),
    connectivity_tol_m: float = 1.5,
) -> List[dict]:
    """主入口：将可通行区域按中轴分解为独立几何块（v2, 无重叠、全覆盖）。

    Parameters
    ----------
    skeleton_lines : list of LineString
        src/skeleton/pipeline 输出的 "lines"。
    walkable_polygon_m : Polygon
        统一的可通行区域多边形（米坐标）。
    min_area_m2 : float
        丢弃面积过小的碎块（默认 1 m²）。
    junctions : list of (x, y)
        骨架交叉口点（degree≥3），用于判定端点类型。
    terminals : list of (x, y)
        骨架端点（degree=1），用于判定端点类型。
    connectivity_tol_m : float
        端点匹配容差（默认 1.5m）。

    Returns
    -------
    list of {
        "geometry": Polygon,
        "approx_shape": "rect" | "trapezoid",
        "area_m2": float, "aspect_ratio": float,
        "width_m": float, "length_m": float,
        "skeleton_line": LineString,
        "endpoint_types": (str, str),
    }
    """
    # 归一化 walkable
    raw = walkable_polygon_m
    if raw.is_empty:
        return []
    # 兼容 MultiPolygon → 拆分成多个 Polygon 独立处理
    if raw.geom_type == "MultiPolygon":
        polys = [g for g in raw.geoms
                 if isinstance(g, Polygon) and not g.is_empty]
    elif raw.geom_type == "Polygon":
        polys = [raw] if not raw.is_empty else []
    else:
        return []
    polys = [p.buffer(0) for p in polys if not p.is_empty]
    if not polys:
        return []

    # 预处理：junctions / terminals → canonical set
    junction_set: Set[Tuple[float, float]] = set()
    for j in junctions:
        junction_set.add(_canonical_pt((j[0], j[1]), connectivity_tol_m))
    terminal_set: Set[Tuple[float, float]] = set()
    for t in terminals:
        terminal_set.add(_canonical_pt((t[0], t[1]), connectivity_tol_m))

    total_segs = len([l for l in skeleton_lines
                      if isinstance(l, LineString) and not l.is_empty])
    all_blocks: List[dict] = []

    for poly in polys:
        # 1. 仅用当前 polygon 组件内的骨架线（避免跨组件分界污染）
        relevant_lines = _lines_touching_poly(skeleton_lines, poly)
        if not relevant_lines:
            continue

        # 2. 用 relevant_lines 生成分界线（junction bisectors + terminal perpendiculars）
        cuts = generate_cut_lines(relevant_lines, tol_m=connectivity_tol_m)

        # 3. polygonize：boundary + cuts → 子多边形
        sub_polys = _polygonize_partition(poly, cuts)
        if not sub_polys:
            # polygonize 无结果（可能 cuts 太少）→ 整块兜底赋给最近的骨架段
            assignment = _assign_polygons_to_segments([poly], relevant_lines)
            all_blocks.extend(_build_blocks_for_lines(
                assignment, relevant_lines, min_area_m2,
                junction_set, terminal_set, connectivity_tol_m))
            continue

        # 4. 子多边形 → 最近骨架段
        assigned = _assign_polygons_to_segments(sub_polys, relevant_lines)

        # 5. 合并同属相同骨架段的子多边形 → 最终 block
        blocks = _build_blocks_for_lines(assigned, relevant_lines, min_area_m2,
                                         junction_set, terminal_set,
                                         connectivity_tol_m)
        all_blocks.extend(blocks)

    # ── 全局去重叠：逐块减去与更大块的重叠区域 ──
    if len(all_blocks) > 1:
        # 按面积从大到小排序，大块优先
        all_blocks.sort(key=lambda b: b["area_m2"], reverse=True)
        cleaned: List[dict] = []
        union_so_far = None
        for b in all_blocks:
            g = b["geometry"]
            if union_so_far is not None:
                g = g.difference(union_so_far)
                if g.is_empty:
                    continue
                if g.geom_type in ("MultiPolygon", "GeometryCollection"):
                    parts = [p for p in g.geoms
                             if isinstance(p, Polygon) and not p.is_empty
                             and p.area >= min_area_m2]
                    if not parts:
                        continue
                    g = max(parts, key=lambda p: p.area)
                elif not isinstance(g, Polygon):
                    continue
                if g.area < min_area_m2:
                    continue
                # 更新面积/形状元数据（几何变了）
                mbr = g.minimum_rotated_rectangle
                if not mbr.is_empty:
                    mc = list(mbr.exterior.coords)
                    w1 = math.hypot(mc[1][0]-mc[0][0], mc[1][1]-mc[0][1])
                    w2 = math.hypot(mc[2][0]-mc[1][0], mc[2][1]-mc[1][1])
                    ws = min(w1, w2)
                    wl = max(w1, w2)
                    b["geometry"] = g
                    b["area_m2"] = g.area
                    b["width_m"] = ws
                    b["length_m"] = wl
                    b["aspect_ratio"] = wl / ws if ws > 1e-9 else 999
                    b["approx_shape"] = "rect" if b["aspect_ratio"] < 2.5 else "trapezoid"
            if union_so_far is None:
                union_so_far = g
            else:
                union_so_far = union_so_far.union(g)
            cleaned.append(b)
        all_blocks = cleaned

    if all_blocks:
        print(f"    [decompose v2] {len(all_blocks)} 个几何块 "
              f"(共 {total_segs} 条中轴段, polygonize 分区后归并)")
    return all_blocks


# ── 分类器（从 v1 继承，无变化）────────────────────────────────────────

def classify_block(block: dict,
                   elevator_geoms_m: Sequence = (),
                   stair_geoms_m: Sequence = (),
                   ) -> str:
    """根据几何特征 + 块连接度 + 井道关系标注空间块用途。

    分类规则（v2，引入端点连接度）：
    - "terminal" 端点 = 死胡同/袋形 → 服务型空间（前室）
    - "junction" 端点 = 交叉口 → 穿越型空间（走道）
    - 仅当一个块至少有一端是 terminal 且贴近井道时才判为前室

    具体优先级：
    1. 电梯前室：贴近电梯(<1.5m) 且 至少一端为 terminal 且 面积≤150
    2. 楼梯前室：贴近楼梯(<3.0m) 且 至少一端为 terminal 且 面积≤60
    3. 门厅/大厅：面积>50 且 长宽比<2.0 且 至少一端非 terminal
    4. 默认：corridor（穿越型走道）
    """
    geo = block["geometry"]
    aspect = block["aspect_ratio"]
    area = block["area_m2"]
    ep = block.get("endpoint_types", ("mid", "mid"))
    has_terminal = ("terminal" in ep)

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

    # 电梯前室：贴近 + 袋形（至少一端是死胡同）
    if (d_elev is not None and d_elev < 1.5 and area <= 150.0
            and has_terminal):
        return "elevator_lobby"

    # 楼梯前室：贴近 + 袋形
    if (d_stair is not None and d_stair < 3.0 and area <= 60.0
            and has_terminal):
        return "stair_lobby"

    # 大厅/门厅：大空间 + 方正 + 非纯穿越
    if area > 50.0 and aspect < 2.0:
        return "lobby"

    return "corridor"
