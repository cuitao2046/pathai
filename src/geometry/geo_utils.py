# -*- coding: utf-8 -*-
"""通用几何工具：坐标变换 / 线段角度长度 / 距离 / 曲线中点 / 共线合并 / shapely 吸附。

原内嵌于 src/parsing/parse_cad_pdf.py（审查 B1）：
  pt2m / seg_len / seg_angle / norm_angle / angle_diff / seg_midpoint /
  point_to_seg_dist / bezier_mid / _point_line_distance / merge_collinear / snap

下沉到 src/geometry/ 后仅依赖 math/collections/shapely 与 src.common.constants
的米制常量，不含任何 PDF/图层/语义逻辑。
"""
import collections
import math

from shapely import snap as shp_snap

from src.common.constants import ORIGIN_X, ORIGIN_Y, SCALE


def pt2m(p):
    """PDF pt 坐标 -> 局部米制坐标（Y 翻转）"""
    return ((p[0] - ORIGIN_X) * SCALE, (ORIGIN_Y - p[1]) * SCALE)


def seg_len(a, b):
    return math.hypot(b[0] - a[0], b[1] - a[1])


def seg_angle(a, b):
    return math.atan2(b[1] - a[1], b[0] - a[0])


def norm_angle(ang):
    """归一化到 [0, pi)"""
    a = ang % math.pi
    return a


def angle_diff(a1, a2):
    d = abs(norm_angle(a1) - norm_angle(a2))
    return min(d, math.pi - d)


def seg_midpoint(a, b):
    return ((a[0] + b[0]) / 2.0, (a[1] + b[1]) / 2.0)


def point_to_seg_dist(p, a, b):
    """点到线段距离及投影参数 t"""
    ax, ay = a
    bx, by = b
    dx, dy = bx - ax, by - ay
    L2 = dx * dx + dy * dy
    if L2 == 0:
        return math.hypot(p[0] - ax, p[1] - ay), 0.0
    t = ((p[0] - ax) * dx + (p[1] - ay) * dy) / L2
    t_clamped = max(0.0, min(1.0, t))
    px, py = ax + t_clamped * dx, ay + t_clamped * dy
    return math.hypot(p[0] - px, p[1] - py), t


def bezier_mid(bz):
    """三次贝塞尔曲线中点（t=0.5，四重线性插值）。
    用于门摆弧弧中点的统一计算（审查 E5：收敛 detect_doors/parse_floor 两处重复实现）。"""
    p1, p2, p3, p4 = bz
    def lerp(a, b, t=0.5):
        return (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t)
    q1, q2, q3 = lerp(p1, p2), lerp(p2, p3), lerp(p3, p4)
    r1, r2 = lerp(q1, q2), lerp(q2, q3)
    return lerp(r1, r2)


def _point_line_distance(p, a, b):
    """点到**无限长直线** ab 的距离（区别于点到线段）。"""
    dx, dy = b[0] - a[0], b[1] - a[1]
    L = math.hypot(dx, dy)
    if L < 1e-12:
        return math.hypot(p[0] - a[0], p[1] - a[1])
    return abs((p[0] - a[0]) * dy - (p[1] - a[1]) * dx) / L


def merge_collinear(segs, angle_tol_deg=2.0, axis_tol=2.0, gap_tol=30.0,
                    micro_gap=6.0, short_seg=30.0, record_gaps=False):
    """
    合并共线虚线/断线线段（支持任意角度）：
    CAD 导出时常把点划线/虚线墙体打成一串带间隙的短划，直接栅格化会断线。
    按方向角分桶 -> 桶内按线位(法向坐标)分带 -> 带内沿轴向合并间隙。
    桥接规则：间隙 <= gap_tol(30pt) 即桥接（虚线间隙及门洞处墙线中断），
    门洞位置由门/窗封口线单独封闭，不受影响。
    （曾尝试按"双短线段不桥接"保留厕位门洞，但虚线墙同样是短划+大间隙，
       会大面积断墙，已回滚。）

    record_gaps=True 时同步返回桥接的"墙缝"(band_center, angle_rad, left_pt, right_pt, gap)，
    用于无摆弧开口（DK 洞口）几何检测；左右端点为原始分段端点（端点外侧），不是合并后端点。
    """
    buckets = collections.defaultdict(list)
    for a, b in segs:
        ang = math.degrees(norm_angle(seg_angle(a, b)))  # [0, 180)
        key = (round(ang / angle_tol_deg) * angle_tol_deg) % 180.0
        buckets[key].append((a, b))

    out = []
    gaps = [] if record_gaps else None
    for key, group in buckets.items():
        ang = math.radians(key)
        ux, uy = math.cos(ang), math.sin(ang)
        nx, ny = -uy, ux
        band_map = {}
        for a, b in group:
            pos = ((a[0] + b[0]) / 2) * nx + ((a[1] + b[1]) / 2) * ny
            lo, hi = sorted((a[0] * ux + a[1] * uy, b[0] * ux + b[1] * uy))
            bk = None
            for k in band_map:
                if abs(k - pos) <= axis_tol:
                    bk = k
                    break
            if bk is None:
                bk = pos
                band_map[bk] = []
            band_map[bk].append((lo, hi, a, b))
        for pos, ivs in band_map.items():
            ivs.sort(key=lambda t: t[0])
            cur_lo, cur_hi, cur_a, cur_b = ivs[0]
            for lo, hi, a, b in ivs[1:]:
                gap = lo - cur_hi
                if gap <= gap_tol:
                    if record_gaps and 0 < gap <= gap_tol:
                        # 记录被桥接的墙缝：左右端点 = 两侧分段的外侧端点
                        L_pt = (cur_lo * ux + pos * nx, cur_lo * uy + pos * ny)
                        R_pt = (hi * ux + pos * nx, hi * uy + pos * ny)
                        center = ((cur_hi + lo) / 2 * ux + pos * nx,
                                  (cur_hi + lo) / 2 * uy + pos * ny)
                        gaps.append({
                            "center": center,
                            "axis_rad": math.atan2(uy, ux),
                            "left": L_pt,
                            "right": R_pt,
                            "gap": gap,
                            "left_len": cur_hi - cur_lo,
                            "right_len": hi - lo,
                        })
                    cur_hi = max(cur_hi, hi)
                    cur_b = b
                else:
                    out.append(((cur_lo * ux + pos * nx, cur_lo * uy + pos * ny),
                                (cur_hi * ux + pos * nx, cur_hi * uy + pos * ny)))
                    cur_lo, cur_hi, cur_a, cur_b = lo, hi, a, b
            out.append(((cur_lo * ux + pos * nx, cur_lo * uy + pos * ny),
                        (cur_hi * ux + pos * nx, cur_hi * uy + pos * ny)))
    if record_gaps:
        return out, gaps
    return out


def snap(geom, target, tol):
    try:
        return shp_snap(geom, target, tol)
    except Exception:
        return geom
