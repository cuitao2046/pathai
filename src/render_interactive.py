# -*- coding: utf-8 -*-
"""交互式网页版楼层渲染 —— 读取 parse_cad_pdf.py 生成的 v9 GeoJSON，
生成一个自包含的 HTML（SVG + 原生 JS），支持：
  - 缩放 / 平移（滚轮 + 拖拽 + 按钮）
  - 图层开关（房间/墙体/窗/楼梯/电梯/柱/各类门/拓扑节点·边/跨层/无障碍）
  - 悬停提示（tooltip）与点击查看详情（右侧面板）
  - 楼层快速跳转（1F / 2F）
  - 点击拓扑节点高亮其相连边

参照 render_v7.py 的坐标变换与图层组织方式，但面向 v9 schema
（拓扑节点类型 room/doorway/intersection/facility/facility_entrance；
门类型 swing/fire/opening；边用 from/to 节点 id 引用）。

用法:
    python render_interactive.py [geojson_path]
默认输入 result/school_building_01_map_v9.geojson，输出 result/floor_layout_v9_interactive.html
"""
import collections
import json
import math
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
GEO_IN = str(BASE_DIR / "result" / "school_building_01_map_v9.geojson")
HTML_OUT = str(BASE_DIR / "result" / "floor_layout_v9_interactive.html")

SCALE = 7.0          # 1m = 7px
MARGIN_X = 50
MARGIN_Y = 30
FLOOR_TITLE_H = 46

# 房间类型配色（v9 roomType 关键词）
ROOM_COLORS = {
    "classroom": "#FFF9C4", "office": "#D7CCC8", "meeting": "#F8BBD0",
    "toilet": "#B2DFDB", "corridor": "#F5F5F5", "lobby": "#FFF3E0",
    "staircase": "none", "elevator_hall": "#F8BBD0", "storage": "#CFD8DC",
    "equipment": "#B0BEC5", "medical": "#FFEBEE", "lab": "#B3E5FC",
    "reception": "#FCE4EC", "shaft": "#ECEFF1", "atrium": "#FAFAFA",
    "library": "#DCEDC8", "activity": "#E1F5FE", "entrance": "#C8E6C9",
    "accessible_entrance": "#BBDEFB", "room": "#FAFAFA", "other": "#FAFAFA",
    "elevator_lobby": "#FFE0B2", "stair_lobby": "#D7CCC8",
}
DOOR_COLORS = {"swing": "#2196F3", "fire": "#FF5722", "opening": "#1E8449"}
# 门类型中文名（与 topology.py 的 doorway 节点 label 保持一致）
DOOR_TYPE_CN = {"swing": "普通门", "fire": "防火门", "opening": "门洞"}
NODE_COLORS = {
    "room": "#E67E22", "doorway": "#C0392B", "intersection": "#27AE60",
    "facility_entrance": "#2980B9",
}
FACILITY_COLORS = {"staircase": "#8E44AD", "elevator": "#16A085"}

# 建筑外轮廓：面积过滤阈值（m²）。小于该值的连通块视为家具/孤立柱簇噪声，不绘制。
OUTLINE_MIN_AREA = 100.0


# ----------------------------------------------------------------------------
# 纯 Python 建筑外轮廓计算（无 shapely/numpy 依赖）：
#   房间/楼梯/电梯/柱 闭合多边形 栅格化填充 + 墙体段细带 → 膨胀弥合门洞缺口
#   → 外部泛洪取补集得建筑实体 → 逐连通块 Moore 追踪最外轮廓 → Douglas-Peucker 简化
# ----------------------------------------------------------------------------
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


def fmt(v):
    return f"{v:.1f}"


# 文字标签包围盒启发式过滤阈值（CAD PDF 中部分文字标签的矢量矩形被
# 误识别为房间多边形，会与真实房间重叠。典型特征：面积小 + 形状窄长。）
LABEL_BBOX_MAX_AREA = 6.0     # m²，小于此值才有嫌疑
LABEL_BBOX_MIN_ASPECT = 1.5   # 长边/短边，超过此值判定为文字框


def _is_label_bbox(ring):
    """判定闭合多边形是否疑似文字标签的包围盒（小面积 + 窄长形状）。"""
    xs = [p[0] for p in ring[:-1]]
    ys = [p[1] for p in ring[:-1]]
    if len(xs) < 3:
        return True
    w = max(xs) - min(xs)
    h = max(ys) - min(ys)
    if w <= 0 or h <= 0:
        return True
    a = 0.0
    for j in range(len(ring) - 1):
        a += ring[j][0] * ring[j + 1][1] - ring[j + 1][0] * ring[j][1]
    a = abs(a) / 2.0
    if a >= LABEL_BBOX_MAX_AREA:
        return False
    aspect = max(w, h) / min(w, h)
    return aspect >= LABEL_BBOX_MIN_ASPECT


def info_attr(d):
    """把任意可序列化 dict 编码为 data-info 属性（JS 端 JSON.parse）。"""
    s = json.dumps(d, ensure_ascii=False).replace("'", "\\'")
    return "data-info='" + s + "'"


def build_node_lookup(geo_json):
    lookup = {}
    for fk, fd in geo_json["floors"].items():
        for n in fd.get("topology", {}).get("nodes", []):
            lookup[n["id"]] = {"floor": int(fk), "coordinates": tuple(n["coordinates"])}
    return lookup


