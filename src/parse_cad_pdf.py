# -*- coding: utf-8 -*-
"""
PathAI - CAD 平面图 (PDF) -> GeoJSON 解析器

依据 docs/03-地图构建指南.md 的流程：
  图纸解析 -> 坐标归一化 -> 几何矢量化 -> 语义标注 -> 输出四层语义地图(几何+语义部分)

规则（与用户需求对齐）：
1. 只解析 PDF 默认打开(ON)的图层，其余图层忽略。
2. window 图层中剔除非窗口线条（窗口编号标识以矢量曲线存储，而非 text 文本）。
3. 门洞元素部署在 window 图层（普通门）与 DOOR_FIRE 图层（防火门）；
   每个门洞只保留一扇门（同一门洞识别出多扇门时去重）。
4. 封闭空间（教室、卫生间等）通过墙体线多边形化识别，并关联其所有门洞。

坐标系：局部米制坐标系（与 school_building_01_map_v7.geojson 一致），
PDF pt -> 米：xm = (xpt - OX) * S, ym = (OY - ypt) * S（Y 轴翻转）。
"""

import fitz  # PyMuPDF
import json
import math
import re
import sys
import collections

from shapely.ops import unary_union, polygonize
from shapely.geometry import LineString, Point, Polygon, box
from shapely import snap as shp_snap

# ---------------------------------------------------------------- 配置

PDF_F1 = r"E:\code\pathai\A20-002-II-初中学部 1# 教学楼首层平面图-A0_BIAD-无签名.pdf"
PDF_F2 = r"E:\code\pathai\A20-003-II-初中学部 1# 教学楼二层平面图-A0_BIAD-无签名.pdf"
OUT_GEOJSON = r"E:\code\pathai\result\school_building_01_map_v8.geojson"

# 比例尺校准：轴网 8400mm = 158.8pt（AXIS 层间距众数），
# 与窗编号 M2GW5924(5900mm)=111.5pt 互证。v7 的 0.0644 偏大 22%，已弃用。
SCALE = 0.0529          # 米 / pt
ORIGIN_X = 2019.1       # pt
ORIGIN_Y = 1154.8       # pt

# 需要解析的语义图层（仅当其在 PDF 中默认 ON 时才解析）
LAYER_WALL = "WALL"
LAYER_WINDOW = "window"
LAYER_DOOR_FIRE = "DOOR_FIRE"
LAYER_STAIR = "STAIR"
LAYER_ELEVATOR = "A-FLOR-EVTR"
LAYER_COLUMNS = ("COLUMN", "柱子-刚结构")

# 参与房间多边形化的结构图层（默认开启时）。
# 该 CAD 导出的墙体分散在多个结构图层中，需取并集才能闭合房间轮廓；
# 纯标注图层（轴线/文字/标高等）不参与，避免切分房间。
LAYERS_STRUCT = ("WALL", "A-FLOR-STRS", "STAIR", "A-FLOR-EVTR",
                 "COLUMN", "柱子-刚结构")
# 家具级图层（卫生器具/金属构件）：既含真实墙体段（卫生间隔墙等，缺了
# 会导致房间不闭合），又含厕位隔断/洗手台等会把房间内部切碎的构件线。
# 处理：以细线(1px)单独栅格化参与封闭；凡围出 <ABSORB_CELL_M2 微单元的
# 家具线在微单元邻域内擦除（打通厕位与走道），真实隔墙两侧都是大房间、
# 邻域无微单元，不受影响。
LAYERS_FURNITURE = ("A-METAL-S", "A-TECH-SANT")
LAYERS_ANNO_EXCLUDE = ("AXIS", "A-ANNO-150-TXT", "A-ANNO-LEVL", "A-ANNO-TTLB",
                       "A-ANNO-SYMB")

PT_PER_M = 1.0 / SCALE  # ≈ 15.53 pt/m
RENDER_ZOOM = 3.0       # 结构层渲染放大倍数 (px/pt)

# 阈值（pt）
TINY_STROKE = 8.0            # <0.5m 的短线，候选标识glyph
CONNECTOR_SNAP = 1.5         # 窗端头连接线吸附
WINDOW_GROUP_GAP = 6.0       # 窗平行线组内垂直间距上限
DOOR_CLUSTER_PERP = 6.0      # 门洞线段聚类垂直间距
DOOR_CLUSTER_AXIS_GAP = 6.0  # 门洞线段聚类轴向间隙
MIN_DOOR_WIDTH_PT = 9.0      # 房间门最小宽度（≈0.48m），更小的为厕位/器具弧
WALL_SNAP = 0.8              # 墙线端点吸附网格
GRID_QUANT = 1.0             # 端点量化网格(pt)，消除 CAD 线端点微小间隙
WALL_BODY_SHORT_SIDE_PT = 7.0   # 墙体单元格短边阈值(≈0.45m)，小于此且细长视为墙身段
MIN_ROOM_AREA_M2 = 3.0
MAX_ROOM_AREA_M2 = 600.0
ABSORB_CELL_M2 = 2.0       # 小于该面积的自由单元为可填充微单元（厕位格等）
MERGE_REGION_M2 = 9.0      # 小于该面积的未标注单元可经守卫式泛洪并入邻房
WALL_EXT_PT = 6.0          # 墙线端点外延（桥接 T 型接头/窗洞收口缝隙）
LABEL_MIN_SIZE = 11.5        # 房间名称最小字号(pt)
TITLE_BLOCK_X = 2900.0       # 图签区 x 起点（右侧剔除）

# 非封闭空间的室外/开敞标签：不参与房间探测（避免抢占附近区域）
LABEL_SKIP_RE = re.compile(
    r"(车道|车库入口|出入口|雨棚|屋面|上空|庭园|台阶|坡道|散水|屋顶平台)")

ROOM_TYPE_RULES = [
    ("卫生间", "toilet"), ("洗手间", "toilet"),
    ("楼梯", "staircase"), ("电梯", "elevator_hall"),
    ("走道", "corridor"), ("走廊", "corridor"), ("门厅", "lobby"), ("大厅", "lobby"),
    ("教室", "classroom"), ("合班", "classroom"), ("书法", "classroom"), ("美术", "classroom"),
    ("音乐", "classroom"), ("实验室", "lab"), (" resource", "classroom"),
    ("办公", "office"), ("会议", "meeting"), ("接待", "meeting"),
    ("设备", "equipment"), ("机房", "equipment"), ("配电", "equipment"),
    ("水井", "shaft"), ("风井", "shaft"), ("排风井", "shaft"), ("管井", "shaft"), ("井", "shaft"),
    ("储藏", "storage"), ("存放", "storage"), ("资料", "storage"), ("档案", "storage"),
    ("广播", "equipment"), ("管控", "equipment"),
    ("图书", "library"), ("阅览", "library"),
    ("卫生室", "medical"), ("心理", "counseling"), ("辅导", "counseling"),
    ("活动", "activity"), ("社团", "activity"),
    ("传达", "reception"), ("前台", "reception"),
    ("庭园", "atrium"), ("上空", "atrium"),
]


