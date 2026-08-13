# -*- coding: utf-8 -*-
"""合班教室识别与注入：语义种子 + 多方向射线投票真实墙体，隔离式注入。

原内嵌于 src/parsing/parse_cad_pdf.py（审查 B1）：
  _HEBAN_* 射线投票参数 / _heban_real_polygon_v2 / inject_heban_classroom_rooms

只服务合班教室自身：优先真实闭合墙体多边形，失败回退 3m 占位；
只追加门归属，不抢其它封闭房间的门、不修改其它房间的 roomType/walkable。
"""
import math

from shapely.geometry import Point, Polygon
from shapely.ops import unary_union

from src.common.constants import ORIGIN_X, ORIGIN_Y, SCALE
from src.geometry.geo_utils import pt2m
from src.topology import OBJ_TYPE, obj_id

# ── 射线投票参数 ──
_HEBAN_AXIS_TOL = 2.0          # pt：轴向判定容差
_HEBAN_MIN_SEG_LEN = 5.0       # pt：最小有效墙段长度
_HEBAN_H_SPAN = 180.0          # pt：水平射线跨度
_HEBAN_V_SPAN = 140.0          # pt：垂直射线跨度
_HEBAN_RAY_SAMPLES = 401       # 射线采样数
_HEBAN_RAY_BIN = 3.0           # pt：投票分箱大小
_HEBAN_MIN_SUPPORT = 0.50      # 最低支持率


def _heban_real_polygon_v2(label_pt_pt, all_segs, closures):
    """
    合班教室真实闭合墙体识别 v2 —— 语义种子 + 多方向射线投票。

    替代 v1 的多尺度形态学闭运算方案。核心思路：
      1) 从标签点（语义种子）向四个方向发射大量射线；
      2) 每条射线碰撞最近墙体，统计碰撞坐标的众数；
      3) 门洞缺口仅影响少数射线，不改变众数；
      4) 四个方向的众数坐标构成房间矩形边界。

    参数同 _heban_real_polygon，返回 shapely Polygon 或 None。
    """
    from collections import Counter

    seed_x, seed_y = label_pt_pt

    # 合并所有墙体来源（结构墙 + 封口线）
    all_lines = list(all_segs) + list(closures)
    if not all_lines:
        return None

    tol = _HEBAN_AXIS_TOL
    min_len = _HEBAN_MIN_SEG_LEN
    vert = []   # [(x, y_min, y_max), ...]
    horz = []   # [(y, x_min, x_max), ...]

    for (x1, y1), (x2, y2) in all_lines:
        if abs(x1 - x2) <= tol and abs(y1 - y2) > min_len:
            x = (x1 + x2) / 2.0
            vert.append((x, min(y1, y2), max(y1, y2)))
        elif abs(y1 - y2) <= tol and abs(x1 - x2) > min_len:
            y = (y1 + y2) / 2.0
            horz.append((y, min(x1, x2), max(x1, x2)))

    if len(vert) < 5 or len(horz) < 5:
        return None

    # ── 射线投票辅助函数 ──

    def _cluster_mode(values):
        """对射线碰撞坐标做分箱投票，返回 (均值坐标, 支持数)。"""
        if not values:
            return None, 0
        hist = Counter(int(round(v / _HEBAN_RAY_BIN)) for v in values)
        best_bin, support = hist.most_common(1)[0]
        center = best_bin * _HEBAN_RAY_BIN
        in_cluster = [v for v in values
                      if abs(v - center) <= _HEBAN_RAY_BIN * 0.60]
        return sum(in_cluster) / len(in_cluster), support

    def _vote_vertical(side):
        """向水平方向发射射线，投票找左/右墙。"""
        values = []
        for i in range(_HEBAN_RAY_SAMPLES):
            y = seed_y - _HEBAN_V_SPAN + 2.0 * _HEBAN_V_SPAN * i / (_HEBAN_RAY_SAMPLES - 1)
            candidates = [
                x for x, y0, y1 in vert
                if y0 - tol <= y <= y1 + tol
                and ((side == "left" and x < seed_x - tol)
                     or (side == "right" and x > seed_x + tol))
            ]
            if candidates:
                values.append(max(candidates) if side == "left" else min(candidates))
        coord, support = _cluster_mode(values)
        if coord is None:
            return None
        ratio = support / len(values) if values else 0
        if ratio < _HEBAN_MIN_SUPPORT:
            return None
        return coord

    def _vote_horizontal(side):
        """向垂直方向发射射线，投票找上/下墙。"""
        values = []
        for i in range(_HEBAN_RAY_SAMPLES):
            x = seed_x - _HEBAN_H_SPAN + 2.0 * _HEBAN_H_SPAN * i / (_HEBAN_RAY_SAMPLES - 1)
            candidates = [
                y for y, x0, x1 in horz
                if x0 - tol <= x <= x1 + tol
                and ((side == "top" and y < seed_y - tol)
                     or (side == "bottom" and y > seed_y + tol))
            ]
            if candidates:
                values.append(max(candidates) if side == "top" else min(candidates))
        coord, support = _cluster_mode(values)
        if coord is None:
            return None
        ratio = support / len(values) if values else 0
        if ratio < _HEBAN_MIN_SUPPORT:
            return None
        return coord

    # ── 四方向投票 ──
    left = _vote_vertical("left")
    right = _vote_vertical("right")
    top = _vote_horizontal("top")
    bottom = _vote_horizontal("bottom")

    if any(v is None for v in (left, right, top, bottom)):
        return None

    if not (left < right and top < bottom):
        return None

    poly = Polygon([
        (left, top), (right, top),
        (right, bottom), (left, bottom),
    ])
    if not poly.is_valid:
        poly = poly.buffer(0)
    if poly.is_empty:
        return None

    area_m2 = float(poly.area) * (SCALE ** 2)
    if not (30.0 <= area_m2 <= 300.0):
        return None

    return poly


