# -*- coding: utf-8 -*-
"""
PathAI - CAD 平面图 (PDF) -> GeoJSON 解析器

依据 docs/03-地图构建指南.md 的流程：
  图纸解析 -> 坐标归一化 -> 几何矢量化 -> 语义标注 -> 输出四层语义地图(几何+语义部分)

规则（与用户需求对齐）：
1. 只解析 PDF 默认打开(ON)的图层，其余图层忽略。
2. window 图层中剔除非窗口线条；窗口编号标识以矢量曲线或 text 文本形式存储，
   二者均会提取（DK 编号优先正则匹配 text 实体，不足时由矢量笔画识别补充）。
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
from pathlib import Path

from shapely.ops import unary_union, polygonize
from shapely.geometry import LineString, Point, Polygon, box
from shapely import snap as shp_snap

# 拓扑建模（指南 第五章）
from topology import build_floor_topology, build_cross_floor_edges

# ---------------------------------------------------------------- 配置

# 路径自动适配：以本文件位置推导项目根目录，不依赖固定盘符/路径
PROJECT_ROOT = Path(__file__).resolve().parent.parent   # .../src -> 项目根
RESULT_DIR = PROJECT_ROOT / "result"
PDF_F1 = str(PROJECT_ROOT / "A20-002-II-初中学部 1# 教学楼首层平面图-A0_BIAD-无签名.pdf")
PDF_F2 = str(PROJECT_ROOT / "A20-003-II-初中学部 1# 教学楼二层平面图-A0_BIAD-无签名.pdf")
OUT_GEOJSON = str(RESULT_DIR / "school_building_01_map_v9.geojson")

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
# 家具级图层（金属构件）：既含真实墙体段（卫生间隔墙等，缺了
# 会导致房间不闭合），又含厕位隔断/洗手台等会把房间内部切碎的构件线。
# 处理：以细线(1px)单独栅格化参与封闭；凡围出 <ABSORB_CELL_M2 微单元的
# 家具线在微单元邻域内擦除（打通厕位与走道），真实隔墙两侧都是大房间、
# 邻域无微单元，不受影响。
LAYERS_FURNITURE = ("A-METAL-S",)
# 强制剔除的图层：整层元素不参与任何解析（不计入墙体封闭、门/窗/房间识别）。
# A-TECH-SANT（卫生/给排水器具等）已设为默认关闭，且 PyMuPDF 的
# page.get_drawings() 不感知图层可见性、会照常返回其全部矢量元素，
# 故在此显式剔除，确保即使该图层在 PDF 中标记为开启也被忽略。
LAYERS_IGNORE = ("A-TECH-SANT",)
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
# 摆弧门去重"中心距守卫"：同一门的多段弧中心几乎重合(<此值)才视为同门；
# 相邻两房间的门中心通常相隔 ≥ 门宽(>20pt)，用此守卫避免被叶段投影间隙误并。
# 取值与 dedupe_doorways 的 MERGE_PT(13) 同量级，保证 detect_doors 与全局去重一致。
DOOR_CLUSTER_CENTER_PT = 14.0
MIN_DOOR_WIDTH_PT = 9.0      # 房间门最小宽度（≈0.48m），更小的为厕位/器具弧
WALL_SNAP = 0.8              # 墙线端点吸附网格
GRID_QUANT = 1.0             # 端点量化网格(pt)，消除 CAD 线端点微小间隙
WALL_BODY_SHORT_SIDE_PT = 7.0   # 墙体单元格短边阈值(≈0.45m)，小于此且细长视为墙身段
MIN_OPENING_WIDTH_PT = 12.0  # 墙缝开口最小宽度（≈0.64m），下限过滤虚线墙残缝
MAX_OPENING_WIDTH_PT = 80.0  # 墙缝开口最大宽度（≈4.2m），上限过滤跨房大洞
OPENING_FLANK_MIN_PT = 18.0  # 开口两侧墙段最小长度（短于该值视为虚线残段）
OPENING_CODE_NEAR_PT = 60.0  # DK 矢量编号块到开口中心的最近距离（CAD 标签常贴墙体外侧，宽容）
DEFAULT_OPENING_WIDTH_PT = 30.0  # 无墙缝可量时门洞默认宽度（≈1.0m，落在 [MIN,MAX] 区间内）
DK_SNAP_WALL_PT = 50.0     # DK 块距最近墙 ≤ 此值则把门洞中心吸附到墙线（准确落在墙体内）
DK_DEDUP_PT = 18.0         # 与已有窗/门/门洞中心距离小于此值则视为重复，跳过
# DK 块距任一摆弧门(普通门)中心 < 此值 → 该 DK 是普通门门口的标注(门宽/编号)，
# 而非洞口；不据此生成门洞。门洞(DK 洞口)只应出现在没有摆弧门的卫生间/楼梯间。
DK_NEAR_ARC_PT = 22.0
MIN_ROOM_AREA_M2 = 3.0
MAX_ROOM_AREA_M2 = 600.0
ABSORB_CELL_M2 = 2.0       # 小于该面积的自由单元为可填充微单元（厕位格等）
MERGE_REGION_M2 = 9.0      # 小于该面积的未标注单元可经守卫式泛洪并入邻房
WALL_EXT_PT = 6.0          # 墙线端点外延（桥接 T 型接头/窗洞收口缝隙）
LABEL_MIN_SIZE = 9.5         # 房间名称最小字号(pt)（门厅无障碍出入口/人防主出入口约 10~11pt）
TITLE_BLOCK_X = 2900.0       # 图签区 x 起点（右侧剔除）

# --- 楼梯/电梯井编号（图纸权威标识，如 II-B2-01#ST / II-02#EL）---
# 同一井道在各层沿用同一编号，是比"几何中心距离"可靠得多的跨层配对依据；
# 且可补齐某层踏步线缺失导致的漏检（F1 的 B3-01 仅剩 ~4m² 碎片、B1-03 无踏步线）。
FACILITY_CODE_RE = re.compile(r"^[A-Z0-9]+(?:-[A-Z0-9]+)*#(ST|EL)$")
FACILITY_CODE_NEAR_M = 2.0   # 编号文字到设施 bbox 的最大容差(米)
STAIR_MAX_ASPECT = 3.0       # 无编号且长宽比超此值 → 判为伪楼梯并剔除

# 非封闭空间的室外/开敞标签：不参与房间探测（避免抢占附近区域）
# 教学楼中"无障碍出入口/门厅无障碍出入口/人防主出入口"等均为公共空间节点，
# 应被识别为房间（或作为 topology facility_entrance 节点），故不列入黑名单。
LABEL_SKIP_RE = re.compile(
    r"(非机动车车库入口|非机动车车库出入口|消防车道|车道|雨棚|屋面|上空|庭园|台阶|坡道|散水|屋顶平台|泄爆井|不上人屋面)")

ROOM_TYPE_RULES = [
    ("卫生间", "toilet"), ("洗手间", "toilet"),
    ("楼梯", "staircase"), ("电梯", "elevator_hall"),
    ("走道", "corridor"), ("走廊", "corridor"),
    ("门厅", "lobby"), ("大厅", "lobby"),
    ("门厅无障碍出入口", "accessible_entrance"),
    ("无障碍出入口", "accessible_entrance"),
    ("人防主出入口", "entrance"), ("出入口", "entrance"),
    ("教室", "classroom"), ("合班", "classroom"), ("书法", "classroom"), ("美术", "classroom"),
    ("音乐", "classroom"), ("实验室", "lab"),
    (" resource", "classroom"), (" resource教室", "classroom"),
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

# 公共空间（指南 4.2：卫生间/楼梯间/电梯间/走廊为公共，大厅/出入口/无障碍出入口也属于公共）
ROOM_PUBLIC_TYPES = {
    "toilet", "staircase", "elevator_hall", "corridor", "lobby",
    "entrance", "accessible_entrance", "atrium",
}

# 无障碍可达性（指南 4.2：楼梯对视障禁用；电梯/出入口/走廊/大厅均无障碍）
ACCESSIBLE_TYPES = {
    "classroom", "lab", "office", "meeting", "equipment", "storage",
    "library", "medical", "counseling", "activity", "reception",
    "corridor", "lobby", "entrance", "accessible_entrance",
    "elevator_hall", "atrium", "toilet",
}
NON_ACCESSIBLE_TYPES = {"staircase", "shaft"}

# 拓扑建模时是否需要从走廊接入（指南 4.2：是否有独立出入口）
# 走廊/门厅/大厅/出入口/楼梯/电梯厅均视为公共接入点，其他房间通过门接入走廊
INDEPENDENT_ENTRANCE_TYPES = {
    "corridor", "lobby", "entrance", "accessible_entrance",
    "staircase", "elevator_hall", "atrium",
}


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


def extract_facility_codes(page):
    """
    提取楼梯井/电梯井编号（II-B2-01#ST、II-02#EL），返回 [(code, (cx, cy)_pt, kind)]。

    这些编号是图纸的权威标识：同一井道在各楼层沿用同一编号，
    因此可直接作为跨层配对键，避免"按中心距离猜"带来的漏配/错配；
    编号字号较小（#EL 约 8.4pt），不能复用 extract_room_labels 的字号门槛。
    """
    out = []
    d = page.get_text("dict")
    for block in d["blocks"]:
        if block.get("type") != 0:
            continue
        for line in block.get("lines", []):
            txt = "".join(s["text"] for s in line["spans"]).strip()
            m = FACILITY_CODE_RE.match(txt)
            if not m:
                continue
            x0, y0, x1, y1 = line["bbox"]
            if x1 > TITLE_BLOCK_X:
                continue
            out.append((txt, ((x0 + x1) / 2, (y0 + y1) / 2), m.group(1)))
    return out


def extract_dk_text_labels(page):
    """
    从文本对象中提取 DK 门窗编号（如 DK1224、DK2424）。

    CAD 导出的 DK 标注有两种形态：
      1) 矢量曲线（被 cluster_window_glyph_codes 聚类后由 is_dk_block 识别）；
      2) 文本实体（text span，尤其竖排/旋转标注更常以此存储）。
    后者用正则直接匹配最稳定，且天然支持旋转；本函数作为矢量 DK 识别的重要补充。

    返回 [(cx, cy), ...]（PDF pt，已按 8pt 去重）。
    """
    out = []
    seen = []
    d = page.get_text("dict")
    for block in d.get("blocks", []):
        if block.get("type") != 0:
            continue
        for line in block.get("lines", []):
            txt = "".join(s["text"] for s in line["spans"]).strip()
            # 去掉可能夹杂的空格/全角空格
            txt_clean = txt.replace(" ", "").replace("\u3000", "")
            if not re.match(r"^DK\d+$", txt_clean):
                continue
            x0, y0, x1, y1 = line["bbox"]
            cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
            if any(math.hypot(cx - sx, cy - sy) < 8.0 for sx, sy in seen):
                continue
            seen.append((cx, cy))
            out.append((cx, cy))
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
        # 图签区文字（小字号常含管理/版本/日期关键词）—— 不视为房间标签
        if any(k in t for k in ("归档记录", "出图日期", "图号", "版本号",
                                 "图名", "会  签", "会 签", "会签",
                                 "验证签字", "教育建筑", "产业发展研究院",
                                 "初中学部", "教学楼", "项目名称", "工程名称",
                                 "出图", "设计", "校对", "审核", "审定",
                                 "BIAD", "A20-", "平面图", "学校",
                                 "建设单位", "设计单位", "图纸",
                                 "2m范围内", "门窗洞口", "范围内", "年   月   日",
                                 "年 月 日", "年 月")):
            return False
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
        # 弧中点：门向房间内开，弧必然鼓入所服务房间的内部
        door_segs.append({"hinge": center, "tip": tip, "radius": r,
                          "kind": kind, "arc_mid": m})

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
        # 中心距守卫：同一门多段弧中心几乎重合，相邻两房间门中心相隔 ≥ 门宽(>20pt)。
        # 避免同一面墙上相邻两房间的门(如 R1021/R1022)被叶段投影间隙误并为一道。
        c1 = seg_midpoint(a1, b1)
        c2 = seg_midpoint(a2, b2)
        if math.hypot(c1[0] - c2[0], c1[1] - c2[1]) > DOOR_CLUSTER_CENTER_PT:
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
        # MIN_DOOR_WIDTH_PT 仅用于剔除 window 层的厕位/器具小弧；
        # DOOR_FIRE 层只放防火门，半径>=4pt(已在上游 arc_to_door 守卫)即有效，
        # 不再二次过滤，避免漏掉窄/小尺寸防火门（合班教室等）。
        if rep["kind"] == "swing" and rep["radius"] < MIN_DOOR_WIDTH_PT:
            continue  # 厕位/器具小弧，非房间门
        hinge, tip = rep["hinge"], rep["tip"]
        center = seg_midpoint(hinge, tip)
        doors.append({
            "center": center,
            "width_pt": rep["radius"],
            "axis": (hinge, tip),
            "kind": rep["kind"],
            "arc_mid": rep["arc_mid"],
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


def cluster_window_glyph_codes(window_lines, link=6.0, min_strokes=8,
                               with_strokes=False):
    """
    把 window 图层短笔画聚成"矢量文字块"用于确认开口类型。
    返回 [(cx, cy, w, h, n_strokes), ...]  (PDF pt)
    with_strokes=True 时每个元素追加第 6 项 = 该块的原始短笔画列表
    [((x0,y0),(x1,y1)), ...]，供 DK 等特定前缀的几何识别/渲染使用。
    """
    TINY = 8.0
    shorts = [(a, b) for a, b in window_lines if seg_len(a, b) < TINY]
    if not shorts:
        return []
    n = len(shorts)
    parent = list(range(n))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb
    bbs = []
    for a, b in shorts:
        bbs.append((min(a[0], b[0]), min(a[1], b[1]),
                    max(a[0], b[0]), max(a[1], b[1])))
    CELL = max(link * 2, 8.0)
    grid = collections.defaultdict(list)
    for i, b in enumerate(bbs):
        cx, cy = (b[0] + b[2]) / 2, (b[1] + b[3]) / 2
        grid[(int(cx // CELL), int(cy // CELL))].append(i)
    for (gx, gy), idxs in grid.items():
        neigh = []
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                neigh.extend(grid.get((gx + dx, gy + dy), []))
        for i in idxs:
            for j in neigh:
                if j <= i:
                    continue
                b1, b2 = bbs[i], bbs[j]
                d = max(0.0, max(b1[0], b2[0]) - min(b1[2], b2[2]))
                d2 = max(0.0, max(b1[1], b2[1]) - min(b1[3], b2[3]))
                if math.hypot(d, d2) < link:
                    union(i, j)
    groups = collections.defaultdict(list)
    for i in range(n):
        groups[find(i)].append(i)
    out = []
    for g in groups.values():
        if len(g) < min_strokes:
            continue
        x0 = min(bbs[i][0] for i in g)
        y0 = min(bbs[i][1] for i in g)
        x1 = max(bbs[i][2] for i in g)
        y1 = max(bbs[i][3] for i in g)
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
        if with_strokes:
            out.append((cx, cy, x1 - x0, y1 - y0, len(g),
                        [shorts[i] for i in g]))
        else:
            out.append((cx, cy, x1 - x0, y1 - y0, len(g)))
    return out


def find_wall_openings(dk_blocks, all_segs, wall_gaps=None,
                       swing_centers=None):
    """以 window 图层中的 DK 矢量编号块直接生成门洞（洞口）。

    规则（用户 2026-08-05 明确，简化为单一判据）：
      门洞 = window 图层中带 DK 矢量 strokes 的部分（"DK" 是 window
      组件的字形标签，本身就在墙线/洞口上方）。

    旋转方向：DK 文字在 CAD 中有 0° / 90° / 180° / 270° 四种放置方向
    （门外圈文字正对室内阅读者），DK 识别由已经旋转无关的 is_dk_block 完成；
    本函数不再关心 DK 旋转，仅根据 DK 块中心放置门洞几何。

    几何吸附：
      1) 若 DK 块 50pt 内存在墙缝 → 复用墙缝实测轴与宽度（最准确）；
      2) 否则吸附到最近墙线段：中心落在墙上、轴⊥墙、宽 = DEFAULT_OPENING_WIDTH_PT。

    避让（保留一项）：门洞应避开已识别的摆弧门(swing)。普通房间门口的 DK
    编号是该摆弧门的标注而非洞口；但若 DK 与摆弧门中心 ≥ DK_NEAR_ARC_PT
    （普通教室走廊侧的门距明显更远），仍照常生成门洞。

    返回 [{'center','axis','width_pt','kind':'opening'}]，与 detect_doors 输出同构。
    全局去重交由 dedupe_doorways 完成。
    """
    gap_list = wall_gaps or []

    def nearest_seg(p):
        best = None
        for a, b in all_segs:
            d, t = point_to_seg_dist(p, a, b)
            if best is None or d < best[0]:
                best = (d, t, a, b)
        return best

    def nearest_gap(p, tol=DK_SNAP_WALL_PT):
        best = None
        for g in gap_list:
            gd = math.hypot(p[0] - g["center"][0], p[1] - g["center"][1])
            if gd <= tol and (best is None or gd < best[0]):
                best = (gd, g)
        return best

    out = []
    seen = []
    for cx, cy in dk_blocks:
        c = (cx, cy)
        # 与本次已生成门洞去重（多个 DK 块被聚到同一中心时只取一扇）
        if any(math.hypot(c[0] - s[0], c[1] - s[1]) < DK_DEDUP_PT for s in seen):
            continue
        # 避让摆弧门：门口紧贴摆弧门的 DK 是该门的编号/宽度标注，不是洞口
        if swing_centers is not None and any(
                math.hypot(c[0] - sc[0], c[1] - sc[1]) < DK_NEAR_ARC_PT
                for sc in swing_centers):
            continue

        # 优先复用邻近墙缝（实测轴与宽度）
        ng = nearest_gap(c)
        if ng is not None:
            g = ng[1]
            out.append({
                "center": g["center"],
                "axis": (g["left"], g["right"]),
                "width_pt": g["gap"],
                "kind": "opening",
            })
            seen.append(c)
            continue

        # 否则吸附到最近墙线段
        best = nearest_seg(c)
        if best is None:
            continue
        d, t, a, b = best
        px = a[0] + t * (b[0] - a[0])
        py = a[1] + t * (b[1] - a[1])
        # 距离 ≤ DK_SNAP_WALL_PT 才吸附到墙线（避免远处孤点被误判为洞口）
        center = (px, py) if d <= DK_SNAP_WALL_PT else c
        dx, dy = b[0] - a[0], b[1] - a[1]
        L = math.hypot(dx, dy) or 1.0
        perpx, perpy = -dy / L, dx / L
        w2 = DEFAULT_OPENING_WIDTH_PT / 2.0
        axis = ((center[0] - perpx * w2, center[1] - perpy * w2),
                (center[0] + perpx * w2, center[1] + perpy * w2))
        out.append({
            "center": center,
            "axis": axis,
            "width_pt": DEFAULT_OPENING_WIDTH_PT,
            "kind": "opening",
        })
        seen.append(c)
    return out


def dedupe_doorways(doors):
    """合并真正重合的门：两门中心极近(<阈值)即视为同一洞口，仅保留一扇。
    注意：无摆弧门洞(opening)的轴垂直于墙，而摆弧门(arc)的轴平行于墙，二者轴方向
    相差约 90°，故不能用"轴平行+轴向重叠"判定重合（会在墙角误并相邻的不同门）。
    正确判据是"中心是否落在同一洞口"——同一洞口的门中心都在墙线上、彼此很近，
    故仅用中心距离即可；相邻不同门洞(>容差)各自保留。
    优先级：摆弧门(swing/fire) > 无摆弧门洞(opening)，避免丢失门类信息、不重复计入。
    """
    MERGE_PT = 13.0   # 同一洞口中心距离阈值(pt)

    def link(d1, d2):
        return math.hypot(d1["center"][0] - d2["center"][0],
                          d1["center"][1] - d2["center"][1]) < MERGE_PT

    groups = cluster_items(doors, link)
    out = []
    for g in groups:
        rep = next((d for d in g if d.get("kind") != "opening"), g[0])
        out.append(rep)
    return out


def _stroke_features(a, b):
    dx = b[0]-a[0]; dy = b[1]-a[1]
    L = math.hypot(dx, dy)
    if L < 0.001:
        return None
    ang = math.degrees(math.atan2(dy, dx))
    ang_mod = ang % 180.0
    return {"cx":(a[0]+b[0])/2, "cy":(a[1]+b[1])/2, "L":L, "ang":ang,
            "ang_mod":ang_mod,
            "is_vert": abs(ang_mod-90) < 28,
            "is_horiz": min(ang_mod, 180-ang_mod) < 22}


def _has_opp_diagonals_in(strokes, min_len=0.5):
    """判定给定 strokes 同时存在向上斜线与向下斜线（K 的核心特征）。

    在小尺度 CAD 矢量文字下，两条对向斜线长度差异较大（一粗一细），
    不要求严苛 45°；只要不是接近垂直、接近水平，且 dy 有正有负即满足。
    """
    n_up = n_dn = 0
    for f in strokes:
        if f["L"] < min_len:
            continue
        am = f["ang_mod"]  # 0..180
        if am < 15 or am > 165:
            continue  # 接近水平
        if abs(am - 90) < 18:
            continue  # 接近垂直
        if f["ang"] < 0:
            n_up += 1
        elif f["ang"] > 0:
            n_dn += 1
    return (n_up > 0 and n_dn > 0), n_up, n_dn


def _glyph_vertical_angle(feats):
    """从长笔画中估计字形的"竖直"主方向（0~180）。

    DK/MGD 等编号首字符都有明显长竖，K 也有长竖，因此竖直方向是长笔画最密集的角度。
    按 10° 分桶取众数；没有长笔画时默认 90°（页面竖直）。
    """
    longs = [f for f in feats if f["L"] >= 4.0]
    if not longs:
        return 90.0
    buckets = collections.Counter()
    for f in longs:
        key = round(f["ang_mod"] / 10.0) * 10.0
        buckets[key] += 1
    return buckets.most_common(1)[0][0]


def _cluster_along_axis(feats, axis="x", gap=4.0):
    """沿指定轴把笔画 feat 聚成字符列（约 1 个列 = 1 个字符）。

    axis='x' 用于水平书写（左→右），'y' 用于竖排（上→下/下→上）。
    返回 [[feat, ...], ...] 按书写轴递增排序的列列表。
    """
    if not feats:
        return []
    coord = "cy" if axis == "y" else "cx"
    srt = sorted(feats, key=lambda f: f[coord])
    cols = [[srt[0]]]
    for f in srt[1:]:
        col_min = cols[-1][0][coord]
        if f[coord] - col_min > gap:
            cols.append([f])
        else:
            cols[-1].append(f)
    return cols


def is_dk_block(strokes, bbox):
    """严格 DK 识别：D 在前、K 在后、二者相邻。

    之前用「左 40% 有竖笔 + 中段有对向斜线」的笼统区域判定，
    会把 M1524 / C01224 等非 DK 块误判。现改为按字符列逐一验证。

    规则：
      - 把块内所有笔画沿书写轴向聚成字符列（水平书写→按 x，竖排→按 y）；
      - 至少需要 2 列（前端至少 D + K）；
      - 第 0 列 = D：≤2 条字形长竖茎 + 茎右侧有弧线笔画；
      - 第 1 列 = K：≤2 条字形长竖茎 + 同时存在上/下对向斜线；
      - 以上都满足才算 DK。

    竖排文字（贴侧墙旋转 90°/270° 的 DK）：字符沿 Y 排列、字形
    "竖直"方向非页面竖直，聚类轴向同步切换；D 的「右侧弧线」也沿
    书写方向定义。
    """
    x0, y0, x1, y1 = bbox
    w = x1 - x0
    if w < 8.0:
        return False, "block_too_narrow"
    if len(strokes) < 6:
        return False, "too_few_strokes"

    # 提取笔画特征
    all_feats = []
    for s in strokes:
        f = _stroke_features(s[0], s[1])
        if f:
            all_feats.append(f)
    if len(all_feats) < 6:
        return False, "too_few_eff_strokes"

    glyph_ang = _glyph_vertical_angle(all_feats)

    def is_glyph_vert(f2):
        diff = abs((f2["ang_mod"] - glyph_ang + 90) % 180 - 90)
        return diff < 25

    # 判断文字是否竖排（字形"竖直"方向离页面竖直 > 40°）
    vert_text = abs((glyph_ang % 180) - 90) > 40

    # 沿书写轴聚成字符列
    cols = _cluster_along_axis(all_feats, axis="y" if vert_text else "x", gap=4.0)
    if len(cols) < 2:
        return False, "too_few_cols"

    # --- 第 0 列 → 须为 D：有长竖茎 + 弧线在茎右侧 ---
    # 注：CAD 中 D 的弧线由 2-4 段近竖直短划组成，竖茎数可能 >2，不设上限。
    col0 = cols[0]
    col0_verts = [f for f in col0 if is_glyph_vert(f) and f["L"] >= 4.0]
    if len(col0_verts) == 0:
        return False, "d_no_vert"
    stem0 = max(col0_verts, key=lambda f: f["L"])
    # D 的弧线位于竖茎右侧（书写方向正侧）
    # 水平书写："右侧"=cx 更大；竖排："右侧"=cy 更大
    if vert_text:
        right_strokes = [f for f in col0
                         if f["cy"] > stem0["cy"] + 1.5]
    else:
        right_strokes = [f for f in col0
                         if f["cx"] > stem0["cx"] + 1.5]
    if len(right_strokes) < 1:
        return False, "d_no_right_arc"

    # --- 第 1 列 → 须为 K：有长竖茎 + 对向斜线（K 的特征）---
    # K 的两条斜线的互对向性（一条向上、一条向下）是刚需；竖茎数不限。
    col1 = cols[1]
    col1_verts = [f for f in col1 if is_glyph_vert(f) and f["L"] >= 4.0]
    if len(col1_verts) == 0:
        return False, "k_no_vert"
    has_opp, n_up, n_dn = _has_opp_diagonals_in(col1, min_len=0.5)
    if not has_opp:
        return False, f"k_no_opp(up={n_up},dn={n_dn})"

    return True, "DK"


def recognize_dk_glyph_blocks(window_lines, with_strokes=True):
    """对 window 图层矢量文字块做 DK 识别，返回 [(cx, cy), ...] DK 块中心。"""
    blocks = cluster_window_glyph_codes(window_lines, with_strokes=True)
    dk = []
    for blk in blocks:
        cx, cy, _w, _h, _n, segs = blk
        xs = [p[0] for s in segs for p in s]
        ys = [p[1] for s in segs for p in s]
        bbox = (min(xs), min(ys), max(xs), max(ys))
        ok, _reason = is_dk_block(segs, bbox)
        if ok:
            dk.append((cx, cy))
    return dk


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


def rasterize_walls(all_segs, closures, furn_segs=()):
    """
    全部墙体线段(结构+家具,2px) + 门/窗封口线(3px) -> 二值墙图。
    流程：原始绘制(端点外延) -> 画门窗封口线 -> 闭运算密封墙线断口。
    （注：该图真实墙也是 2px 单线，开运算去薄墙会连真墙一起溶掉，不可用）
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
    room_cids = []
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
        room_cids.append(cid)
    if dup_labels:
        print(f"    [QA] 多标签共域(疑似误合并): {dup_labels}")
    # owner 归属图随房间一并返回，供门洞两侧投票使用
    return {"polys": rooms, "owner": owner, "minx": minx, "miny": miny,
            "Z": Z, "cids": room_cids}


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
                   *LAYERS_FURNITURE} - set(LAYERS_IGNORE))
    active = [l for l in wanted if l in on_layers]
    skipped = [l for l in wanted if l not in on_layers]
    if skipped:
        print(f"[F{floor_no}] 跳过(非默认开启): {skipped}")
    if LAYERS_IGNORE:
        print(f"[F{floor_no}] 强制剔除图层(整层忽略): {LAYERS_IGNORE}")

    items = extract_layer_items(page, set(active))
    labels = extract_room_labels(page)
    facility_codes = extract_facility_codes(page)
    # DK 文本标注（旋转/竖排标注常以 text span 存储）须在 doc 关闭前提取，
    # 稍后在门洞识别阶段与矢量 DK 合并去重。
    dk_text_labels = extract_dk_text_labels(page)
    doc.close()

    # --- 结构线段并集（墙体分散在多个默认开启的结构图层中）
    struct_segs = []
    for lname in LAYERS_STRUCT:
        li = items.get(lname)
        if not li:
            continue
        struct_segs.extend(wall_segments(li))
    # 门洞(无门开口)检测：用较大桥接容差重算墙缝，使所有 ≤MAX_OPENING_WIDTH_PT 的墙缝
    # 都被记录为候选（不被默认 30pt 桥接吞掉）。仅取 gaps 列表，合并后的线段丢弃（不影响栅格化）。
    _, opening_gaps = merge_collinear(struct_segs, gap_tol=MAX_OPENING_WIDTH_PT,
                                     record_gaps=True)
    # 合并虚线/点划线断段，恢复连续墙体；同时记录桥接的墙缝（用于无摆弧开口检测）
    struct_segs, wall_gaps = merge_collinear(struct_segs, record_gaps=True)
    # --- 家具层线段（细线参与封闭，微单元邻域内可擦除；剔除 LAYERS_IGNORE）
    furn_segs = []
    for lname in LAYERS_FURNITURE:
        if lname in LAYERS_IGNORE:
            continue
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
          f"家具层线段: {len(furn_segs)}, "
          f"被桥接墙缝: {len(wall_gaps)}")

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

    def _bez_mid(bz):
        p1, p2, p3, p4 = bz
        def lp(a, b, t=0.5):
            return (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t)
        q1, q2, q3 = lp(p1, p2), lp(p2, p3), lp(p3, p4)
        r1, r2 = lp(q1, q2), lp(q2, q3)
        return lp(r1, r2)

    def fire_arc_near_wall(bz, tol=15.0):
        """防火门摆弧贴墙判定（较 swing 门宽松）。

        摆弧的铰链=弧圆心，应落在墙/家具隔墙上；任一摆弧端点（含闭门端，
        沿墙方向）也应在墙上。现实图纸中合班教室等防火门常绘于家具隔墙，
        铰链/端点相对墙线有 6~15pt 偏移，故用较大容差，避免漏识。
        """
        p1, p4 = bz[0], bz[3]
        xs = [p[0] for p in bz]
        ys = [p[1] for p in bz]
        x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
        m = _bez_mid(bz)
        corners = [(x0, y0), (x0, y1), (x1, y0), (x1, y1)]
        # 弧圆心（铰链）= 离弧中点最远的角点
        center = max(corners,
                     key=lambda c: (c[0] - m[0]) ** 2 + (c[1] - m[1]) ** 2)
        for a, b in all_segs:
            if (point_to_seg_dist(center, a, b)[0] < tol
                    or point_to_seg_dist(p1, a, b)[0] < tol
                    or point_to_seg_dist(p4, a, b)[0] < tol):
                return True
        return False

    def ordinary_door_arc(bz, tol_anchor=12.0):
        """普通门（window 层摆弧）识别：铰链(弧圆心)或端点贴墙即锚定。
        放宽容差以容忍绘制偏移/家具隔墙，捕获此前被 near_wall(tol=6) 漏掉的普通门；
        完全脱离墙体的悬空残段仍被排除。摆弧本就位于门口（WALL 层该处无连续墙体），
        故"对应位置无墙"由摆弧的门口语义保证，不再额外做墙缝匹配。"""
        p1, p4 = bz[0], bz[3]
        xs = [p[0] for p in bz]
        ys = [p[1] for p in bz]
        x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
        m = _bez_mid(bz)
        corners = [(x0, y0), (x0, y1), (x1, y0), (x1, y1)]
        center = max(corners,
                     key=lambda c: (c[0] - m[0]) ** 2 + (c[1] - m[1]) ** 2)  # 铰链
        for a, b in all_segs:
            if (point_to_seg_dist(center, a, b)[0] < tol_anchor
                    or point_to_seg_dist(p1, a, b)[0] < tol_anchor
                    or point_to_seg_dist(p4, a, b)[0] < tol_anchor):
                return True
        return False

    fire = items.get(LAYER_DOOR_FIRE, {"lines": [], "quads": [], "curves": []})
    win_arcs = [bz for bz in win_door_curves if ordinary_door_arc(bz)]
    fire_arcs = [bz for bz in fire["curves"] if fire_arc_near_wall(bz, tol=15.0)]
    print(f"[F{floor_no}] 门弧线贴墙过滤: window {len(win_door_curves)}->{len(win_arcs)}, "
          f"DOOR_FIRE {len(fire['curves'])}->{len(fire_arcs)}")
    doors = detect_doors(win_arcs, fire["lines"], fire_arcs,
                         struct_segs=all_segs)
    print(f"[F{floor_no}] 门洞(去重后): {len(doors)}")
    # 普通门(摆弧)中心集合：供门洞识别避让。普通房间的门是 window 层摆弧元素，
    # 其门口的 DK 矢量标注(门宽/编号)紧邻摆弧门；该 DK 是门标注而非洞口，不生成门洞。
    swing_centers = [dr["center"] for dr in doors if dr.get("kind") == "swing"]

    # --- 无摆弧门洞（DK 洞口/出入口推拉门/楼梯间门洞）：规则为 window 图层中带
    #     DK 矢量 strokes 的部分直接生成门洞。门洞常以"类窗元素"画在 window 层，
    #     DK 标注就在该元素上（标注相对元素中心偏移 0~10pt）。故处理分两步：
    #       1) DK 块落在某个 window 组上（距离 < 阈值）→ 该 window 组实为门洞，
    #          转为 opening 并从窗口列表移除（不再当作窗封口/渲染）；
    #       2) 其余 DK 块 → 吸附到最近墙线段生成门洞（纯洞口，无窗框）。
    glyph_codes = cluster_window_glyph_codes(win["lines"])
    dk_blocks = recognize_dk_glyph_blocks(win["lines"])
    # 补充文本实体形式的 DK 标注（旋转/竖排标注常以 text span 存储），与矢量 DK 合并去重
    dk_text = dk_text_labels
    n_text_added = 0
    for c in dk_text:
        if not any(math.hypot(c[0] - bc[0], c[1] - bc[1]) < 8.0 for bc in dk_blocks):
            dk_blocks.append(c)
            n_text_added += 1
    print(f"[F{floor_no}] window 矢量编号块: {len(glyph_codes)}  "
          f"DK前缀(矢量): {len(dk_blocks) - n_text_added}  DK文本补充: {n_text_added}  "
          f"合计: {len(dk_blocks)}")

    DK_WIN_CONVERT_PT = 13.0   # DK 块距 window 组中心 < 此值 → 该组判为门洞
    converted_idx = set()
    dk_consumed = set()
    win_openings = []  # 由 window 组转化而来的门洞
    for cx, cy in dk_blocks:
        best_i, best_d = -1, 1e9
        for i, wg in enumerate(window_groups):
            if i in converted_idx:
                continue
            d = math.hypot(cx - wg["center"][0], cy - wg["center"][1])
            if d < best_d:
                best_d, best_i = d, i
        if best_i >= 0 and best_d < DK_WIN_CONVERT_PT \
                and not any(math.hypot(cx - sc[0], cy - sc[1]) < DK_NEAR_ARC_PT
                            for sc in swing_centers):
            wg = window_groups[best_i]
            win_openings.append({
                "center": wg["center"],
                "axis": wg["axis"],
                "width_pt": wg["length_pt"],
                "kind": "opening",
                "arc_mid": wg["center"],
                "merged": 1,
            })
            converted_idx.add(best_i)
            dk_consumed.add((round(cx, 1), round(cy, 1)))
    # 移除已被判为门洞的 window 组（避免既当窗又当门）
    for i in sorted(converted_idx, reverse=True):
        window_groups.pop(i)
    print(f"[F{floor_no}] DK→门洞(由 window 组转化): {len(win_openings)}")

    remaining_dk = [c for c in dk_blocks
                    if (round(c[0], 1), round(c[1], 1)) not in dk_consumed]
    # 简化：每个 DK 块 → 一个门洞，吸附到最近墙（不再依赖房间类型 / 紧邻摆弧门过滤；
    # 摆弧门避让由 swing_centers 内部处理；真正重合 → 由 dedupe_doorways 合并）。
    wall_openings = find_wall_openings(remaining_dk, all_segs,
                                       wall_gaps=opening_gaps,
                                       swing_centers=swing_centers)
    # 转换为 detect_doors 输出格式（center/axis/width_pt/arc_mid=中心、
    # kind=opening），并入统一门列表参与归属链
    for wo in win_openings + wall_openings:
        doors.append({
            "center": wo["center"],
            "axis": wo["axis"],
            "width_pt": wo["width_pt"],
            "kind": "opening",
            "arc_mid": wo["center"],   # 无摆弧，归属几何中心即可
            "merged": 1,
        })
    print(f"[F{floor_no}] 无摆弧门洞(DK约束): {len(wall_openings)}")

    # --- 全局门去重：仅合并"真正同一洞口"的门（DK 门洞可能与摆弧门重合），
    #     避免相邻不同门洞被误删；合并后每个洞口只保留一扇（优先摆弧门）。
    before = len(doors)
    doors = dedupe_doorways(doors)
    print(f"[F{floor_no}] 门去重: {before} -> {len(doors)}")

    # --- DOOR_FIRE 防火门仅由摆弧(curve)经 detect_doors 生成（需求：只处理 arc based door）---
    # fire["lines"]/fire["quads"] 不用于门识别，由 detect_doors 的 fire_curves 分支处理。

    # --- 封口线（窗 + 摆弧门洞 + 无摆弧开口，带端头盖帽）
    closures = []
    for wg in window_groups:
        if wg["length_pt"] > 2.0:
            closures.extend(opening_closures(wg["axis"]))
    for dr in doors:
        closures.extend(opening_closures(dr["axis"]))

    # --- 房间多边形（全部墙线密封 + 分水岭归属 + 守卫式泛洪，标签探测）
    room_res = build_rooms(
        all_segs, closures, furn_segs=furn_segs, label_points=labels,
        dump_path=str(RESULT_DIR / f"_debug_wallmask_f{floor_no}.png"))
    labeled_polys = room_res["polys"]
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

    # --- 门洞归属 pass 0：门弧中点（摆动侧）落在房间内部 -> 该房间所有。
    #     门向内开，弧必然鼓入所服务房间；这是最可靠的归属信号
    #     （如 MGD1124 弧鼓入乐器存放室而非相邻的音乐教室）
    for dr in doors:
        dr["rooms"] = []
        am = dr.get("arc_mid")
        if not am:
            continue
        p = Point(am)
        containers = []
        nearest, nearest_d = None, 8.0
        for r in rooms:
            poly = r["polygon_pt"]
            if poly.contains(p):
                containers.append((poly.exterior.distance(p), r))
            else:
                d = poly.exterior.distance(p)
                if d < nearest_d:
                    nearest, nearest_d = r, d
        if containers:
            # 取弧中点最深入其内部的房间
            containers.sort(key=lambda x: -x[0])
            dr["rooms"].append(containers[0][1]["id"])
        elif nearest is not None:
            dr["rooms"].append(nearest["id"])
    # --- 门洞归属 pass 1：两侧投票。门轴两侧 ±6pt 各取一点，
    #     在 owner 归属图中找 45px(15pt) 内最近的已标注房间像素 ——
    #     门天然归属于两侧的空间（一侧房间一侧走廊，或两侧房间），
    #     与邻房角点距离无关（修正 MGD1124 误归音乐教室的问题）
    import numpy as np
    owner = room_res["owner"]
    ominx, ominy, oZ = room_res["minx"], room_res["miny"], room_res["Z"]
    oH, oW = owner.shape
    cid_to_room = {}
    for r, cid in zip(rooms, room_res["cids"] or []):
        cid_to_room[cid] = r["id"]
    labeled_vals = list(cid_to_room.keys())
    lab_map = np.where(np.isin(owner, labeled_vals), owner,
                       0).astype(np.int32) if labeled_vals else None

    def side_vote(dr, off_pt=6.0, search=45):
        a, b = dr["axis"]
        ang = seg_angle(a, b)
        nx, ny = -math.sin(ang), math.cos(ang)
        votes = []
        for s in (+1.0, -1.0):
            px = dr["center"][0] + nx * off_pt * s
            py = dr["center"][1] + ny * off_pt * s
            X = int(round((px - ominx) * oZ))
            Y = int(round((py - ominy) * oZ))
            x0, x1 = max(0, X - search), min(oW, X + search + 1)
            y0, y1 = max(0, Y - search), min(oH, Y + search + 1)
            sub = lab_map[y0:y1, x0:x1]
            ys_idx, xs_idx = np.where(sub > 0)
            if not len(ys_idx):
                continue
            d2 = (ys_idx + y0 - Y) ** 2 + (xs_idx + x0 - X) ** 2
            i = int(np.argmin(d2))
            cid = int(sub[ys_idx[i], xs_idx[i]])
            if cid in cid_to_room:
                votes.append(cid_to_room[cid])
        return votes

    if lab_map is not None:
        for dr in doors:
            for rid in side_vote(dr):
                if rid not in dr["rooms"]:
                    dr["rooms"].append(rid)
    # --- 门洞归属兜底 1：门中心距房间边界 4pt 以内（仅补空，
    #     不与侧投票冲突 —— 该规则会把邻房角点误挂为门所属）
    for dr in doors:
        if dr["rooms"]:
            continue
        c = Point(dr["center"])
        for r in rooms:
            if r["id"] in dr["rooms"]:
                continue
            if r["polygon_pt"].exterior.distance(c) < 4.0 or \
               r["polygon_pt"].buffer(2.0).contains(c):
                dr["rooms"].append(r["id"])
    # 归属兜底 2：门中心 12pt 内找最近房间边界
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
    # 归属兜底 3（无门房间认领孤儿门）：卫生间/储藏等被隔断切碎的房间，
    # 多边形未能延伸到门所在的外壳墙体；其门就在 30pt 内。
    # 限定：接收方当前无门（避免误抢邻房的门）、30pt 内最近且
    # 次近候选 >1.5 倍距离（唯一性）；每个房间最多认领一扇。
    door_count = {r["id"]: 0 for r in rooms}
    for dr in doors:
        for rid in dr["rooms"]:
            if rid in door_count:
                door_count[rid] += 1
    for dr in doors:
        if dr["rooms"]:
            continue
        c = Point(dr["center"])
        cand = []
        for r in rooms:
            if r["roomType"] in ("staircase", "elevator_hall", "shaft",
                                 "atrium"):
                continue
            if door_count.get(r["id"], 0) > 0:
                continue
            d = r["polygon_pt"].exterior.distance(c)
            cand.append((d, r))
        cand.sort(key=lambda x: x[0])
        if cand and cand[0][0] < 30.0 and \
                (len(cand) == 1 or cand[1][0] > 1.5 * cand[0][0]):
            dr["rooms"].append(cand[0][1]["id"])
            door_count[cand[0][1]["id"]] += 1
    # 归属兜底 4（零门房间偷取多门房间的门）：门与所属房间多边形之间
    # 隔着高窗洞口带/门斗等小空间时（如 MGD1124 与乐器存放室），
    # 门会先被 4pt 规则挂到邻房角点上。零门房间作为封闭空间必须有门，
    # 允许其从仍保留 >=1 扇门的多门房间处偷取 30pt 内最近的门。
    for r in rooms:
        if r["roomType"] in ("staircase", "elevator_hall", "shaft",
                             "atrium"):
            continue
        if door_count.get(r["id"], 0) > 0:
            continue
        c_poly = r["polygon_pt"]
        best, best_d = None, 30.0
        for dr in doors:
            if r["id"] in dr["rooms"]:
                best = None
                break
            d = c_poly.exterior.distance(Point(dr["center"]))
            if d < best_d:
                # 只能偷当前归属房间门数 >=2 的门（给其留至少 1 扇）
                owners = [o for o in dr["rooms"]
                          if door_count.get(o, 0) >= 2]
                if dr["rooms"] and not owners:
                    continue
                best, best_d = dr, d
        if best is not None:
            for o in list(best["rooms"]):
                if door_count.get(o, 0) >= 2:
                    best["rooms"].remove(o)
                    door_count[o] -= 1
            if r["id"] not in best["rooms"]:
                best["rooms"].append(r["id"])
                door_count[r["id"]] += 1

    # --- 门洞(opening)范围约束：仅保留"楼梯间 / 卫生间"的门洞 ---
    # 用户规则：门洞 = window 图层中带 DK 矢量 strokes 的部分；且只识别
    # 楼梯间与卫生间的门洞。其余房间的可通行门应由 swing/fire 摆弧门提供，
    # 不应由 DK 洞口充当。
    # 判定（满足任一即保留）：
    #   1) 归属链已将其挂到 staircase/toilet 房间；或
    #   2) 门洞几何中心落在某"卫生间房间多边形"或"楼梯间 stair_box"的
    #      临近范围(OPENING_SCOPE_PT=30pt≈1.6m)内。
    # 采用临近判定是因为楼梯间在本图仅以 stair_box(facility) 形式存在、缺少
    # staircase 房间多边形，纯靠归属会把楼梯间门洞误判给走廊而被剔除。
    room_type_by_id = {r["id"]: r["roomType"] for r in rooms}
    toilet_polys = [r["polygon_pt"] for r in rooms if r["roomType"] == "toilet"]
    stair_pts = []
    for _ln in ("STAIR", "A-FLOR-STRS"):
        _si = items.get(_ln, {"lines": [], "quads": []})
        for _seg in _si["lines"]:
            stair_pts.append(_seg)
        for _q in _si["quads"]:
            stair_pts.append((_q[0], _q[2]))
    stair_boxes = bbox_clusters(stair_pts, gap_pt=4 * PT_PER_M) if stair_pts else []
    stair_boxes = [b for b in stair_boxes
                   if 10 < (b[2] - b[0]) * (b[3] - b[1]) * SCALE * SCALE < 200]
    stair_polys = [box(b[0], b[1], b[2], b[3]) for b in stair_boxes]

    # --- 规则（2026-08-05 明确）：门洞 = window 图层中带 DK 矢量 strokes 的部分。
    #     简化为单一判据，不再按"仅卫生间/楼梯间附近"做范围过滤——
    #     卫生间通往公共空间的门洞过去会被 _opening_in_scope 误删，现已移除。

    # --- 卫生间的防火门直接丢弃（用户 2026-08-05 明确：不用考虑） ---
    # 卫生间内的 fire 门是防火门，不视为真实出入口，也不参与后续重分类/内部门洞过滤。
    n_toilet_fire = sum(1 for dr in doors
                        if dr.get("kind") == "fire"
                        and any(room_type_by_id.get(rid) == "toilet"
                                for rid in dr.get("rooms", [])))
    if n_toilet_fire:
        doors = [dr for dr in doors
                 if not (dr.get("kind") == "fire"
                         and any(room_type_by_id.get(rid) == "toilet"
                                 for rid in dr.get("rooms", [])))]
        print(f"[F{floor_no}] 卫生间防火门直接丢弃: {n_toilet_fire}")

    # --- 卫生间/楼梯间中带 DK 标注的摆弧门/防火门 → 重分类为门洞(opening) ---
    # 规则：卫生间与楼梯间的门以 window 图层 DK 洞口标注为准；若某摆弧门/防火门
    # 落在 toilet/staircase 房间，且其门体附近存在 DK 编号块，则说明它实为洞口，
    # 不应作为普通摆弧门（否则红框处含 DK 的 window 组件虽被 detect_doors 识别成
    # swing，却失去"门洞"语义，导航拓扑里丢失洞口可达性信息）。
    # 注意：卫生间 fire 门已在上一步丢弃，此处重分类的 fire 门仅针对 staircase。
    n_reclass = 0
    for dr in doors:
        if dr.get("kind") not in ("swing", "fire"):
            continue
        if not any(room_type_by_id.get(rid) in ("toilet", "staircase")
                   for rid in dr.get("rooms", [])):
            continue
        cc = dr["center"]
        if any(math.hypot(cc[0] - dk[0], cc[1] - dk[1]) < 14.0
               for dk in dk_blocks):
            dr["kind"] = "opening"
            dr["arc_mid"] = cc
            n_reclass += 1
    if n_reclass:
        print(f"[F{floor_no}] 卫生间/楼梯间 DK 摆弧门重分类为门洞: {n_reclass}")

    # --- 服务核心内部门：仅过滤普通门/防火门(swing/fire)，门洞(opening)豁免 ---
    # 规则（用户 2026-08-05 简化）：封闭空间内部的门洞要保留（DK → 开门是
    # 单一判据）。仅当门是真正可推拉/开合的摆弧门(swing/fire)且两侧皆属服务
    # 核心时删除，避免男↔女卫生间内部对男卫生间密闭；门洞两侧皆通行，不算隔断门。
    _CORE = {"toilet", "staircase", "equipment", "shaft"}
    _CIRC = {"corridor", "lobby", "atrium", "entrance",
             "accessible_entrance", "elevator_hall"}

    def _door_is_internal_core(dr):
        if dr.get("kind") == "opening":
            return False  # 门洞两侧皆通行，不算"内部隔断门"，豁免
        rms = dr.get("rooms", []) or []  # noqa
        if not rms:
            return False  # 无归属门（楼梯/管井范围兜底）→ 不删
        c = Point(dr["center"])
        # 所属房间须含核心房间
        owner_core = [rid for rid in rms if room_type_by_id.get(rid) in _CORE]
        if not owner_core:
            return False
        # 门须确实属于该核心房间（中心距其边界 < 30pt≈2m）
        if not any(r["polygon_pt"].exterior.distance(c) < 30
                   for r in rooms if r["id"] in owner_core):
            return False
        # 公共面向（距公共空间 < 1.2m）→ 保留
        for r in rooms:
            if room_type_by_id.get(r["id"]) in _CIRC \
               and r["polygon_pt"].exterior.distance(c) < 19:
                return False
        # 触达另一侧核心房间(<2.7m) → 判为内部隔断，删除
        for r in rooms:
            if r["id"] in rms:
                continue
            if room_type_by_id.get(r["id"]) in _CORE \
               and r["polygon_pt"].exterior.distance(c) < 51:
                return True
        return False

    n_core_before = len(doors)
    doors = [dr for dr in doors if not _door_is_internal_core(dr)]
    n_core_after = len(doors)
    if n_core_before != n_core_after:
        print(f"[F{floor_no}] 服务核心内部 swing/fire 删除(开门豁免): "
              f"{n_core_before} -> {n_core_after}")

    # --- QA：封闭空间必须识别出所有门洞（走廊/门厅/出入口/楼梯/电梯厅/管井/中庭为公共过渡空间）
    zero_door = [r["label"] for r in rooms
                 if not any(r["id"] in dr["rooms"] for dr in doors)
                 and r["roomType"] not in ("staircase", "elevator_hall", "shaft",
                                           "atrium", "corridor", "lobby",
                                           "entrance", "accessible_entrance")]
    orphan_doors = [dr for dr in doors if not dr["rooms"]]
    print(f"[F{floor_no}] QA: 无门房间 {len(zero_door)} 个 {zero_door[:10]}, "
          f"无归属门 {len(orphan_doors)} 个")

    # --- 楼梯 / 电梯 / 柱
    # 楼梯来自 STAIR + A-FLOR-STRS 两层（F2 STAIR 仅 2 个 quad，
    # 但 A-FLOR-STRS 含完整踏步/护栏线；合并才能稳定聚类）
    stair_pts = []
    for lname in ("STAIR", "A-FLOR-STRS"):
        si = items.get(lname, {"lines": [], "quads": []})
        for seg in si["lines"]:
            stair_pts.append(seg)
        for q in si["quads"]:
            stair_pts.append((q[0], q[2]))
    stair_boxes = bbox_clusters(stair_pts, gap_pt=4 * PT_PER_M) if stair_pts else []
    stair_boxes = [b for b in stair_boxes
                   if 10 < (b[2] - b[0]) * (b[3] - b[1]) * SCALE * SCALE < 200]

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
        "labels_all": [t for t, _ in labels],
        "labels_all_with_pt": labels,
        "facility_codes": facility_codes,
    }