# ---------------------------------------------------------------- 工具

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


class UnionFind:
    def __init__(self, n):
        self.p = list(range(n))

    def find(self, a):
        while self.p[a] != a:
            self.p[a] = self.p[self.p[a]]
            a = self.p[a]
        return a

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.p[ra] = rb


def cluster_items(items, should_link):
    """通用聚类：items 列表 + should_link(i, j) -> 簇列表"""
    n = len(items)
    uf = UnionFind(n)
    for i in range(n):
        for j in range(i + 1, n):
            if should_link(items[i], items[j]):
                uf.union(i, j)
    groups = collections.defaultdict(list)
    for i in range(n):
        groups[uf.find(i)].append(items[i])
    return list(groups.values())


# ---------------------------------------------------------------- PDF 提取

def get_default_on_layers(doc):
    """读取 PDF 默认打开(ON)的图层名集合"""
    return {item["text"] for item in doc.layer_ui_configs() if item["on"]}


def extract_layer_items(page, layer_names):
    """
    从页面提取指定图层的矢量元素。
    返回 dict: layer -> {'lines': [(a,b)], 'quads': [[p1..p4]], 'curves': [bezier4]}
    """
    out = {name: {"lines": [], "quads": [], "curves": []} for name in layer_names}
    for d in page.get_drawings():
        layer = d.get("layer") or ""
        if layer not in out:
            continue
        bucket = out[layer]
        for it in d["items"]:
            kind = it[0]
            if kind == "l":
                p1, p2 = it[1], it[2]
                bucket["lines"].append(((p1.x, p1.y), (p2.x, p2.y)))
            elif kind == "qu":
                q = it[1]
                bucket["quads"].append([(q.ul.x, q.ul.y), (q.ur.x, q.ur.y),
                                        (q.lr.x, q.lr.y), (q.ll.x, q.ll.y)])
            elif kind == "re":
                r = it[1]
                bucket["quads"].append([(r.x0, r.y0), (r.x1, r.y0),
                                        (r.x1, r.y1), (r.x0, r.y1)])
            elif kind == "c":
                p1, p2, p3, p4 = it[1], it[2], it[3], it[4]
                bucket["curves"].append([(p1.x, p1.y), (p2.x, p2.y),
                                         (p3.x, p3.y), (p4.x, p4.y)])
    return out


def extract_room_labels(page):
    """
    提取房间名称文本（大字号、排除图签区），合并多行标签（如 学生/社团/活动区）。
    返回 [(text, (cx, cy)_pt)]
    """
    d = page.get_text("dict")
    raw = []
    for block in d["blocks"]:
        if block.get("type") != 0:
            continue
        for line in block["lines"]:
            txt = "".join(s["text"] for s in line["spans"]).strip()
            if not txt:
                continue
            size = max(s["size"] for s in line["spans"])
            x0, y0, x1, y1 = line["bbox"]
            if size < LABEL_MIN_SIZE or x1 > TITLE_BLOCK_X:
                continue
            raw.append({"text": txt, "size": size,
                        "bbox": (x0, y0, x1, y1),
                        "cx": (x0 + x1) / 2, "cy": (y0 + y1) / 2,
                        "w": x1 - x0, "h": y1 - y0})

    # 过滤明显非房间名的内容
    def is_room_label(t):
        if re.search(r"[0-9a-zA-Z]{3,}", t):      # 编号/英文
            return False
        if re.fullmatch(r"[0-9.\s%㎡()（）*≥<=-]+", t):
            return False
        if any(k in t for k in ("面积", "分区", "排烟", "平面", "教学", "项目", "设计")):
            return False
        if len(t) > 12:
            return False
        return bool(re.search(r"[一-鿿]", t))

    cands = [r for r in raw if is_room_label(r["text"])]

    # 合并垂直堆叠的多行标签（行间距 < 2.2 倍行高，水平重叠）
    cands.sort(key=lambda r: (r["cx"], r["cy"]))
    used = [False] * len(cands)
    merged = []
    for i, r in enumerate(cands):
        if used[i]:
            continue
        group = [r]
        used[i] = True
        for j in range(i + 1, len(cands)):
            r2 = cands[j]
            if used[j]:
                continue
            # 水平中心接近 且 垂直间距小（堆叠行）
            if abs(r2["cx"] - r["cx"]) < max(r["w"], r2["w"], 20) * 0.6 + 10 and \
               abs(r2["cy"] - r["cy"]) < (r["h"] + r2["h"]) * 2.2:
                # 文本拼接后仍是合理房间名
                group.append(r2)
                used[j] = True
        group.sort(key=lambda g: g["cy"])
        text = "".join(g["text"] for g in group)
        cx = sum(g["cx"] for g in group) / len(group)
        cy = sum(g["cy"] for g in group) / len(group)
        merged.append((text, (cx, cy)))
    return merged


# ---------------------------------------------------------------- window 图层分类

