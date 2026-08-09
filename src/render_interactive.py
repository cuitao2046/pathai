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


def _seg_crosses_wall(p1, p2, A, B):
    """路径段 p1->p2 是否真正「穿透」墙体线段 A-B（与 route_rules 同源）。

    判定：两端点位于墙线两侧(opposite sides)且交点落在线段内；共线/同侧
    （沿墙并行）不算穿墙。
    """
    ax, ay = A[0], A[1]
    bx, by = B[0], B[1]
    px, py = p1[0], p1[1]
    qx, qy = p2[0], p2[1]
    dx, dy = bx - ax, by - ay

    def side(x, y):
        return (bx - ax) * (y - ay) - (by - ay) * (x - ax)

    s1 = side(px, py)
    s2 = side(qx, qy)
    if s1 == 0 and s2 == 0:
        return False  # 共线：沿墙，非穿透
    if s1 * s2 > 0:
        return False  # 同侧：沿墙并行，非穿透
    if abs(dx) < 1e-12 and abs(dy) < 1e-12:
        return False  # 退化墙线
    ex, ey = qx - px, qy - py
    det = dx * ey - dy * ex
    if abs(det) < 1e-12:
        return False
    u = (ex * (ay - py) - ey * (ax - px)) / det  # 沿墙 A->B 参数
    t = (dy * (px - ax) - dx * (py - ay)) / det  # 沿路径 p1->p2 参数
    return (-1e-9) <= t <= (1 + 1e-9) and (-1e-9) <= u <= (1 + 1e-9)


def compute_route_rule_extras(geo):
    """为前端 Dijkstra 预计算路由规则辅助量（对齐 src/route_rules.py）。

    返回 dict：
    - edge_door_type: edge_id -> doorType(str|None)，从门节点推导；
    - room_best_door: room 节点 id -> 该房间最高优先级门类型(swing>fire>opening)；
    - wall_crossing_titi: 两端均为 intersection 且直线段真正穿墙的 TI<->TI 边 id 集合。
    """
    DOOR_PENALTY = {"swing": 0.0, "fire": 0.5, "opening": 1.0}
    node_by_id = {}
    for fk, fd in geo["floors"].items():
        for n in (fd.get("topology", {}) or {}).get("nodes", []):
            node_by_id[n["id"]] = n

    def edge_door_type(e):
        a = node_by_id.get(e["from"])
        b = node_by_id.get(e["to"])
        if a and a.get("type") == "doorway":
            return a.get("doorType")
        if b and b.get("type") == "doorway":
            return b.get("doorType")
        return None

    edge_door_type_map = {}
    for fk, fd in geo["floors"].items():
        for e in (fd.get("topology", {}) or {}).get("edges", []):
            edge_door_type_map[e["id"]] = edge_door_type(e)

    # 房间最佳门类型（每间房取优先级最高的门）
    best_door = {}
    for n in node_by_id.values():
        if n.get("type") == "doorway":
            for rid in (n.get("rooms") or []):
                t = n.get("doorType")
                cur = best_door.get(rid)
                if cur is None or DOOR_PENALTY.get(t, 9) < DOOR_PENALTY.get(cur, 9):
                    best_door[rid] = t
    room_best_door = {}
    for n in node_by_id.values():
        if n.get("type") == "room":
            rid = n.get("roomId") or n["id"]
            if rid in best_door:
                room_best_door[n["id"]] = best_door[rid]

    # 穿墙 TI<->TI 边集合
    wall_lines = []
    for fk, fd in geo["floors"].items():
        for w in (fd.get("geometry", {}) or {}).get("walls", []):
            g = w.get("geometry", {})
            if g.get("type") == "LineString" and len(g.get("coordinates", [])) >= 2:
                cs = g["coordinates"]
                wall_lines.append((tuple(cs[0]), tuple(cs[-1])))
    wall_crossing_titi = set()
    for fk, fd in geo["floors"].items():
        for e in (fd.get("topology", {}) or {}).get("edges", []):
            a = node_by_id.get(e["from"])
            b = node_by_id.get(e["to"])
            if not a or not b:
                continue
            if a.get("type") != "intersection" or b.get("type") != "intersection":
                continue
            ca, cb = a.get("coordinates"), b.get("coordinates")
            if not ca or not cb:
                continue
            for (A, B) in wall_lines:
                if _seg_crosses_wall(ca, cb, A, B):
                    wall_crossing_titi.add(e["id"])
                    break

    return {
        "edge_door_type": edge_door_type_map,
        "room_best_door": room_best_door,
        "wall_crossing_titi": wall_crossing_titi,
    }


def build_anno_script(min_x, max_y, svh_per_floor, sorted_floors):
    """生成「区域标注」交互脚本（独立 <script>，普通字符串，花括号为字面量）。

    通过 __CONSTS__ 占位符注入坐标变换常量，使浏览器端能把屏幕拖拽框
    （SVG 用户空间）反算回米制局部坐标，并写回房间类型。
    """
    floor_keys_js = json.dumps([str(k) for k in sorted_floors], ensure_ascii=False)
    consts = (
        "var GEOX = {ox:%r, oy:%r, scale:7.0, marginX:50, marginY:30, "
        "titleH:46, perFloor:%d, nFloors:%d, floorKeys:%s};"
        % (min_x, max_y, svh_per_floor, len(sorted_floors), floor_keys_js)
    )
    tpl = '''<script>
// ===== 区域标注：手动指定房间类型并写回 GeoJSON =====
__CONSTS__

var ANNO_MODE = false;
var annoDrawing = false;
var annoStartSvg = null;
var annoRectEl = null;
var ANNO_OVERRIDES = [];

var ANNO_ROOM_COLORS = {
  "classroom":"#FFF9C4","office":"#D7CCC8","meeting":"#F8BBD0","reception":"#FFE0B2",
  "medical":"#C8E6C9","storage":"#D7CCC8","equipment":"#CFD8DC","shaft":"#B0BEC5",
  "toilet":"#BBDEFB","staircase":"#FFCDD2","corridor":"#FAFAFA","lobby":"#FFF59D",
  "activity":"#F8BBD0","atrium":"#F3E5F5","elevator_lobby":"#FFCCBC","stair_lobby":"#FFE0B2",
  "room":"#FAFAFA","other":"#FAFAFA","entrance":"#BBDEFB","accessible_entrance":"#BBDEFB"
};

function roomLayerClass(t){
  if (t==="elevator_lobby") return "layer_lobby_elevator";
  if (t==="stair_lobby") return "layer_lobby_stair";
  if (t==="corridor"||t==="lobby"||t==="activity"||t==="atrium") return "layer_"+t;
  return "layer_room";
}
// SVG 用户空间 -> 米制局部坐标（精确复刻 Python tosvg 的反变换）
function svg2geo(sx, sy){
  var i = Math.min(GEOX.nFloors-1, Math.max(0, Math.floor(sy / GEOX.perFloor)));
  var fk = GEOX.floorKeys[i];
  var gx = (sx - GEOX.marginX) / GEOX.scale + GEOX.ox;
  var gy = GEOX.oy - (sy - i*GEOX.perFloor - GEOX.titleH - GEOX.marginY) / GEOX.scale;
  return {floor: fk, x: gx, y: gy};
}
// 屏幕坐标 -> SVG 用户空间（自动含缩放/平移的 CSS transform）
function clientToSvg(cx, cy){
  var pt = svg.createSVGPoint(); pt.x = cx; pt.y = cy;
  var m = svg.getScreenCTM(); if(!m) return {x:cx, y:cy};
  var p = pt.matrixTransform(m.inverse());
  return {x: p.x, y: p.y};
}
function annoHint(m){ var h=document.getElementById('anno-hint'); if(h) h.textContent=m; }
function annoList(m){ var h=document.getElementById('anno-list'); if(h) h.textContent=m; }
function toggleAnnoMode(){
  ANNO_MODE = !ANNO_MODE; window.annoMode = ANNO_MODE;
  var btn = document.getElementById('btn-anno-toggle');
  btn.textContent = ANNO_MODE ? '退出标注模式' : '进入标注模式';
  btn.classList.toggle('active', ANNO_MODE);
  wrapper.style.cursor = ANNO_MODE ? 'crosshair' : '';
  annoHint(ANNO_MODE ? '标注模式：在图上拖拽框选区域，松手即把落在框内(质心)的房间标记为所选类型。' : '已退出标注模式。');
}
function startAnnoDraw(e){
  if(!ANNO_MODE) return;
  annoDrawing = true; annoStartSvg = clientToSvg(e.clientX, e.clientY);
  if(annoRectEl && annoRectEl.parentNode) annoRectEl.parentNode.removeChild(annoRectEl);
  annoRectEl = document.createElementNS('http://www.w3.org/2000/svg','rect');
  annoRectEl.setAttribute('fill','rgba(33,150,243,0.18)');
  annoRectEl.setAttribute('stroke','#1976D2'); annoRectEl.setAttribute('stroke-dasharray','4,3');
  annoRectEl.setAttribute('stroke-width','1');
  svg.appendChild(annoRectEl);
  e.preventDefault(); e.stopPropagation();
}
document.addEventListener('mousemove', function(e){
  if(!annoDrawing || !ANNO_MODE) return;
  var sp = clientToSvg(e.clientX, e.clientY);
  var x = Math.min(sp.x, annoStartSvg.x), y = Math.min(sp.y, annoStartSvg.y);
  annoRectEl.setAttribute('x', x); annoRectEl.setAttribute('y', y);
  annoRectEl.setAttribute('width', Math.abs(sp.x-annoStartSvg.x));
  annoRectEl.setAttribute('height', Math.abs(sp.y-annoStartSvg.y));
});
document.addEventListener('mouseup', function(e){
  if(!annoDrawing || !ANNO_MODE) return;
  annoDrawing = false;
  var sp = clientToSvg(e.clientX, e.clientY);
  var x0 = Math.min(sp.x, annoStartSvg.x), y0 = Math.min(sp.y, annoStartSvg.y);
  var x1 = Math.max(sp.x, annoStartSvg.x), y1 = Math.max(sp.y, annoStartSvg.y);
  if(annoRectEl && annoRectEl.parentNode) annoRectEl.parentNode.removeChild(annoRectEl);
  annoRectEl = null;
  if((x1-x0)<4 || (y1-y0)<4){ annoHint('框选过小，已取消。'); return; }
  applyAnnoRect(x0,y0,x1,y1);
});
// 把框选矩形（SVG 用户空间）映射到米制，标注重心落在框内的房间
function applyAnnoRect(x0,y0,x1,y1){
  var cs = [svg2geo(x0,y0), svg2geo(x1,y0), svg2geo(x0,y1), svg2geo(x1,y1)];
  var gxmin=1e9,gxmax=-1e9,gymin=1e9,gymax=-1e9;
  cs.forEach(function(c){ gxmin=Math.min(gxmin,c.x); gxmax=Math.max(gxmax,c.x); gymin=Math.min(gymin,c.y); gymax=Math.max(gymax,c.y); });
  var fk = cs[0].floor;
  var target = document.getElementById('anno-type').value;
  var gRooms = ((FULL_DATA.floors[fk]||{}).geometry||{}).rooms || [];
  var matched = [];
  gRooms.forEach(function(r){
    var props = r.properties||{};
    var c = props.centroid;
    if(!c && r.geometry && r.geometry.coordinates && r.geometry.coordinates[0]){
      var ring = r.geometry.coordinates[0]; var sx=0,sy=0;
      ring.forEach(function(p){ sx+=p[0]; sy+=p[1]; });
      c=[sx/ring.length, sy/ring.length];
    }
    if(!c) return;
    if(c[0]>=gxmin && c[0]<=gxmax && c[1]>=gymin && c[1]<=gymax) matched.push(r);
  });
  if(matched.length===0){ annoHint('框选区域内没有房间质心，未标注（可放大后重试）。'); return; }
  var changed = [];
  matched.forEach(function(r){
    var props = r.properties||{};
    var roomId = props.roomId;
    var oldType = props.roomType || props.type || 'room';
    props.roomType = target; if('type' in props) props.type = target;
    var rid = r.id;
    var sRooms = ((FULL_DATA.floors[fk]||{}).semantic||{}).rooms || [];
    sRooms.forEach(function(s){ if(s.geometryId===rid) s.type=target; });
    var el = document.querySelector('[data-roomid="'+ (roomId||'') +'"]');
    if(el){
      var poly = el.querySelector('polygon');
      var col = ANNO_ROOM_COLORS[target] || '#FAFAFA';
      if(poly){ poly.setAttribute('fill', target==='corridor'?'none':col); poly.setAttribute('stroke', target==='staircase'?'#E57373':'#999'); }
      el.setAttribute('class', roomLayerClass(target));
    }
    ANNO_OVERRIDES.push({floor: parseInt(fk,10), roomId: roomId, type: target});
    changed.push((props.label||roomId||rid) + ' (' + oldType + '->' + target + ')');
  });
  annoList('已标注 ' + changed.length + ' 间：' + changed.join('、'));
  annoHint('已应用。点「保存 GeoJSON」写回文件，或「导出标注」供重解析后复现。');
}
function exportAnnoOverrides(){
  if(ANNO_OVERRIDES.length===0){ alert('尚无标注项。'); return; }
  var blob = new Blob([JSON.stringify({overrides: ANNO_OVERRIDES}, null, 2)], {type:'application/json'});
  var url = URL.createObjectURL(blob);
  var a = document.createElement('a'); a.href=url; a.download='room_overrides.json'; a.click();
  setTimeout(function(){ URL.revokeObjectURL(url); }, 2000);
  annoList('已导出 ' + ANNO_OVERRIDES.length + ' 条覆盖项 -> room_overrides.json');
}
</script>'''
    return tpl.replace('__CONSTS__', consts)