def reconcile_facilities(f1, f2):
    """用图纸井道编号（II-xx#ST / II-xx#EL）校正楼梯/电梯识别结果。

    做三件事：
      1) **赋号**：编号文字落在 bbox 内（或 2m 容差内）→ 全局贪心最近匹配，
         保证一个编号只绑定一个 bbox；
      2) **剔伪**：无编号且长宽比 > STAIR_MAX_ASPECT 的"楼梯"判为误检
         （F1 曾出现 6.1m×32m 的条带被 <200m² 面积阈值放行）；
      3) **补漏**：某编号在本层有文字标注但无 bbox（该层踏步线缺失/过碎），
         而另一层有同编号 bbox → 按"bbox 相对编号文字的偏移"平移复制过来。
         F1 的 II-B3-01#ST（仅剩 ~4m² 碎片，被 10m² 阈值滤掉）与
         II-B1-03#ST（A-FLOR-STRS 层无踏步线）即由此补齐。

    最终按编号排序写回 data[key] 与 data[key + "_codes"]，
    使 N{floor}-ST{i} 的编号顺序在两层间一致、可读且确定。
    """
    near_pt = FACILITY_CODE_NEAR_M / SCALE
    report = {}

    def assign(data, key, kind):
        """返回 (boxes, codes, cands)；codes 与 boxes 等长，未命中为 None。"""
        boxes = list(data.get(key, []))
        cands = [(t, p) for t, p, k in data.get("facility_codes", []) if k == kind]
        pairs = []
        for i, (x0, y0, x1, y1) in enumerate(boxes):
            for j, (_t, (cx, cy)) in enumerate(cands):
                dx = max(x0 - cx, 0.0, cx - x1)
                dy = max(y0 - cy, 0.0, cy - y1)
                d = math.hypot(dx, dy)
                if d <= near_pt:
                    pairs.append((d, i, j))
        pairs.sort()
        codes = [None] * len(boxes)
        used_box, used_code = set(), set()
        for _d, i, j in pairs:
            if i in used_box or j in used_code:
                continue
            codes[i] = cands[j][0]
            used_box.add(i)
            used_code.add(j)
        return boxes, codes, cands

    for key, kind in (("stair_boxes", "ST"), ("evtr_boxes", "EL")):
        b1, c1, cand1 = assign(f1, key, kind)
        b2, c2, cand2 = assign(f2, key, kind)

        # --- 2) 剔除伪楼梯（形状离谱且无编号）---
        dropped = {"1": [], "2": []}
        if kind == "ST":
            def keep(b, c):
                if c:
                    return True
                w, h = abs(b[2] - b[0]), abs(b[3] - b[1])
                lo, hi = min(w, h), max(w, h)
                return lo > 0 and hi / lo <= STAIR_MAX_ASPECT

            for tag, bs, cs in (("1", b1, c1), ("2", b2, c2)):
                kept = [(b, c) for b, c in zip(bs, cs) if keep(b, c)]
                dropped[tag] = [b for b, c in zip(bs, cs) if not keep(b, c)]
                bs[:], cs[:] = [b for b, _ in kept], [c for _, c in kept]

        # --- 3) 用另一层补齐漏检 ---
        pos1 = dict((t, p) for t, p in cand1)
        pos2 = dict((t, p) for t, p in cand2)
        added = {"1": [], "2": []}
        for tag, bs, cs, pos_self, pos_other, bs_other, cs_other in (
                ("1", b1, c1, pos1, pos2, b2, c2),
                ("2", b2, c2, pos2, pos1, b1, c1)):
            box_by_code_other = dict((c, b) for b, c in zip(bs_other, cs_other) if c)
            have = set(c for c in cs if c)
            for code in sorted(set(pos_self) - have):
                src = box_by_code_other.get(code)
                if src is None or code not in pos_other:
                    continue
                dx = pos_self[code][0] - pos_other[code][0]
                dy = pos_self[code][1] - pos_other[code][1]
                bs.append((src[0] + dx, src[1] + dy, src[2] + dx, src[3] + dy))
                cs.append(code)
                added[tag].append(code)

        # --- 排序（有编号者按编号升序，无编号者置后）并写回 ---
        for tag, data, bs, cs in (("1", f1, b1, c1), ("2", f2, b2, c2)):
            order = sorted(range(len(bs)), key=lambda i: (cs[i] is None, cs[i] or ""))
            data[key] = [bs[i] for i in order]
            data[key + "_codes"] = [cs[i] for i in order]
        report[kind] = {"added": added, "dropped":
                        dict((k, len(v)) for k, v in dropped.items())}

    return report


