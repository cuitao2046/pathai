# -*- coding: utf-8 -*-
"""墙线栅格化：全部墙段 + 门窗封口线 -> 二值墙图（闭运算密封断口）。

原内嵌于 src/parsing/parse_cad_pdf.py（审查 B1）：rasterize_walls。
cv2/numpy 惰性导入（函数体内），import 本模块不引入重依赖。
"""
from src.geometry.geo_utils import seg_len

RENDER_ZOOM = 3.0       # 结构层渲染放大倍数 (px/pt)
WALL_EXT_PT = 6.0       # 墙线端点外延（桥接 T 型接头/窗洞收口缝隙）


def rasterize_walls(all_segs, closures):
    """
    全部墙体线段(结构,2px) + 门/窗封口线(3px) -> 二值墙图。
    流程：原始绘制(端点外延) -> 画门窗封口线 -> 闭运算密封墙线断口。
    （注：该图真实墙也是 2px 单线，开运算去薄墙会连真墙一起溶掉，不可用）
    返回 (walls_uint8, minx, miny, W, H, Z)；
    px->pt: (px/Z+minx, py/Z+miny)
    """
    import cv2
    import numpy as np

    Z = RENDER_ZOOM
    segs = list(all_segs) + list(closures)
    xs = [p[0] for s in segs for p in s]
    ys = [p[1] for s in segs for p in s]
    margin = 20.0
    minx, miny = min(xs) - margin, min(ys) - margin
    W = int((max(xs) - min(xs) + 2 * margin) * Z) + 1
    H = int((max(ys) - min(ys) + 2 * margin) * Z) + 1

    def to_px(p):
        return (int(round((p[0] - minx) * Z)), int(round((p[1] - miny) * Z)))

    def extend(a, b, ext):
        L = seg_len(a, b)
        if L < 1e-6:
            return a, b
        ux, uy = (b[0] - a[0]) / L, (b[1] - a[1]) / L
        return ((a[0] - ux * ext, a[1] - uy * ext),
                (b[0] + ux * ext, b[1] + uy * ext))

    walls = np.zeros((H, W), np.uint8)
    for a, b in all_segs:
        # 端点外延：桥接墙线在 T 型接头/窗洞收口处的 2~6pt 缝隙
        ea, eb = extend(a, b, WALL_EXT_PT)
        cv2.line(walls, to_px(ea), to_px(eb), 255, thickness=2)
    # 门/窗洞口封口线（含端头盖帽）
    for a, b in closures:
        cv2.line(walls, to_px(a), to_px(b), 255, thickness=3)
    # 闭运算桥接 CAD 转角/T 型接头细缝（9px ≈ 3pt ≈ 0.19m，
    # 远小于门洞宽度 ≥14pt，不会误封闭门洞）
    walls = cv2.morphologyEx(walls, cv2.MORPH_CLOSE,
                             cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (9, 9)))
    # 方向性闭运算：桥接轴对齐墙线的端部缝隙（17px ≈ 5.7pt），
    # 对门窗洞口（≥30px）无影响
    walls = cv2.morphologyEx(walls, cv2.MORPH_CLOSE,
                             cv2.getStructuringElement(cv2.MORPH_RECT, (17, 1)))
    walls = cv2.morphologyEx(walls, cv2.MORPH_CLOSE,
                             cv2.getStructuringElement(cv2.MORPH_RECT, (1, 17)))
    return walls, minx, miny, W, H, Z
