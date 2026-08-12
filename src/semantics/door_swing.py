# -*- coding: utf-8 -*-
"""门开向/铰链侧属性推导（指南 §3.2）。

原内嵌于 src/parsing/parse_cad_pdf.py（审查 B1）：
  DOOR_PROBE_STEPS_M / door_swing_attributes

从门摆弧几何推导 openDirection / hingeSide / swingIntoRoom，
依赖米制坐标（Y 翻转右手系）下的左右手性判定。
"""
import math

from shapely.geometry import Point

from src.common.constants import SCALE
from src.geometry.geo_utils import pt2m

# 门开向探测：沿摆弧鼓出侧法向逐级外推，判断门扇扫入哪个房间
DOOR_PROBE_STEPS_M = (0.5, 0.9, 1.4)


def door_swing_attributes(dr, rooms_by_id, public_types):
    """从摆弧几何推导门的开向与铰链侧（指南 §3.2「开向：内开/外开/左开/右开」）。

    几何依据：
      - hinge 为门轴（落在墙线上），tip 为闭门端（门扇关闭时沿墙方向的端点），
        故 hinge→tip 即墙线方向，radius=门宽；
      - arc_mid 为摆弧中点，必然鼓向门扇扫过的一侧 = 门的开向侧。

    判定：
      - openDirection：沿鼓出侧法向从门中心外推，落入的房间若为公共通行空间
        （走廊/门厅等）则为「外开」outward，否则为「内开」inward；
      - hingeSide：站在开向侧面向门洞，铰链在观察者左手边为「左开」left。
        左右手性依赖坐标系朝向，故在**米制坐标**（Y 已翻转为向上的右手系）下计算，
        直接用 pt 坐标会左右颠倒。
    """
    out = {"openDirection": None, "hingeSide": None, "swingIntoRoom": None,
           "swingIntoRoomType": None, "openDirectionSource": None}
    if dr.get("kind") == "opening":
        return out                      # 无门扇洞口，不存在开向
    axis, arc_mid = dr.get("axis"), dr.get("arc_mid")
    if not axis or not arc_mid:
        return out
    hinge_pt, tip_pt = axis
    cx_pt, cy_pt = dr["center"]

    # 墙线方向（pt）
    ux, uy = tip_pt[0] - hinge_pt[0], tip_pt[1] - hinge_pt[1]
    L = math.hypot(ux, uy)
    if L < 1e-9:
        return out
    ux, uy = ux / L, uy / L
    # 摆弧中点相对墙线的侧向分量 → 决定鼓出侧法向
    side = (arc_mid[0] - cx_pt) * (-uy) + (arc_mid[1] - cy_pt) * ux
    if abs(side) < 1e-9:
        return out
    sgn = 1.0 if side > 0 else -1.0
    nx_pt, ny_pt = -uy * sgn, ux * sgn   # pt 空间中指向开门侧的单位法向

    # --- openDirection：沿法向外推，找门扇扫入的房间 ---
    # 门的 rooms 常只记录一侧（另一侧走廊未必成为归属房间），故先查门自身
    # 关联的房间，再回退到全库房间；两者都落空时用「反向排除」推断。
    own_ids = [rid for rid in (dr.get("rooms") or []) if rid in rooms_by_id]
    other_ids = [rid for rid in rooms_by_id if rid not in set(own_ids)]
    for step_m in DOOR_PROBE_STEPS_M:
        d_pt = step_m / SCALE
        probe = Point(cx_pt + nx_pt * d_pt, cy_pt + ny_pt * d_pt)
        for rid in own_ids + other_ids:
            poly = rooms_by_id[rid].get("polygon_pt")
            if poly is not None and poly.contains(probe):
                rtype = rooms_by_id[rid].get("roomType")
                out["swingIntoRoom"] = rid
                out["swingIntoRoomType"] = rtype
                out["openDirection"] = ("outward" if rtype in public_types
                                        else "inward")
                out["openDirectionSource"] = "polygon"
                break
        if out["openDirection"]:
            break

    # 反向排除：门只关联到一个房间且门扇明显不扫入它，则必然扫向对侧。
    # 对侧若是该房间之外的通行空间即为外开；若已知侧本身是走廊则反之。
    if out["openDirection"] is None and len(own_ids) == 1:
        known_type = rooms_by_id[own_ids[0]].get("roomType")
        out["openDirection"] = ("inward" if known_type in public_types
                                else "outward")
        out["openDirectionSource"] = "inferred_opposite"

    # --- hingeSide：在米制右手系下判定左右开 ---
    hx, hy = pt2m(hinge_pt)
    cx_m, cy_m = pt2m((cx_pt, cy_pt))
    mx, my = pt2m((cx_pt + nx_pt, cy_pt + ny_pt))
    nx_m, ny_m = mx - cx_m, my - cy_m
    nl = math.hypot(nx_m, ny_m)
    if nl > 1e-12:
        nx_m, ny_m = nx_m / nl, ny_m / nl
        # 观察者站在开向侧面向门：视线 d=-n，其左手方向 = (n_y, -n_x)
        dot_left = (hx - cx_m) * ny_m + (hy - cy_m) * (-nx_m)
        out["hingeSide"] = "left" if dot_left > 0 else "right"
    return out