def inject_heban_classroom_rooms(rooms, doors, labels_with_pt, floor_no,
                                  all_segs=(), closures=()):
    """
    合班教室注入（隔离版）：只服务合班自身，不影响其他空间。

    约束：
      1) 优先用局部闭合墙体识别得到真实多边形；失败才回退到 3m 占位方块；
      2) 只「追加」门归属，从不删除其它 room id；
      3) 不抢已明确归属其它封闭房间的门；
      4) 不修改其它房间的 roomType / walkable。
    """
    if not labels_with_pt:
        return 0

    OPEN = {"corridor", "lobby", "activity", "atrium",
            "elevator_lobby", "stair_lobby", "entrance", "accessible_entrance"}
    room_by_id = {r["id"]: r for r in rooms}

    def _lab(r):
        return r.get("label") or ""

    def _door_free_for_heban(dr):
        """门未被其它封闭房间独占时才可挂合班。"""
        for rid in dr.get("rooms") or []:
            r = room_by_id.get(rid)
            if r is None:
                continue
            if "合班" in _lab(r):
                continue
            rt = r.get("roomType") or ""
            if rt not in OPEN and rt != "infrastructure":
                # 已属于教室/办公/卫生间等封闭空间 → 不抢
                return False
        return True

    targets = [r for r in rooms if "合班" in _lab(r)]

    if not targets:
        for entry in labels_with_pt:
            label = entry[0]
            pt_pt = entry[1]
            if "合班" not in label:
                continue
            # 标签是否已落在某个已有封闭房间内？若是则不注入，避免叠房间
            cx_m, cy_m = pt2m(pt_pt)
            skip = False
            for r in rooms:
                if r.get("roomType") in OPEN:
                    continue
                poly = r.get("polygon_pt")
                if poly is None or getattr(poly, "is_empty", True):
                    continue
                try:
                    if poly.contains(Point(pt_pt[0], pt_pt[1])):
                        skip = True
                        break
                except Exception:
                    pass
            if skip:
                print(f"[F{floor_no}] 合班标签已在其它封闭房间内，跳过注入")
                continue

            def m2pt(xm, ym):
                return (xm / SCALE + ORIGIN_X, ORIGIN_Y - ym / SCALE)

            # 优先识别真实闭合墙体多边形；失败才回退 3m 占位方块
            real_poly = _heban_real_polygon_v2(pt_pt, all_segs, closures)
            if real_poly is not None:
                poly = real_poly
                source = "heban_inject_real"
                print(f"[F{floor_no}] 合班教室识别真实闭合墙体: "
                      f"面积≈{float(poly.area) * (SCALE ** 2):.1f}m²")
            else:
                # 极小占位（3m×3m），仅作拓扑质心，降低压盖邻室风险
                half = 1.5
                corners_m = [
                    (cx_m - half, cy_m - half),
                    (cx_m + half, cy_m - half),
                    (cx_m + half, cy_m + half),
                    (cx_m - half, cy_m + half),
                ]
                corners_pt = [m2pt(x, y) for x, y in corners_m]
                poly = Polygon(corners_pt)
                source = "heban_inject"
                print(f"[F{floor_no}] 合班教室真实墙体识别失败，回退 3m 占位")
            # 若与其它封闭房间相交，尝试差集；失败则仍用原多边形（拓扑用）
            try:
                others = []
                for r in rooms:
                    if r.get("roomType") in OPEN:
                        continue
                    op = r.get("polygon_pt")
                    if op is not None and not getattr(op, "is_empty", True):
                        others.append(op)
                if others:
                    diff = poly.difference(unary_union(others))
                    if not diff.is_empty:
                        if diff.geom_type == "Polygon":
                            poly = diff
                        elif diff.geom_type == "MultiPolygon" and diff.geoms:
                            poly = max(diff.geoms, key=lambda g: g.area)
            except Exception:
                pass

            seq = sum(1 for r in rooms if "-RM-" in str(r.get("id", ""))) + 1
            rid = obj_id(f"F{floor_no}", OBJ_TYPE["room"], seq)
            used_ids = {r["id"] for r in rooms}
            while rid in used_ids:
                seq += 1
                rid = obj_id(f"F{floor_no}", OBJ_TYPE["room"], seq)

            coords_m = [list(pt2m((x, y))) for x, y in poly.exterior.coords]
            room = {
                "id": rid,
                "label": label if "教室" in label else "合班教室",
                "roomType": "classroom",
                "polygon_pt": poly,
                "centroid_pt": (pt_pt[0], pt_pt[1]),
                "coords_m": coords_m,
                "centroid_m": [cx_m, cy_m],
                "area_m2": round(float(poly.area) * (SCALE ** 2), 2),
                "synthetic": True,
                "source": source,
            }
            rooms.append(room)
            room_by_id[rid] = room
            targets.append(room)
            print(f"[F{floor_no}] 注入合班教室(隔离): {rid} @ "
                  f"({cx_m:.1f},{cy_m:.1f}) 占位≈{room['area_m2']}m²")

    if not targets:
        return 0

    for room in targets:
        poly_pt = room.get("polygon_pt")
        cand = []
        for dr in doors:
            if not _door_free_for_heban(dr):
                continue
            dpt = dr.get("center")
            if not dpt:
                continue
            # 用门到房间多边形边界的距离（米），替代质心距离。
            # 门应落在房间墙面上，边界距离天然区分"墙上门"与"隔壁走廊门"。
            if poly_pt is not None and not poly_pt.is_empty:
                bdist_pt = poly_pt.exterior.distance(Point(dpt[0], dpt[1]))
                bdist_m = bdist_pt * SCALE
            else:
                dm = pt2m(dpt)
                bdist_m = math.hypot(dm[0] - room["centroid_m"][0],
                                     dm[1] - room["centroid_m"][1])
            if bdist_m > 1.0:  # 门中心距墙面超过 1m → 不是该房间的门
                continue
            pri = 0 if dr.get("kind") == "fire" else (
                1 if dr.get("kind") == "opening" else 2)
            cand.append((pri, bdist_m, dr))
        cand.sort(key=lambda x: (x[0], x[1]))  # 按 (优先级, 边界距离) 排序，不比较 dict
        picked = []
        for pri, d, dr in cand:
            if len(picked) >= 6:  # 上限放宽到 6，边界距离判据足以防误挂
                break
            picked.append((d, dr))
        for d, dr in picked:
            rooms_list = dr.setdefault("rooms", [])
            if room["id"] not in rooms_list:
                rooms_list.append(room["id"])  # 只追加，不删原有
        if not picked:
            print(f"[F{floor_no}] 警告: 合班 {room['id']} 无可用门"
                  f"（近门均已属其它封闭房间或不在墙上）")
        else:
            print(f"[F{floor_no}] 合班 {room['id']} 关联门 {len(picked)} 扇"
                  f"（只追加, 最近 {picked[0][0]:.1f}m）")
    return len(targets)
