# -*- coding: utf-8 -*-
"""距离舍入契约：与前端 `Math.round` 同口径（A2 前端/后端对拍）。

后端 RouteGraph 报出的路径距离、前端 Dijkstra 显示与语音播报的距离必须是
**同一个数**。Python 内置 round() 对 float 做「就近取偶」，在 `.xx5` 半分位
会与 JS 的「半数向上」分叉：28.415 → 后端 28.41 / 前端 28.42（实测差 0.01m）。

本模块不依赖 shapely 等重库，供 src、backend 与回归测试共用。
"""
import math


def round_distance(value: float, digits: int = 2) -> float:
    """按 JS `Math.round(v*10^d)/10^d` 舍入（非负数下等价于 floor(v*10^d+0.5)）。

    两者计算的是同一个 IEEE-754 乘积，只有舍入规则不同；因此复刻 Math.round
    即可保证与前端逐位一致，无需放宽对拍容差。
    """
    factor = 10 ** digits
    return math.floor(value * factor + 0.5) / factor
