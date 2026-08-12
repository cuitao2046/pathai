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
import heapq
from pathlib import Path

# 包内导入兼容：将项目根加入 sys.path，支持 `python src/parsing/parse_cad_pdf.py` 直接运行
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from shapely.geometry import Point, Polygon, box

# 全局常量唯一来源（比例/原点/步行速度等，见 docs/code-review-2026-08-12.md D1-D4）
from src.common.constants import SCALE, PT_PER_M

# 拓扑建模（指南 第五章）
# 注：跨层边不从此导入——本文件使用内嵌的 cross_floor_edges（图纸井道编号配对），
# topology.build_cross_floor_edges 为几何配对版，两者并存见 docs/code-review-2026-08-12.md A3。
from src.topology import obj_id, OBJ_TYPE

# B1：PDF 读取与图层/文本提取下沉到 src/parsing/pdf_layers.py
from src.parsing.pdf_layers import (extract_layer_items, extract_room_labels,
                                    extract_facility_codes,
                                    extract_dk_text_labels,
                                    get_default_on_layers, LAYER_ELEVATOR)
# B1：几何工具/聚类/栅格化下沉到 src/geometry/
from src.geometry.geo_utils import (angle_diff, bezier_mid, merge_collinear,
                                    point_to_seg_dist, pt2m, seg_angle, seg_len,
                                    seg_midpoint)
from src.geometry.clustering import cluster_items
from src.geometry.rasterize import rasterize_walls
# B1：语义分类（房间类型/楼梯电梯/合班教室）下沉到 src/semantics/
from src.semantics.room_types import classify_room_type
from src.semantics.stair_elevator import (
    detect_elevator_boxes, detect_elevator_doors, detect_stair_boxes)
from src.semantics.heban import inject_heban_classroom_rooms
# B1：GeoJSON 组装下沉到 src/io/geojson_writer.py（13 个函数一并迁移，
# 本文件仅保留 build_geojson 名字以便 tests 以 module.build_geojson 访问）。
from src.io.geojson_writer import (
    build_geojson, reset_manual_skeleton, set_use_skeleton)

# ---------------------------------------------------------------- 配置

# 路径自动适配：以本文件位置推导项目根目录，不依赖固定盘符/路径
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent   # .../src -> 项目根
RESULT_DIR = PROJECT_ROOT / "result"
PDF_F1 = str(PROJECT_ROOT / "A20-002-II-初中学部 1# 教学楼首层平面图-A0_BIAD-无签名.pdf")
PDF_F2 = str(PROJECT_ROOT / "A20-003-II-初中学部 1# 教学楼二层平面图-A0_BIAD-无签名.pdf")
OUT_GEOJSON = str(RESULT_DIR / "school_building_01_map_v9.geojson")
# 手绘骨架 JSON 由 src/import_manual_skeleton.py 导出，加载逻辑已随
# build_geojson 迁至 src/io/geojson_writer.py（set_use_skeleton/reset_manual_skeleton）。

# 比例尺/原点已统一到 src/common/constants.py（SCALE/ORIGIN_X/ORIGIN_Y），
# 校准依据见该模块注释：轴网 8400mm = 158.8pt，与窗编号 M2GW5924 互证。

# 需要解析的语义图层（仅当其在 PDF 中默认 ON 时才解析）
LAYER_WALL = "WALL"
LAYER_WINDOW = "window"
LAYER_DOOR_FIRE = "DOOR_FIRE"
LAYER_STAIR = "STAIR"
LAYER_COLUMNS = ("COLUMN", "柱子-刚结构")

# 参与房间多边形化的结构图层（默认开启时）。
# 该 CAD 导出的墙体分散在多个结构图层中，需取并集才能闭合房间轮廓；
# 纯标注图层（轴线/文字/标高等）不参与，避免切分房间。
LAYERS_STRUCT = ("WALL", "A-WALL-CONC", "A-WALL-FINI", "A-FLOR-STRS",
                 "STAIR", "A-FLOR-EVTR", "COLUMN", "柱子-刚结构", "填充线",
                 "A-METAL-S")
# A-METAL-S 加入 LAYERS_STRUCT 使家具薄墙参与 rastorization 做连通域切割
# （风井/水井 vs 男/女卫生间之间的薄墙在 A-METAL-S 中，不加则 gap 导致连成一片）。
# 厕位隔断等会被后续 build_rooms 的 absorb/merge 逻辑处理为可填充单元。
# 家具级图层（金属构件）：既含真实墙体段（卫生间隔墙等，缺了
# 会导致房间不闭合），又含厕位隔断/洗手台等会把房间内部切碎的构件线。
# 处理：以细线(1px)单独栅格化参与封闭；凡围出 <ABSORB_CELL_M2 微单元的
# 家具线在微单元邻域内擦除（打通厕位与走道），真实隔墙两侧都是大房间、
# 邻域无微单元，不受影响。
LAYERS_FURNITURE = ()  # DISABLED: A-METAL-S 已迁至 LAYERS_STRUCT，不再单独作为家具层。
# 注意：空配置使下方 furn_segs 收集循环恒为空、整条 furn_segs 参数链成为死代码
# （审查 A5）。若后续需要单独处理家具层，请补回真实图层名并同步验证链上各分支。
# 强制剔除的图层：整层元素不参与任何解析（不计入墙体封闭、门/窗/房间识别）。
# A-TECH-SANT（卫生/给排水器具等）已设为默认关闭，且 PyMuPDF 的
# page.get_drawings() 不感知图层可见性、会照常返回其全部矢量元素，
# 故在此显式剔除，确保即使该图层在 PDF 中标记为开启也被忽略。
LAYERS_IGNORE = ("A-TECH-SANT",)
LAYERS_ANNO_EXCLUDE = ("AXIS", "A-ANNO-150-TXT", "A-ANNO-LEVL", "A-ANNO-TTLB",
                       "A-ANNO-SYMB")

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