def classify_window_layer(lines, quads, curves):
    """
    window 图层元素分类：
      - 剔除标识 glyph 曲线（窗口编号的矢量笔画，非 text）
      - 真实窗口线 -> window segments
      - 门扇弧线 -> 交给门洞识别
    返回 (window_groups, door_curves, removed_glyph_count)
      window_groups: [{'axis': (a,b), 'lines': [...], 'center': p}]
    """
    long_lines = []   # >= TINY_STROKE
    short_lines = []  # <  TINY_STROKE
    for a, b in lines:
        if seg_len(a, b) >= TINY_STROKE:
            long_lines.append((a, b))
        else:
            short_lines.append((a, b))

    # 短连接线：端点吸附到长线的短线保留（窗框端头），其余视为 glyph 笔画剔除
    kept_short = []
    removed = 0
    for a, b in short_lines:
        attach = False
        for la, lb in long_lines:
            for p in (a, b):
                dist, t = point_to_seg_dist(p, la, lb)
                if dist < CONNECTOR_SNAP:
                    attach = True
                    break
            if attach:
                break
        if attach:
            kept_short.append((a, b))
        else:
            removed += 1

    # 窗口分组：平行 + 垂直间距小 + 轴向投影重叠
    def link(s1, s2):
        a1, b1 = s1
        a2, b2 = s2
        if angle_diff(seg_angle(a1, b1), seg_angle(a2, b2)) > math.radians(5):
            return False
        # 垂直间距
        d, _ = point_to_seg_dist(seg_midpoint(a2, b2), a1, b1)
        if d > WINDOW_GROUP_GAP:
            return False
        # 轴向重叠检查
        ang = seg_angle(a1, b1)
        ux, uy = math.cos(ang), math.sin(ang)
        def proj(p):
            return p[0] * ux + p[1] * uy
        lo1, hi1 = sorted((proj(a1), proj(b1)))
        lo2, hi2 = sorted((proj(a2), proj(b2)))
        overlap = min(hi1, hi2) - max(lo1, lo2)
        return overlap > -CONNECTOR_SNAP

    groups = cluster_items(long_lines, link)
    window_groups = []
    for g in groups:
        ang = seg_angle(*g[0])
        ux, uy = math.cos(ang), math.sin(ang)
        pts = [p for seg in g for p in seg]
        projs = [p[0] * ux + p[1] * uy for p in pts]
        lo, hi = min(projs), max(projs)
        # 组中心（所有线中点均值）
        mx = sum(seg_midpoint(a, b)[0] for a, b in g) / len(g)
        my = sum(seg_midpoint(a, b)[1] for a, b in g) / len(g)
        # 组中心在主轴上的投影点
        t0 = (mx * ux + my * uy)
        # 轴向端点
        px_perp = (-uy, ux)
        perp_off = mx * px_perp[0] + my * px_perp[1]
        a = (lo * ux + perp_off * px_perp[0], lo * uy + perp_off * px_perp[1])
        b = (hi * ux + perp_off * px_perp[0], hi * uy + perp_off * px_perp[1])
        window_groups.append({
            "axis": (a, b),
            "lines": g + [s for s in kept_short
                          if point_to_seg_dist(seg_midpoint(*s), a, b)[0] < WINDOW_GROUP_GAP],
            "center": (mx, my),
            "length_pt": hi - lo,
        })
    return window_groups, curves, removed


# ---------------------------------------------------------------- 门洞识别与去重

def detect_doors(win_curves, fire_lines, fire_curves, struct_segs=None):
    """
    门洞识别：
      - 铰链门 = 摆弧（bezier 曲线）。弧的圆心=门轴铰链（在墙线上），
        弧半径=门宽，门洞线段 = 铰链 -> 弧的"闭门端"（沿墙方向的端点）。
      - 圆心取 bbox 角点中离弧中点最远者（弧背向圆心鼓出）；
        闭门端取离墙线最近的端点（面板端伸入房间、远离墙线）。
      - 每个门洞只保留一扇门：门洞线段按 平行+垂直间距+轴向间隙 聚类去重。
    返回 [{'center': p_pt, 'width_pt': w, 'axis': (a,b), 'kind': 'swing'|'fire'}]
    """
    door_segs = []  # (hinge, tip, radius, kind)

    def bezier_mid(bz):
        p1, p2, p3, p4 = bz
        def lerp(a, b, t=0.5):
            return (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t)
        q1, q2, q3 = lerp(p1, p2), lerp(p2, p3), lerp(p3, p4)
        r1, r2 = lerp(q1, q2), lerp(q2, q3)
        return lerp(r1, r2)

    def nearest_wall_dist(p):
        if not struct_segs:
            return 0.0
        return min(point_to_seg_dist(p, a, b)[0] for a, b in struct_segs)

    def arc_to_door(bz, kind):
        p1, p2, p3, p4 = bz  # bezier: start, c1, c2, end
        xs = [p1[0], p2[0], p3[0], p4[0]]
        ys = [p1[1], p2[1], p3[1], p4[1]]
        x0, x1 = min(xs), max(xs)
        y0, y1 = min(ys), max(ys)
        w, h = x1 - x0, y1 - y0
        # 四分之一圆弧的 bbox 两维接近（允许误差），半径≈max(w,h)
        r = max(w, h)
        if r < 4.0:  # 过小忽略（<0.26m）
            return
        m = bezier_mid(bz)
        corners = [(x0, y0), (x0, y1), (x1, y0), (x1, y1)]
        # 圆心（铰链）= 离弧中点最远的角点（弧背向圆心鼓出）
        center = max(corners,
                     key=lambda c: (c[0] - m[0]) ** 2 + (c[1] - m[1]) ** 2)
        # 闭门端 = 离墙线最近的弧端点（另一端为伸入房间的面板端）
        if nearest_wall_dist(p1) <= nearest_wall_dist(p4):
            tip = p1
        else:
            tip = p4
        door_segs.append({"hinge": center, "tip": tip, "radius": r, "kind": kind})

    for bz in win_curves:
        arc_to_door(bz, "swing")
    for bz in fire_curves:
        arc_to_door(bz, "fire")

    # 门洞去重：平行 + 垂直间距 + 轴向间隙
    def link(d1, d2):
        a1, b1 = d1["hinge"], d1["tip"]
        a2, b2 = d2["hinge"], d2["tip"]
        if angle_diff(seg_angle(a1, b1), seg_angle(a2, b2)) > math.radians(30):
            return False
        d, _ = point_to_seg_dist(seg_midpoint(a2, b2), a1, b1)
        if d > DOOR_CLUSTER_PERP:
            return False
        ang = seg_angle(a1, b1)
        ux, uy = math.cos(ang), math.sin(ang)
        lo1, hi1 = sorted((a1[0] * ux + a1[1] * uy, b1[0] * ux + b1[1] * uy))
        lo2, hi2 = sorted((a2[0] * ux + a2[1] * uy, b2[0] * ux + b2[1] * uy))
        gap = max(lo1, lo2) - min(hi1, hi2)
        return gap < DOOR_CLUSTER_AXIS_GAP

    groups = cluster_items(door_segs, link)
    doors = []
    for g in groups:
        # 同洞多门 -> 只保留一扇：取最宽的门洞线段代表
        rep = max(g, key=lambda d: d["radius"])
        if rep["radius"] < MIN_DOOR_WIDTH_PT:
            continue  # 厕位/器具小弧，非房间门
        hinge, tip = rep["hinge"], rep["tip"]
        center = seg_midpoint(hinge, tip)
        doors.append({
            "center": center,
            "width_pt": rep["radius"],
            "axis": (hinge, tip),
            "kind": rep["kind"],
            "merged": len(g),
        })
    return doors


# ---------------------------------------------------------------- 墙体与房间

def wall_segments(wall_items):
    """WALL 图层 -> 墙面线段集合（quad 拆 4 条边，line 原样），全部保持"墙面线"语义"""
    segs = []
    for a, b in wall_items["lines"]:
        if seg_len(a, b) > 0.5:
            segs.append((a, b))
    for q in wall_items["quads"]:
        edges = [(q[0], q[1]), (q[1], q[2]), (q[2], q[3]), (q[3], q[0])]
        for a, b in edges:
            if seg_len(a, b) > 0.3:
                segs.append((a, b))
    return segs