def main():
    geo = json.load(open(GEO_IN, encoding="utf-8"))
    node_lookup = build_node_lookup(geo)

    # ---- 全局范围（所有楼层共用变换，便于跨层对齐） ----
    min_x, min_y = float("inf"), float("inf")
    max_x, max_y = float("-inf"), float("-inf")
    for fk in geo["floors"]:
        for room in geo["floors"][fk]["geometry"].get("rooms", []):
            for p in room["geometry"]["coordinates"][0]:
                min_x, min_y = min(min_x, p[0]), min(min_y, p[1])
                max_x, max_y = max(max_x, p[0]), max(max_y, p[1])

    svw = int((max_x - min_x) * SCALE + MARGIN_X * 2)
    svh_per_floor = int((max_y - min_y) * SCALE + MARGIN_Y * 2 + FLOOR_TITLE_H)
    svh = svh_per_floor * len(geo["floors"]) + 20
    ox, oy = min_x, max_y

    sorted_floors = sorted(geo["floors"].keys(), key=lambda x: int(x))
    cf = geo.get("crossFloorEdges", [])
    n_cf_stair = sum(1 for e in cf if e.get("type") == "staircase")
    n_cf_elev = sum(1 for e in cf if e.get("type") == "elevator")
    # 已建立跨层连接的井道编号（用于在楼梯/电梯上标注"是否跨层连通"）
    cf_codes = {e.get("code") for e in cf if e.get("code")}

    parts = []
    parts.append(f'''<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>室内盲导 · 交互式楼层可视化 v9</title>
<style>
body {{ font-family: 'Microsoft YaHei', 'PingFang SC', sans-serif; background: #f8f9fa; margin: 0; padding: 16px; color: #333; }}
.header {{ max-width: 1280px; margin: 0 auto 12px; }}
.header h2 {{ margin: 0 0 4px; font-size: 20px; }}
.header .meta {{ color: #888; font-size: 12px; line-height: 1.6; }}
.header .meta .tag {{ display: inline-block; background: #E3F2FD; color: #1565C0; padding: 1px 8px; border-radius: 3px; font-weight: bold; }}
#app {{ max-width: 1280px; margin: 0 auto; display: flex; gap: 12px; align-items: flex-start; }}
#left {{ flex: 1 1 auto; min-width: 0; display: flex; flex-direction: column; gap: 10px; }}
#svg-container {{ position: relative; border: 1px solid #ddd; background: #fefefe; overflow: hidden; height: calc(100vh - 240px); min-height: 480px; border-radius: 6px; }}
#svg-wrapper {{ width: 100%; height: 100%; overflow: hidden; cursor: grab; }}
#svg-wrapper.grabbing {{ cursor: grabbing; }}
svg {{ display: block; background: #fff; }}
.layer_room polygon {{ opacity: 0.55; cursor: pointer; }}
.layer_wall path {{ stroke: #333; stroke-width: 0.8; fill: none; stroke-linecap: round; }}
.layer_building_outline polygon {{ fill: none; stroke: #222; stroke-width: 1.4; stroke-linejoin: round; stroke-linecap: round; pointer-events: none; }}
.layer_window path {{ stroke: #81D4FA; stroke-width: 0.9; fill: none; stroke-dasharray: 4,2; }}
.layer_stairs polygon {{ opacity: 0.6; cursor: pointer; }}
.layer_elevator polygon {{ opacity: 0.6; cursor: pointer; }}
.layer_column polygon {{ fill: #B0BEC5; stroke: #78909C; stroke-width: 0.3; opacity: 0.7; }}
.layer_walkable polygon {{ fill: #A5D6A7; stroke: #43A047; stroke-width: 0.4; opacity: 0.35; pointer-events: none; }}
.layer_door circle, .layer_door polygon, .layer_door rect {{ cursor: pointer; }}
.layer_door_swing *, .layer_door_opening *, .layer_door_fire * {{ cursor: pointer; }}
.layer_topo_node *, .layer_topo_edge path {{ cursor: pointer; }}
.layer_topo_edge path {{ stroke: #27AE60; stroke-width: 0.5; fill: none; opacity: 0.45; stroke-dasharray: 3,2; }}
.layer_risk * {{ cursor: pointer; }}
.layer_ramp *, .layer_tactile *, .layer_material * {{ cursor: pointer; }}
.layer_crossfloor path {{ stroke-width: 1.6; fill: none; stroke-dasharray: 6,4; opacity: 0.65; cursor: pointer; }}
text {{ font-family: 'Microsoft YaHei', 'PingFang SC', sans-serif; pointer-events: none; }}
.selected {{ stroke: #FFC107 !important; stroke-width: 2.4 !important; }}
/* 点击拓扑节点时被选中节点 / 相连边高亮（直接作用于几何图形，确保可见） */
.layer_topo_node.selected circle, .layer_topo_node.selected rect, .layer_topo_node.selected polygon {{ stroke: #FFC107 !important; stroke-width: 2.6 !important; }}
.layer_topo_edge.selected path {{ stroke: #FFC107 !important; stroke-width: 2.2 !important; opacity: 0.95 !important; stroke-dasharray: none !important; }}
/* 点击拓扑节点时直接可达（邻居）节点的高亮，用青色与选中节点区分 */
.layer_topo_node.neighbor circle, .layer_topo_node.neighbor rect, .layer_topo_node.neighbor polygon {{ stroke: #00BCD4 !important; stroke-width: 2.6 !important; }}
.zoom-controls {{ position: absolute; top: 10px; right: 10px; display: flex; flex-direction: column; gap: 4px; z-index: 10; }}
.zoom-btn {{ width: 34px; height: 34px; border: 1px solid #ccc; background: #fff; border-radius: 4px; cursor: pointer; font-size: 17px; display: flex; align-items: center; justify-content: center; box-shadow: 0 1px 3px rgba(0,0,0,0.1); }}
.zoom-btn:hover {{ background: #f0f0f0; }}
.zoom-info {{ position: absolute; bottom: 10px; right: 10px; background: rgba(255,255,255,0.9); padding: 4px 8px; border-radius: 4px; font-size: 12px; color: #666; z-index: 10; border: 1px solid #ddd; }}
#tooltip {{ position: absolute; pointer-events: none; background: rgba(33,33,33,0.92); color: #fff; padding: 6px 9px; border-radius: 5px; font-size: 12px; line-height: 1.5; z-index: 30; display: none; max-width: 280px; box-shadow: 0 2px 8px rgba(0,0,0,0.25); }}
#floor-jump {{ position: absolute; top: 10px; left: 10px; z-index: 10; display: flex; gap: 4px; }}
.floor-btn {{ border: 1px solid #ccc; background: #fff; border-radius: 4px; cursor: pointer; font-size: 12px; padding: 4px 10px; box-shadow: 0 1px 3px rgba(0,0,0,0.1); }}
.floor-btn.active {{ background: #1565C0; color: #fff; border-color: #1565C0; }}
.layer-controls {{ background: #fff; padding: 8px 12px; border-radius: 6px; border: 1px solid #e0e0e0; display: flex; flex-wrap: wrap; gap: 5px 12px; align-items: center; font-size: 12px; }}
.layer-controls b {{ margin-right: 2px; }}
.layer-controls label {{ cursor: pointer; white-space: nowrap; user-select: none; }}
.layer-controls label input {{ margin-right: 3px; }}
.layer-controls .bulk-btn {{ margin-left: 8px; padding: 3px 12px; border: 1px solid #1565C0; background: #fff; color: #1565C0; border-radius: 3px; cursor: pointer; font-size: 12px; font-weight: bold; }}
.layer-controls .bulk-btn:hover {{ background: #E3F2FD; }}
.layer-controls .bulk-btn.primary {{ background: #1565C0; color: #fff; }}
.layer-controls .bulk-btn.primary:hover {{ background: #0D47A1; }}
#legend-panel {{ width: 210px; flex: 0 0 210px; background: #fff; border: 1px solid #e0e0e0; border-radius: 6px; padding: 12px 14px; font-size: 12px; max-height: calc(100vh - 200px); overflow-y: auto; align-self: flex-start; position: sticky; top: 12px; }}
#legend-panel h4 {{ margin: 0 0 8px; font-size: 13px; border-bottom: 1px solid #eee; padding-bottom: 4px; }}
.lg-sec {{ margin-bottom: 8px; }}
.lg-title {{ font-weight: bold; color: #555; margin-bottom: 3px; font-size: 11px; }}
.lg-item {{ display: flex; align-items: center; margin: 2px 0; line-height: 1.5; font-size: 11px; }}
.lg-sw {{ width: 14px; height: 14px; margin-right: 6px; flex-shrink: 0; border-radius: 2px; border: 1px solid #ccc; }}
.lg-sw.line {{ background: transparent; border: none; position: relative; }}
.lg-sw.line::before {{ content: ''; position: absolute; top: 50%; left: 0; right: 0; height: 2px; transform: translateY(-50%); }}
#detail {{ margin-top: 12px; background: #fff; border: 1px solid #e0e0e0; border-radius: 6px; padding: 12px 14px; font-size: 13px; }}
#detail h4 {{ margin: 0 0 6px; font-size: 14px; }}
#detail .row {{ display: flex; justify-content: space-between; padding: 2px 0; border-bottom: 1px dashed #eee; }}
#detail .row span:first-child {{ color: #888; }}
#detail .badge {{ display: inline-block; padding: 1px 7px; border-radius: 3px; font-size: 11px; font-weight: bold; }}
.badge.ok {{ background: #E8F5E9; color: #2E7D32; }}
.badge.no {{ background: #FFEBEE; color: #C62828; }}
</style></head><body>
<div class="header">
  <h2>初中学部1#教学楼 · 交互式楼层图 <span class="tag">v9</span></h2>
  <p class="meta">
    坐标系: <b>米制局部坐标系</b>（x向右 y向上）&nbsp;|&nbsp; 缩放: 1m = {SCALE:.0f}px &nbsp;|&nbsp;
    范围: x∈[{min_x:.1f}, {max_x:.1f}]m, y∈[{min_y:.1f}, {max_y:.1f}]m &nbsp;|&nbsp;
    建筑尺寸: ~{max_x-min_x:.0f}m × {max_y-min_y:.0f}m
  </p>
  <p class="meta" style="margin-top:2px">
    v9 更新: 无摆弧门洞(DK洞口)识别 · 出入口/公共空间识别 · 拓扑图按指南第五章建模（room/doorway/intersection/facility/facility_entrance 五类节点）&nbsp;|&nbsp;
    跨层连接: {len(cf)} 条（楼梯 {n_cf_stair} / 电梯 {n_cf_elev}）
  </p>
</div>
<div id="app">
<div id="left">
<div class="layer-controls" id="layerControls">
  <b>图层:</b>
  <label><input type="checkbox" checked onchange="toggleLayer('room', this.checked)"> 房间</label>
  <label><input type="checkbox" checked onchange="toggleLayer('walkable', this.checked)"> 可通行区域</label>
  <label><input type="checkbox" checked onchange="toggleLayer('wall', this.checked)"> 墙体</label>
  <label><input type="checkbox" checked onchange="toggleLayer('window', this.checked)"> 窗户</label>
  <label><input type="checkbox" checked onchange="toggleLayer('stairs', this.checked)"> 楼梯</label>
  <label><input type="checkbox" checked onchange="toggleLayer('elevator', this.checked)"> 电梯</label>
  <label><input type="checkbox" checked onchange="toggleLayer('column', this.checked)"> 柱子</label>
  <label><input type="checkbox" checked onchange="toggleLayer('building_outline', this.checked)"> 建筑外轮廓</label>
  <label><input type="checkbox" checked onchange="toggleLayer('door_swing', this.checked)"> 普通门</label>
  <label><input type="checkbox" checked onchange="toggleLayer('door_opening', this.checked)"> 门洞</label>
  <label><input type="checkbox" checked onchange="toggleLayer('door_fire', this.checked)"> 防火门</label>
  <label><input type="checkbox" onchange="toggleLayer('topo_node', this.checked)"> 拓扑节点</label>
  <label><input type="checkbox" onchange="toggleLayer('topo_edge', this.checked)"> 拓扑边</label>
  <label><input type="checkbox" checked onchange="toggleLayer('crossfloor', this.checked)"> 跨层连接</label>
  <label><input type="checkbox" onchange="toggleLayer('risk', this.checked)"> 风险点</label>
  <label><input type="checkbox" onchange="toggleLayer('ramp', this.checked)"> 坡道</label>
  <label><input type="checkbox" onchange="toggleLayer('tactile', this.checked)"> 盲道</label>
  <label><input type="checkbox" onchange="toggleLayer('material', this.checked)"> 地面材质</label>
  <button class="bulk-btn primary" onclick="setAll(true)" title="一键全选所有图层">全选</button>
  <button class="bulk-btn" onclick="setAll(false)" title="一键取消所有图层">全不选</button>
  <button class="bulk-btn" onclick="exportSelectedSVG()" title="将当前勾选（所选）的图层导出为独立的 SVG 图片文件">导出所选图层 SVG</button>
</div>
<div id="svg-container">
<div id="floor-jump"></div>
<div class="zoom-controls">
  <button class="zoom-btn" onclick="zoomIn()" title="放大">+</button>
  <button class="zoom-btn" onclick="zoomOut()" title="缩小">−</button>
  <button class="zoom-btn" onclick="resetView()" title="重置">&#8634;</button>
</div>
<div id="tooltip"></div>
<div id="svg-wrapper">
<svg id="main-svg" xmlns="http://www.w3.org/2000/svg" width="{svw}" height="{svh}" style="display:block">
''')

    # ---------------- 逐层生成 SVG ----------------
    for i, fk in enumerate(sorted_floors):
        floor = int(fk)
        fd = geo["floors"][fk]
        geom = fd["geometry"]
        topo = fd.get("topology", {})
        acc = fd.get("accessibility", {})
        fbase_y = i * svh_per_floor

        def tosvg(cx, cy):
            sx = MARGIN_X + (cx - ox) * SCALE
            sy = fbase_y + FLOOR_TITLE_H + MARGIN_Y + (oy - cy) * SCALE
            return fmt(sx), fmt(sy)

        title_cn = "首层" if floor == 1 else f"{floor}层"
        n_wall = len(geom.get("walls", []))
        n_room = len(geom.get("rooms", []))
        n_door = len(geom.get("doors", []))
        _dt = collections.Counter(
            d["properties"].get("doorType", "swing") for d in geom.get("doors", []))
        n_stair = len(geom.get("stairs", []))
        n_elev = len(geom.get("elevators", []))
        n_col = len(geom.get("columns", []))
        n_win = len(geom.get("windowSegments", []))
        n_node = len(topo.get("nodes", []))
        n_edge = len(topo.get("edges", []))
        n_risk = len(acc.get("riskNodes", []))
        n_ramp = len(acc.get("ramps", []))
        n_tp = len(acc.get("tactilePaths", []))
        n_gmc = len(acc.get("groundMaterialChanges", []))

        parts.append(f'<!-- Floor {fk} -->\n')
        parts.append(
            f'<text x="20" y="{fbase_y + 26}" font-size="15" font-weight="bold" fill="#333">'
            f'{title_cn} {floor}F（v9 米制）</text>\n'
        )
        stats = (f'墙:{n_wall} 窗:{n_win} 房间:{n_room} '
                 f'门:{n_door}(普通门:{_dt.get("swing", 0)} 门洞:{_dt.get("opening", 0)} '
                 f'防火门:{_dt.get("fire", 0)}) '
                 f'楼梯:{n_stair} 电梯:{n_elev} 柱:{n_col} '
                 f'拓扑节点:{n_node} 边:{n_edge}')
        parts.append(
            f'<text x="200" y="{fbase_y + 26}" font-size="9" fill="#999">{stats}</text>\n'
        )

        # 1. 可通行区域（Walkable Polygon，T1：公共空间扣除柱子/井道/墙体障碍物）
        # 画在最底层：房间/前室颜色在其上，避免绿色覆盖电梯/楼梯前室等类型色
        n_walk = 0
        for r in geom.get("rooms", []):
            wp = r["properties"].get("walkablePolygon")
            if not wp:
                continue
            n_walk += 1
            for rings in wp["coordinates"]:
                for ri, ring in enumerate(rings):
                    pts = " ".join(f"{tosvg(x, y)[0]},{tosvg(x, y)[1]}"
                                   for x, y in ring)
                    # 外环浅绿填充；内环（柱洞）仅描边不填充
                    fill = "none" if ri > 0 else "#A5D6A7"
                    parts.append(
                        f'<g class="layer_walkable"><polygon points="{pts}" '
                        f'fill="{fill}" stroke="#43A047" stroke-width="0.4"/></g>\n'
                    )
        if n_walk:
            print(f"  [F{fk}] 可通行区域图层: {n_walk} 个")

        # 2. 房间
        n_skip_bbox = 0
        for r in geom.get("rooms", []):
            ring = r["geometry"]["coordinates"][0]
            if _is_label_bbox(ring):
                # 文字标签的包围盒：跳过绘制，避免与真实房间重叠
                n_skip_bbox += 1
                continue
            pts = " ".join(f"{tosvg(p[0], p[1])[0]},{tosvg(p[0], p[1])[1]}" for p in ring)
            p = r["properties"]
            rtype = p.get("roomType", "room")
            color = ROOM_COLORS.get(rtype, "#FAFAFA")
            label = p.get("label", "")
            tip = f"房间：{label or '—'}\\n类型：{rtype}\\n编号：{p.get('roomId','')}"
            det = {"title": label or "房间", "rows": [
                ("房间编号", p.get("roomId", "—")),
                ("类型", rtype),
                ("楼层", p.get("floor", floor)),
                ("公共空间", "是" if p.get("public") else "否"),
                ("无障碍可达", "是" if p.get("accessible") else "否"),
                ("独立出入口", "是" if p.get("hasIndependentEntrance") else "否"),
            ]}
            # corridor 类型常因文字标签/多区域合并产生，渲染时只画虚线轮廓（不填色），
            # 避免覆盖下方真房间图层。保留轮廓便于核实位置。
            if rtype == "corridor":
                _fill = "none"
                _stroke = "#B0BEC5"
                _sw = 0.6
                _dash = "4,3"
            elif rtype == "staircase":
                _fill = color
                _stroke = "#E57373"
                _sw = 1.2
                _dash = "6,3"
            else:
                _fill = color
                _stroke = "#999"
                _sw = 0.5
                _dash = "none"
            parts.append(
                f'<g class="layer_room" {info_attr({"tip": tip, "detail": det})}>'
                f'<polygon points="{pts}" fill="{_fill}" stroke="{_stroke}" stroke-width="{_sw}" stroke-dasharray="{_dash}"/></g>\n'
            )
            if label:
                cx_s = sum(p_[0] for p_ in ring[:-1]) / max(len(ring) - 1, 1)
                cy_s = sum(p_[1] for p_ in ring[:-1]) / max(len(ring) - 1, 1)
                sx_s, sy_s = tosvg(cx_s, cy_s)
                parts.append(
                    f'<g class="layer_room">{info_attr({"tip": tip, "detail": det}) if False else ""}'
                    f'<text x="{sx_s}" y="{sy_s}" font-size="6" text-anchor="middle" '
                    f'fill="#333">{label}</text></g>\n'
                )
        if n_skip_bbox:
            print(f"  [F{fk}] 跳过 {n_skip_bbox} 个文字标签包围盒（面积<{LABEL_BBOX_MAX_AREA}m² 且长宽比≥{LABEL_BBOX_MIN_ASPECT}）")

        # 2. 墙体
        for w in geom.get("walls", []):
            c = w["geometry"]["coordinates"]
            x1, y1 = tosvg(c[0][0], c[0][1])
            x2, y2 = tosvg(c[1][0], c[1][1])
            parts.append(f'<g class="layer_wall"><path d="M {x1} {y1} L {x2} {y2}"/></g>\n')

        # 3. 窗户段
        for wn in geom.get("windowSegments", []):
            c = wn["geometry"]["coordinates"]
            x1, y1 = tosvg(c[0][0], c[0][1])
            x2, y2 = tosvg(c[1][0], c[1][1])
            parts.append(f'<g class="layer_window"><path d="M {x1} {y1} L {x2} {y2}"/></g>\n')

        # 4. 楼梯
        for st in geom.get("stairs", []):
            ring = st["geometry"]["coordinates"][0]
            pts = " ".join(f"{tosvg(p[0], p[1])[0]},{tosvg(p[0], p[1])[1]}" for p in ring)
            label_s = st["properties"].get("label", "")
            code_s = st["properties"].get("code")
            cent = st["properties"].get("centroid")
            linked = code_s in cf_codes if code_s else False
            tip = f"楼梯：{label_s}" + ("（跨层连通 1F↔2F）" if linked else "（本层独有）")
            det = {"title": label_s or "楼梯", "rows": [
                ("类型", "楼梯间"),
                ("井道编号", code_s or "（图纸未标注）"),
                ("跨层连通", "是 · 1F↔2F" if linked else "否 · 仅本层"),
                ("无障碍", "否（视障禁用）"),
            ]}
            parts.append(
                f'<g class="layer_stairs" {info_attr({"tip": tip, "detail": det})}>'
                f'<polygon points="{pts}" fill="#FFCCBC" stroke="#E64A19" stroke-width="0.8"/></g>\n'
            )
            if label_s and cent:
                sx_s, sy_s = tosvg(cent[0], cent[1])
                parts.append(
                    f'<g class="layer_stairs"><text x="{sx_s}" y="{sy_s}" '
                    f'font-size="5" text-anchor="middle" fill="#BF360C">{label_s}</text></g>\n'
                )

        # 5. 电梯
        for ev in geom.get("elevators", []):
            ring = ev["geometry"]["coordinates"][0]
            pts = " ".join(f"{tosvg(p[0], p[1])[0]},{tosvg(p[0], p[1])[1]}" for p in ring)
            label_e = ev["properties"].get("label", "")
            code_e = ev["properties"].get("code")
            cent = ev["properties"].get("centroid")
            linked = code_e in cf_codes if code_e else False
            tip = f"电梯：{label_e}" + ("（跨层连通 1F↔2F）" if linked else "（本层独有）")
            det = {"title": label_e or "电梯", "rows": [
                ("类型", "电梯间"),
                ("井道编号", code_e or "（图纸未标注）"),
                ("跨层连通", "是 · 1F↔2F" if linked else "否 · 仅本层"),
                ("无障碍", "是"),
            ]}
            parts.append(
                f'<g class="layer_elevator" {info_attr({"tip": tip, "detail": det})}>'
                f'<polygon points="{pts}" fill="#F8BBD0" stroke="#C2185B" stroke-width="0.8"/></g>\n'
            )
            if label_e and cent:
                sx_s, sy_s = tosvg(cent[0], cent[1])
                parts.append(
                    f'<g class="layer_elevator"><text x="{sx_s}" y="{sy_s}" '
                    f'font-size="5" text-anchor="middle" fill="#880E4F">{label_e}</text></g>\n'
                )

        # 6. 柱
        for col in geom.get("columns", []):
            ring = col["geometry"]["coordinates"][0]
            pts = " ".join(f"{tosvg(p[0], p[1])[0]},{tosvg(p[0], p[1])[1]}" for p in ring)
            parts.append(f'<g class="layer_column"><polygon points="{pts}"/></g>\n')

        # 7. 门（swing=普通门 / opening=门洞 / fire=防火门）
        # 每类门单独一个图层类名（layer_door_swing / _opening / _fire），
        # 使图层面板可分别开关；同时保留基类 layer_door 供样式复用。
        for dr in geom.get("doors", []):
            c = dr["geometry"]["coordinates"]
            sx, sy = tosvg(c[0], c[1])
            p = dr["properties"]
            dtype = p.get("doorType", "swing")
            w = float(p.get("width_m", 0.9))
            dname = DOOR_TYPE_CN.get(dtype, dtype)
            tip = f"{dname}\\n宽度：{w:.2f}m"
            det = {"title": dname,
                   "rows": [
                       ("门编号", p.get("id", "—")),
                       ("类型", f"{dname}（{dtype}）"),
                       ("宽度", f"{w:.2f} m"),
                       ("归属房间", "、".join(p.get("rooms", [])) or "—"),
                       ("来源图层", p.get("sourceLayer", "—")),
                   ]}
            attr = info_attr({"tip": tip, "detail": det, "id": p.get("id", ""), "kind": "door"})
            dcls = f'layer_door layer_door_{dtype if dtype in ("swing", "fire", "opening") else "swing"}'
            if dtype == "fire":
                s = max(3.0, w * SCALE * 0.22)
                parts.append(
                    f'<g class="{dcls}" {attr}>'
                    f'<rect x="{float(sx)-s/2:.1f}" y="{float(sy)-s/2:.1f}" width="{s:.1f}" height="{s:.1f}" '
                    f'fill="#FF5722" opacity="0.9"/></g>\n'
                )
            elif dtype == "opening":
                s = max(3.2, w * SCALE * 0.24)
                diamond = (f"{float(sx)},{float(sy)-s:.1f} {float(sx)+s:.1f},{float(sy)} "
                           f"{float(sx)},{float(sy)+s:.1f} {float(sx)-s:.1f},{float(sy)}")
                parts.append(
                    f'<g class="{dcls}" {attr}>'
                    f'<polygon points="{diamond}" fill="#1E8449" opacity="0.9"/></g>\n'
                )
            else:
                r = max(2.2, w * SCALE * 0.16)
                parts.append(
                    f'<g class="{dcls}" {attr}>'
                    f'<circle cx="{sx}" cy="{sy}" r="{r:.1f}" fill="#2196F3" opacity="0.85"/></g>\n'
                )

        # 8. 拓扑节点
        node_map = {n["id"]: n for n in topo.get("nodes", [])}
        for n in topo.get("nodes", []):
            cx, cy = n["coordinates"]
            sx, sy = tosvg(cx, cy)
            ntype = n.get("type", "doorway")
            label_n = n.get("label", "")
            nid = n["id"]
            # 详情
            rows = [("节点ID", nid), ("类型", ntype)]
            if ntype == "facility":
                rows.append(("设施类型", n.get("facilityType", "—")))
                rows.append(("视障可达", "是" if n.get("blindAccessible") else "否"))
                rows.append(("轮椅可达", "是" if n.get("wheelchairAccessible") else "否"))
            if ntype == "facility_entrance":
                rows.append(("设施类型", n.get("facilityType", "—")))
            if ntype == "room":
                rows.append(("房间", n.get("label", "—")))
            if ntype == "intersection":
                _rt = n.get("roomType", "")
                _rt_cn = {"corridor": "走道/走廊", "lobby": "门厅/大厅",
                          "activity": "活动空间", "atrium": "中庭"}.get(_rt, _rt or "开放空间")
                rows.append(("空间类型", _rt_cn))
            if label_n:
                rows.append(("标签", label_n))
            det = {"title": label_n or ntype, "rows": rows}
            _type_disp = label_n or ntype
            if ntype == "intersection":
                _rt = n.get("roomType", "")
                _type_disp = {"corridor": "走道/走廊", "lobby": "门厅/大厅",
                              "activity": "活动空间", "atrium": "中庭"}.get(_rt, "开放空间")
            tip = f"拓扑节点：{label_n or ntype}\\n类型：{_type_disp}"
            attr = info_attr({"tip": tip, "detail": det, "id": nid, "kind": "node"})

            if ntype == "room":
                parts.append(
                    f'<g class="layer_topo_node" {attr}>'
                    f'<circle cx="{sx}" cy="{sy}" r="3" fill="{NODE_COLORS["room"]}" opacity="0.85"/></g>\n'
                )
            elif ntype == "doorway":
                parts.append(
                    f'<g class="layer_topo_node" {attr}>'
                    f'<circle cx="{sx}" cy="{sy}" r="2.4" fill="{NODE_COLORS["doorway"]}" opacity="0.85"/></g>\n'
                )
            elif ntype == "intersection":
                s = 3.2
                parts.append(
                    f'<g class="layer_topo_node" {attr}>'
                    f'<rect x="{float(sx)-s:.1f}" y="{float(sy)-s:.1f}" width="{s*2:.1f}" height="{s*2:.1f}" '
                    f'fill="{NODE_COLORS["intersection"]}" opacity="0.85"/></g>\n'
                )
            elif ntype == "facility":
                color = FACILITY_COLORS.get(n.get("facilityType", "staircase"), "#8E44AD")
                parts.append(
                    f'<g class="layer_topo_node" {attr}>'
                    f'<circle cx="{sx}" cy="{sy}" r="4.2" fill="{color}" opacity="0.9"/></g>\n'
                )
                if label_n:
                    parts.append(
                        f'<g class="layer_topo_node"><text x="{float(sx)+6:.1f}" y="{float(sy)+3:.1f}" '
                        f'font-size="5" fill="{color}">{label_n}</text></g>\n'
                    )
            elif ntype == "facility_entrance":
                s = 4.5
                tri = (f"{float(sx)},{float(sy)-s:.1f} {float(sx)-s:.1f},{float(sy)+s:.1f} "
                       f"{float(sx)+s:.1f},{float(sy)+s:.1f}")
                parts.append(
                    f'<g class="layer_topo_node" {attr}>'
                    f'<polygon points="{tri}" fill="{NODE_COLORS["facility_entrance"]}" opacity="0.9"/></g>\n'
                )
                if label_n:
                    parts.append(
                        f'<g class="layer_topo_node"><text x="{float(sx)+6:.1f}" y="{float(sy)+3:.1f}" '
                        f'font-size="5" fill="{NODE_COLORS["facility_entrance"]}">{label_n}</text></g>\n'
                    )
            else:
                parts.append(
                    f'<g class="layer_topo_node" {attr}>'
                    f'<circle cx="{sx}" cy="{sy}" r="3" fill="#7F8C8D" opacity="0.8"/></g>\n'
                )

        # 9. 拓扑边
        for e in topo.get("edges", []):
            n1 = node_map.get(e.get("from"))
            n2 = node_map.get(e.get("to"))
            if not n1 or not n2:
                continue
            x1, y1 = tosvg(n1["coordinates"][0], n1["coordinates"][1])
            x2, y2 = tosvg(n2["coordinates"][0], n2["coordinates"][1])
            det = {"title": f"导航边 {e.get('id','')}", "rows": [
                ("起始", e.get("from", "—")),
                ("终点", e.get("to", "—")),
                ("距离", f"{e.get('distance',0):.2f} m"),
                ("预估时间", f"{e.get('estimatedTime',0):.1f} s"),
                ("可达等级", e.get("accessibilityLevel", "—")),
                ("风险等级", e.get("riskLevel", "—")),
                ("可步行", "是" if e.get("walkable") else "否"),
                ("轮椅", "是" if e.get("wheelchairAccessible") else "否"),
                ("视障", "是" if e.get("blindAccessible") else "否"),
            ]}
            tip = f"导航边\\n距离 {e.get('distance',0):.1f}m · 视障 {('是' if e.get('blindAccessible') else '否')}"
            attr = info_attr({"tip": tip, "detail": det, "from": e.get("from", ""),
                              "to": e.get("to", ""), "kind": "edge"})
            parts.append(
                f'<g class="layer_topo_edge" {attr}><path d="M {x1} {y1} L {x2} {y2}"/></g>\n'
            )

        # 10. 风险 / 坡道 / 盲道 / 材质（若存在）
        for r in acc.get("riskNodes", []):
            cx, cy = r["coordinates"]
            sx, sy = tosvg(cx, cy)
            rtype = r.get("type", r.get("riskType", "stair_entrance"))
            rlabel = r.get("label", "")
            tip = f"风险点：{rlabel or rtype}"
            det = {"title": rlabel or "风险点", "rows": [("类型", rtype), ("描述", r.get("description", "—"))]}
            attr = info_attr({"tip": tip, "detail": det, "kind": "risk"})
            parts.append(
                f'<g class="layer_risk" {attr}>'
                f'<circle cx="{sx}" cy="{sy}" r="5" fill="#F44336" opacity="0.6"/>'
                f'<text x="{sx}" y="{float(sy)+1.6:.1f}" font-size="3.5" text-anchor="middle" fill="#fff">!</text></g>\n'
            )
        for rp in acc.get("ramps", []):
            loc = rp.get("location", rp.get("coordinates", [0, 0]))
            sx, sy = tosvg(loc[0], loc[1])
            attr = info_attr({"tip": "坡道", "detail": {"title": "坡道", "rows": [("编号", rp.get("id", "—"))]}, "kind": "ramp"})
            parts.append(
                f'<g class="layer_ramp" {attr}>'
                f'<circle cx="{sx}" cy="{sy}" r="8" fill="none" stroke="#4CAF50" '
                f'stroke-width="1.5" stroke-dasharray="3,2"/></g>\n'
            )
        for tp in acc.get("tactilePaths", []):
            path = tp.get("path", [])
            if len(path) < 2:
                continue
            d_parts = []
            for pt in path:
                sx_pt, sy_pt = tosvg(pt[0], pt[1])
                d_parts.append(f"M {sx_pt} {sy_pt}" if not d_parts else f"L {sx_pt} {sy_pt}")
            attr = info_attr({"tip": "盲道路径", "detail": {"title": "盲道路径", "rows": [("段数", len(path))]}, "kind": "tactile"})
            parts.append(
                f'<g class="layer_tactile" {attr}>'
                f'<path d="{" ".join(d_parts)}" stroke="#FFD600" stroke-width="1.8" fill="none" '
                f'stroke-linecap="round" opacity="0.8"/></g>\n'
            )
        for gmc in acc.get("groundMaterialChanges", []):
            pt = gmc.get("coordinates", [0, 0])
            sx, sy = tosvg(pt[0], pt[1])
            sz = 6
            pts_d = (f"{sx},{float(sy)-sz} {float(sx)+sz*0.7:.1f},{sy} {sx},{float(sy)+sz} {float(sx)-sz*0.7:.1f},{sy}")
            desc = gmc.get("description", gmc.get("id", ""))[:25]
            attr = info_attr({"tip": "地面材质变化", "detail": {"title": "地面材质变化", "rows": [("描述", desc)]}, "kind": "material"})
            parts.append(
                f'<g class="layer_material" {attr}>'
                f'<polygon points="{pts_d}" fill="#9C27B0" opacity="0.75" stroke="#6A1B9A" stroke-width="0.5"/>'
                f'<text x="{float(sx)+9:.1f}" y="{float(sy)+3:.1f}" font-size="4" fill="#6A1B9A">{desc}</text></g>\n'
            )

        # 10.5 建筑外轮廓（实线）—— 基于闭合空间多边形 + 墙体段栅格化，膨胀弥合门洞缺口，
        # 取「外部泛洪的补集」作为建筑实体后追踪最外轮廓；按相对面积阈值过滤小噪声块，用实线绘制。
        outline_polys = building_outline(geom, cell=0.1, wall_hw=1, close_r=14)
        if outline_polys:
            _areas = [_area(p) for p in outline_polys]
            _max_a = max(_areas)
            # 仅保留最大块的 ≥5%（或绝对 ≥150m²），剔除家具/孤立柱簇等小噪声连通块
            _thr = max(150.0, 0.05 * _max_a)
            outline_polys = [p for p, a in zip(outline_polys, _areas) if a >= _thr]
        if outline_polys:
            total_oa = sum(_area(p) for p in outline_polys)
            print(f"  [F{fk}] 建筑外轮廓: {len(outline_polys)} 块, 总面积 ~{total_oa:.0f} m²")
        for poly in outline_polys:
            pts = " ".join(f"{tosvg(px, py)[0]},{tosvg(px, py)[1]}" for px, py in poly)
            parts.append(
                f'<g class="layer_building_outline" pointer-events="none">'
                f'<polygon points="{pts}" fill="none" stroke="#222" '
                f'stroke-width="1.4" stroke-linejoin="round" stroke-linecap="round"/></g>\n'
            )

        # 楼层分隔线
        if i < len(sorted_floors) - 1:
            sep_y = (i + 1) * svh_per_floor
            parts.append(
                f'<line x1="0" y1="{sep_y}" x2="{svw}" y2="{sep_y}" '
                f'stroke="#e0e0e0" stroke-width="1" stroke-dasharray="4,2"/>\n'
            )

    # ---------------- 跨层连接线（含端点提示详情） ----------------
    for e in cf:
        from_info = node_lookup.get(e.get("from", ""))
        to_info = node_lookup.get(e.get("to", ""))
        if not from_info or not to_info:
            continue
        f1, f2 = from_info["floor"], to_info["floor"]
        c1 = from_info["coordinates"]
        c2 = to_info["coordinates"]
        idx1 = sorted_floors.index(str(f1)) if str(f1) in sorted_floors else 0
        idx2 = sorted_floors.index(str(f2)) if str(f2) in sorted_floors else 0
        base1 = idx1 * svh_per_floor + FLOOR_TITLE_H + MARGIN_Y
        base2 = idx2 * svh_per_floor + FLOOR_TITLE_H + MARGIN_Y
        sx1 = MARGIN_X + (c1[0] - ox) * SCALE
        sy1 = base1 + (oy - c1[1]) * SCALE
        sx2 = MARGIN_X + (c2[0] - ox) * SCALE
        sy2 = base2 + (oy - c2[1]) * SCALE
        etype = e.get("type", "")
        if etype == "staircase":
            line_color, text_color, etype_label = "#E53935", "#C62828", "楼梯连接"
        elif etype == "elevator":
            line_color, text_color, etype_label = "#1E88E5", "#1565C0", "电梯连接"
        else:
            line_color, text_color, etype_label = "#9C27B0", "#7B1FA2", etype
        mid_x = (sx1 + sx2) / 2
        mid_y = (sy1 + sy2) / 2
        eid = e.get("id", "")
        code = e.get("code") or ""
        matched_by = "图纸井道编号" if e.get("matchedBy") == "code" else "几何中心距离"
        blind_ok = "✓" if e.get("blindAccessible") else "✗"
        wheel_ok = "✓" if e.get("wheelchairAccessible") else "✗"
        det = {"title": f"跨层连接 {code or eid}", "rows": [
            ("类型", etype_label),
            ("井道编号", code or "（图纸未标注）"),
            ("连接", f"{f1}F ↔ {f2}F"),
            ("配对依据", matched_by),
            ("边 ID", eid),
            ("距离", f"{e.get('distance',0):.2f} m"),
            ("视障可达", "是" if e.get("blindAccessible") else "否"),
            ("轮椅可达", "是" if e.get("wheelchairAccessible") else "否"),
        ]}
        tip = f"跨层：{code or eid} · {etype_label} {f1}F↔{f2}F"
        attr = info_attr({"tip": tip, "detail": det, "kind": "crossfloor"})
        # 端点处也标注井道编号：缩放到单层时依然能直接读出是哪个楼梯/电梯井
        endpoint_tags = "".join(
            f'<text x="{fmt(sx + 5)}" y="{fmt(sy - 4)}" font-size="4.5" '
            f'fill="{text_color}" opacity="0.9">{code}</text>'
            for sx, sy in ((sx1, sy1), (sx2, sy2))) if code else ""
        parts.append(
            f'<g class="layer_crossfloor" {attr}>'
            f'<path d="M {fmt(sx1)} {fmt(sy1)} L {fmt(sx2)} {fmt(sy2)}" stroke="{line_color}"/>'
            f'<circle cx="{fmt(sx1)}" cy="{fmt(sy1)}" r="3" fill="{line_color}" opacity="0.7"/>'
            f'<circle cx="{fmt(sx2)}" cy="{fmt(sy2)}" r="3" fill="{line_color}" opacity="0.7"/>'
            f'{endpoint_tags}'
            f'<text x="{fmt(mid_x)}" y="{fmt(mid_y-10)}" font-size="6" fill="{text_color}" '
            f'text-anchor="middle" opacity="0.8">{code or eid}</text>'
            f'<text x="{fmt(mid_x)}" y="{fmt(mid_y+4)}" font-size="5" fill="{text_color}" '
            f'text-anchor="middle" opacity="0.72">{etype_label} ({f1}F↔{f2}F)</text>'
            f'<text x="{fmt(mid_x)}" y="{fmt(mid_y+15)}" font-size="4" fill="{text_color}" '
            f'text-anchor="middle" opacity="0.66">盲:{blind_ok} 轮椅:{wheel_ok}</text></g>\n'
        )

    parts.append(
        f'<text x="20" y="{svh-8}" font-size="9" fill="#999">'
        f'跨层连接: {len(cf)} 条 | 建筑: {geo.get("venueName","")} | 版本: {geo.get("version","?")}</text>\n'
    )

    # ---------------- 图例 + 详情面板 + JS ----------------
    parts.append('''</svg>
</div><!-- /svg-wrapper -->
</div><!-- /svg-container -->
<div id="detail"><h4>点击任意要素查看详情</h4><div style="color:#999;font-size:12px">悬停查看提示，点击锁定详情；再次点击同一要素可取消选中。点击拓扑节点会高亮其<b style="color:#FFC107">相连边</b>与<b style="color:#00BCD4">直接可达节点</b>（青色）。</div></div>
</div><!-- /left -->
<div id="legend-panel">
  <h4>图例说明 (v9)</h4>
  <div class="lg-sec"><div class="lg-title">建筑要素</div>
    <div class="lg-item"><div class="lg-sw" style="background:#333"></div>墙体</div>
    <div class="lg-item"><div class="lg-sw line" style="--c:#81D4FA"></div>窗户段</div>
    <div class="lg-item"><div class="lg-sw" style="background:#FFF9C4"></div>房间/教室</div>
    <div class="lg-item"><div class="lg-sw" style="background:transparent;border:1px dashed #B0BEC5"></div>走道/通道（虚线轮廓）</div>
    <div class="lg-item"><div class="lg-sw" style="background:#B0BEC5;border:1px solid #78909C"></div>柱子</div>
    <div class="lg-item"><div class="lg-sw" style="background:transparent;border:2px solid #222"></div>建筑外轮廓（实线）</div>
  </div>
  <div class="lg-sec"><div class="lg-title">门</div>
    <div class="lg-item"><div class="lg-sw" style="background:#2196F3;border-radius:50%;width:11px;height:11px;margin-left:1px"></div>普通门（window 摆弧）</div>
    <div class="lg-item"><div class="lg-sw" style="background:#1E8449;width:12px;height:12px;transform:rotate(45deg);margin-left:1px"></div>门洞（DK 标注墙缝）</div>
    <div class="lg-item"><div class="lg-sw" style="background:#FF5722;width:11px;height:11px;margin-left:1px"></div>防火门（DOOR_FIRE）</div>
  </div>
  <div class="lg-sec"><div class="lg-title">拓扑节点 (v9)</div>
    <div class="lg-item"><div class="lg-sw" style="background:#E67E22;border-radius:50%;width:11px;height:11px"></div>房间节点</div>
    <div class="lg-item"><div class="lg-sw" style="background:#C0392B;border-radius:50%;width:10px;height:10px"></div>门口节点</div>
    <div class="lg-item"><div class="lg-sw" style="background:#27AE60;width:11px;height:11px"></div>走廊交叉口</div>
    <div class="lg-item"><div class="lg-sw" style="background:#8E44AD;border-radius:50%;width:12px;height:12px"></div>楼梯接入</div>
    <div class="lg-item"><div class="lg-sw" style="background:#16A085;border-radius:50%;width:12px;height:12px"></div>电梯接入</div>
    <div class="lg-item"><div class="lg-sw" style="background:#2980B9;clip-path:polygon(50% 0,0 100%,100% 100%)"></div>出入口</div>
  </div>
  <div class="lg-sec"><div class="lg-title">无障碍 / 跨层</div>
    <div class="lg-item"><div class="lg-sw" style="background:#4CAF50;height:0;border-bottom:2px dashed #4CAF50"></div>坡道</div>
    <div class="lg-item"><div class="lg-sw" style="background:#FFD600;height:0;border-bottom:2px solid #FFD600"></div>盲道路径</div>
    <div class="lg-item"><div class="lg-sw" style="background:#9C27B0;transform:rotate(45deg)"></div>地面材质</div>
    <div class="lg-item"><div class="lg-sw" style="background:#E53935;height:0;border-bottom:2px dashed #E53935"></div>楼梯跨层</div>
    <div class="lg-item"><div class="lg-sw" style="background:#1E88E5;height:0;border-bottom:2px dashed #1E88E5"></div>电梯跨层</div>
  </div>
  <div class="lg-sec"><div class="lg-title">风险</div>
    <div class="lg-item"><div class="lg-sw" style="background:#F44336;border-radius:50%"></div>风险点</div>
  </div>
</div><!-- /legend-panel -->
</div><!-- /app -->
<script>
var svg = document.getElementById('main-svg');
var wrapper = document.getElementById('svg-wrapper');
var container = document.getElementById('svg-container');
var tip = document.getElementById('tooltip');
var scale = 1, translateX = 0, translateY = 0;
var isDragging = false, startX = 0, startY = 0;

function applyTransform() {{
  svg.style.transform = 'translate(' + translateX + 'px,' + translateY + 'px) scale(' + scale + ')';
  svg.style.transformOrigin = '0 0';
  document.querySelector('.zoom-info') || (function(){{}}());
}}
function setZoomInfo() {{
  var z = document.querySelector('.zoom-info');
  if (z) z.textContent = '缩放: ' + Math.round(scale * 100) + '%';
}}
function zoomIn() {{ scale = Math.min(scale * 1.3, 20); applyTransform(); setZoomInfo(); }}
function zoomOut() {{ scale = Math.max(scale / 1.3, 0.15); applyTransform(); setZoomInfo(); }}
function resetView() {{ scale = 1; translateX = 0; translateY = 0; applyTransform(); setZoomInfo(); }}

wrapper.addEventListener('wheel', function(e) {{
  e.preventDefault();
  var delta = e.deltaY > 0 ? 0.9 : 1.1;
  var rect = wrapper.getBoundingClientRect();
  var mx = e.clientX - rect.left, my = e.clientY - rect.top;
  var ns = Math.max(0.15, Math.min(20, scale * delta));
  var ratio = ns / scale;
  translateX = mx - ratio * (mx - translateX);
  translateY = my - ratio * (my - translateY);
  scale = ns; applyTransform(); setZoomInfo();
}}, {{ passive: false }});

wrapper.addEventListener('mousedown', function(e) {{
  isDragging = true; startX = e.clientX - translateX; startY = e.clientY - translateY;
  wrapper.classList.add('grabbing');
}});
document.addEventListener('mousemove', function(e) {{
  if (!isDragging) return;
  translateX = e.clientX - startX; translateY = e.clientY - startY;
  applyTransform();
}});
document.addEventListener('mouseup', function() {{ isDragging = false; wrapper.classList.remove('grabbing'); }});

// ---- 悬停提示 ----
wrapper.addEventListener('mousemove', function(e) {{
  var t = e.target;
  var info = t.getAttribute && t.getAttribute('data-info');
  if (!info) {{ tip.style.display = 'none'; return; }}
  var d; try {{ d = JSON.parse(info); }} catch (err) {{ tip.style.display = 'none'; return; }}
  tip.textContent = (d.tip || '').replace(/\\\\n/g, '\\n');
  tip.style.display = 'block';
  var cr = container.getBoundingClientRect();
  var x = e.clientX - cr.left + 14, y = e.clientY - cr.top + 14;
  if (x + 290 > cr.width) x = e.clientX - cr.left - 290;
  if (y + 80 > cr.height) y = cr.height - 90;
  tip.style.left = x + 'px'; tip.style.top = y + 'px';
}});
wrapper.addEventListener('mouseleave', function() {{ tip.style.display = 'none'; }});

// ---- 点击任意要素：按当前状态切换选中，并动态调整关联状态 ----
// 关联状态包括：拓扑图层展示（选中拓扑节点时自动显示）、选中详情面板等
var DETAIL_PLACEHOLDER = '<h4>点击任意要素查看详情</h4><div style="color:#999;font-size:12px">悬停查看提示，点击锁定详情；再次点击同一要素可取消选中。点击拓扑节点会高亮其<b style="color:#FFC107">相连边</b>与<b style="color:#00BCD4">直接可达节点</b>（青色）。</div>';
function clearHighlight() {{
  document.querySelectorAll('.selected').forEach(function(el){{ el.classList.remove('selected'); }});
  document.querySelectorAll('.neighbor').forEach(function(el){{ el.classList.remove('neighbor'); }});
}}
function resetDetail() {{ document.getElementById('detail').innerHTML = DETAIL_PLACEHOLDER; }}
// 按节点 id 找到对应的拓扑节点组并加高亮 class
function markNode(id, cls) {{
  document.querySelectorAll('.layer_topo_node').forEach(function(g) {{
    var f = g.getAttribute('data-info');
    if (!f) return;
    try {{ var nd = JSON.parse(f); if (nd.id === id) g.classList.add(cls); }} catch(e){{}}
  }});
}}
// 联动：确保某图层可见（并同步勾选框状态）
function ensureLayer(name, checked) {{
  document.querySelectorAll('.layer_' + name).forEach(function(el){{ el.style.display = checked ? '' : 'none'; }});
  var cb = document.querySelector('#layerControls input[onchange*="' + name + '"]');
  if (cb) cb.checked = checked;
}}
function showDetail(d) {{
  var box = document.getElementById('detail');
  var h = '<h4>' + (d.title || '详情') + '</h4>';
  (d.rows || []).forEach(function(r) {{
    h += '<div class="row"><span>' + r[0] + '</span><span>' + r[1] + '</span></div>';
  }});
  box.innerHTML = h;
}}
wrapper.addEventListener('click', function(e) {{
  var t = e.target.closest('[data-info]');
  if (!t) return;
  var info = t.getAttribute('data-info');
  var d; try {{ d = JSON.parse(info); }} catch (err) {{ return; }}
  // 已选中 → 再次点击取消选中，并还原关联状态
  if (t.classList.contains('selected')) {{
    clearHighlight(); resetDetail();
    return;
  }}
  // 未选中 → 切换为选中：先还原其它，再建立本要素的关联状态
  clearHighlight(); t.classList.add('selected');
  showDetail(d.detail || {{ title: d.tip || '详情', rows: [] }});
  // 拓扑节点 → 联动拓扑图层展示，并高亮相连边 + 直接可达节点
  if (d.kind === 'node' && d.id) {{
    ensureLayer('topo_node', true);
    ensureLayer('topo_edge', true);
    var nbCount = 0;
    document.querySelectorAll('.layer_topo_edge').forEach(function(g) {{
      var f = g.getAttribute('data-info');
      if (!f) return;
      try {{
        var ed = JSON.parse(f);
        if (ed.from === d.id || ed.to === d.id) {{
          g.classList.add('selected');                 // 高亮相连边
          var nb = (ed.from === d.id) ? ed.to : ed.from; // 直接可达节点 id
          if (nb) {{ markNode(nb, 'neighbor'); nbCount++; }} // 高亮直接可达节点
        }}
      }} catch(e){{}}
    }});
    // 在详情面板补充「直接可达」统计
    var stat = document.createElement('div');
    stat.className = 'row';
    stat.innerHTML = '<span>直接可达节点</span><span>' + nbCount + ' 个</span>';
    document.getElementById('detail').appendChild(stat);
  }}
}});

// ---- 图层开关 ----
var allLayers = ['room','walkable','wall','window','stairs','elevator','column','building_outline',
  'door_swing','door_opening','door_fire',
  'topo_node','topo_edge','crossfloor','risk','ramp','tactile','material'];
// 显示状态严格跟随勾选框：勾选=显示，取消=隐藏（避免「勾选反而隐藏」的倒挂）
function toggleLayer(name, checked) {{
  document.querySelectorAll('.layer_' + name).forEach(function(el){{ el.style.display = checked ? '' : 'none'; }});
}}
function setAll(v) {{
  allLayers.forEach(function(n){{
    document.querySelectorAll('.layer_' + n).forEach(function(el){{ el.style.display = v ? '' : 'none'; }});
  }});
  document.querySelectorAll('#layerControls input[type=checkbox]').forEach(function(cb){{ cb.checked = v; }});
}}

// ---- 导出所选图层为独立 SVG 图片 ----
// 导出的是「当前在图层面板勾选的图层」：先取消不需要的图层再点导出即可。
// 关键：页面 SVG 的描边/填充全部来自 <head> 的外部 <style>，脱离 HTML 后会丢失样式，
// 因此必须把该 <style> 嵌入到克隆出的 SVG 内部，导出的文件才能正常上色。
function exportSelectedSVG() {{
  // 1. 收集当前勾选（=所选）图层
  var selected = allLayers.filter(function(n){{
    var cb = document.querySelector('#layerControls input[onchange*="' + n + '"]');
    return cb ? cb.checked : true;
  }});
  if (selected.length === 0) {{ alert('请先在上方勾选至少一个图层再导出。'); return; }}

  // 2. 克隆主 SVG，去掉缩放/平移的 style 变换，得到全幅原始坐标
  var clone = svg.cloneNode(true);
  clone.removeAttribute('style');
  clone.setAttribute('xmlns', 'http://www.w3.org/2000/svg');
  clone.setAttribute('xmlns:xlink', 'http://www.w3.org/1999/xlink');

  // 3. 仅保留所选图层，删除未选图层对应的 <g>
  var groups = clone.querySelectorAll('[class*="layer_"]');
  groups.forEach(function(g){{
    var cls = g.getAttribute('class') || '';
    var keep = selected.some(function(n){{ return cls.indexOf('layer_' + n) !== -1; }});
    if (!keep) g.parentNode.removeChild(g);
  }});

  // 4. 嵌入页面 <style>（保留颜色/描边），加白底背景，并剔除交互用 data-info 减体积
  var pageStyle = document.querySelector('style');
  if (pageStyle) {{
    var styleEl = document.createElementNS('http://www.w3.org/2000/svg', 'style');
    styleEl.textContent = pageStyle.textContent;
    clone.insertBefore(styleEl, clone.firstChild);
  }}
  var rect = document.createElementNS('http://www.w3.org/2000/svg', 'rect');
  rect.setAttribute('x', 0); rect.setAttribute('y', 0);
  rect.setAttribute('width', clone.getAttribute('width'));
  rect.setAttribute('height', clone.getAttribute('height'));
  rect.setAttribute('fill', '#ffffff');
  clone.insertBefore(rect, clone.firstChild.nextSibling);
  clone.querySelectorAll('[data-info]').forEach(function(el){{ el.removeAttribute('data-info'); }});

  // 5. 序列化并触发下载
  var data = new XMLSerializer().serializeToString(clone);
  var xmlDecl = '<?xml version="1.0" encoding="UTF-8"?>';
  if (data.indexOf(xmlDecl) !== 0) data = xmlDecl + data;
  var blob = new Blob([data], {{type: 'image/svg+xml;charset=utf-8'}});
  var url = URL.createObjectURL(blob);
  var a = document.createElement('a');
  a.href = url; a.download = 'PathAI_所选图层_楼层图.svg';
  document.body.appendChild(a); a.click(); document.body.removeChild(a);
  setTimeout(function(){{ URL.revokeObjectURL(url); }}, 1500);
  console.log('已导出所选图层 SVG，包含图层：', selected.join(', '));
}}

// 初始化：按勾选框实际状态同步各图层可见性（未勾选图层初始应隐藏）
allLayers.forEach(function(n){{
  var cb = document.querySelector('#layerControls input[onchange*="' + n + '"]');
  var show = cb ? cb.checked : true;
  document.querySelectorAll('.layer_' + n).forEach(function(el){{ el.style.display = show ? '' : 'none'; }});
}});

// ---- 楼层跳转 ----
function buildFloorJump(total, perFloor) {{
  var box = document.getElementById('floor-jump');
  var names = {{1:'1F 首层'}};
  for (var i=0;i<total;i++) {{
    var f = i+1;
    var b = document.createElement('button');
    b.className = 'floor-btn' + (i===0?' active':'');
    b.textContent = names[f] || (f+'F');
    b.onclick = (function(idx){{ return function(){{ jumpFloor(idx, total, perFloor, this); }}; }})(i);
    box.appendChild(b);
  }}
}}
function jumpFloor(idx, total, perFloor, btn) {{
  document.querySelectorAll('.floor-btn').forEach(function(b){{ b.classList.remove('active'); }});
  if (btn) btn.classList.add('active');
  var targetY = -(idx * perFloor) + 8;
  var minY = container.clientHeight - svg.clientHeight * scale;
  if (targetY > 0) targetY = 0;
  if (targetY < minY) targetY = minY;
  translateY = targetY; applyTransform(); setZoomInfo();
}}
buildFloorJump(__NFLOORS__, __PERFLOOR__);
applyTransform(); setZoomInfo();
</script>
</body></html>''')

    # 尾部 JS 块在普通字符串内写成 {{ }} 双花括号（避免 f-string 转义歧义），
    # 此处一次性还原为合法 JS 单花括号，并注入楼层数 / 每层高度。
    parts[-1] = (parts[-1]
                 .replace("{{", "{").replace("}}", "}")
                 .replace("__NFLOORS__", str(len(sorted_floors)))
                 .replace("__PERFLOOR__", str(svh_per_floor)))

    with open(HTML_OUT, "w", encoding="utf-8") as f:
        f.write("".join(parts))

    print(f"已生成: {HTML_OUT}")
    print(f"  SVG: {svw} × {svh} px | 每层 {svh_per_floor} px")
    print(f"  坐标范围: x[{min_x:.1f}, {max_x:.1f}], y[{min_y:.1f}, {max_y:.1f}]")
    print(f"  楼层: {len(sorted_floors)} 层 | 跨层连接: {len(cf)} 条")


if __name__ == "__main__":
    main()