# --- 楼梯/电梯井编号（图纸权威标识，如 II-B2-01#ST / II-02#EL）---
# 编号文本正则已随提取函数迁至 src/parsing/pdf_layers.py（B1）；
# 编号到设施 bbox 的匹配容差 FACILITY_CODE_NEAR_M 已随 reconcile_facilities
# 迁至 src/io/geojson_writer.py。
STAIR_ROOM_DEDUP_M = 2.0     # 注入楼梯间 room 时与已有 staircase 中心距 < 此值则跳过

# 非封闭空间的室外/开敞标签：不参与房间探测（避免抢占附近区域）
# 教学楼中"无障碍出入口/门厅无障碍出入口/人防主出入口"等均为公共空间节点，
# 应被识别为房间（或作为 topology facility_entrance 节点），故不列入黑名单。
LABEL_SKIP_RE = re.compile(
    r"(非机动车车库入口|非机动车车库出入口|消防车道|车道|雨棚|屋面|上空|庭园|台阶|坡道|散水|屋顶平台|泄爆井|不上人屋面)")

# 开放空间类型 → 独立编号前缀（区别于封闭房间的 RM- 系列）；
# 走廊/门厅/大厅/活动/中庭与房间/管井/电梯/楼梯间等封闭空间是不同类型，
# 不能合并编号，故在生成 room id 时分流到各自 OBJ_TYPE 键。
_OPEN_ID_KEY = {
    "corridor": "corridor", "lobby": "lobby",
    "activity": "activity", "atrium": "atrium",
}

# 功能房间统一类型（需求⑳+1）：办公室/实验室/教室等封闭功能房间一律 type="room"，
# 原用途（classroom/office/lab...）落到 roomSubType 子类别；走廊/门厅/楼梯/卫生间/
# 电梯厅/管井/出入口等公共/设施型保持独立 type 不变。
FUNCTIONAL_ROOM_TYPES = {
    "room", "classroom", "lab", "office", "meeting", "storage",
    "equipment", "library", "medical", "counseling", "activity",
    "reception",
}

# 公共空间/无障碍可达性/独立出入口等类型集合（ROOM_PUBLIC_TYPES、
# NON_ACCESSIBLE_TYPES、INDEPENDENT_ENTRANCE_TYPES）已随 build_geojson
# 迁至 src/io/geojson_writer.py（原 ACCESSIBLE_TYPES 为死代码，迁移时删除）。


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

# 门属性常量（DOOR_SURVEY_FIELDS/DOOR_SUBTYPE/DOOR_CIRCULATION_TYPES，
# 指南 §3.2/§6.2）已随 build_geojson 迁至 src/io/geojson_writer.py。

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


# ---- 墙体厚度与材质（指南 §3.1「CAD 中墙体是双线表示」/ §3.3 thickness+material）
# estimate_wall_thickness / wall_material_from_thickness 及配套阈值
# （WALL_THICKNESS_*、WALL_PAIR_*）已随 build_geojson 迁至 src/io/geojson_writer.py。


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
    """DISABLED: 未在任何调用链中使用（死代码），保留仅供 git 历史参考。
    # 用户约定「门不合并」：每扇门独立成 TD，见 docs/设计决策记录.md ADR-门不合并。
    # 原实现：仅合并**同类型**重合的门（swing↔swing、fire↔fire、opening↔opening）。

    不同类型（如 swing 与 opening 标在相邻不同洞口）不合并——用户明确：
    门洞和普通门不做去重合并，只针对同类型门去重。

    同一类型内：两门中心极近(<13pt)即视为同一洞口，仅保留一扇。
    """
    MERGE_PT = 13.0

    def link(d1, d2):
        return math.hypot(d1["center"][0] - d2["center"][0],
                          d1["center"][1] - d2["center"][1]) < MERGE_PT

    by_kind = collections.defaultdict(list)
    for d in doors:
        by_kind[d.get("kind", "unknown")].append(d)

    out = []
    for _kind, items in by_kind.items():
        groups = cluster_items(items, link)
        for g in groups:
            out.append(g[0])  # 同类型任意取一扇即可
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


def _has_d_shape(col, is_glyph_vert):
    """列内是否含 D 字形：有长竖茎 + 至少 1 个非茎笔画（弧线）。"""
    verts = [f for f in col if is_glyph_vert(f) and f["L"] >= 4.0]
    if not verts:
        return False
    stem = max(verts, key=lambda f: f["L"])
    # 弧线可能位于茎任一侧（旋转无关），只要存在非茎笔画即可
    others = [f for f in col if f is not stem]
    return len(others) >= 1


def _has_k_shape(col, is_glyph_vert):
    """列内是否含 K 字形：有长竖茎 + 同时存在上/下对向斜线。"""
    verts = [f for f in col if is_glyph_vert(f) and f["L"] >= 4.0]
    if not verts:
        return False
    has_opp, _, _ = _has_opp_diagonals_in(col, min_len=0.5)
    return has_opp


