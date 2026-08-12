# -*- coding: utf-8 -*-
"""建筑外轮廓纯 Python 计算（无 shapely/numpy 依赖）。

原内嵌于 src/rendering/render_interactive.py（审查 B2）：
  房间/楼梯/电梯/柱 闭合多边形 栅格化填充 + 墙体段细带 → 膨胀弥合门洞缺口
  → 外部泛洪取补集得建筑实体 → 逐连通块 Moore 追踪最外轮廓 → Douglas-Peucker 简化

下沉到 src/geometry/ 后 parsing（parse_cad_pdf.py）与 rendering（render_interactive.py）
共同引用，解除 parsing -> rendering 的依赖方向倒挂。
"""
import math


def _rasterize_rings(rings, minx, miny, cell, nx, ny):
    """扫描线填充若干多边形环，返回 inside 网格。"""
    inside = [[False] * nx for _ in range(ny)]
    gx_ = lambda x: int((x - minx) / cell)
    gy_ = lambda y: int((y - miny) / cell)
    for ring in rings:
        if len(ring) < 3:
            continue
        ys = [p[1] for p in ring]
        y0, y1 = min(ys), max(ys)
        gy0 = max(0, gy_(y0))
        gy1 = min(ny - 1, gy_(y1))
        for gy in range(gy0, gy1 + 1):
            wy = miny + (gy + 0.5) * cell
            xi = []
            n = len(ring)
            for i in range(n):
                x1, y1_ = ring[i]
                x2, y2_ = ring[(i + 1) % n]
                if (y1_ <= wy < y2_) or (y2_ <= wy < y1_):
                    t = (wy - y1_) / (y2_ - y1_)
                    xi.append(x1 + t * (x2 - x1))
            xi.sort()
            for k in range(0, len(xi) - 1, 2):
                a = gx_(xi[k])
                b = gx_(xi[k + 1])
                for gxx in range(max(0, a), min(nx - 1, b) + 1):
                    inside[gy][gxx] = True
    return inside


def _dist_transform(inside, nx, ny):
    """到最近 True 单元的 Chamfer 距离（正交=1, 对角=√2），用于 O(n) 膨胀/腐蚀。"""
    INF = 1e9
    d = [[0.0 if inside[y][x] else INF for x in range(nx)] for y in range(ny)]
    s2 = 1.41421356
    for y in range(ny):
        for x in range(nx):
            if x > 0:
                d[y][x] = min(d[y][x], d[y][x - 1] + 1.0)
            if y > 0:
                d[y][x] = min(d[y][x], d[y - 1][x] + 1.0)
            if x > 0 and y > 0:
                d[y][x] = min(d[y][x], d[y - 1][x - 1] + s2)
        for x in range(nx - 1, -1, -1):
            if y > 0 and x < nx - 1:
                d[y][x] = min(d[y][x], d[y - 1][x + 1] + s2)
    for y in range(ny - 1, -1, -1):
        for x in range(nx - 1, -1, -1):
            if x < nx - 1:
                d[y][x] = min(d[y][x], d[y][x + 1] + 1.0)
            if y < ny - 1:
                d[y][x] = min(d[y][x], d[y + 1][x] + 1.0)
            if x < nx - 1 and y < ny - 1:
                d[y][x] = min(d[y][x], d[y + 1][x + 1] + s2)
        for x in range(nx):
            if y < ny - 1 and x > 0:
                d[y][x] = min(d[y][x], d[y + 1][x - 1] + s2)
    return d


def _dilate(inside, nx, ny, r_cells):
    d = _dist_transform(inside, nx, ny)
    return [[d[y][x] <= r_cells for x in range(nx)] for y in range(ny)]


def _trace_contour(inside, nx, ny, minx, miny, cell, tol, start=None):
    """Moore 邻域追踪 inside 区域外轮廓，返回米制闭合多边形。沿内部单元前进。"""
    def isin(x, y):
        return 0 <= x < nx and 0 <= y < ny and inside[y][x]

    if start is None:
        for y in range(ny):
            for x in range(nx):
                if inside[y][x]:
                    if (x == 0 or y == 0 or x == nx - 1 or y == ny - 1
                            or not isin(x - 1, y) or not isin(x + 1, y)
                            or not isin(x, y - 1) or not isin(x, y + 1)):
                        start = (x, y)
                        break
            if start:
                break
    if not start:
        return []

    neigh = [(0, -1), (1, -1), (1, 0), (1, 1), (0, 1), (-1, 1), (-1, 0), (-1, -1)]
    cx, cy = start
    bx, by = cx - 1, cy
    contour = [(cx, cy)]
    steps = 0
    maxsteps = nx * ny * 4
    while steps < maxsteps:
        steps += 1
        try:
            bidx = neigh.index((bx - cx, by - cy))
        except ValueError:
            bidx = -1
        found = False
        for off in range(1, 9):
            idx = (bidx + off) % 8
            dx, dy = neigh[idx]
            nx2, ny2 = cx + dx, cy + dy
            if isin(nx2, ny2):
                bx, by = cx, cy
                cx, cy = nx2, ny2
                if (cx, cy) == start:
                    return _simplify([(minx + (x + 0.5) * cell, miny + (y + 0.5) * cell)
                                      for x, y in contour], tol)
                contour.append((cx, cy))
                found = True
                break
        if not found:
            break
    return _simplify([(minx + (x + 0.5) * cell, miny + (y + 0.5) * cell)
                     for x, y in contour], tol)