def opening_closures(axis, cap_len=10.0):
    """
    门/窗洞口的封口线：轴线 + 两端垂直盖帽（连接两侧墙面，封闭墙体单元格）。
    axis: (a, b) pt 坐标
    """
    a, b = axis
    ang = seg_angle(a, b)
    nx, ny = -math.sin(ang), math.cos(ang)  # 垂直方向
    half = cap_len / 2.0
    segs = [(a, b)]
    for p in (a, b):
        segs.append(((p[0] - nx * half, p[1] - ny * half),
                     (p[0] + nx * half, p[1] + ny * half)))
    return segs


def merge_collinear(segs, angle_tol_deg=2.0, axis_tol=2.0, gap_tol=30.0,
                    micro_gap=6.0, short_seg=30.0):
    """
    合并共线虚线/断线线段（支持任意角度）：
    CAD 导出时常把点划线/虚线墙体打成一串带间隙的短划，直接栅格化会断线。
    按方向角分桶 -> 桶内按线位(法向坐标)分带 -> 带内沿轴向合并间隙。
    桥接规则：间隙 <= gap_tol(30pt) 即桥接（虚线间隙及门洞处墙线中断），
    门洞位置由门/窗封口线单独封闭，不受影响。
    （曾尝试按"双短线段不桥接"保留厕位门洞，但虚线墙同样是短划+大间隙，
       会大面积断墙，已回滚。）
    """
    buckets = collections.defaultdict(list)
    for a, b in segs:
        ang = math.degrees(norm_angle(seg_angle(a, b)))  # [0, 180)
        key = (round(ang / angle_tol_deg) * angle_tol_deg) % 180.0
        buckets[key].append((a, b))

    out = []
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
            band_map[bk].append((lo, hi))
        for pos, ivs in band_map.items():
            ivs.sort()
            cur_lo, cur_hi = ivs[0]
            for lo, hi in ivs[1:]:
                if lo - cur_hi <= gap_tol:
                    cur_hi = max(cur_hi, hi)
                else:
                    out.append(((cur_lo * ux + pos * nx, cur_lo * uy + pos * ny),
                                (cur_hi * ux + pos * nx, cur_hi * uy + pos * ny)))
                    cur_lo, cur_hi = lo, hi
            out.append(((cur_lo * ux + pos * nx, cur_lo * uy + pos * ny),
                        (cur_hi * ux + pos * nx, cur_hi * uy + pos * ny)))
    return out