def is_dk_block(strokes, bbox):
    """严格 DK 识别：D 在前、K 在后、二者相邻。

    之前用「左 40% 有竖笔 + 中段有对向斜线」的笼统区域判定，
    会把 M1524 / C01224 等非 DK 块误判。现改为按字符列验证。

    关键改进：**支持所有 4 个旋转方向**——
      - 0° / 90° 旋转：列序 = D-K（col0=D, col1=K）
      - 180° / 270° 旋转：列序 = K-D（col0=K, col1=D）
      故只验证相邻两列中存在 D-形 + K-形配对，不限定列序。

    规则：
      - 块内所有笔画沿书写轴向聚成字符列（水平→x，竖排→y）；
      - 至少 2 列；
      - col0 与 col1 一为 D-形（有茎+弧）、一为 K-形（有茎+对向斜）；
      - 二者相邻（不允许中间隔其它字符列）—— 自然成立因为仅取 cols[0:2]。
    """
    x0, y0, x1, y1 = bbox
    w = x1 - x0
    if w < 8.0:
        return False, "block_too_narrow"
    if len(strokes) < 6:
        return False, "too_few_strokes"

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

    # 判断是否竖排（贴侧墙 90°/270° 旋转）
    vert_text = abs((glyph_ang % 180) - 90) > 40

    cols = _cluster_along_axis(all_feats, axis="y" if vert_text else "x", gap=4.0)
    if len(cols) < 2:
        return False, "too_few_cols"

    # 验证 cols[0] + cols[1] 形成 DK 配对（顺序不限，覆盖 180°/270° 反转情况）
    col0_d = _has_d_shape(cols[0], is_glyph_vert)
    col0_k = _has_k_shape(cols[0], is_glyph_vert)
    col1_d = _has_d_shape(cols[1], is_glyph_vert)
    col1_k = _has_k_shape(cols[1], is_glyph_vert)

    if col0_d and col1_k:
        return True, "DK_DK"
    if col0_k and col1_d:
        return True, "DK_KD"
    return False, f"no_dk_pair(d0={col0_d},k0={col0_k},d1={col1_d},k1={col1_k})"


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
    from scipy.ndimage import distance_transform_edt

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

    # --- 开放空间(走廊/门厅/大厅/活动/中庭)扫描预排除 -------------------
    # 「先忽略走道/过道」：在房间分水岭生长之前，先把开放空间组件固定为其
    # 各自组件 cid，并从房间生长种子中移除，使其不参与房间像素竞争。这样
    # 走道/过道等巨大开放面积绝对无法抢占相邻封闭房间像素，保证走廊/过道
    # 的空间识别不会影响房间等封闭空间的识别。
    #   仅固定「纯开放组件」（组件内不含任何封闭空间标签）；开放+封闭共域的
    #   共享组件仍交给下方几何分割处理（保持既有分治行为，避免误切房间）。
    _OPEN_RT = {"corridor", "lobby", "activity", "atrium"}
    _open_cids = set()
    _enclosed_cids = set()
    for _entry in (label_points or []):
        if len(_entry) == 3:
            _t, (_lx, _ly), _sz = _entry
        else:
            _t, (_lx, _ly) = _entry
        if LABEL_SKIP_RE.search(_t):
            continue
        _px, _py = to_px((_lx, _ly))
        if 0 <= _px < W and 0 <= _py < H:
            _c = int(cc[_py, _px])
            if _c == 0 or _c in border_ids:
                continue
            if classify_room_type(_t) in _OPEN_RT:
                _open_cids.add(_c)
            else:
                _enclosed_cids.add(_c)
    _pure_open = [c for c in _open_cids if c not in _enclosed_cids]
    if _pure_open:
        _po = np.isin(cc, np.array(sorted(_pure_open), dtype=np.int64))
        owner[_po] = cc[_po].astype(np.float32)   # 固定为组件 cid（已占领）
        region_ids = region_ids[~np.isin(
            region_ids, np.array(sorted(_pure_open), dtype=np.int64))]
        region_mask = np.isin(cc, region_ids)
        owner[region_mask] = cc[region_mask].astype(np.float32)

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
        for entry in label_points:
            # 兼容 (text,(cx,cy)) 与 (text,(cx,cy),size) 两种形态
            if len(entry) == 3:
                text, (lx, ly), size = entry
            else:
                text, (lx, ly) = entry
                size = 0.0
            if LABEL_SKIP_RE.search(text):
                continue
            probes.append((text, to_px((lx, ly)), size))

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

    # 同一原始连通域出现多个不同中文语义标签时，不再让第一个标签占据整个 cid。
    # 以标签为等权 marker，在原始自由空间内做墙距加权的多源测地分割：
    #   - 只能在当前 cc 内传播，不穿越墙体；
    #   - 代价在狭窄处升高，分界倾向落在井道/卫生间之间的窄颈；
    #   - marker 不按 roomType 或 cid 设置优先级。
    labels_by_cid = collections.defaultdict(list)
    _open_label_dropped = []
    for text, (px, py), size in probes:
        cid = comp_at(px, py)
        # --- 开放空间标签「不得跨墙认领封闭房间」-------------------------
        # 本图纸的走道/过道/门厅等公共交通空间并未被墙完全封闭（经建筑
        # 出入口与室外连通），其自由空间连通域会并入室外大组件(border)。
        # 此时 comp_at 的邻域众数兜底是**欧氏方框**投票，会直接跨过墙体，
        # 把走道标签投到墙另一侧相邻封闭房间的组件上；该房间于是被当作
        # 「走道 + 房间」多标签共域触发几何分割，被切掉一角
        # ——渲染图中「美术工作室」右下角被走道占据即由此产生。
        # 规则：开放空间标签只认自己所在的那个封闭连通域；只要它落在
        # 室外/未封闭组件，或兜底投票把它挪到了别的组件，一律丢弃该标签，
        # 绝不让走道/过道侵蚀房间等封闭空间。
        if classify_room_type(text) in _OPEN_RT:
            c0 = int(cc[py, px]) if (0 <= px < W and 0 <= py < H) else 0
            if c0 == 0 or c0 in border_ids or c0 != cid:
                _open_label_dropped.append(text)
                continue
        if cid == 0 or cid in border_ids:
            continue
        labels_by_cid[cid].append((text, px, py, size))
    if _open_label_dropped:
        _cnt = collections.Counter(_open_label_dropped)
        print("    开放空间标签落在未封闭公共域(不参与房间识别): "
              + ", ".join(f"{k}x{v}" for k, v in _cnt.most_common()))

    matched = []
    dup_labels = []
    next_split_id = int(n)
    for cid, labels in labels_by_cid.items():
        # 同名标签属于同一语义 marker；不同语义才触发几何分割。
        distinct = []
        for item in labels:
            if item[0] not in {x[0] for x in distinct}:
                distinct.append(item)
            else:
                dup_labels.append(item[0])
        if len(distinct) <= 1:
            matched.append((distinct[0][0], cid))
            continue

        # 多标签共域分割策略：
        #  1) 竖井+卫生间混合 → 按几何分割（既有规则）；
        #  2) 开放空间(走廊/门厅/大厅/活动/中庭)与封闭空间(房间/管井/电梯/楼梯间等)
        #     共域 → 必须分割，二者是不同类型、不能合并处理；
        #  3) 其余同类型多标签 → 不切碎，取所在封闭空间内字号最大的中文标签
        #     作为该空间语义层（ANNO-TEXT 层最大字体规则）。
        distinct_types = {classify_room_type(x[0]) for x in distinct}
        OPEN_TYPES = {"corridor", "lobby", "activity", "atrium"}
        ENCLOSED_TYPES = {"room", "classroom", "lab", "office", "meeting",
                          "equipment", "storage", "library", "medical",
                          "counseling", "reception", "infrastructure", "toilet",
                          "elevator_hall", "staircase"}
        is_shaft_toilet_mix = ("infrastructure" in distinct_types and
                               "toilet" in distinct_types)
        has_open = bool(distinct_types & OPEN_TYPES)
        has_enc = bool(distinct_types & ENCLOSED_TYPES)
        needs_split = is_shaft_toilet_mix or (has_open and has_enc)
        if not needs_split:
            # 同一封闭空间内多标签：字号最大的中文标签为该空间语义层（用户规则）。
            # 但「最大字号」必须唯一才具备裁决力：若多个标签并列最大字号，
            # 它们是同级空间名（如「水井」与「饮水」同为 9.5pt），
            # 字号无法定语义层 —— 退回几何分割让它们各自成空间。
            _mx = max(x[3] for x in distinct)
            _top = [x for x in distinct if x[3] >= _mx - 0.05]
            if len(_top) == 1:
                winner = _top[0]
                matched.append((winner[0], cid))
                dup_labels.extend(x[0] for x in distinct if x[0] != winner[0])
                continue
            # 并列最大字号 -> 仅在这些同级空间名之间分割；
            # 更小字号的标签视为其所在空间的附注，不单独成空间。
            dup_labels.extend(x[0] for x in distinct if x not in _top)
            distinct = _top

        component = (cc == cid)
        ys0, xs0 = np.where(component)
        if len(xs0) == 0:
            continue
        clearance = cv2.distanceTransform(component.astype(np.uint8), cv2.DIST_L2, 5)
        # 每个标签吸附到本连通域内最近的自由像素，避免从墙/文字墨线穿越。
        seeds = []
        used_seed = set()
        for text, px, py, size in distinct:
            d2 = (xs0 - px) ** 2 + (ys0 - py) ** 2
            order = np.argsort(d2)
            seed = None
            for oi in order:
                cand = (int(xs0[oi]), int(ys0[oi]))
                if cand not in used_seed:
                    seed = cand
                    used_seed.add(cand)
                    break
            if seed is not None:
                seeds.append((text, seed))
        if len(seeds) <= 1:
            matched.append((distinct[0][0], cid))
            continue

        # 多源 Dijkstra：墙距越小，穿越代价越高；同代价不以 marker/cid 决胜。
        inf = np.float32(1e30)
        cost = np.full((H, W), inf, np.float32)
        owner_local = np.full((H, W), -1, np.int32)
        heap = []
        for mi, (_text, (sx, sy)) in enumerate(seeds):
            cost[sy, sx] = 0.0
            owner_local[sy, sx] = mi
            heapq.heappush(heap, (0.0, mi, sx, sy))
        while heap:
            cur, mi, x, y = heapq.heappop(heap)
            if cur > float(cost[y, x]) + 1e-5 or owner_local[y, x] != mi:
                continue
            for nx, ny in ((x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1)):
                if nx < 0 or nx >= W or ny < 0 or ny >= H or not component[ny, nx]:
                    continue
                step = 1.0 + 4.0 / (float(clearance[ny, nx]) + 1.0)
                nc = cur + step
                if nc + 1e-5 < float(cost[ny, nx]):
                    cost[ny, nx] = nc
                    owner_local[ny, nx] = mi
                    heapq.heappush(heap, (nc, mi, nx, ny))
        for mi, (text, _seed) in enumerate(seeds):
            new_id = next_split_id
            next_split_id += 1
            part = component & (owner_local == mi)
            owner[part] = np.float32(new_id)
            matched.append((text, new_id))
        print(f"    [分割] 多标签连通域 cid={cid}: "
              f"{[x[0] for x in distinct]} -> {len(seeds)} 个子空间")

    seen = {cid for _, cid in matched}

    # --- 标签播种：标签落在被隔断切碎的小单元里（整个空间无 >=2m2 区域），
    #     直接认领标签处的小单元为房间种子（标签本身即权威），
    #     后续守卫式泛洪会把同属该空间的其它单元并入
    seed_min_px = 0.3 / m2_per_px
    seed_max_px = MERGE_REGION_M2 / m2_per_px
    for text, (px, py), size in probes:
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
        # 风井/水井通常小于普通房间 3m²；二次分割后允许最小 0.2m²，
        # 否则正确切出的红框小井道会再次被全局房间面积阈值丢弃。
        min_px_for_label = (0.2 / m2_per_px
                            if classify_room_type(text) == "infrastructure"
                            else area_min_px)
        if not (min_px_for_label <= area <= area_max_px):
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

    # ---- 真实公共交通空间（走道/门厅）提取 ------------------------------
    # 走道/过道在本图纸中并未被墙完全封闭：它经建筑出入口与室外连通，自由空间
    # 连通域会整片并入「室外」大组件，因此无法像房间那样按连通域直接识别
    # （此前正是靠标签邻域投票跨墙抓一块房间充数，才出现走道侵占房间的现象）。
    # 这里改为按几何反推真实走道：
    #   1) 把墙体膨胀 R(≈1.5m)，封住建筑出入口 -> 求出真正的室外域；
    #   2) 将室外域反向膨胀 R 还原 -> 得到「建筑内部自由空间」；
    #   3) 内部自由空间中不属于任何已识别房间的部分 = 真实公共交通空间。
    n_circ = 0
    try:
        R = max(4, int(round(1.5 / (SCALE / Z))))       # 1.5m -> px
        dist_free = cv2.distanceTransform(free, cv2.DIST_L2, 5)
        free_d = (dist_free > R).astype(np.uint8)       # 墙膨胀 R 后的自由空间
        _nd, ccd = cv2.connectedComponents(free_d, connectivity=4)
        out_ids = set(np.unique(np.concatenate([
            ccd[0, :], ccd[-1, :], ccd[:, 0], ccd[:, -1]])))
        out_ids.discard(0)
        outdoor_d = np.isin(ccd, np.array(sorted(out_ids), dtype=np.int32))
        # 反向膨胀 R 还原室外域（出入口已被封住，不会漏进建筑内部）
        dt_out = cv2.distanceTransform(
            (~outdoor_d).astype(np.uint8) * 255, cv2.DIST_L2, 5)
        outdoor = dt_out <= R
        circ = ((free > 0) & (~outdoor) & (owner <= 0)).astype(np.uint8)
        # 去掉房间内未归属微单元造成的碎点，并抹平锯齿
        circ = cv2.morphologyEx(
            circ, cv2.MORPH_OPEN,
            cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (9, 9)))
        # 交通域整片相连（主走道串联各分区），直接成面会得到一个覆盖整层的
        # 巨型多边形。改为用图纸上的开放空间标注(走道/门厅/出入口/庭园上空)
        # 作为种子做最近邻切分——走廊狭长，欧氏 Voronoi 已足够贴近测地切分，
        # 于是每段走道各自成空间，质心也落在真实走道上。
        seeds = [(t, px, py, sz) for t, (px, py), sz in probes
                 if classify_room_type(t) in _OPEN_RT
                 and 0 <= px < W and 0 <= py < H]
        # 用最近邻(Voronoi)把交通域切成「每段走道各自成面」。
        # 实现：以种子点为源做多源距离变换，求每个像素的最近种子下标。
        seg_key = np.zeros((H, W), np.int32)        # 0 = 无种子 -> 默认"走道"
        seed_name = {0: "走道"}
        if seeds:
            sy = np.array([int(py) for _t, _px, py, _s in seeds], dtype=np.int64)
            sx = np.array([int(px) for _t, px, _py, _s in seeds], dtype=np.int64)
            inv = np.ones((H, W), dtype=bool)        # 非种子=前景，种子=原点
            inv[sy, sx] = False
            _dist, (iy, ix) = distance_transform_edt(
                inv, return_distances=True, return_indices=True)
            seed_lut = np.full((H, W), -1, np.int32)
            seed_lut[sy, sx] = np.arange(len(seeds), dtype=np.int32)
            nearest = seed_lut[iy, ix]               # 每像素最近种子下标 0..n-1
            nearest = np.where(nearest < 0, 0, nearest)
            seg_key = (nearest + 1).astype(np.int32)  # 1..n，与 0(默认)区分
            seed_name = {i + 1: t for i, (t, _px, _py, _s) in enumerate(seeds)}
            seed_name[0] = "走道"
        nc, ccc, stc, _cc2 = cv2.connectedComponentsWithStats(
            (circ > 0).astype(np.uint8), connectivity=4)
        # 同一连通块内再按种子归属切开
        combo = ccc.astype(np.int64) * (int(seg_key.max()) + 1) + seg_key
        combo[circ == 0] = -1
        CIRC_MIN_M2 = 4.0
        for key in np.unique(combo):
            if key < 0:
                continue
            mask_c = (combo == key).astype(np.uint8) * 255
            if int(np.count_nonzero(mask_c)) * m2_per_px < CIRC_MIN_M2:
                continue
            name = seed_name.get(int(key % (int(seg_key.max()) + 1)), "走道")
            ys_i, xs_i = np.where(mask_c)
            mg = 20
            x0c, x1c = max(0, xs_i.min() - mg), min(W, xs_i.max() + mg)
            y0c, y1c = max(0, ys_i.min() - mg), min(H, ys_i.max() + mg)
            cnts, _h = cv2.findContours(mask_c[y0c:y1c, x0c:x1c],
                                        cv2.RETR_EXTERNAL,
                                        cv2.CHAIN_APPROX_SIMPLE)
            if not cnts:
                continue
            approx = cv2.approxPolyDP(max(cnts, key=cv2.contourArea),
                                      Z * 1.5, True)
            pts = [((float(p[0][0] + x0c) / Z + minx),
                    (float(p[0][1] + y0c) / Z + miny)) for p in approx]
            if len(pts) < 3:
                continue
            poly = Polygon(pts)
            if not poly.is_valid:
                poly = poly.buffer(0)
            if poly.is_empty:
                continue
            # 交通域写入 owner，使门洞两侧投票能把门归属到走道
            new_cid = next_split_id
            next_split_id += 1
            owner[combo == key] = np.float32(new_cid)
            rooms.append((name, poly))
            room_cids.append(new_cid)
            n_circ += 1
        print(f"    公共交通空间(走道/门厅)识别: {n_circ} 处, "
              f"总面积 {float(np.count_nonzero(circ)) * m2_per_px:.0f}m2")
    except Exception as e:                                   # pragma: no cover
        print(f"    [WARN] 公共交通空间提取失败: {e}")

    # owner 归属图随房间一并返回，供门洞两侧投票使用
    return {"polys": rooms, "owner": owner, "minx": minx, "miny": miny,
            "Z": Z, "cids": room_cids}