def build_geojson(f1, f2):
    facility_report = reconcile_facilities(f1, f2)
    for kind, info in facility_report.items():
        for fl in ("1", "2"):
            if info["added"][fl]:
                print(f"[F{fl}] {kind} 依图纸编号补齐: {info['added'][fl]}")
            if info["dropped"].get(fl):
                print(f"[F{fl}] {kind} 剔除无编号伪设施: {info['dropped'][fl]} 个")

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
                               "centroid": r["centroid_m"],
                               "public": r["roomType"] in ROOM_PUBLIC_TYPES,
                               "accessible": r["roomType"] not in NON_ACCESSIBLE_TYPES,
                               "hasIndependentEntrance": r["roomType"] in INDEPENDENT_ENTRANCE_TYPES,
                               "floor": int(floor_no)},
            })
            rooms_s.append({
                "id": r["id"], "type": r["roomType"], "label": r["label"],
                "centroid": r["centroid_m"], "geometryId": r["id"],
                "public": r["roomType"] in ROOM_PUBLIC_TYPES,
                "accessible": r["roomType"] not in NON_ACCESSIBLE_TYPES,
                "hasIndependentEntrance": r["roomType"] in INDEPENDENT_ENTRANCE_TYPES,
                "floor": int(floor_no),
            })
        doors = []
        for i, dr in enumerate(data["doors"]):
            cx, cy = pt2m(dr["center"])
            kind = dr["kind"]
            doors.append({
                "type": "Feature",
                "id": f"D{floor_no}-{i + 1:04d}",
                "geometry": {"type": "Point", "coordinates": [cx, cy]},
                "properties": {
                    "type": "door",
                    "doorType": kind,
                    "width_m": round(dr["width_pt"] * SCALE, 2),
                    "mergedCount": dr.get("merged", 1),
                    "rooms": dr["rooms"],
                    "sourceLayer": ("window" if kind == "swing"
                                    else "DOOR_FIRE" if kind == "fire"
                                    else "window+geometry"),
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
        stair_codes = data.get("stair_boxes_codes", [None] * len(data["stair_boxes"]))
        stairs = []
        for i, bxd in enumerate(data["stair_boxes"]):
            x0, y0, x1, y1 = bxd
            corners = [pt2m((x0, y0)), pt2m((x1, y0)), pt2m((x1, y1)), pt2m((x0, y1))]
            coords = [list(c) for c in corners] + [list(corners[0])]
            cen = pt2m(((x0 + x1) / 2, (y0 + y1) / 2))
            code = stair_codes[i] if i < len(stair_codes) else None
            stairs.append({
                "type": "Feature",
                "id": f"ST{floor_no}-{i + 1:02d}",
                "geometry": {"type": "Polygon", "coordinates": [coords]},
                "properties": {"type": "staircase",
                               "code": code,
                               "label": code or f"楼梯{floor_no}F-{i + 1}",
                               "centroid": list(cen)},
            })
        evtr_codes = data.get("evtr_boxes_codes", [None] * len(data["evtr_boxes"]))
        elevators = []
        for i, bxd in enumerate(data["evtr_boxes"]):
            x0, y0, x1, y1 = bxd
            corners = [pt2m((x0, y0)), pt2m((x1, y0)), pt2m((x1, y1)), pt2m((x0, y1))]
            coords = [list(c) for c in corners] + [list(corners[0])]
            cen = pt2m(((x0 + x1) / 2, (y0 + y1) / 2))
            code = evtr_codes[i] if i < len(evtr_codes) else None
            elevators.append({
                "type": "Feature",
                "id": f"EL{floor_no}-{i + 1:02d}",
                "geometry": {"type": "Polygon", "coordinates": [coords]},
                "properties": {"type": "elevator",
                               "code": code,
                               "label": code or f"电梯{floor_no}F-{i + 1}",
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

        # --- 拓扑层（指南 第五章）：节点三类（room/intersection/doorway/facility）+ 边
        doors_for_topo = []
        for dr in data["doors"]:
            cx, cy = pt2m(dr["center"])
            doors_for_topo.append({
                "center_m": [cx, cy],
                "kind": dr["kind"],
                "width_pt": dr["width_pt"],
                "rooms": dr["rooms"],
            })
        # 额外 facility_entrance 节点：未匹配到房间多边形但语义属于公共空间的标签
        # （无障碍出入口/门厅/人防主出入口/合班教室/图书资料室等），按坐标就近接入拓扑
        extra_nodes = []
        used_labels = {r["label"] for r in data["rooms"]}
        for label, pt_pt in data.get("labels_all_with_pt", []):
            if label in used_labels:
                continue
            if not any(k in label for k in ("门厅", "出入口", "合班", "图书",
                                             "传达", "前台", "活动", "心理",
                                             "辅导", "社团", "管理", "管控")):
                continue
            cx, cy = pt2m(pt_pt)
            extra_nodes.append({
                "label": label,
                "coordinates": [cx, cy],
                "facilityType": "accessible_entrance" if "无障碍" in label
                                else "entrance",
            })
        topo = build_floor_topology(floor_no, data["rooms"], doors_for_topo,
                                    stairs, elevators,
                                    extra_nodes=extra_nodes)
        nodes = topo["nodes"]
        edges = topo["edges"]

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
        """楼梯/电梯跨楼层边：**按图纸井道编号配对**（II-B2-01#ST 等）。

        编号是图纸的权威标识，同一井道各层同号，因此配对不再依赖中心距离
        （旧法 3.5m 阈值会漏配踏步线缺失/井道偏移较大的楼梯）。
        无编号的设施退化为几何配对（<3.5m），保证兼容性。
        """
        edges = []

        def center_m(bxd):
            x0, y0, x1, y1 = bxd
            return pt2m(((x0 + x1) / 2, (y0 + y1) / 2))

        for kind, key, prefix, blind_ok in (
                ("staircase", "stair_boxes", "CF-ST", False),
                ("elevator", "evtr_boxes", "CF-EL", True)):
            kind_short = "ST" if kind == "staircase" else "EL"
            codes1 = f1.get(key + "_codes", [None] * len(f1[key]))
            codes2 = f2.get(key + "_codes", [None] * len(f2[key]))
            idx2_by_code = dict((c, j) for j, c in enumerate(codes2) if c)
            n = 0
            for i, b1 in enumerate(f1[key]):
                code = codes1[i] if i < len(codes1) else None
                best = idx2_by_code.get(code) if code else None
                if best is None:                      # 无编号 → 退化为几何配对
                    c1 = center_m(b1)
                    best_d = 3.5
                    for j, b2 in enumerate(f2[key]):
                        if codes2[j] if j < len(codes2) else None:
                            continue                  # 已被编号占用，不参与几何配对
                        c2 = center_m(b2)
                        d = math.hypot(c1[0] - c2[0], c1[1] - c2[1])
                        if d < best_d:
                            best, best_d = j, d
                if best is None:
                    continue
                n += 1
                edges.append({
                    "id": f"{prefix}-{n:03d}",
                    "code": code,
                    "from": f"N1-{kind_short}{i + 1:03d}",
                    "to": f"N2-{kind_short}{best + 1:03d}",
                    "fromFloor": 1, "toFloor": 2, "type": kind,
                    "matchedBy": "code" if code else "geometry",
                    "distance": 4.2,
                    "estimatedTime": 60.0 if kind == "staircase" else 15.0,
                    "accessibilityLevel": (999 if kind == "staircase" else 0),
                    "riskLevel": (10 if kind == "staircase" else 1),
                    "walkable": True,
                    "wheelchairAccessible": blind_ok,
                    "blindAccessible": blind_ok,
                })
        return edges

    return {
        "venueId": "school-building-01",
        "venueName": "初中学部1#教学楼",
        "version": "9.0.0",
        "coordinateSystem": "local_meters",
        "scale": SCALE,
        "origin": {"x": ORIGIN_X, "y": ORIGIN_Y, "unit": "pt"},
        "generator": "src/parse_cad_pdf.py",
        "notes": "仅解析 PDF 默认开启图层；window 已剔除标识矢量笔画；"
                 "门洞分三类——摆弧门(swing, window 层)、防火门(fire, DOOR_FIRE)、"
                 "无摆弧开口(opening, 墙缝几何+window 矢量编号块确认)；"
                 "拓扑图按指南第五章规范构建（room/doorway/intersection/facility）。",
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