def _trace_contour_from(inside, start, nx, ny, minx, miny, cell, tol):
    return _trace_contour(inside, nx, ny, minx, miny, cell, tol, start=start)


def _simplify(pts, tol):
    if len(pts) < 3:
        return pts

    def dp(start, end):
        dmax, idx = 0.0, 0
        x1, y1 = pts[start]
        x2, y2 = pts[end]
        dx, dy = x2 - x1, y2 - y1
        denom = math.hypot(dx, dy) or 1e-9
        for i in range(start + 1, end):
            x0, y0 = pts[i]
            d = abs(dy * x0 - dx * y0 + x2 * y1 - y2 * x1) / denom
            if d > dmax:
                dmax, idx = d, i
        if dmax > tol:
            return dp(start, idx)[:-1] + dp(idx, end)
        return [pts[start], pts[end]]

    res = dp(0, len(pts) - 1)
    if res[0] != res[-1]:
        res.append(res[0])
    return res


def _area(pts):
    return abs(sum(pts[i][0] * pts[(i + 1) % len(pts)][1] - pts[(i + 1) % len(pts)][0] * pts[i][1]
                  for i in range(len(pts)))) / 2


def building_outline(floor_geom, cell=0.1, wall_hw=1, close_r=14,
                     fill_keys=("rooms", "stairs", "elevators", "columns")):
    """计算建筑外轮廓（实线外部边缘）。

    综合 墙段 + 各闭合空间多边形栅格化 → 膨胀弥合门洞缺口 → 边界泛洪得 outside
    → building=非 outside → 逐连通块追踪最外轮廓（一栋楼可有多个独立楼体）。
    返回若干米制闭合多边形（已含闭合末点）。
    """
    rings = []
    for k in fill_keys:
        for o in floor_geom.get(k, []):
            c = o["geometry"]["coordinates"]
            if c and len(c[0]) >= 3:
                rings.append(c[0])
    segs = [w["geometry"]["coordinates"] for w in floor_geom.get("walls", [])]
    if not rings and not segs:
        return []

    allpts = [p for r in rings for p in r] + [p for s in segs for p in s]
    minx = min(p[0] for p in allpts); maxx = max(p[0] for p in allpts)
    miny = min(p[1] for p in allpts); maxy = max(p[1] for p in allpts)
    pad = max(cell * 4, 1.0)
    minx -= pad; maxx += pad; miny -= pad; maxy += pad
    nx = int((maxx - minx) / cell) + 1
    ny = int((maxy - miny) / cell) + 1

    inside = _rasterize_rings(rings, minx, miny, cell, nx, ny)
    gx_ = lambda x: int((x - minx) / cell)
    gy_ = lambda y: int((y - miny) / cell)
    for (x1, y1), (x2, y2) in segs:
        d = math.hypot(x2 - x1, y2 - y1)
        n = max(1, int(d / cell * 2))
        for t in range(n + 1):
            tt = t / n
            px = x1 + (x2 - x1) * tt
            py = y1 + (y2 - y1) * tt
            gx = gx_(px); gy = gy_(py)
            for dx in range(-wall_hw, wall_hw + 1):
                for dy in range(-wall_hw, wall_hw + 1):
                    xx, yy = gx + dx, gy + dy
                    if 0 <= xx < nx and 0 <= yy < ny:
                        inside[yy][xx] = True

    if close_r > 0:
        # 纯膨胀：弥合门洞缺口（闭运算的腐蚀会把膨胀填出的窄桥重新蚀断，故仅膨胀）。
        inside = _dilate(inside, nx, ny, close_r)

    # 外部泛洪：从网格边界出发，穿过所有非建筑单元标记 outside
    outside = [[False] * nx for _ in range(ny)]
    stack = []
    for x in range(nx):
        for y in (0, ny - 1):
            if not inside[y][x]:
                outside[y][x] = True; stack.append((x, y))
    for y in range(ny):
        for x in (0, nx - 1):
            if not inside[y][x] and not outside[y][x]:
                outside[y][x] = True; stack.append((x, y))
    while stack:
        x, y = stack.pop()
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            xx, yy = x + dx, y + dy
            if 0 <= xx < nx and 0 <= yy < ny and not outside[yy][xx] and not inside[yy][xx]:
                outside[yy][xx] = True; stack.append((xx, yy))

    building = [[not outside[y][x] for x in range(nx)] for y in range(ny)]

    # 逐连通块追踪各自最外轮廓
    visited = [[False] * nx for _ in range(ny)]
    polys = []
    for sy in range(ny):
        for sx in range(nx):
            if not building[sy][sx] or visited[sy][sx]:
                continue
            comp = []
            stk = [(sx, sy)]
            visited[sy][sx] = True
            while stk:
                x, y = stk.pop()
                comp.append((x, y))
                for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                    xx, yy = x + dx, y + dy
                    if 0 <= xx < nx and 0 <= yy < ny and building[yy][xx] and not visited[yy][xx]:
                        visited[yy][xx] = True
                        stk.append((xx, yy))
            start = None
            for (x, y) in comp:
                if any(not building[y + dy][x + dx]
                       for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1))
                       if 0 <= x + dx < nx and 0 <= y + dy < ny):
                    start = (x, y)
                    break
            if start is None:
                start = comp[0]
            contour = _trace_contour_from(building, start, nx, ny, minx, miny, cell, cell * 1.5)
            if len(contour) >= 3:
                polys.append(contour)
    return polys