def _tf_coord(nodes, nid):
    for n in nodes:
        if n["id"] == nid:
            return n["coordinates"]
    return [0.0, 0.0]


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
    # labels 现在返回 (names, codes)，拆开供 build_rooms 用
    room_names, room_codes = labels
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
    # 审查 A5：LAYERS_FURNITURE 为空配置，本循环恒不执行，furn_segs 恒空（死代码链）。
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
        m = bezier_mid(bz)
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

    def ordinary_door_arc(bz, tol_anchor=14.0):
        """普通门（window 层摆弧）识别：铰链(弧圆心)或端点贴墙即锚定。
        放宽容差以容忍绘制偏移/家具隔墙，捕获此前被 near_wall(tol=6) 漏掉的普通门；
        完全脱离墙体的悬空残段仍被排除。摆弧本就位于门口（WALL 层该处无连续墙体），
        故"对应位置无墙"由摆弧的门口语义保证，不再额外做墙缝匹配。"""
        p1, p4 = bz[0], bz[3]
        xs = [p[0] for p in bz]
        ys = [p[1] for p in bz]
        x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
        m = bezier_mid(bz)
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

    # --- 门不做合并（用户明确约定）：同一物理开口只保留检测出的门，禁止去重合并。
    #     原 dedupe_doorways 会把同类型中心距<13pt 的门合并为一扇，
    #     导致拓扑 TD rooms 归属混叠（如 F2-TD-0010 出现双归属）——已禁用。
    before = len(doors)
    doors = list(doors)
    print(f"[F{floor_no}] 门（不做合并）: {before} -> {len(doors)}")

    # --- 提前计算楼梯间 bbox（要在 build_rooms 之前得到位置，便于稍后作为 staircase room 加入
    #     rooms 列表，让门归属能找到楼梯间）。统一 detect_stair_boxes：
    #     STAIR + A-FLOR-STRS 双层合并聚类 + 面积 3-80m² + 长宽比 ≤3 过滤。
    #     F2 STAIR 仅 2 个 quad，A-FLOR-STRS 含完整踏步/护栏线。
    _stair_boxes = detect_stair_boxes(items)
    print(f"[F{floor_no}] 统一楼梯 bbox: {len(_stair_boxes)} 个")

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
        all_segs, closures, furn_segs=furn_segs, label_points=room_names,
        dump_path=str(RESULT_DIR / f"_debug_wallmask_f{floor_no}.png"))
    labeled_polys = room_res["polys"]
    print(f"[F{floor_no}] 房间多边形(标签探测): {len(labeled_polys)}")

    rooms = []
    room_seq = 0
    used_labels = set()
    label_by_text = {}
    for li, entry in enumerate(room_names):
        text = entry[0]
        label_by_text.setdefault(text, li)
    for idx, (label, poly) in enumerate(labeled_polys):
        if label is None:
            continue
        if label in label_by_text:
            used_labels.add(label_by_text[label])
        room_seq += 1
        _rt = classify_room_type(label)
        # 开放空间(走廊/门厅/大厅/活动/中庭)与封闭房间区分编号前缀，
        # 二者是不同类型空间，ID 不应混为 RM- 系列
        _type_key = _OPEN_ID_KEY.get(_rt, "room")
        centroid_m = pt2m((poly.centroid.x, poly.centroid.y))
        coords_m = [list(pt2m((x, y))) for x, y in poly.exterior.coords]
        rooms.append({
            "id": obj_id(f"F{floor_no}", OBJ_TYPE[_type_key], room_seq),
            "label": label,
            "roomType": _rt,
            "code": "",
            "polygon_pt": poly,
            "coords_m": coords_m,
            "centroid_m": list(centroid_m),
        })

    # 英文编号仅附着到已有中文语义房间，不参与重分类；全局一对一分配，
    # 优先 code 点落在多边形内部，其次才允许距边界 <1m。
    # II-WR-* 是卫生间编号，只能附着到 toilet，禁止误挂到相邻 infrastructure（风井/管道井）。
    code_candidates = []
    for ci, (code, (cx, cy)) in enumerate(room_codes):
        p = Point(cx, cy)
        for ri, room in enumerate(rooms):
            if code.startswith("II-WR-") and room["roomType"] != "toilet":
                continue
            poly = room["polygon_pt"]
            inside = poly.covers(p)
            d = 0.0 if inside else poly.exterior.distance(p)
            if inside or d < 1.0 * PT_PER_M:
                code_candidates.append((0 if inside else 1, d, ci, ri))
    used_codes, coded_rooms = set(), set()
    for _outside, _dist, ci, ri in sorted(code_candidates):
        if ci in used_codes or ri in coded_rooms:
            continue
        rooms[ri]["code"] = room_codes[ci][0]
        used_codes.add(ci)
        coded_rooms.add(ri)
    print(f"[F{floor_no}] 语义房间(带标签): {len(rooms)}")

    # --- 追加楼梯间 room（build_rooms 栅格化通常漏掉开放式楼梯井）---
    # STAIRCASE 类型让门归属能找到这些房间，使 II-Bx-yz#ST 类 DK 门洞被认领。
    # 去重：若已有 staircase 房间中心距 bbox 中心 < STAIR_ROOM_DEDUP_M，则只更新 code，
    # 不重复注入（避免 build_rooms 已识别出楼梯间时出现重复房间）。
    dedup_pt = STAIR_ROOM_DEDUP_M / SCALE
    injected = 0
    for sb_idx, (x0, y0, x1, y1) in enumerate(_stair_boxes):
        cx_b, cy_b = (x0 + x1) / 2.0, (y0 + y1) / 2.0
        dup = False
        for r in rooms:
            if r["roomType"] != "staircase":
                continue
            if math.hypot(r["polygon_pt"].centroid.x - cx_b,
                          r["polygon_pt"].centroid.y - cy_b) < dedup_pt:
                dup = True
                # 若该已有楼梯间 room 无编号而此 bbox 有编号 → 补 code
                if not r["code"]:
                    best_code, best_d = None, 1.0 * PT_PER_M
                    for code, (cx, cy) in room_codes:
                        p = Point(cx, cy)
                        d = math.hypot(cx - cx_b, cy - cy_b)
                        if d < best_d:
                            best_d, best_code = d, code
                    if best_code and best_d < 1.0 * PT_PER_M:
                        r["code"] = best_code
                break
        if dup:
            continue
        poly = box(x0, y0, x1, y1)
        # 匹配 A-ANNO-150-TXT 编号（代码点在多边形内或距边界 < 1m）
        best_code, best_d = None, 1.0 * PT_PER_M
        for code, (cx, cy) in room_codes:
            p = Point(cx, cy)
            d = poly.exterior.distance(p)
            if d < best_d:
                best_d, best_code = d, code
        rooms.append({
            "id": obj_id(f"F{floor_no}", OBJ_TYPE["room"], room_seq + sb_idx + 1),
            "label": f"楼梯间{injected + 1}",
            "roomType": "staircase",
            "code": best_code or "",
            "polygon_pt": poly,
            "coords_m": [list(pt2m((x, y))) for x, y in poly.exterior.coords],
            "centroid_m": list(pt2m((poly.centroid.x, poly.centroid.y))),
        })
        injected += 1
    print(f"[F{floor_no}] 含楼梯间后房间数: {len(rooms)} (新注入 {injected})")

    # --- 合班教室类型纠正：仅改自身 label 含「合班」的房间，不动其它空间 ---
    _OPEN_FORCE = {"corridor", "lobby", "activity", "atrium",
                   "elevator_lobby", "stair_lobby"}
    n_heban = 0
    for r in rooms:
        lab = r.get("label") or ""
        if "合班" not in lab:
            continue
        if r.get("roomType") in _OPEN_FORCE or r.get("roomType") != "classroom":
            r["roomType"] = "classroom"
            n_heban += 1
        # 只清合班自己的 walkable，避免它进入公共骨架；不改其它房间
        if r.get("roomType") == "classroom":
            r.pop("walkable_poly_pt", None)
    if n_heban:
        print(f"[F{floor_no}] 合班教室类型纠正(仅自身): {n_heban} 处 → classroom")

    # --- 门洞归属 pass 0：普通门（swing/fire）铰链端贴墙房间归属（用户规则）。
    #     门铰链固定在墙上，铰链端所在/贴附的房间即门所服务房间（如 F2-D-0010
    #     铰链贴化学教室墙 0.1m，旧 arc_mid 逻辑误把门归到走道）。优先归属
    #     功能房间（room 类），无 room 类才考虑其他空间（走廊等）；opening 门洞
    #     无铰链语义，仍用弧中点判定。
    _HINGE_WALL_TOL = 12.0  # pt ≈ 0.63m，与 pipeline 贴墙补全 _WALL_TOL(0.6m) 同量级
    for dr in doors:
        dr["rooms"] = []
        kind = dr.get("kind")
        axis = dr.get("axis")
        if kind in ("swing", "fire") and axis:
            hp = Point(axis[0])  # 铰链端（门轴，落在墙线上）
            near = []
            for r in rooms:
                poly = r["polygon_pt"]
                if poly.contains(hp):
                    near.append((0.0, r))
                else:
                    d = poly.exterior.distance(hp)
                    if d < _HINGE_WALL_TOL:
                        near.append((d, r))
            if near:
                # 优先功能房间（room 类），同类取最近；无 room 类才取其他（走廊等）
                func = [x for x in near
                        if x[1]["roomType"] in FUNCTIONAL_ROOM_TYPES]
                pool = func or near
                pool.sort(key=lambda x: x[0])
                dr["rooms"].append(pool[0][1]["id"])
                continue
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
    # ⚠️ 跳过开放空间（走廊/门厅/活动/中庭/前室）——其连通由 TI 承担，
    #    不应参与门归属（否则会把封闭房间的门抢走，见 F2-D-0024 归属被
    #    CR-0033 走道偷走的历史 bug）。
    _NO_DOOR_SKIP = ("staircase", "elevator_hall", "infrastructure",
                     "atrium") + tuple(_OPEN_ID_KEY)
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
            if r["roomType"] in _NO_DOOR_SKIP:
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
    # ⚠️ 跳过开放空间（走廊/门厅/活动/中庭/前室）——开放空间连通由 TI 承担，
    #    不需要门归属；否则走廊会把封闭房间的门偷走（如 F2-D-0024 的
    #    RM-0015 被 CR-0033 走道偷走 → 门归属 CR 空洞）。
    for r in rooms:
        if r["roomType"] in _NO_DOOR_SKIP:
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
    # 统一楼梯 bbox（与早期注入/最终 geometry 共用 detect_stair_boxes）
    stair_boxes = detect_stair_boxes(items)
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

    # --- 需求⑳：清理门归属中的无效房间 ID ---
    # 部分门在归属 pass 中引用到后续被过滤/合并掉的房间（如 F2-CR 编号空洞：
    # CR-0033/34/42/45 在语义房间中不存在），导致门归属悬空。统一剔除
    # 不存在的房间 ID，保证门归属一律为合法元素 ID。
    _valid_room_ids = {r["id"] for r in rooms}
    _cleaned = 0
    for dr in doors:
        rms = dr.get("rooms") or []
        kept = [rid for rid in rms if rid in _valid_room_ids]
        if len(kept) != len(rms):
            dr["rooms"] = kept
            _cleaned += 1
    if _cleaned:
        print(f"[F{floor_no}] 清理门归属无效房间 ID: {_cleaned} 扇")

    # --- 服务核心内部门：仅过滤普通门/防火门(swing/fire)，门洞(opening)豁免 ---
    # 规则（用户 2026-08-05 简化）：封闭空间内部的门洞要保留（DK → 开门是
    # 单一判据）。仅当门是真正可推拉/开合的摆弧门(swing/fire)且两侧皆属服务
    # 核心时删除，避免男↔女卫生间内部对男卫生间密闭；门洞两侧皆通行，不算隔断门。
    _CORE = {"toilet", "staircase", "equipment", "infrastructure"}
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
                 and r["roomType"] not in ("staircase", "elevator_hall", "infrastructure",
                                           "atrium", "corridor", "lobby",
                                           "entrance", "accessible_entrance")]
    orphan_doors = [dr for dr in doors if not dr["rooms"]]
    print(f"[F{floor_no}] QA: 无门房间 {len(zero_door)} 个 {zero_door[:10]}, "
          f"无归属门 {len(orphan_doors)} 个")

    # --- 楼梯 / 电梯 / 柱
    # 统一 detect_stair_boxes（STAIR + A-FLOR-STRS 合并 + 面积/长宽比过滤），
    # 与早期注入 / 门洞范围判定共用同一检测路径，消除多路径参数不一致。
    stair_boxes = detect_stair_boxes(items)

    evtr_boxes = detect_elevator_boxes(items)

    # 需求⑱：电梯井外墙上的窗户 → 电梯门元素（归属对应电梯）
    elevator_doors = detect_elevator_doors(window_groups, evtr_boxes, floor_no)

    col_boxes = []
    for lname in LAYER_COLUMNS:
        ci = items.get(lname)
        if not ci:
            continue
        for q in ci["quads"]:
            xs = [p[0] for p in q]
            ys = [p[1] for p in q]
            col_boxes.append((min(xs), min(ys), max(xs), max(ys)))

    # 注：Walkable Polygon 生成已移至 build_geojson（reconcile_facilities 补齐
    # 楼梯/电梯 bbox 之后），保证扣除用的井道列表与最终 GeoJSON 一致。

    # 合班教室：墙未闭合时注入封闭房间并关联门洞 → 可导航至门口。
    # 注入阶段会尝试用局部墙图识别合班的真实闭合墙体多边形，仅影响合班自身。
    inject_heban_classroom_rooms(rooms, doors, room_names, floor_no,
                                all_segs=all_segs, furn_segs=furn_segs,
                                closures=closures)

    return {
        "rooms": rooms,
        "doors": doors,
        "window_groups": window_groups,
        "wall_segs": all_segs,
        "stair_boxes": stair_boxes,
        "evtr_boxes": evtr_boxes,
        "elevator_doors": elevator_doors,
        "col_boxes": col_boxes,
        "labels_unmatched": [t for i, e in enumerate(room_names) if i not in used_labels for t in [e[0]]],
        "labels_all": [e[0] for e in room_names],
        "labels_all_with_pt": room_names,
        "room_codes": room_codes,
        "facility_codes": facility_codes,
    }


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description="PathAI CAD PDF → GeoJSON")
    ap.add_argument("--use-skeleton", dest="use_skeleton", action="store_true",
                    default=None, help="启用中轴骨架拓扑（默认看 geojson_writer.USE_SKELETON）")
    ap.add_argument("--no-skeleton", dest="no_skeleton", action="store_true",
                    help="禁用骨架拓扑，使用质心最近邻")
    ap.add_argument("--no-manual-skeleton", dest="no_manual_skeleton",
                    action="store_true",
                    help="忽略手绘骨架 JSON，强制走自动中轴骨架")
    args = ap.parse_args(argv)
    if args.no_skeleton:
        set_use_skeleton(False)
    elif args.use_skeleton:
        set_use_skeleton(True)
    if args.no_manual_skeleton:
        reset_manual_skeleton()

    f1 = parse_floor(PDF_F1, 1)
    f2 = parse_floor(PDF_F2, 2)
    geo = build_geojson(f1, f2)
    RESULT_DIR.mkdir(parents=True, exist_ok=True)
    with open(OUT_GEOJSON, "w", encoding="utf-8") as fp:
        json.dump(geo, fp, ensure_ascii=False, indent=2)
    for fl, data in (("1", f1), ("2", f2)):
        print(f"[F{fl}] 未匹配标签: {data['labels_unmatched'][:20]}")
    print("输出:", OUT_GEOJSON)


if __name__ == "__main__":
    main()