def main():
    geo = json.load(open(GEO_IN, encoding="utf-8"))
    node_lookup = build_node_lookup(geo)

    # 指纹采集网格（generate_fingerprint_grid.py 生成的独立清单，默认隐藏图层）
    fp_path = Path(GEO_IN).parent / "fingerprint_grid.json"
    fp_floors = {}
    if fp_path.exists():
        try:
            fp_data = json.load(open(fp_path, encoding="utf-8"))
            fp_floors = fp_data.get("floors", {})
        except Exception as e:
            print("  [warn] 读取 fingerprint_grid.json 失败：", e)
    else:
        print("  [hint] 未找到 fingerprint_grid.json，可先运行 generate_fingerprint_grid.py")

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
.header {{ max-width: 1400px; margin: 0 auto 12px; }}
.header h2 {{ margin: 0 0 4px; font-size: 20px; }}
.header .meta {{ color: #888; font-size: 12px; line-height: 1.6; }}
.header .meta .tag {{ display: inline-block; background: #E3F2FD; color: #1565C0; padding: 1px 8px; border-radius: 3px; font-weight: bold; }}
#app {{ max-width: 1400px; margin: 0 auto; display: flex; gap: 12px; align-items: flex-start; }}
#left {{ flex: 1 1 auto; min-width: 0; display: flex; flex-direction: column; gap: 10px; }}
#svg-container {{ position: relative; border: 1px solid #ddd; background: #fefefe; overflow: hidden; height: calc(100vh - 240px); min-height: 480px; border-radius: 6px; }}
#svg-wrapper {{ width: 100%; height: 100%; overflow: hidden; cursor: grab; }}
#svg-wrapper.grabbing {{ cursor: grabbing; }}
svg {{ display: block; background: #fff; }}
.layer_room polygon {{ opacity: 0.55; cursor: pointer; }}
.layer_lobby_elevator polygon, .layer_lobby_stair polygon {{ opacity: 0.85; cursor: pointer; }}
.layer_corridor polygon, .layer_lobby polygon, .layer_activity polygon, .layer_atrium polygon {{ opacity: 0.55; cursor: pointer; }}
.layer_wall path {{ stroke: #333; stroke-width: 0.8; fill: none; stroke-linecap: round; }}
.layer_building_outline polygon {{ fill: none; stroke: #222; stroke-width: 1.4; stroke-linejoin: round; stroke-linecap: round; pointer-events: none; }}
.layer_window path {{ stroke: #81D4FA; stroke-width: 0.9; fill: none; stroke-dasharray: 4,2; }}
.layer_stairs polygon {{ opacity: 0.6; cursor: pointer; }}
.layer_elevator polygon {{ opacity: 0.6; cursor: pointer; }}
.layer_column polygon {{ fill: #B0BEC5; stroke: #78909C; stroke-width: 0.3; opacity: 0.7; }}
.layer_walkable polygon {{ fill: #A5D6A7; stroke: #43A047; stroke-width: 0.4; opacity: 0.35; pointer-events: none; }}
.layer_skeleton path, .layer_skeleton polyline {{ stroke: #00ACC1; stroke-width: 1.4; fill: none; opacity: 0.85; pointer-events: none; }}
.layer_skeleton_node circle {{ fill: #E53935; stroke: #B71C1C; stroke-width: 0.6; opacity: 0.9; pointer-events: none; }}
.layer_door circle, .layer_door polygon, .layer_door rect {{ cursor: pointer; }}
.layer_door_swing *, .layer_door_opening *, .layer_door_fire * {{ cursor: pointer; }}
.layer_topo_node *, .layer_topo_edge path, .layer_topo_edge_titi path {{ cursor: pointer; }}
.layer_topo_edge path {{ stroke: #27AE60; stroke-width: 1.6; fill: none; opacity: 0.65; stroke-dasharray: 3,2; }}
.layer_topo_edge path:hover {{ stroke-width: 3.4; opacity: 0.9; }}
.layer_topo_edge_titi path {{ stroke: #9CCC65; stroke-width: 1.2; fill: none; opacity: 0.55; stroke-dasharray: 2,4; }}
.layer_topo_edge_titi path:hover {{ stroke-width: 3; opacity: 0.9; }}
.layer_risk * {{ cursor: pointer; }}
.layer_ramp *, .layer_tactile *, .layer_material * {{ cursor: pointer; }}
.layer_crossfloor path {{ stroke-width: 1.6; fill: none; stroke-dasharray: 6,4; opacity: 0.65; cursor: pointer; }}
text {{ font-family: 'Microsoft YaHei', 'PingFang SC', sans-serif; pointer-events: none; }}
.selected {{ stroke: #FFC107 !important; stroke-width: 2.4 !important; }}
/* 点击拓扑节点时被选中节点 / 相连边高亮（直接作用于几何图形，确保可见） */
.layer_topo_node.selected circle, .layer_topo_node.selected rect, .layer_topo_node.selected polygon {{ stroke: #FFC107 !important; stroke-width: 2.6 !important; }}
.layer_topo_edge.selected path, .layer_topo_edge_titi.selected path {{ stroke: #FFC107 !important; stroke-width: 2.2 !important; opacity: 0.95 !important; stroke-dasharray: none !important; }}
.layer_topo_node.path-start circle, .layer_topo_node.path-start rect, .layer_topo_node.path-start polygon {{ stroke: #4CAF50 !important; stroke-width: 3 !important; }}
.layer_topo_node.path-end circle, .layer_topo_node.path-end rect, .layer_topo_node.path-end polygon {{ stroke: #E91E63 !important; stroke-width: 3 !important; }}
.layer_topo_node.path-via circle, .layer_topo_node.path-via rect, .layer_topo_node.path-via polygon {{ stroke: #FF9800 !important; stroke-width: 2.2 !important; }}
#path-bar {{ display: flex; flex-wrap: wrap; gap: 8px; align-items: center; padding: 8px 10px; background: #FFF3E0; border: 1px solid #FFCC80; border-radius: 6px; font-size: 12px; margin-bottom: 8px; }}
#path-bar button {{ border: 1px solid #ccc; background: #fff; border-radius: 4px; padding: 4px 10px; cursor: pointer; font-size: 12px; }}
#path-bar button.active {{ background: #E91E63; color: #fff; border-color: #C2185B; }}
#path-bar .hint {{ color: #666; }}
#path-bar .result {{ color: #BF360C; font-weight: bold; }}
#edge-bar {{ display: flex; flex-wrap: wrap; gap: 8px; align-items: center; padding: 8px 10px; background: #FCE4EC; border: 1px solid #F48FB1; border-radius: 6px; font-size: 12px; margin-bottom: 8px; }}
#edge-bar button {{ border: 1px solid #ccc; background: #fff; border-radius: 4px; padding: 4px 10px; cursor: pointer; font-size: 12px; }}
#edge-bar button.active {{ background: #E91E63; color: #fff; border-color: #C2185B; }}
#edge-bar .hint {{ color: #666; }}
#edge-bar .result {{ color: #AD1457; font-weight: bold; }}
/* 区域标注工具条 */
#anno-bar {{ display: flex; flex-wrap: wrap; gap: 8px; align-items: center; padding: 8px 10px; background: #E8F5E9; border: 1px solid #A5D6A7; border-radius: 6px; font-size: 12px; margin-bottom: 8px; }}
#anno-bar .hint {{ color: #555; }}
#anno-bar .result {{ color: #2E7D32; font-weight: bold; }}
#anno-bar button {{ border: 1px solid #ccc; background: #fff; border-radius: 4px; padding: 4px 10px; cursor: pointer; font-size: 12px; }}
#anno-bar button.active {{ background: #2E7D32; color: #fff; border-color: #1B5E20; }}
#anno-bar select {{ font-size: 12px; padding: 3px 6px; border-radius: 4px; border: 1px solid #ccc; }}
/* 点击拓扑节点时直接可达（邻居）节点的高亮，用青色与选中节点区分 */
.layer_topo_node.neighbor circle, .layer_topo_node.neighbor rect, .layer_topo_node.neighbor polygon {{ stroke: #00BCD4 !important; stroke-width: 2.6 !important; }}
.layer_fingerprint circle {{ cursor: pointer; }}
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
/* 图例：移到「要素详情」下方，占据左栏整宽，内部自适应多列 */
#legend-panel {{ background: #fff; border: 1px solid #e0e0e0; border-radius: 6px; padding: 12px 14px; font-size: 12px; }}
#legend-panel h4 {{ margin: 0 0 8px; font-size: 13px; border-bottom: 1px solid #eee; padding-bottom: 4px; }}
#legend-panel .lg-grid {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(190px, 1fr)); gap: 2px 20px; align-items: start; }}
.lg-sec {{ margin-bottom: 8px; break-inside: avoid; }}
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
/* ---------- 右栏：路径规划完整路径列表 ---------- */
#route-panel {{ width: 288px; flex: 0 0 288px; background: #fff; border: 1px solid #e0e0e0; border-radius: 6px; padding: 12px 12px 10px; font-size: 12px; max-height: calc(100vh - 120px); display: flex; flex-direction: column; align-self: flex-start; position: sticky; top: 12px; }}
#route-panel h4 {{ margin: 0 0 8px; font-size: 13px; border-bottom: 1px solid #eee; padding-bottom: 5px; display: flex; justify-content: space-between; align-items: center; }}
#route-panel h4 .rp-mode {{ font-size: 11px; font-weight: normal; color: #fff; background: #1565C0; border-radius: 3px; padding: 1px 7px; }}
#route-summary {{ font-size: 12px; line-height: 1.7; margin-bottom: 8px; }}
#route-summary .rp-od {{ color: #333; font-weight: bold; word-break: break-all; }}
#route-summary .rp-kv {{ display: flex; justify-content: space-between; color: #666; border-bottom: 1px dashed #eee; padding: 2px 0; }}
#route-summary .rp-kv b {{ color: #AD1457; }}
#route-summary .rp-note {{ margin-top: 5px; background: #FFF8E1; border: 1px solid #FFE082; color: #8D6E63; border-radius: 4px; padding: 4px 7px; line-height: 1.5; }}
.rp-empty {{ color: #999; line-height: 1.6; }}
#route-steps {{ list-style: none; margin: 0; padding: 0; overflow-y: auto; flex: 1 1 auto; min-height: 0; }}
#route-steps li {{ display: flex; gap: 7px; padding: 5px 4px; border-radius: 4px; cursor: pointer; border-bottom: 1px solid #f4f4f4; }}
#route-steps li:hover {{ background: #FCE4EC; }}
#route-steps li.active {{ background: #F8BBD0; }}
.rp-idx {{ flex: 0 0 18px; width: 18px; height: 18px; border-radius: 50%; color: #fff; font-size: 10px; line-height: 18px; text-align: center; font-weight: bold; }}
.rp-body {{ flex: 1 1 auto; min-width: 0; }}
.rp-name {{ color: #222; font-weight: bold; line-height: 1.4; word-break: break-all; }}
.rp-meta {{ color: #888; font-size: 11px; line-height: 1.5; }}
.rp-tag {{ display: inline-block; padding: 0 5px; border-radius: 3px; font-size: 10px; margin-right: 4px; color: #fff; }}
.rp-seg {{ flex: 0 0 auto; text-align: right; color: #AD1457; font-size: 11px; white-space: nowrap; }}
.rp-seg .rp-cum {{ display: block; color: #aaa; font-size: 10px; }}
.rp-xf {{ color: #E53935; font-weight: bold; }}
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
  <label><input type="checkbox" checked onchange="toggleLayer('corridor', this.checked)"> 走道</label>
  <label><input type="checkbox" checked onchange="toggleLayer('lobby', this.checked)"> 门厅</label>
  <label><input type="checkbox" checked onchange="toggleLayer('activity', this.checked)"> 活动区</label>
  <label><input type="checkbox" checked onchange="toggleLayer('atrium', this.checked)"> 中庭</label>
  <label><input type="checkbox" checked onchange="toggleLayer('lobby_elevator', this.checked)"> 电梯前室</label>
  <label><input type="checkbox" checked onchange="toggleLayer('lobby_stair', this.checked)"> 楼梯前室</label>
  <label><input type="checkbox" checked onchange="toggleLayer('walkable', this.checked)"> 可通行区域</label>
  <label><input type="checkbox" checked onchange="toggleLayer('skeleton', this.checked)"> 走廊骨架</label>
  <label><input type="checkbox" onchange="toggleLayer('skeleton_node', this.checked)"> 骨架交叉口</label>
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
  <label><input type="checkbox" onchange="toggleLayer('topo_edge_titi', this.checked)" title="交叉口(TI)之间的拓扑边，默认隐藏；走廊连通主视觉由青色骨架展示">交叉口连接边</label>
  <label><input type="checkbox" checked onchange="toggleLayer('crossfloor', this.checked)"> 跨层连接</label>
  <label><input type="checkbox" onchange="toggleLayer('risk', this.checked)"> 风险点</label>
  <label><input type="checkbox" onchange="toggleLayer('ramp', this.checked)"> 坡道</label>
  <label><input type="checkbox" onchange="toggleLayer('tactile', this.checked)"> 盲道</label>
  <label><input type="checkbox" onchange="toggleLayer('material', this.checked)"> 地面材质</label>
  <label><input type="checkbox" onchange="toggleLayer('fingerprint', this.checked)"> 指纹网格</label>
  <button class="bulk-btn primary" onclick="setAll(true)" title="一键全选所有图层">全选</button>
  <button class="bulk-btn" onclick="setAll(false)" title="一键取消所有图层">全不选</button>
  <button class="bulk-btn" onclick="exportSelectedSVG()" title="将当前勾选（所选）的图层导出为独立的 SVG 图片文件">导出所选图层 SVG</button>
</div>
<div id="path-bar">
  <b>路径规划</b>
  <button type="button" id="btn-path-mode" onclick="togglePathMode()">选点导航</button>
  <label>模式
    <select id="path-mode-select" onchange="recomputePathIfReady()">
      <option value="normal">普通</option>
      <option value="blind">视障</option>
      <option value="wheelchair">轮椅</option>
    </select>
  </label>
  <button type="button" onclick="clearPath()">清除路径</button>
  <span class="hint" id="path-hint">开启后依次点击两个拓扑节点（起点→终点）</span>
  <span class="result" id="path-result"></span>
</div>
<div id="edge-bar">
  <b>拓扑边编辑</b>
  <button type="button" id="btn-del-edge" onclick="deleteSelectedEdge()" disabled title="先单击选中一条拓扑边">删除选中拓扑边</button>
  <button type="button" onclick="saveGeojson()" title="将增删的拓扑边写回 GeoJSON 文件（弹窗选择保存位置）">保存 GeoJSON</button>
  <span class="hint" id="edge-hint">双击拓扑节点添加拓扑边（依次双击两个节点）；单击拓扑边后可用「删除选中拓扑边」</span>
  <span class="result" id="edge-list"></span>
</div>
<div id="anno-bar">
  <b>区域标注</b>
  <button type="button" id="btn-anno-toggle" onclick="toggleAnnoMode()">进入标注模式</button>
  <label>目标类型
    <select id="anno-type">
      <option value="elevator_lobby">电梯前厅</option>
      <option value="stair_lobby">楼梯前厅</option>
      <option value="lobby">门厅/大堂</option>
      <option value="corridor">走廊</option>
      <option value="activity">活动室</option>
      <option value="atrium">中庭</option>
      <option value="room">普通房间</option>
    </select>
  </label>
  <button type="button" onclick="exportAnnoOverrides()" title="导出已标注的覆盖项，供重新解析后 apply_room_overrides.py 复现">导出标注(JSON)</button>
  <button type="button" onclick="saveGeojson()" title="将标注类型写回 GeoJSON 文件">保存 GeoJSON</button>
  <span class="hint" id="anno-hint">点「进入标注模式」后，在图上拖拽框选区域，松手即把该层落在框内（质心）的房间标记为所选类型。</span>
  <span class="result" id="anno-list"></span>
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

        # 1b. 走廊中轴骨架（T3–T5，来自 floors.N.skeleton）
        skel_fc = fd.get("skeleton") or {}
        skel_feats = skel_fc.get("features") or []
        n_skel = 0
        for feat in skel_feats:
            geom_s = feat.get("geometry") or {}
            if geom_s.get("type") != "LineString":
                continue
            coords = geom_s.get("coordinates") or []
            if len(coords) < 2:
                continue
            pts = " ".join(f"{tosvg(x, y)[0]},{tosvg(x, y)[1]}" for x, y in coords)
            L = (feat.get("properties") or {}).get("length_m", "")
            tip = f"骨架段 {feat.get('id','')}\n长度: {L}m"
            parts.append(
                f'<g class="layer_skeleton"><polyline points="{pts}" '
                f'fill="none" stroke="#00ACC1" stroke-width="1.4" '
                f'opacity="0.85"><title>{tip}</title></polyline></g>\n'
            )
            n_skel += 1
        # 骨架交叉口（TI 中 type=intersection 且来自骨架）
        n_junc = 0
        for n in (topo.get("nodes") or []):
            if n.get("type") != "intersection":
                continue
            # 仅当有骨架时绘制红色交叉口点，避免与旧质心 TI 混淆过多
            if not skel_feats:
                break
            cx, cy = n.get("coordinates") or [0, 0]
            sx, sy = tosvg(cx, cy)
            parts.append(
                f'<g class="layer_skeleton_node">'
                f'<circle cx="{sx}" cy="{sy}" r="2.2" '
                f'fill="#E53935" stroke="#B71C1C" stroke-width="0.5"/>'
                f'</g>\n'
            )
            n_junc += 1
        if n_skel:
            print(f"  [F{fk}] 走廊骨架: {n_skel} 段, 交叉口标记 {n_junc} 个")

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
            # 开放空间/前室为完全独立图层（不受「房间」开关影响）：
            # 交互走 [data-info] 属性、导出走 [class*="layer_"]，脱离 layer_room 无副作用
            if rtype == "elevator_lobby":
                layer_cls = "layer_lobby_elevator"
            elif rtype == "stair_lobby":
                layer_cls = "layer_lobby_stair"
            elif rtype in ("corridor", "lobby", "activity", "atrium"):
                layer_cls = "layer_" + rtype
            else:
                layer_cls = "layer_room"
            parts.append(
                f'<g class="{layer_cls}" data-roomid="{p.get("roomId","")}" {info_attr({"tip": tip, "detail": det})}>'
                f'<polygon points="{pts}" fill="{_fill}" stroke="{_stroke}" stroke-width="{_sw}" stroke-dasharray="{_dash}"/></g>\n'
            )
            if label:
                cx_s = sum(p_[0] for p_ in ring[:-1]) / max(len(ring) - 1, 1)
                cy_s = sum(p_[1] for p_ in ring[:-1]) / max(len(ring) - 1, 1)
                sx_s, sy_s = tosvg(cx_s, cy_s)
                parts.append(
                    f'<g class="{layer_cls}">{info_attr({"tip": tip, "detail": det}) if False else ""}'
                    f'<text x="{sx_s}" y="{sy_s}" font-size="6" text-anchor="middle" '
                    f'fill="#333">{label}</text></g>\n'
                )
        if n_skip_bbox:
            print(f"  [F{fk}] 跳过 {n_skip_bbox} 个文字标签包围盒（面积<{LABEL_BBOX_MAX_AREA}m² 且长宽比≥{LABEL_BBOX_MIN_ASPECT}）")

        # 2. 墙体
        MAT_CN = {"concrete": "混凝土", "brick": "砖墙", "partition": "轻质隔墙"}
        for w in geom.get("walls", []):
            c = w["geometry"]["coordinates"]
            x1, y1 = tosvg(c[0][0], c[0][1])
            x2, y2 = tosvg(c[1][0], c[1][1])
            wp = w.get("properties", {})
            t_m = wp.get("thickness")
            mat = wp.get("material")
            t_disp = f"{t_m*100:.0f} cm" if isinstance(t_m, (int, float)) else "—"
            mat_disp = MAT_CN.get(mat, mat or "—")
            wtip = f"墙体\\n厚度：{t_disp}\\n材质：{mat_disp}"
            wdet = {"title": "墙体", "rows": [
                ("厚度", t_disp),
                ("材质", mat_disp),
                ("材质来源", wp.get("materialSource", "—")),
                ("来源图层", wp.get("sourceLayer", "—")),
            ]}
            parts.append(
                f'<g class="layer_wall" {info_attr({"tip": wtip, "detail": wdet, "kind": "wall"})}>'
                f'<path d="M {x1} {y1} L {x2} {y2}"/></g>\n'
            )

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
            # 新属性：开启方向 / 合页侧 / 子类
            od = p.get("openDirection")
            OD_CN = {"inward": "内开", "outward": "外开", "none": "无（门洞）"}
            hs = p.get("hingeSide")
            HS_CN = {"left": "左铰链", "right": "右铰链"}
            od_disp = OD_CN.get(od, od or "—")
            hs_disp = HS_CN.get(hs, hs or "—")
            if od in ("inward", "outward") and hs:
                od_full = f"{od_disp} · {hs_disp}"
            else:
                od_full = od_disp
            sub = p.get("doorSubType") or "—"
            wa = p.get("wheelchairAccessible")
            wa_disp = "是" if wa else ("否" if wa is False else "—")
            swing_room = p.get("swingIntoRoom") or "—"
            surv = p.get("surveyRequired") or []
            surv_disp = "、".join(surv) if surv else "无"
            tip = f"{dname}\\n宽度：{w:.2f}m\\n开启：{od_full}"
            det = {"title": dname,
                   "rows": [
                       ("门编号", dr.get("id") or p.get("id") or "—"),
                       ("类型", f"{dname}（{dtype}）"),
                       ("子类", sub),
                       ("开启方向", od_full),
                       ("摆向房间", swing_room),
                       ("宽度", f"{w:.2f} m"),
                       ("轮椅可达", wa_disp),
                       ("归属房间", "、".join(p.get("rooms", [])) or "—"),
                       ("来源图层", p.get("sourceLayer", "—")),
                       ("待现场核实", surv_disp),
                   ]}
            attr = info_attr({"tip": tip, "detail": det, "id": dr.get("id", p.get("id", "")), "kind": "door"})
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
            _rl = n.get("riskLevel")
            if isinstance(_rl, (int, float)):
                rows.append(("风险等级", f"{_rl:g}"))
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
                # 门显示完整编号（楼层-类型-序号），替代类型名"普通门/防火门/门洞"
                door_no = nid
                parts.append(
                    f'<g class="layer_topo_node" {attr}>'
                    f'<circle cx="{sx}" cy="{sy}" r="2.4" fill="{NODE_COLORS["doorway"]}" opacity="0.85"/>'
                    f'<text x="{float(sx)+3:.1f}" y="{float(sy)+2:.1f}" font-size="4.2" '
                    f'fill="{NODE_COLORS["doorway"]}">{door_no}</text></g>\n'
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
        # 骨架模式下 TI↔TI 连接边可达数十万条（早期全量渲染让 HTML 数百 MB，
        # 已改为沿骨架段邻接，F1 53 万→2069 条）。TI↔TI 边独立成
        # layer_topo_edge_titi 图层（默认隐藏，由图层面板开关控制），
        # 走廊连通主视觉仍由 layer_skeleton（青色 polyline）展示。
        n_titi = 0
        for e in topo.get("edges", []):
            n1 = node_map.get(e.get("from"))
            n2 = node_map.get(e.get("to"))
            if not n1 or not n2:
                continue
            is_titi = (n1.get("type") == "intersection"
                       and n2.get("type") == "intersection")
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
                              "to": e.get("to", ""), "id": e.get("id", ""),
                              "kind": "edge"})
            cls = "layer_topo_edge_titi" if is_titi else "layer_topo_edge"
            parts.append(
                f'<g class="{cls}" {attr}><path d="M {x1} {y1} L {x2} {y2}"/></g>\n'
            )
            if is_titi:
                n_titi += 1

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

        # 11. 指纹采集网格（独立清单文件，默认隐藏）
        fp_fd = fp_floors.get(str(fk))
        if fp_fd:
            n_fp = 0
            for p in fp_fd.get("points", []):
                cx, cy = p["coordinates"][0], p["coordinates"][1]
                sx, sy = tosvg(cx, cy)
                is_safe = p.get("regionType") == "safe"
                col = "#FF7043" if is_safe else "#42A5F5"
                r = 2.4 if is_safe else 1.8
                prio = p.get("priority", 3)
                src = p.get("source", "")
                tip = f"指纹采集点 {p.get('id','')}\\n区域：{'安全节点' if is_safe else '普通'} · 优先级 {prio} · 来源 {src}"
                det = {"title": f"指纹采集点 {p.get('id','')}", "rows": [
                    ("楼层", f"{p.get('floor','?')}F"),
                    ("区域类型", "安全节点" if is_safe else "普通"),
                    ("采集优先级", str(prio)),
                    ("来源", src),
                    ("坐标", f"({cx:.2f}, {cy:.2f})"),
                ]}
                if p.get("nearNodeId"):
                    det["rows"].append(("邻近节点", f"{p['nearNodeId']} ({p.get('nearNodeType','')})"))
                attr = info_attr({"tip": tip, "detail": det, "kind": "fingerprint", "id": p.get("id", "")})
                parts.append(
                    f'<g class="layer_fingerprint" {attr}>'
                    f'<circle cx="{sx}" cy="{sy}" r="{r}" fill="{col}" '
                    f'fill-opacity="0.85" stroke="#fff" stroke-width="0.4"/></g>\n'
                )
                n_fp += 1
            if n_fp:
                print(f"  [F{fk}] 指纹网格图层: {n_fp} 个点")

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


    # ---------------- 路径规划图数据（前端 Dijkstra） ----------------
    # 预计算路由规则辅助量（对齐 src/route_rules.py）：门类型、房间最佳门、
    # 穿墙 TI<->TI 边集合。前端据此在浏览器内执行与后端完全一致的受限 Dijkstra。
    _rule_extras = compute_route_rule_extras(geo)
    _edge_door_type_map = _rule_extras["edge_door_type"]
    _room_best_door = _rule_extras["room_best_door"]
    _wall_crossing_titi = _rule_extras["wall_crossing_titi"]

    path_nodes = {}
    path_edges = []
    for fi, fk in enumerate(sorted_floors):
        fd = geo["floors"][fk]
        fbase_y = fi * svh_per_floor
        for n in (fd.get("topology") or {}).get("nodes") or []:
            cx, cy = n["coordinates"]
            sx = MARGIN_X + (cx - ox) * SCALE
            sy = fbase_y + FLOOR_TITLE_H + MARGIN_Y + (oy - cy) * SCALE
            nd = {
                "id": n["id"],
                "type": n.get("type"),
                "label": n.get("label") or "",
                "floor": int(fk),
                "x": round(sx, 1),
                "y": round(sy, 1),
                "mx": cx,
                "my": cy,
                "facilityType": n.get("facilityType"),
                "roomType": n.get("roomType"),
                "roomId": n.get("roomId"),
                "doorType": n.get("doorType"),
                "rooms": n.get("rooms") or [],
            }
            if n.get("type") == "room" and n["id"] in _room_best_door:
                nd["bestDoorType"] = _room_best_door[n["id"]]
            path_nodes[n["id"]] = nd
        for e in (fd.get("topology") or {}).get("edges") or []:
            edt = _edge_door_type_map.get(e.get("id"))
            path_edges.append({
                "id": e.get("id"),
                "from": e.get("from"),
                "to": e.get("to"),
                "distance": float(e.get("distance") or 0),
                "accessibilityLevel": e.get("accessibilityLevel", 0),
                "blindAccessible": e.get("blindAccessible", True),
                "wheelchairAccessible": e.get("wheelchairAccessible", True),
                "crossFloor": False,
                "type": e.get("type"),
                "doorType": edt,
                "wallCrossing": e.get("id") in _wall_crossing_titi,
            })
    for e in geo.get("crossFloorEdges") or []:
        path_edges.append({
            "id": e.get("id"),
            "from": e.get("from"),
            "to": e.get("to"),
            "distance": float(e.get("distance") or 0),
            "accessibilityLevel": e.get("accessibilityLevel", 0),
            "blindAccessible": e.get("blindAccessible", True),
            "wheelchairAccessible": e.get("wheelchairAccessible", True),
            "crossFloor": True,
            "type": e.get("type"),
            "doorType": None,
            "wallCrossing": False,
        })
    path_graph_js = json.dumps(
        {"nodes": path_nodes, "edges": path_edges},
        ensure_ascii=False, separators=(",", ":"))
    parts.append(
        f'<script type="application/json" id="path-graph-data">{path_graph_js}</script>\n'
    )
    # 完整 GeoJSON 数据：供「拓扑边编辑」在浏览器内增删边后整体写回文件
    full_geojson_js = json.dumps(geo, ensure_ascii=False, separators=(",", ":"))
    parts.append(
        f'<script type="application/json" id="full-geojson-data">{full_geojson_js}</script>\n'
    )

    # ---------------- 图例 + 详情面板 + JS ----------------
    parts.append('''<g id="manual-edge-layer"></g>
</svg>
</div><!-- /svg-wrapper -->
</div><!-- /svg-container -->
<div id="detail"><h4>点击任意要素查看详情</h4><div style="color:#999;font-size:12px">悬停查看提示，点击锁定详情；再次点击同一要素可取消选中。点击拓扑节点会高亮其<b style="color:#FFC107">相连边</b>与<b style="color:#00BCD4">直接可达节点</b>（青色）。</div></div>
<div id="legend-panel">
  <h4>图例说明 (v9)</h4>
  <div class="lg-grid">
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
  </div><!-- /lg-grid -->
</div><!-- /legend-panel -->
</div><!-- /left -->
<div id="route-panel">
  <h4><span>路径规划 · 完整路径</span><span class="rp-mode" id="rp-mode">普通</span></h4>
  <div id="route-summary" class="rp-empty">尚未规划路径。<br>点上方「选点导航」后依次点击起点、终点拓扑节点，这里会列出完整途经节点清单（可点击定位）。</div>
  <ol id="route-steps"></ol>
</div><!-- /route-panel -->
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
  var rect = wrapper.getBoundingClientRect();
  var mx = e.clientX - rect.left, my = e.clientY - rect.top;
  if (e.ctrlKey) {{
    // 触控板双指捏合 / Ctrl+滚轮：围绕光标平滑缩放
    var factor = Math.exp(-e.deltaY * 0.012);
    var ns = Math.max(0.15, Math.min(20, scale * factor));
    var ratio = ns / scale;
    translateX = mx - ratio * (mx - translateX);
    translateY = my - ratio * (my - translateY);
    scale = ns; applyTransform(); setZoomInfo();
  }} else if (e.deltaMode === 1) {{
    // 鼠标滚轮（行模式）：保持原缩放行为（围绕光标）
    var delta = e.deltaY > 0 ? 0.9 : 1.1;
    var ns2 = Math.max(0.15, Math.min(20, scale * delta));
    var ratio2 = ns2 / scale;
    translateX = mx - ratio2 * (mx - translateX);
    translateY = my - ratio2 * (my - translateY);
    scale = ns2; applyTransform(); setZoomInfo();
  }} else {{
    // 触控板双指滑动（像素模式平滑滚动）：平移画布（跟手）
    translateX += e.deltaX;
    translateY += e.deltaY;
    applyTransform();
  }}
}}, {{ passive: false }});

// Safari 旧式触控板手势（gesture 事件）：捏合缩放兼容
var lastGestureScale = 1;
wrapper.addEventListener('gesturestart', function(e) {{ e.preventDefault(); lastGestureScale = 1; }});
wrapper.addEventListener('gesturechange', function(e) {{
  e.preventDefault();
  var rect = wrapper.getBoundingClientRect();
  var mx = e.clientX - rect.left, my = e.clientY - rect.top;
  var factor = e.scale / lastGestureScale;
  lastGestureScale = e.scale;
  var ns = Math.max(0.15, Math.min(20, scale * factor));
  var ratio = ns / scale;
  translateX = mx - ratio * (mx - translateX);
  translateY = my - ratio * (my - translateY);
  scale = ns; applyTransform(); setZoomInfo();
}});

wrapper.addEventListener('mousedown', function(e) {{
  if (window.annoMode) {{ startAnnoDraw(e); return; }}
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
// 单击选中延迟抑制：双击（拓扑边加边）时取消挂起的单击选中，避免详情面板闪烁；
// 拓扑边点击即时响应（不受双击抑制影响，双击边无操作）。
var clickTimer = null;
wrapper.addEventListener('click', function(e) {{
  if (window.annoMode) return;   // 标注模式下拖拽框选，不触发要素选中
  var t = e.target.closest('[data-info]');
  if (!t) return;
  var info = t.getAttribute('data-info');
  var d; try {{ d = JSON.parse(info); }} catch (err) {{ return; }}
  // 拓扑边：即时选中/取消 + 详情 + 记录选中（启用删除按钮）
  if (d.kind === 'edge' && d.id) {{
    if (t.classList.contains('selected')) {{
      clearHighlight(); resetDetail();
      selectedEdgeId = null; selectedEdgeEl = null; updateDeleteBtn();
      return;
    }}
    clearHighlight(); t.classList.add('selected');
    showDetail(d.detail || {{ title: d.tip || '详情', rows: [] }});
    selectedEdgeId = d.id; selectedEdgeEl = t; updateDeleteBtn();
    return;
  }}
  if (clickTimer) {{ clearTimeout(clickTimer); clickTimer = null; }}
  clickTimer = setTimeout(function() {{
    clickTimer = null;
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
      document.querySelectorAll('.layer_topo_edge, .layer_topo_edge_titi').forEach(function(g) {{
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
  }}, 250);
}});

// ---- 图层开关 ----
var allLayers = ['room','corridor','lobby','activity','atrium','lobby_elevator','lobby_stair','walkable','skeleton','skeleton_node','wall','window','stairs','elevator','column','building_outline',
  'door_swing','door_opening','door_fire',
  'topo_node','topo_edge','topo_edge_titi','crossfloor','risk','ramp','tactile','material','fingerprint'];
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

// ---- 路径规划：图上选点 + Dijkstra 最优路径 ----
var pathMode = false;
var pathStart = null, pathEnd = null;
var PATH_GRAPH = null;
(function(){
  var el = document.getElementById('path-graph-data');
  if (el) { try { PATH_GRAPH = JSON.parse(el.textContent); } catch(e) { console.warn(e); } }
})();

function togglePathMode() {
  pathMode = !pathMode;
  var btn = document.getElementById('btn-path-mode');
  if (btn) btn.classList.toggle('active', pathMode);
  var hint = document.getElementById('path-hint');
  if (pathMode) {
    ensureLayer('topo_node', true);
    ensureLayer('topo_edge', true);
    if (hint) hint.textContent = '请点击起点拓扑节点…';
    pathStart = pathEnd = null;
    clearPathVisual();
    resetRoutePanel();
  } else {
    if (hint) hint.textContent = '开启后依次点击两个拓扑节点（起点→终点）';
  }
}

function clearPathVisual() {
  document.querySelectorAll('.path-start,.path-end,.path-via').forEach(function(el){
    el.classList.remove('path-start','path-end','path-via');
  });
  var g = document.getElementById('path-route-layer');
  if (g) g.innerHTML = '';
}

function clearPath() {
  pathStart = pathEnd = null;
  clearPathVisual();
  var r = document.getElementById('path-result');
  if (r) r.textContent = '';
  var h = document.getElementById('path-hint');
  if (h) h.textContent = pathMode ? '请点击起点拓扑节点…' : '开启后依次点击两个拓扑节点（起点→终点）';
  resetRoutePanel();
}

function edgeAllowed(e, mode) {
  if (mode === 'blind') {
    if (e.blindAccessible === false) return false;
    if (Number(e.accessibilityLevel) === 999) return false;
    // 规则 2：盲模式跨层必须走电梯，禁用楼梯跨层边
    if (e.crossFloor && e.type === 'staircase') return false;
  }
  if (mode === 'wheelchair') {
    if (e.wheelchairAccessible === false) return false;
    if (Number(e.accessibilityLevel) === 999) return false;
  }
  return true;
}

// 门类型边权惩罚（米），越小越优先。与 route_rules.DOOR_PENALTY 一致。
function doorPenalty(dt) {
  var P = { swing: 0.0, fire: 0.5, opening: 1.0 };
  return (dt in P) ? P[dt] : 9;
}

function isSameFloor(s, e) {
  var ns = PATH_GRAPH.nodes[s], ne = PATH_GRAPH.nodes[e];
  return !!(ns && ne && ns.floor === ne.floor);
}

// 门类型边权（仅在 room<->door 边施加，避免每扇门重复惩罚）
function edgeWeight(e, nodes) {
  var w = Number(e.distance) || 0;
  var dt = e.doorType;
  if (dt) {
    var a = nodes[e.from], b = nodes[e.to];
    if ((a && a.type === 'room') || (b && b.type === 'room')) {
      w += doorPenalty(dt);
    }
  }
  return w;
}

// 规则 3：禁止 room->door->room 穿透（门必须连接公共空间）
function doorPassThroughBlocked(u, prev, nb, nodes) {
  var nu = nodes[u];
  if (!nu || nu.type !== 'doorway') return false;
  if (u === prev || u === nb) return false;
  var np = nodes[prev], nnb = nodes[nb];
  if (np && nnb && np.type === 'room' && nnb.type === 'room') return true;
  return false;
}

// 构造受限邻接表（对齐 route_rules._build_adjacency）
function buildPathAdj(mode, doorFilter, allowWall) {
  var nodes = PATH_GRAPH.nodes;
  var adj = {};
  (PATH_GRAPH.edges || []).forEach(function(e) {
    if (!edgeAllowed(e, mode)) return;
    // 规则 3：剔除穿墙 TI<->TI 边（默认不穿墙；桥边回退时 allowWall=true 重新纳入）
    if (!allowWall && e.wallCrossing) return;
    var a = e.from, b = e.to;
    if (!nodes[a] || !nodes[b]) return;
    // 规则 3：房间只可使用优先级不低于自身最佳门的门
    if (doorFilter) {
      var ta = nodes[a].type, tb = nodes[b].type;
      if (ta === 'room' && tb === 'doorway') {
        var best = nodes[a].bestDoorType;
        if (best != null && doorPenalty(e.doorType) > doorPenalty(best)) return;
      }
      if (tb === 'room' && ta === 'doorway') {
        var best2 = nodes[b].bestDoorType;
        if (best2 != null && doorPenalty(e.doorType) > doorPenalty(best2)) return;
      }
    }
    var w = edgeWeight(e, nodes);
    if (!adj[a]) adj[a] = [];
    if (!adj[b]) adj[b] = [];
    adj[a].push({ to: b, w: w, id: e.id });
    adj[b].push({ to: a, w: w, id: e.id });
  });
  return adj;
}

// 核心 Dijkstra（应用规则 1 中间节点白名单 + 规则 3 门穿透防护）
function dijkstraCore(startId, endId, mode, adj) {
  var nodes = PATH_GRAPH.nodes;
  // 规则 1：同层禁 facility 中转；跨层允许 facility 中转（电梯/楼梯用于跨层）
  var MID_TYPES = isSameFloor(startId, endId)
    ? { intersection: 1, facility_entrance: 1, doorway: 1 }
    : { intersection: 1, facility_entrance: 1, doorway: 1, facility: 1 };
  var dist = {}, prev = {}, prevEdge = {};
  Object.keys(nodes).forEach(function(id){ dist[id] = Infinity; });
  dist[startId] = 0;
  var pq = [[0, startId]]; // [d, id] simple list
  while (pq.length) {
    pq.sort(function(a,b){ return a[0]-b[0]; });
    var cur = pq.shift();
    var d = cur[0], u = cur[1];
    if (d !== dist[u]) continue;
    if (u === endId) break;
    // 中间节点白名单：房间(room)禁止中转；同层额外禁 facility 中转
    if (u !== startId && u !== endId) {
      var _ut = nodes[u] && nodes[u].type;
      if (!MID_TYPES[_ut]) continue;
    }
    var nbrs = adj[u] || [];
    for (var i = 0; i < nbrs.length; i++) {
      var nb = nbrs[i];
      // 规则 3：门不得作为两房间直连通道
      var prevU = (prev[u] != null) ? prev[u] : u;
      if (doorPassThroughBlocked(u, prevU, nb.to, nodes)) continue;
      var nd = d + nb.w;
      if (nd < dist[nb.to]) {
        dist[nb.to] = nd;
        prev[nb.to] = u;
        prevEdge[nb.to] = nb.id;
        pq.push([nd, nb.to]);
      }
    }
  }
  if (dist[endId] === Infinity) return null;
  var path = [];
  var edgeIds = [];
  for (var at = endId; at; at = prev[at]) {
    path.push(at);
    if (prevEdge[at]) edgeIds.push(prevEdge[at]);
    if (at === startId) break;
  }
  path.reverse();
  edgeIds.reverse();
  // 距离对齐 route_rules：保留 2 位小数（含门类型边权惩罚）
  return { nodes: path, edges: edgeIds, distance: Math.round(dist[endId] * 100) / 100 };
}

function dijkstra(startId, endId, mode) {
  if (!PATH_GRAPH) return null;
  // 三层回退（对齐 route_rules.shortest_path）：
  // 1) 仅用最佳门 + 不穿墙 TI<->TI 边；
  // 2) 若不可达（最佳门未接入路网）回退允许所有门；
  // 3) 仍不可达（穿墙边是桥边）回退纳入穿墙边保连通。
  var sp = dijkstraCore(startId, endId, mode, buildPathAdj(mode, true, false));
  var note = null;
  if (!sp) {
    sp = dijkstraCore(startId, endId, mode, buildPathAdj(mode, false, false));
    if (sp) note = 'door_fallback';
  }
  if (!sp) {
    sp = dijkstraCore(startId, endId, mode, buildPathAdj(mode, true, true));
    if (sp) note = 'wall_fallback';
  }
  if (sp && note) sp.note = note;
  return sp;
}

function markNodeClass(id, cls) {
  document.querySelectorAll('.layer_topo_node').forEach(function(g) {
    var f = g.getAttribute('data-info');
    if (!f) return;
    try {
      var nd = JSON.parse(f);
      if (nd.id === id) g.classList.add(cls);
    } catch(e) {}
  });
}

function drawPath(result) {
  clearPathVisual();
  if (!result || !result.nodes.length) return;
  var svg = document.getElementById('main-svg');
  var layer = document.getElementById('path-route-layer');
  if (!layer) {
    layer = document.createElementNS('http://www.w3.org/2000/svg', 'g');
    layer.setAttribute('id', 'path-route-layer');
    layer.setAttribute('class', 'layer_path_route');
    svg.appendChild(layer);
  }
  layer.innerHTML = '';
  var pts = [];
  result.nodes.forEach(function(id, idx) {
    var n = PATH_GRAPH.nodes[id];
    if (!n) return;
    pts.push(n.x + ',' + n.y);
    if (idx === 0) markNodeClass(id, 'path-start');
    else if (idx === result.nodes.length - 1) markNodeClass(id, 'path-end');
    else markNodeClass(id, 'path-via');
  });
  if (pts.length >= 2) {
    var pathEl = document.createElementNS('http://www.w3.org/2000/svg', 'polyline');
    pathEl.setAttribute('points', pts.join(' '));
    pathEl.setAttribute('fill', 'none');
    pathEl.setAttribute('stroke', '#E91E63');
    pathEl.setAttribute('stroke-width', '3.2');
    pathEl.setAttribute('stroke-linecap', 'round');
    pathEl.setAttribute('stroke-linejoin', 'round');
    pathEl.setAttribute('opacity', '0.95');
    layer.appendChild(pathEl);
  }
  // 高亮对应拓扑边
  document.querySelectorAll('.layer_topo_edge, .layer_topo_edge_titi').forEach(function(g) {
    var f = g.getAttribute('data-info');
    if (!f) return;
    try {
      var ed = JSON.parse(f);
      if (result.edges.indexOf(ed.id) >= 0) g.classList.add('selected');
    } catch(e) {}
  });
}

// ---- 右栏：路径规划「完整路径列表」 ----
var RP_TYPE_META = {
  room: { name: '房间', color: '#E67E22' },
  doorway: { name: '门口', color: '#C0392B' },
  intersection: { name: '交叉口', color: '#27AE60' },
  facility: { name: '设施', color: '#8E44AD' },
  facility_entrance: { name: '设施接入', color: '#16A085' }
};
var RP_FAC_NAME = { staircase: '楼梯', elevator: '电梯', entrance: '出入口', escalator: '扶梯' };
var RP_DOOR_NAME = { swing: '普通门', fire: '防火门', opening: '门洞' };
var RP_MODE_NAME = { normal: '普通', blind: '视障', wheelchair: '轮椅' };
var RP_EMPTY_HTML = '尚未规划路径。<br>点上方「选点导航」后依次点击起点、终点拓扑节点，这里会列出完整途经节点清单（可点击定位）。';
var _rpEdgeMap = null;

function rpEdgeMap() {
  if (_rpEdgeMap) return _rpEdgeMap;
  _rpEdgeMap = {};
  ((PATH_GRAPH && PATH_GRAPH.edges) || []).forEach(function(e) { _rpEdgeMap[e.id] = e; });
  return _rpEdgeMap;
}

function rpEsc(s) {
  return String(s == null ? '' : s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

function rpSetMode(mode) {
  var el = document.getElementById('rp-mode');
  if (el) el.textContent = RP_MODE_NAME[mode] || mode;
}

function resetRoutePanel() {
  var listEl = document.getElementById('route-steps');
  if (listEl) listEl.innerHTML = '';
  var sumEl = document.getElementById('route-summary');
  if (sumEl) {
    sumEl.className = 'rp-empty';
    sumEl.innerHTML = RP_EMPTY_HTML;
  }
}

// 把视图平移到某个节点（保持当前缩放），用于在右栏点击列表项时定位
function focusRouteNode(id) {
  var n = PATH_GRAPH && PATH_GRAPH.nodes[id];
  if (!n) return;
  var rect = wrapper.getBoundingClientRect();
  translateX = rect.width / 2 - n.x * scale;
  translateY = rect.height / 2 - n.y * scale;
  applyTransform();
}

function renderRouteList(result, mode, startId, endId) {
  rpSetMode(mode);
  var sumEl = document.getElementById('route-summary');
  var listEl = document.getElementById('route-steps');
  if (!sumEl || !listEl) return;
  listEl.innerHTML = '';
  var sNode = (PATH_GRAPH.nodes[startId] || {});
  var eNode = (PATH_GRAPH.nodes[endId] || {});
  if (!result) {
    sumEl.className = 'rp-empty';
    sumEl.innerHTML = '<b style="color:#C62828">不可达</b><br>' +
      rpEsc(sNode.label || startId) + ' → ' + rpEsc(eNode.label || endId) +
      '<br>当前模式（' + rpEsc(RP_MODE_NAME[mode] || mode) + '）下无满足导航规则的连通路径。';
    return;
  }
  // 逐段几何距离（取自拓扑边 distance，不含门型惩罚）
  var em = rpEdgeMap();
  var segs = [], geo = 0, nXf = 0;
  for (var i = 0; i < result.edges.length; i++) {
    var ed = em[result.edges[i]] || {};
    var dd = Number(ed.distance) || 0;
    geo += dd;
    segs.push({ d: dd, cum: geo, e: ed });
    if (ed.crossFloor) nXf++;
  }
  var nDoor = 0;
  result.nodes.forEach(function(id) {
    var n = PATH_GRAPH.nodes[id];
    if (n && n.type === 'doorway') nDoor++;
  });
  // ---- 摘要 ----
  var noteHtml = '';
  if (result.note === 'door_fallback') {
    noteHtml = '<div class="rp-note">门回退：房间最佳门未接入路网，已放宽为可用任意附属门。</div>';
  } else if (result.note === 'wall_fallback') {
    noteHtml = '<div class="rp-note">桥边回退：两端仅靠穿墙走廊边连通，为保证可达而保留（可通行区数据待修复项）。</div>';
  }
  var costHtml = '';
  if (Math.abs(result.distance - geo) > 0.05) {
    costHtml = '<div class="rp-kv"><span>规划代价(含门型惩罚)</span><b>' + result.distance.toFixed(2) + '</b></div>';
  }
  sumEl.className = '';
  sumEl.innerHTML =
    '<div class="rp-od">' + rpEsc(sNode.label || startId) + ' → ' + rpEsc(eNode.label || endId) + '</div>' +
    '<div class="rp-kv"><span>总长度</span><b>' + geo.toFixed(1) + ' m</b></div>' +
    '<div class="rp-kv"><span>途经节点</span><b>' + result.nodes.length + ' 个</b></div>' +
    '<div class="rp-kv"><span>经过门</span><b>' + nDoor + ' 扇</b></div>' +
    (nXf ? '<div class="rp-kv"><span>跨层段</span><b>' + nXf + ' 段</b></div>' : '') +
    costHtml + noteHtml;
  // ---- 逐节点清单 ----
  var last = result.nodes.length - 1;
  var html = '';
  result.nodes.forEach(function(id, idx) {
    var n = PATH_GRAPH.nodes[id] || {};
    var meta = RP_TYPE_META[n.type] || { name: n.type || '节点', color: '#607D8B' };
    var color = (idx === 0) ? '#2E7D32' : (idx === last ? '#C62828' : meta.color);
    var name = n.label || '';
    if (!name) {
      if (n.type === 'doorway') name = RP_DOOR_NAME[n.doorType] || '门口';
      else if (n.facilityType) name = RP_FAC_NAME[n.facilityType] || meta.name;
      else name = meta.name;
    }
    var bits = [];
    if (n.type === 'doorway' && n.doorType) bits.push(RP_DOOR_NAME[n.doorType] || n.doorType);
    if (n.facilityType) bits.push(RP_FAC_NAME[n.facilityType] || n.facilityType);
    bits.push('F' + (n.floor == null ? '?' : n.floor));
    bits.push(rpEsc(id));
    var segHtml;
    if (idx === 0) {
      segHtml = '<span class="rp-cum">起点</span>';
    } else {
      var sg = segs[idx - 1] || { d: 0, cum: 0, e: {} };
      var xf = '';
      if (sg.e.crossFloor) {
        var fname = RP_FAC_NAME[sg.e.type] || '跨层';
        xf = '<span class="rp-xf">跨层·' + rpEsc(fname) + '</span><br>';
      }
      segHtml = xf + '+' + sg.d.toFixed(1) + ' m<span class="rp-cum">Σ ' + sg.cum.toFixed(1) + ' m</span>';
    }
    html += '<li data-nid="' + rpEsc(id) + '" title="点击在图上定位该节点">' +
      '<span class="rp-idx" style="background:' + color + '">' + (idx + 1) + '</span>' +
      '<span class="rp-body">' +
        '<span class="rp-name">' + rpEsc(name) + '</span>' +
        '<span class="rp-meta"><span class="rp-tag" style="background:' + meta.color + '">' +
          rpEsc(meta.name) + '</span>' + bits.join(' · ') + '</span>' +
      '</span>' +
      '<span class="rp-seg">' + segHtml + '</span>' +
      '</li>';
  });
  listEl.innerHTML = html;
  Array.prototype.forEach.call(listEl.children, function(li) {
    li.addEventListener('click', function() {
      Array.prototype.forEach.call(listEl.children, function(x) { x.classList.remove('active'); });
      li.classList.add('active');
      focusRouteNode(li.getAttribute('data-nid'));
    });
  });
}

function recomputePathIfReady() {
  var mode = (document.getElementById('path-mode-select') || {}).value || 'normal';
  rpSetMode(mode);
  if (pathStart && pathEnd) {
    runPath(pathStart, pathEnd);
  }
}

function runPath(startId, endId) {
  var mode = (document.getElementById('path-mode-select') || {}).value || 'normal';
  var result = dijkstra(startId, endId, mode);
  var out = document.getElementById('path-result');
  var hint = document.getElementById('path-hint');
  if (!result) {
    if (out) out.textContent = '不可达（当前模式下无连通路径）';
    clearPathVisual();
    markNodeClass(startId, 'path-start');
    markNodeClass(endId, 'path-end');
    renderRouteList(null, mode, startId, endId);
    return;
  }
  drawPath(result);
  renderRouteList(result, mode, startId, endId);
  var sn = PATH_GRAPH.nodes[startId] || {};
  var en = PATH_GRAPH.nodes[endId] || {};
  if (out) {
    var noteTxt = '';
    if (result.note === 'door_fallback') noteTxt = '（门回退：最佳门未接入路网）';
    else if (result.note === 'wall_fallback') noteTxt = '（桥边回退：穿墙走廊边为保连通保留）';
    out.textContent = '路径 ' + result.nodes.length + ' 节点 · ' +
      result.distance.toFixed(1) + ' m · ' +
      (sn.label || startId) + ' → ' + (en.label || endId) + noteTxt;
  }
  if (hint) hint.textContent = '可继续点选新的起点，或点「清除路径」';
}

// 挂到原有节点点击逻辑：pathMode 下优先选点
var _origPathClickInstalled = false;
function installPathClick() {
  if (_origPathClickInstalled) return;
  _origPathClickInstalled = true;
  wrapper.addEventListener('click', function(e) {
    if (!pathMode) return;
    var t = e.target.closest('[data-info]');
    if (!t) return;
    var info = t.getAttribute('data-info');
    var d; try { d = JSON.parse(info); } catch (err) { return; }
    if (d.kind !== 'node' || !d.id) return;
    e.stopPropagation();
    if (!pathStart || (pathStart && pathEnd)) {
      pathStart = d.id;
      pathEnd = null;
      clearPathVisual();
      markNodeClass(pathStart, 'path-start');
      var h = document.getElementById('path-hint');
      if (h) h.textContent = '已选起点，请点击终点…';
      var r = document.getElementById('path-result');
      if (r) r.textContent = '';
      resetRoutePanel();
      return;
    }
    if (d.id === pathStart) return;
    pathEnd = d.id;
    runPath(pathStart, pathEnd);
  }, true); // capture 优先于详情点击
}
installPathClick();

// ---- 拓扑边编辑：双击节点加边 / 选中边删除 / 保存 GeoJSON ----
// 增删边直接修改嵌入的完整 GeoJSON（#full-geojson-data）并在图上实时反映，
// 点「保存 GeoJSON」用 File System Access API 写回文件（不支持则下载完整文件）。
var FULL_DATA = null;
(function(){{
  var el = document.getElementById('full-geojson-data');
  if (el) {{ try {{ FULL_DATA = JSON.parse(el.textContent); }} catch(e) {{ console.warn('full geojson parse failed', e); }} }}
}})();
var edgePick = [];            // 双击选点：首个节点 id
var selectedEdgeId = null;    // 当前单击选中的拓扑边 id
var selectedEdgeEl = null;    // 对应的 SVG 元素
function edgeHint(msg){{ var h = document.getElementById('edge-hint'); if (h) h.textContent = msg; }}
function edgeStatus(msg){{ var s = document.getElementById('edge-list'); if (s) s.textContent = msg; }}
function nextEdgeId(fa, fb){{
  if (fa === fb) {{
    var max = 0;
    ((FULL_DATA.floors[String(fa)].topology || {{}}).edges || []).forEach(function(e){{
      var m = /F\\d+-TE-(\\d+)/.exec(e.id || ''); if (m) max = Math.max(max, parseInt(m[1], 10));
    }});
    return 'F' + fa + '-TE-' + ('0000' + (max + 1)).slice(-4);
  }}
  var max = 0;
  (FULL_DATA.crossFloorEdges || []).forEach(function(e){{
    var m = /FX-XE-(\\d+)/.exec(e.id || ''); if (m) max = Math.max(max, parseInt(m[1], 10));
  }});
  return 'FX-XE-' + ('0000' + (max + 1)).slice(-4);
}}
function edgeExists(a, b){{
  for (var fk in FULL_DATA.floors) {{
    var es = (FULL_DATA.floors[fk].topology || {{}}).edges || [];
    for (var i = 0; i < es.length; i++)
      if ((es[i].from === a && es[i].to === b) || (es[i].from === b && es[i].to === a)) return true;
  }}
  var xs = FULL_DATA.crossFloorEdges || [];
  for (var j = 0; j < xs.length; j++)
    if ((xs[j].from === a && xs[j].to === b) || (xs[j].from === b && xs[j].to === a)) return true;
  return false;
}}
function drawEdgeElement(edge){{
  var a = PATH_GRAPH.nodes[edge.from], b = PATH_GRAPH.nodes[edge.to];
  if (!a || !b) return null;
  var det = {{title: '导航边 ' + edge.id, rows: [
    ['起始', edge.from], ['终点', edge.to],
    ['距离', edge.distance.toFixed(2) + ' m'],
    ['预估时间', (edge.estimatedTime || 0).toFixed(1) + ' s'],
    ['可达等级', edge.accessibilityLevel], ['风险等级', edge.riskLevel],
    ['可步行', '是'], ['轮椅', '是'], ['视障', '是']
  ]}};
  var g = document.createElementNS('http://www.w3.org/2000/svg', 'g');
  g.setAttribute('class', 'layer_topo_edge');
  g.setAttribute('data-info', JSON.stringify({{tip: '导航边 ' + edge.id + ' 距离 ' + edge.distance.toFixed(1) + 'm', detail: det, from: edge.from, to: edge.to, id: edge.id, kind: 'edge'}}));
  var p = document.createElementNS('http://www.w3.org/2000/svg', 'path');
  p.setAttribute('d', 'M ' + a.x + ' ' + a.y + ' L ' + b.x + ' ' + b.y);
  g.appendChild(p);
  svg.appendChild(g);
  return g;
}}
// 双击拓扑节点：第一次选起点，第二次选终点自动加边
wrapper.addEventListener('dblclick', function(e) {{
  var t = e.target.closest('[data-info]');
  if (!t) return;
  var info = t.getAttribute('data-info');
  var d; try {{ d = JSON.parse(info); }} catch (err) {{ return; }}
  if (d.kind !== 'node' || !d.id) return;
  if (clickTimer) {{ clearTimeout(clickTimer); clickTimer = null; }} // 取消挂起的单击选中
  e.preventDefault(); e.stopPropagation();
  ensureLayer('topo_node', true); ensureLayer('topo_edge', true);
  var nd = PATH_GRAPH.nodes[d.id];
  if (!nd) {{ edgeHint('节点不在图中'); return; }}
  if (edgePick.length === 0) {{
    edgePick.push(d.id);
    markNodeClass(d.id, 'path-start');
    edgeHint('已选第一个节点（' + (nd.label || d.id) + '），请双击第二个节点…');
    return;
  }}
  var a = edgePick[0];
  edgePick = [];
  clearPathVisual();
  if (d.id === a) {{ edgeHint('两个节点相同，请重新双击选择'); return; }}
  if (edgeExists(a, d.id)) {{ edgeHint('这两个节点已存在拓扑边，请重新选择'); return; }}
  var na = PATH_GRAPH.nodes[a];
  var distM = Math.sqrt((na.mx - nd.mx) * (na.mx - nd.mx) + (na.my - nd.my) * (na.my - nd.my));
  var edge = {{
    id: nextEdgeId(na.floor, nd.floor),
    from: a, to: d.id,
    distance: Math.round(distM * 100) / 100,
    estimatedTime: Math.round(distM / 0.8 * 10) / 10,
    accessibilityLevel: 0, riskLevel: 0.5,
    walkable: true, wheelchairAccessible: true, blindAccessible: true,
    manual: true
  }};
  if (na.floor === nd.floor) {{
    FULL_DATA.floors[String(na.floor)].topology.edges.push(edge);
  }} else {{
    edge.fromFloor = na.floor; edge.toFloor = nd.floor;
    edge.type = 'manual'; edge.matchedBy = 'manual';
    if (!FULL_DATA.crossFloorEdges) FULL_DATA.crossFloorEdges = [];
    FULL_DATA.crossFloorEdges.push(edge);
  }}
  PATH_GRAPH.edges.push({{id: edge.id, from: edge.from, to: edge.to, distance: edge.distance, accessibilityLevel: 0, blindAccessible: true, wheelchairAccessible: true, manual: true}});
  drawEdgeElement(edge);
  edgeStatus('已添加拓扑边 ' + edge.id + '（' + edge.distance.toFixed(1) + ' m）· 待保存');
  edgeHint('可继续双击两个节点加边，或点「保存 GeoJSON」写回文件');
}}, true);
function updateDeleteBtn(){{
  var btn = document.getElementById('btn-del-edge');
  if (btn) btn.disabled = !selectedEdgeId;
}}
function deleteSelectedEdge(){{
  if (!selectedEdgeId) {{ alert('请先单击选中一条拓扑边'); return; }}
  if (!confirm('确定删除拓扑边 ' + selectedEdgeId + ' ？')) return;
  var removed = false;
  for (var fk in FULL_DATA.floors) {{
    var es = (FULL_DATA.floors[fk].topology || {{}}).edges || [];
    for (var i = es.length - 1; i >= 0; i--)
      if (es[i].id === selectedEdgeId) {{ es.splice(i, 1); removed = true; }}
  }}
  var xs = FULL_DATA.crossFloorEdges || [];
  for (var j = xs.length - 1; j >= 0; j--)
    if (xs[j].id === selectedEdgeId) {{ xs.splice(j, 1); removed = true; }}
  for (var k = PATH_GRAPH.edges.length - 1; k >= 0; k--)
    if (PATH_GRAPH.edges[k].id === selectedEdgeId) PATH_GRAPH.edges.splice(k, 1);
  if (selectedEdgeEl && selectedEdgeEl.parentNode) selectedEdgeEl.parentNode.removeChild(selectedEdgeEl);
  var del = selectedEdgeId;
  selectedEdgeId = null; selectedEdgeEl = null;
  updateDeleteBtn();
  clearHighlight(); resetDetail();
  edgeStatus(removed ? ('已删除拓扑边 ' + del + ' · 待保存') : ('未找到边 ' + del));
}}
// 保存：完整 GeoJSON 写回文件（File System Access API；不支持则下载完整文件）
async function saveGeojson(){{
  if (!FULL_DATA) {{ alert('完整数据未加载，无法保存'); return; }}
  var json = JSON.stringify(FULL_DATA, null, 2);
  if (window.showSaveFilePicker) {{
    try {{
      var handle = await window.showSaveFilePicker({{
        suggestedName: 'school_building_01_map_v9.geojson',
        types: [{{description: 'GeoJSON', accept: {{'application/json': ['.geojson', '.json']}}}}]
      }});
      var w = await handle.createWritable();
      await w.write(json);
      await w.close();
      edgeStatus('已保存 GeoJSON ✔ 建议重渲染 HTML');
      return;
    }} catch (err) {{
      if (err && err.name === 'AbortError') return;
    }}
  }}
  var blob = new Blob([json], {{type: 'application/json'}});
  var url = URL.createObjectURL(blob);
  var a = document.createElement('a');
  a.href = url; a.download = 'school_building_01_map_v9.geojson'; a.click();
  setTimeout(function(){{ URL.revokeObjectURL(url); }}, 2000);
  edgeStatus('已下载完整 GeoJSON（请放到 result/ 目录）');
}}
updateDeleteBtn();


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
    # 注入「区域标注」交互脚本（独立 <script>，含坐标反变换常量）
    anno_script = build_anno_script(min_x, max_y, svh_per_floor, sorted_floors)
    parts[-1] = parts[-1].replace("</body></html>", anno_script + "\n</body></html>")

    with open(HTML_OUT, "w", encoding="utf-8") as f:
        f.write("".join(parts))

    print(f"已生成: {HTML_OUT}")
    print(f"  SVG: {svw} × {svh} px | 每层 {svh_per_floor} px")
    print(f"  坐标范围: x[{min_x:.1f}, {max_x:.1f}], y[{min_y:.1f}, {max_y:.1f}]")
    print(f"  楼层: {len(sorted_floors)} 层 | 跨层连接: {len(cf)} 条")


if __name__ == "__main__":
    main()
