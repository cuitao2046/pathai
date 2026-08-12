"""线段/几何基础判定（审查 B10：收敛穿墙判定多份「同源」实现）。

route_rules._segment_crosses_wall 与 render_interactive._seg_crosses_wall 原为
逐字拷贝的同源实现，现统一到本模块，三处共用 `segments_properly_cross`。
"""


def side(p, a, b):
    """叉积符号：p 相对有向线段 a->b 的位置（>0 左侧 / <0 右侧 / =0 共线）。"""
    return (b[0] - a[0]) * (p[1] - a[1]) - (b[1] - a[1]) * (p[0] - a[0])


def segments_properly_cross(p1, p2, a, b, eps=1e-9):
    """路径段 p1->p2 是否真正「穿透」墙线 a-b。

    判定：两端点位于墙线两侧(opposite sides)且交点落在线段内。
    共线/同侧(沿墙并行)不算穿墙。
    """
    dx = b[0] - a[0]
    dy = b[1] - a[1]
    s1 = side(p1, a, b)
    s2 = side(p2, a, b)
    if s1 == 0 and s2 == 0:
        return False  # 共线：沿墙，非穿透
    if s1 * s2 > 0:
        return False  # 同侧：沿墙并行，非穿透
    if abs(dx) < eps and abs(dy) < eps:
        return False  # 退化墙线
    # 异侧：求交点参数
    ex = p2[0] - p1[0]
    ey = p2[1] - p1[1]
    det = dx * ey - dy * ex
    if abs(det) < eps:
        return False
    u = (ex * (a[1] - p1[1]) - ey * (a[0] - p1[0])) / det  # 沿墙 a->b 参数
    t = (dy * (p1[0] - a[0]) - dx * (p1[1] - a[1])) / det  # 沿路径 p1->p2 参数
    return (0.0 - eps) <= t <= (1.0 + eps) and (0.0 - eps) <= u <= (1.0 + eps)