def rasterize_walls(all_segs, closures, furn_segs=()):
    """
    全部墙体线段(结构+家具,粗2px) + 门/窗封口线(3px) -> 二值墙图。
    另返回家具层单独掩膜(同参数绘制、不做闭运算)，用于判定
    区域间边界是否为家具线（厕位隔断可合并、真实墙不可）。
    返回 (walls_uint8, walls_furn_uint8, minx, miny, W, H, Z)；
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

    walls_furn = np.zeros((H, W), np.uint8)
    for a, b in furn_segs:
        ea, eb = extend(a, b, WALL_EXT_PT)
        cv2.line(walls_furn, to_px(ea), to_px(eb), 255, thickness=2)
    return walls, walls_furn, minx, miny, W, H, Z


def build_rooms(all_segs, closures, furn_segs=(), label_points=None,
                dump_path=None):
    """
    栅格化房间识别（对 CAD 断线/虚线间隙鲁棒）：
      1. 全部墙体线段(结构+家具,粗线闭运算密封) + 门/窗封口线 -> 二值墙图
         —— 纯矢量方案，不引入非 OCG 文字/标注墨线
      2. 连通域提取封闭自由空间；>=ABSORB_CELL_M2 的为"区域"(房间候选)，
         <ABSORB_CELL_M2 的微单元(厕位/器具格)作为"可填充区"
      3. 分水岭式归属：各区域向可填充区生长（真实墙像素阻挡），
         微单元归入最先到达的区域
      4. 未标注小区域合并：被隔断完全围合的厕位排/凹位
         ([ABSORB, MERGE] m²)，若与某已标注房间的边界主要是家具线，
         并入该房间 —— 卫生间多边形因此完整、延伸到门洞所在墙体
      5. 边界接触=室外剔除；按房间标签点探测，轮廓近似多边形化
    返回 [(label, Polygon_pt)]
    """
    import cv2
    import numpy as np

    if not all_segs and not closures:
        return []
    walls, walls_furn, minx, miny, W, H, Z = rasterize_walls(
        all_segs, closures, furn_segs)

    def to_px(p):
        return (int(round((p[0] - minx) * Z)), int(round((p[1] - miny) * Z)))

    if dump_path:
        vis = cv2.cvtColor(walls, cv2.COLOR_GRAY2BGR)
        for a, b in closures:
            cv2.line(vis, to_px(a), to_px(b), (0, 0, 255), thickness=2)
        scale = 4000.0 / max(W, H)
        cv2.imwrite(dump_path,
                    cv2.resize(vis, (int(W * scale), int(H * scale))))

    free = cv2.bitwise_not(walls)
    n, cc, stats, _cent = cv2.connectedComponentsWithStats(free, connectivity=4)

    m2_per_px = (SCALE / Z) * (SCALE / Z)
    absorb_px = ABSORB_CELL_M2 / m2_per_px
    areas_all = stats[:, cv2.CC_STAT_AREA].astype(np.int64)

    # 边界接触 = 室外
    border_ids = set(np.unique(np.concatenate([
        cc[0, :], cc[-1, :], cc[:, 0], cc[:, -1]])))

    # --- 分水岭归属网格 owner：
    #   墙 px = -1e9(阻挡)；大区域 px = 其 cid(正,已占领)；
    #   室外 px = -1(可生长但优先级最低)；微单元 px = 0(可填充)
    owner = np.full((H, W), -1e9, np.float32)
    region_ids = np.where(areas_all >= absorb_px)[0]
    region_ids = region_ids[region_ids != 0]
    region_mask = np.isin(cc, region_ids)
    owner[region_mask] = cc[region_mask].astype(np.float32)
    border_arr = np.fromiter((i in border_ids for i in range(n)),
                             dtype=bool, count=n)
    owner[np.isin(cc, np.where(border_arr)[0])] = -1.0

    # 多源生长：每轮已占领 px 向 4 邻域可填充 px 扩展一格，
    # 取邻域最大 id（房间正 id 优先于室外 -1），墙(-1e9)永不扩散
    k3 = np.ones((3, 3), np.uint8)
    for _ in range(80):  # 80px ≈ 1.4m，足以穿越厕位/管井进深
        fill_mask = (owner == 0)
        if not fill_mask.any():
            break
        grown = cv2.dilate((owner != 0).astype(np.uint8), k3) > 0
        frontier = grown & fill_mask
        if not frontier.any():
            break
        nbr_max = cv2.dilate(owner, k3)
        owner[frontier] = nbr_max[frontier]

    area_min_px = MIN_ROOM_AREA_M2 / m2_per_px
    area_max_px = MAX_ROOM_AREA_M2 / m2_per_px

    rooms = []
    seen = set()
    probes = []
    if label_points:
        for text, (lx, ly) in label_points:
            if LABEL_SKIP_RE.search(text):
                continue
            probes.append((text, to_px((lx, ly))))

    def comp_at(px, py):
        """标签点 -> 所属房间区域 id。
        直接读归属图 owner：标签点落在厕位/器具微单元里时，
        该微单元已归属所在房间；落在墨线/室外时按邻域众数投票
        （标签通常大部分落在自己房间内，众数比最近单像素鲁棒）"""
        if 0 <= px < W and 0 <= py < H and owner[py, px] > 0:
            return int(owner[py, px])
        for r in (6, 12, 24, 40, 60, 90):
            x0, x1 = max(0, px - r), min(W, px + r + 1)
            y0, y1 = max(0, py - r), min(H, py + r + 1)
            sub = owner[y0:y1, x0:x1]
            vals = sub[sub > 0]
            if len(vals):
                ids, cnt = np.unique(vals, return_counts=True)
                return int(ids[np.argmax(cnt)])
        return 0

    dup_labels = []  # QA：多个标签落入同一连通域（疑似误合并）
    matched = []
    for text, (px, py) in probes:
        cid = comp_at(px, py)
        if cid == 0 or cid in border_ids:
            continue
        if cid in seen:
            dup_labels.append(text)
            continue
        seen.add(cid)
        matched.append((text, cid))

    # --- 标签播种：标签落在被隔断切碎的小单元里（整个空间无 >=2m2 区域），
    #     直接认领标签处的小单元为房间种子（标签本身即权威），
    #     后续守卫式泛洪会把同属该空间的其它单元并入
    seed_min_px = 0.3 / m2_per_px
    seed_max_px = MERGE_REGION_M2 / m2_per_px
    for text, (px, py) in probes:
        if any(t == text for t, _ in matched) or text in dup_labels:
            continue
        best, best_d = 0, 1e18
        for r in (3, 8, 15, 25):
            x0, x1 = max(0, px - r), min(W, px + r + 1)
            y0, y1 = max(0, py - r), min(H, py + r + 1)
            sub = cc[y0:y1, x0:x1]
            vals = sub[sub > 0]
            vals = vals[~np.isin(vals, list(border_ids))]
            vals = vals[(areas_all[vals] >= seed_min_px)
                        & (areas_all[vals] < seed_max_px)]
            if len(vals):
                ids, cnt = np.unique(vals, return_counts=True)
                best = int(ids[np.argmax(cnt)])
                break
        if best and best not in seen:
            seen.add(best)
            matched.append((text, best))
            owner[cc == best] = np.float32(best)

    # --- 守卫式泛洪合并：厕位格/凹位被隔断完全围合时无法被分水岭到达。
    #     隔断(双线对)栅格化后 ~10px 厚，真实墙(双面粉刷墙) >=16px；
    #     以 12px 半径从小单元找已标注房间（越过隔断、穿不透真实墙）。
    #     唯一邻居才并入 —— 若 12px 内出现第二个已标注房间则放弃
    #     （真实墙穿不透，邻房不会出现在 12px 内）。
    labeled_cids = {cid for _, cid in matched}
    merge_max_px = MERGE_REGION_M2 / m2_per_px
    k25 = np.ones((25, 25), np.uint8)   # 12px 半径
    cand = [i for i in range(1, n)
            if i not in labeled_cids and i not in border_ids
            and 0.05 / m2_per_px <= areas_all[i] < merge_max_px]
    cand.sort(key=lambda i: -areas_all[i])
    n_merged = 0
    for _pass in range(4):  # 链式并入：外侧单元沿已合并单元逐个并入
        changed = False
        for cid_u in cand:
            if cid_u in labeled_cids:
                continue
            l, t, w, h = (int(stats[cid_u, cv2.CC_STAT_LEFT]),
                          int(stats[cid_u, cv2.CC_STAT_TOP]),
                          int(stats[cid_u, cv2.CC_STAT_WIDTH]),
                          int(stats[cid_u, cv2.CC_STAT_HEIGHT]))
            x0, x1 = max(0, l - 26), min(W, l + w + 26)
            y0, y1 = max(0, t - 26), min(H, t + h + 26)
            cc_sub = cc[y0:y1, x0:x1]
            mask_u = (cc_sub == cid_u)
            owner_sub = owner[y0:y1, x0:x1]
            dil_u = cv2.dilate(mask_u.astype(np.uint8), k25) > 0
            nbrs = {int(v) for v in
                    np.unique(owner_sub[dil_u & (owner_sub > 0)])}
            nbr_labeled = nbrs & labeled_cids
            if len(nbr_labeled) != 1:
                continue
            owner_sub[mask_u] = np.float32(next(iter(nbr_labeled)))
            labeled_cids.add(cid_u)
            n_merged += 1
            changed = True
        if not changed:
            break
    if n_merged:
        print(f"    [合并] 未标注小单元并入房间: {n_merged} 处")

    for text, cid in matched:
        # 房间掩膜 = 原区域 ∪ 归属微单元 ∪ 合并小区域；
        # 闭运算桥接房内隔断缝隙(<=4px)，使多片掩膜连成单一轮廓
        room_mask = (owner == np.float32(cid)).astype(np.uint8) * 255
        room_mask = cv2.morphologyEx(
            room_mask, cv2.MORPH_CLOSE,
            cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (9, 9)))
        area = int(np.count_nonzero(room_mask))
        if not (area_min_px <= area <= area_max_px):
            continue
        ys_idx, xs_idx = np.where(room_mask)
        mg = 20
        x0c, x1c = max(0, xs_idx.min() - mg), min(W, xs_idx.max() + mg)
        y0c, y1c = max(0, ys_idx.min() - mg), min(H, ys_idx.max() + mg)
        sub = room_mask[y0c:y1c, x0c:x1c]
        contours, _ = cv2.findContours(sub, cv2.RETR_EXTERNAL,
                                       cv2.CHAIN_APPROX_SIMPLE)
        if not contours:
            continue
        contour = max(contours, key=cv2.contourArea)
        approx = cv2.approxPolyDP(contour, Z * 1.0, True)  # ~1pt 容差
        pts = [((float(p[0][0] + x0c) / Z + minx),
                (float(p[0][1] + y0c) / Z + miny)) for p in approx]
        if len(pts) < 3:
            continue
        poly = Polygon(pts)
        if not poly.is_valid:
            poly = poly.buffer(0)
            if poly.is_empty:
                continue
        rooms.append((text, poly))
    if dup_labels:
        print(f"    [QA] 多标签共域(疑似误合并): {dup_labels}")
    return rooms


def snap(geom, target, tol):
    try:
        return shp_snap(geom, target, tol)
    except Exception:
        return geom


def classify_room_type(label):
    for kw, tp in ROOM_TYPE_RULES:
        if kw in label:
            return tp
    return "room"


# ---------------------------------------------------------------- 楼梯/电梯/柱

def bbox_clusters(items, gap_pt):
    """对 drawings 的 bbox 中心做网格聚类，返回 bbox 多边形列表（pt）"""
    if not items:
        return []
    boxes = []
    for it in items:
        xs = [p[0] for p in it]
        ys = [p[1] for p in it]
        boxes.append((min(xs), min(ys), max(xs), max(ys)))
    n = len(boxes)
    uf = UnionFind(n)
    for i in range(n):
        for j in range(i + 1, n):
            a, b = boxes[i], boxes[j]
            # bbox 间距 < gap
            dx = max(0, max(a[0], b[0]) - min(a[2], b[2]))
            dy = max(0, max(a[1], b[1]) - min(a[3], b[3]))
            if math.hypot(dx, dy) < gap_pt:
                uf.union(i, j)
    groups = collections.defaultdict(list)
    for i in range(n):
        groups[uf.find(i)].append(boxes[i])
    out = []
    for g in groups.values():
        x0 = min(b[0] for b in g)
        y0 = min(b[1] for b in g)
        x1 = max(b[2] for b in g)
        y1 = max(b[3] for b in g)
        out.append((x0, y0, x1, y1))
    return out


# ---------------------------------------------------------------- 主流程

def parse_floor(pdf_path, floor_no):
    doc = fitz.open(pdf_path)
    page = doc[0]
    on_layers = get_default_on_layers(doc)
    print(f"[F{floor_no}] 默认开启图层: {sorted(on_layers)}")

    wanted = list({LAYER_WALL, LAYER_WINDOW, LAYER_DOOR_FIRE, LAYER_STAIR,
                   LAYER_ELEVATOR, *LAYER_COLUMNS, *LAYERS_STRUCT,
                   *LAYERS_FURNITURE})
    active = [l for l in wanted if l in on_layers]
    skipped = [l for l in wanted if l not in on_layers]
    if skipped:
        print(f"[F{floor_no}] 跳过(非默认开启): {skipped}")

    items = extract_layer_items(page, set(active))
    labels = extract_room_labels(page)
    doc.close()

    # --- 结构线段并集（墙体分散在多个默认开启的结构图层中）
    struct_segs = []
    for lname in LAYERS_STRUCT:
        li = items.get(lname)
        if not li:
            continue
        struct_segs.extend(wall_segments(li))
    # 合并虚线/点划线断段，恢复连续墙体
    struct_segs = merge_collinear(struct_segs)
    # --- 家具层线段（细线参与封闭，微单元邻域内可擦除）
    furn_segs = []
    for lname in LAYERS_FURNITURE:
        li = items.get(lname)
        if not li:
            continue
        furn_segs.extend(wall_segments(li))
    furn_segs = merge_collinear(furn_segs)
    # 门贴墙判定用全部线段（门可能开在家具层隔墙上）
    all_segs = struct_segs + furn_segs
    wall_segs = wall_segments(items.get(LAYER_WALL, {"lines": [], "quads": []}))
    print(f"[F{floor_no}] WALL 图层线段: {len(wall_segs)}, "
          f"结构线段并集(合并后): {len(struct_segs)}, "
          f"家具层线段: {len(furn_segs)}")

    # --- window 图层分类
    win = items.get(LAYER_WINDOW, {"lines": [], "quads": [], "curves": []})
    window_groups, win_door_curves, removed_glyphs = classify_window_layer(
        win["lines"], win["quads"], win["curves"])
    # 真实窗口必须嵌入墙体：组中心距结构线 6pt 以内
    before = len(window_groups)
    window_groups = [wg for wg in window_groups
                     if any(point_to_seg_dist(wg["center"], a, b)[0] < 6.0
                            for a, b in all_segs)]
    print(f"[F{floor_no}] window 图层: 窗口组 {len(window_groups)}"
          f"（贴墙过滤剔除 {before - len(window_groups)} 组）, "
          f"剔除标识笔画 {removed_glyphs}, 门弧线 {len(win_door_curves)}")

    # --- 门洞（window + DOOR_FIRE），每洞一门
    # 门弧线必须位于墙体上：过滤掉 window 图层中的窗口编号标识矢量曲线
    # （这类标识作为矢量曲线存储、靠近窗口但偏离墙面）。
    # 判定用弧线端点而非 bbox 中心：摆弧的铰链端落在墙线上，
    # 而 bbox 中心会随半径鼓入房间内部（距墙 ~r/2）。
    def near_wall(bz, tol=6.0):
        p1, p4 = bz[0], bz[3]
        for a, b in all_segs:
            if point_to_seg_dist(p1, a, b)[0] < tol:
                return True
            if point_to_seg_dist(p4, a, b)[0] < tol:
                return True
        return False

    fire = items.get(LAYER_DOOR_FIRE, {"lines": [], "quads": [], "curves": []})
    win_arcs = [bz for bz in win_door_curves if near_wall(bz)]
    fire_arcs = [bz for bz in fire["curves"] if near_wall(bz)]
    print(f"[F{floor_no}] 门弧线贴墙过滤: window {len(win_door_curves)}->{len(win_arcs)}, "
          f"DOOR_FIRE {len(fire['curves'])}->{len(fire_arcs)}")
    doors = detect_doors(win_arcs, fire["lines"], fire_arcs,
                         struct_segs=all_segs)
    print(f"[F{floor_no}] 门洞(去重后): {len(doors)}")

    # --- 封口线（窗 + 门洞，带端头盖帽）
    closures = []
    for wg in window_groups:
        if wg["length_pt"] > 2.0:
            closures.extend(opening_closures(wg["axis"]))
    for dr in doors:
        closures.extend(opening_closures(dr["axis"]))

    # --- 房间多边形（全部墙线密封 + 分水岭归属 + 家具边界合并，标签探测）
    labeled_polys = build_rooms(
        all_segs, closures, furn_segs=furn_segs, label_points=labels,
        dump_path=rf"E:\code\pathai\result\_debug_wallmask_f{floor_no}.png")
    print(f"[F{floor_no}] 房间多边形(标签探测): {len(labeled_polys)}")

    rooms = []
    used_labels = set()
    label_by_text = {}
    for li, (text, _) in enumerate(labels):
        label_by_text.setdefault(text, li)
    for idx, (label, poly) in enumerate(labeled_polys):
        if label is None:
            continue
        if label in label_by_text:
            used_labels.add(label_by_text[label])
        centroid_m = pt2m((poly.centroid.x, poly.centroid.y))
        coords_m = [list(pt2m((x, y))) for x, y in poly.exterior.coords]
        rooms.append({
            "id": f"R{floor_no}{idx + 1:03d}",
            "label": label,
            "roomType": classify_room_type(label),
            "polygon_pt": poly,
            "coords_m": coords_m,
            "centroid_m": list(centroid_m),
        })
    print(f"[F{floor_no}] 语义房间(带标签): {len(rooms)}")

    # --- 门洞归属：门中心距房间边界 4pt 以内（门洞开在房间墙体上）
    for dr in doors:
        c = Point(dr["center"])
        dr["rooms"] = []
        for r in rooms:
            if r["polygon_pt"].exterior.distance(c) < 4.0 or \
               r["polygon_pt"].buffer(2.0).contains(c):
                dr["rooms"].append(r["id"])
    # 归属兜底：门中心 12pt 内找最近房间边界
    for dr in doors:
        if dr["rooms"]:
            continue
        best, best_d = None, 12.0
        for r in rooms:
            d = r["polygon_pt"].exterior.distance(Point(dr["center"]))
            if d < best_d:
                best, best_d = r, d
        if best:
            dr["rooms"].append(best["id"])

    # --- QA：封闭空间必须识别出所有门洞
    zero_door = [r["label"] for r in rooms
                 if not any(r["id"] in dr["rooms"] for dr in doors)
                 and r["roomType"] not in ("staircase", "elevator_hall", "shaft",
                                           "atrium", "corridor", "lobby")]
    orphan_doors = [dr for dr in doors if not dr["rooms"]]
    print(f"[F{floor_no}] QA: 无门房间 {len(zero_door)} 个 {zero_door[:10]}, "
          f"无归属门 {len(orphan_doors)} 个")

    # --- 楼梯 / 电梯 / 柱
    stair_items = items.get(LAYER_STAIR, {"lines": [], "quads": [], "curves": []})
    stair_pts = [seg for seg in stair_items["lines"]]
    stair_boxes = bbox_clusters(stair_pts, gap_pt=3 * PT_PER_M) if stair_pts else []
    stair_boxes = [b for b in stair_boxes
                   if 4 < (b[2] - b[0]) * (b[3] - b[1]) * SCALE * SCALE < 200]

    evtr_items = items.get(LAYER_ELEVATOR, {"lines": [], "quads": [], "curves": []})
    evtr_pts = [seg for seg in evtr_items["lines"]] + [q[:2] for q in evtr_items["quads"]]
    evtr_boxes = bbox_clusters(evtr_pts, gap_pt=2 * PT_PER_M) if evtr_pts else []

    col_boxes = []
    for lname in LAYER_COLUMNS:
        ci = items.get(lname)
        if not ci:
            continue
        for q in ci["quads"]:
            xs = [p[0] for p in q]
            ys = [p[1] for p in q]
            col_boxes.append((min(xs), min(ys), max(xs), max(ys)))

    return {
        "rooms": rooms,
        "doors": doors,
        "window_groups": window_groups,
        "wall_segs": all_segs,
        "stair_boxes": stair_boxes,
        "evtr_boxes": evtr_boxes,
        "col_boxes": col_boxes,
        "labels_unmatched": [t for i, (t, _) in enumerate(labels) if i not in used_labels],
    }


def build_geojson(f1, f2):
    def floor_block(floor_no, data):
        walls = []
        for i, (a, b) in enumerate(data["wall_segs"]):
            walls.append({
                "type": "Feature",
                "id": f"W{floor_no}-{i + 1:04d}",
                "geometry": {"type": "LineString",
                             "coordinates": [list(pt2m(a)), list(pt2m(b))]},
                "properties": {"type": "wall", "sourceLayer": LAYER_WALL},
            })
        rooms_g, rooms_s = [], []
        for r in data["rooms"]:
            rooms_g.append({
                "type": "Feature",
                "id": r["id"],
                "geometry": {"type": "Polygon", "coordinates": [r["coords_m"]]},
                "properties": {"type": "room", "roomType": r["roomType"],
                               "label": r["label"], "roomId": r["id"],
                               "centroid": r["centroid_m"]},
            })
            rooms_s.append({
                "id": r["id"], "type": r["roomType"], "label": r["label"],
                "centroid": r["centroid_m"], "geometryId": r["id"],
                "public": r["roomType"] in ("toilet", "corridor", "lobby",
                                            "staircase", "elevator_hall"),
                "accessible": r["roomType"] not in ("staircase", "shaft"),
            })
        doors = []
        for i, dr in enumerate(data["doors"]):
            cx, cy = pt2m(dr["center"])
            doors.append({
                "type": "Feature",
                "id": f"D{floor_no}-{i + 1:04d}",
                "geometry": {"type": "Point", "coordinates": [cx, cy]},
                "properties": {
                    "type": "door",
                    "doorType": dr["kind"],
                    "width_m": round(dr["width_pt"] * SCALE, 2),
                    "mergedCount": dr["merged"],
                    "rooms": dr["rooms"],
                    "sourceLayer": "window" if dr["kind"] == "swing" else LAYER_DOOR_FIRE,
                },
            })
        windows = []
        for i, wg in enumerate(data["window_groups"]):
            a, b = wg["axis"]
            windows.append({
                "type": "Feature",
                "id": f"WN{floor_no}-{i + 1:04d}",
                "geometry": {"type": "LineString",
                             "coordinates": [list(pt2m(a)), list(pt2m(b))]},
                "properties": {"type": "window",
                               "length_m": round(wg["length_pt"] * SCALE, 3)},
            })
        stairs = []
        for i, bxd in enumerate(data["stair_boxes"]):
            x0, y0, x1, y1 = bxd
            corners = [pt2m((x0, y0)), pt2m((x1, y0)), pt2m((x1, y1)), pt2m((x0, y1))]
            coords = [list(c) for c in corners] + [list(corners[0])]
            cen = pt2m(((x0 + x1) / 2, (y0 + y1) / 2))
            stairs.append({
                "type": "Feature",
                "id": f"ST{floor_no}-{i + 1:02d}",
                "geometry": {"type": "Polygon", "coordinates": [coords]},
                "properties": {"type": "staircase",
                               "label": f"楼梯{floor_no}F-{i + 1}",
                               "centroid": list(cen)},
            })
        elevators = []
        for i, bxd in enumerate(data["evtr_boxes"]):
            x0, y0, x1, y1 = bxd
            corners = [pt2m((x0, y0)), pt2m((x1, y0)), pt2m((x1, y1)), pt2m((x0, y1))]
            coords = [list(c) for c in corners] + [list(corners[0])]
            cen = pt2m(((x0 + x1) / 2, (y0 + y1) / 2))
            elevators.append({
                "type": "Feature",
                "id": f"EL{floor_no}-{i + 1:02d}",
                "geometry": {"type": "Polygon", "coordinates": [coords]},
                "properties": {"type": "elevator",
                               "label": f"电梯{floor_no}F-{i + 1}",
                               "centroid": list(cen)},
            })
        columns = []
        for i, bxd in enumerate(data["col_boxes"]):
            x0, y0, x1, y1 = bxd
            corners = [pt2m((x0, y0)), pt2m((x1, y0)), pt2m((x1, y1)), pt2m((x0, y1))]
            coords = [list(c) for c in corners] + [list(corners[0])]
            columns.append({
                "type": "Feature",
                "id": f"C{floor_no}-{i + 1:04d}",
                "geometry": {"type": "Polygon", "coordinates": [coords]},
                "properties": {"type": "column", "sourceLayer": "COLUMN"},
            })

        risk_nodes = [{
            "id": f"RISK-ST-{s['id']}", "type": "stair_entrance",
            "riskLevel": 10, "label": s["properties"]["label"],
            "coordinates": s["properties"]["centroid"],
        } for s in stairs]
        a11y_elevators = [{
            "id": f"EL-A11Y-{e['id']}", "label": e["properties"]["label"],
            "coordinates": e["properties"]["centroid"], "floor": int(floor_no),
        } for e in elevators]

        # --- 拓扑层（简版导航图）：房间质心节点 + 门节点 + 门-房间边
        nodes, edges = [], []
        for r in data["rooms"]:
            nodes.append({
                "id": f"N{floor_no}-{r['id']}", "type": "room",
                "label": r["label"], "coordinates": r["centroid_m"],
            })
        for i, dr in enumerate(data["doors"]):
            nid = f"N{floor_no}-D{i + 1:04d}"
            cx, cy = pt2m(dr["center"])
            nodes.append({"id": nid, "type": "door",
                          "label": "防火门" if dr["kind"] == "fire" else "门",
                          "coordinates": [cx, cy]})
            for rid in dr["rooms"]:
                room = next((r for r in data["rooms"] if r["id"] == rid), None)
                if not room:
                    continue
                dist = round(math.hypot(room["centroid_m"][0] - cx,
                                        room["centroid_m"][1] - cy), 2)
                edges.append({
                    "id": f"E{floor_no}-{nid}-{rid}",
                    "from": nid, "to": f"N{floor_no}-{rid}",
                    "distance": dist,
                    "estimatedTime": round(dist / 1.2, 1),
                    "accessibilityLevel": 0, "riskLevel": 0.0,
                    "walkable": True, "wheelchairAccessible": True,
                    "blindAccessible": True,
                    "geometry": {"type": "LineString",
                                 "coordinates": [[cx, cy], room["centroid_m"]]},
                })

        return {
            "geometry": {
                "walls": walls, "rooms": rooms_g, "doors": doors,
                "stairs": stairs, "elevators": elevators, "columns": columns,
                "windowSegments": windows,
            },
            "semantic": {"rooms": rooms_s},
            "topology": {"nodes": nodes, "edges": edges},
            "accessibility": {
                "elevators": a11y_elevators,
                "riskNodes": risk_nodes,
                "ramps": [], "tactilePaths": [], "groundMaterialChanges": [],
            },
        }

    def cross_floor_edges(f1, f2):
        """楼梯/电梯跨楼层边：按米制中心距离匹配（<2.5m 视为同一井道）"""
        edges = []
        def center_m(bxd):
            x0, y0, x1, y1 = bxd
            return pt2m(((x0 + x1) / 2, (y0 + y1) / 2))
        for kind, key, prefix, blind_ok in (
                ("stair", "stair_boxes", "CF-ST", False),
                ("elevator", "evtr_boxes", "CF-EL", True)):
            for i, b1 in enumerate(f1[key]):
                c1 = center_m(b1)
                best, best_d = None, 2.5
                for j, b2 in enumerate(f2[key]):
                    c2 = center_m(b2)
                    d = math.hypot(c1[0] - c2[0], c1[1] - c2[1])
                    if d < best_d:
                        best, best_d = j, d
                if best is not None:
                    edges.append({
                        "id": f"{prefix}-{i + 1:02d}",
                        "from": f"N1-{('ST' if kind == 'stair' else 'EL')}{i + 1:02d}",
                        "to": f"N2-{('ST' if kind == 'stair' else 'EL')}{best + 1:02d}",
                        "fromFloor": 1, "toFloor": 2, "type": kind,
                        "distance": 4.2,
                        "estimatedTime": 60.0 if kind == "stair" else 15.0,
                        "blindAccessible": blind_ok,
                        "wheelchairAccessible": blind_ok,
                    })
        return edges

    return {
        "venueId": "school-building-01",
        "venueName": "初中学部1#教学楼",
        "version": "8.0.0",
        "coordinateSystem": "local_meters",
        "scale": SCALE,
        "origin": {"x": ORIGIN_X, "y": ORIGIN_Y, "unit": "pt"},
        "generator": "src/parse_cad_pdf.py",
        "notes": "仅解析 PDF 默认开启图层；window 图层已剔除窗口标识矢量曲线；"
                 "门洞按 window+DOOR_FIRE 图层弧线识别，每洞一门（去重）。",
        "floors": {
            "1": floor_block("1", f1),
            "2": floor_block("2", f2),
        },
        "crossFloorEdges": cross_floor_edges(f1, f2),
    }


def main():
    f1 = parse_floor(PDF_F1, 1)
    f2 = parse_floor(PDF_F2, 2)
    geo = build_geojson(f1, f2)
    with open(OUT_GEOJSON, "w", encoding="utf-8") as fp:
        json.dump(geo, fp, ensure_ascii=False, indent=2)
    for fl, data in (("1", f1), ("2", f2)):
        print(f"[F{fl}] 未匹配标签: {data['labels_unmatched'][:20]}")
    print("输出:", OUT_GEOJSON)


if __name__ == "__main__":
    main()
