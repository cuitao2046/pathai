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

from shapely.ops import unary_union, polygonize, transform
from shapely.geometry import LineString, MultiPolygon, Point, Polygon, box
from shapely import snap as shp_snap
from shapely.strtree import STRtree

# 全局常量唯一来源（比例/原点/步行速度等，见 docs/code-review-2026-08-12.md D1-D4）
from src.common.constants import SCALE, ORIGIN_X, ORIGIN_Y, BLIND_WALK_SPEED

# 拓扑建模（指南 第五章）
# 注：跨层边不从此导入——本文件使用内嵌的 cross_floor_edges（图纸井道编号配对），
# topology.build_cross_floor_edges 为几何配对版，两者并存见 docs/code-review-2026-08-12.md A3。
from src.topology import (build_floor_topology, obj_id,
                          OBJ_TYPE, assign_node_risk_levels)

try:
    from src.skeleton.pipeline import build_skeleton_topology
    _HAS_SKELETON = True
except ImportError:
    _HAS_SKELETON = False
    build_skeleton_topology = None  # type: ignore
# 复用建筑外轮廓提取（栅格化+弥合门洞+外部泛洪+Moore 追踪），
# 用于 walkable 沿外墙裁剪，避免走廊多边形延伸到户外紧贴墙体的位置。
# B2：轮廓链下沉到 src/geometry/contour.py，parsing 与 rendering 共同引用。
from src.geometry.contour import building_outline

# ---------------------------------------------------------------- 配置

# 路径自动适配：以本文件位置推导项目根目录，不依赖固定盘符/路径
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent   # .../src -> 项目根
RESULT_DIR = PROJECT_ROOT / "result"
PDF_F1 = str(PROJECT_ROOT / "A20-002-II-初中学部 1# 教学楼首层平面图-A0_BIAD-无签名.pdf")
PDF_F2 = str(PROJECT_ROOT / "A20-003-II-初中学部 1# 教学楼二层平面图-A0_BIAD-无签名.pdf")
OUT_GEOJSON = str(RESULT_DIR / "school_building_01_map_v9.geojson")
# 手绘骨架 JSON：由 src/import_manual_skeleton.py 导出。存在时优先于自动中轴骨架，
# 仅替代 TI 节点 / TI-TI 边 / 骨架线；TR/TD/TF/TEN 仍自动生成并挂接到手动 TI。
MANUAL_SKELETON_PATH = str(RESULT_DIR / "skeleton_manual_parsed.json")
MANUAL_SKELETON = None  # None=未加载; {} = 无(文件不存在/被关闭); dict=已加载


def _load_manual_skeleton():
    """按楼层读取手绘骨架 JSON；文件不存在则返回空 dict（视为无手动骨架）。

    仅在文件存在时启用「手动骨架优先于自动中轴骨架」的生成路径。
    """
    global MANUAL_SKELETON
    if MANUAL_SKELETON is not None:
        return MANUAL_SKELETON
    p = Path(MANUAL_SKELETON_PATH)
    if p.exists():
        try:
            MANUAL_SKELETON = json.loads(p.read_text(encoding="utf-8"))
            print(f"[manual-skeleton] 已加载手绘骨架: {MANUAL_SKELETON_PATH} "
                  f"(楼层: {list(MANUAL_SKELETON.keys())})")
        except Exception as e:
            print(f"[WARN] 手绘骨架 JSON 解析失败，回退自动骨架: {e}")
            MANUAL_SKELETON = {}
    else:
        print(f"[manual-skeleton] 未找到 {MANUAL_SKELETON_PATH}，使用自动中轴骨架")
        MANUAL_SKELETON = {}
    return MANUAL_SKELETON

# 比例尺/原点已统一到 src/common/constants.py（SCALE/ORIGIN_X/ORIGIN_Y），
# 校准依据见该模块注释：轴网 8400mm = 158.8pt，与窗编号 M2GW5924 互证。

# Phase2+ 骨架导航管线开关（T3–T8）。True 时用中轴拓扑替代质心最近邻。
USE_SKELETON = True
SKELETON_RESOLUTION = 0.08  # 米/像素；0.05 更精但更慢

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
LABEL_MIN_SIZE = 8.5       # 房间名称最小字号(pt)（略降以捕获 ~9pt 卫生间等小标签）
TITLE_BLOCK_X = 2900.0       # 图签区 x 起点（右侧剔除）

# --- 楼梯/电梯井编号（图纸权威标识，如 II-B2-01#ST / II-02#EL）---
# 同一井道在各层沿用同一编号，是比"几何中心距离"可靠得多的跨层配对依据；
# 且可补齐某层踏步线缺失导致的漏检（F1 的 B3-01 仅剩 ~4m² 碎片、B1-03 无踏步线）。
FACILITY_CODE_RE = re.compile(r"^[A-Z0-9]+(?:-[A-Z0-9]+)*#(ST|EL)$")
FACILITY_CODE_NEAR_M = 2.0   # 编号文字到设施 bbox 的最大容差(米)
STAIR_MAX_ASPECT = 3.0       # 无编号且长宽比超此值 → 判为伪楼梯并剔除
STAIR_MAX_ASPECT_CODED = 5.0 # 有编号时仍拒绝极端细长条（防走廊+踏步被吸进）
STAIR_AREA_MIN_M2 = 3.0      # 楼梯 bbox 最小面积（覆盖踏步线缺失的碎片）
STAIR_AREA_MAX_M2 = 80.0     # 楼梯 bbox 最大面积
STAIR_CLUSTER_GAP_M = 1.5    # 楼梯聚类间距（米）——紧间距避免不同井道合并
ELEV_AREA_MIN_M2 = 1.0       # 电梯井最小面积
ELEV_AREA_MAX_M2 = 30.0      # 电梯井最大面积
STAIR_ROOM_DEDUP_M = 2.0     # 注入楼梯间 room 时与已有 staircase 中心距 < 此值则跳过

# 非封闭空间的室外/开敞标签：不参与房间探测（避免抢占附近区域）
# 教学楼中"无障碍出入口/门厅无障碍出入口/人防主出入口"等均为公共空间节点，
# 应被识别为房间（或作为 topology facility_entrance 节点），故不列入黑名单。
LABEL_SKIP_RE = re.compile(
    r"(非机动车车库入口|非机动车车库出入口|消防车道|车道|雨棚|屋面|上空|庭园|台阶|坡道|散水|屋顶平台|泄爆井|不上人屋面)")

ROOM_TYPE_RULES = [
    ("卫生间", "toilet"), ("洗手间", "toilet"),
    ("楼梯", "staircase"), ("电梯", "elevator_hall"),
    ("走道", "corridor"), ("走廊", "corridor"), ("过道", "corridor"),
    ("门厅", "lobby"), ("大厅", "lobby"),
    ("门厅无障碍出入口", "accessible_entrance"),
    ("无障碍出入口", "accessible_entrance"),
    ("人防主出入口", "entrance"), ("出入口", "entrance"),
    # 合班教室 = 大型封闭教室，禁止当公共/开放空间
    ("合班教室", "classroom"), ("合班", "classroom"),
    ("教室", "classroom"), ("书法", "classroom"), ("美术", "classroom"),
    ("音乐", "classroom"), ("实验室", "lab"),
    (" resource", "classroom"), (" resource教室", "classroom"),
    ("办公", "office"), ("会议", "meeting"), ("接待", "meeting"),
    ("设备", "equipment"), ("机房", "equipment"), ("配电", "equipment"),
    ("水井", "infrastructure"), ("风井", "infrastructure"), ("排风井", "infrastructure"), ("管井", "infrastructure"), ("井", "infrastructure"),
    ("储藏", "storage"), ("存放", "storage"), ("资料", "storage"), ("档案", "storage"),
    ("广播", "equipment"), ("管控", "equipment"),
    # 饮水处为服务核心内的开敞壁龛（无门，紧贴水井/卫生间模块），
    # 归为服务设备类，纳入「服务核心模块豁免」，避免误判为不可达封闭房间。
    ("饮水", "equipment"),
    ("图书", "library"), ("阅览", "library"),
    ("卫生室", "medical"), ("心理", "counseling"), ("辅导", "counseling"),
    ("活动", "activity"), ("社团", "activity"),
    ("传达", "reception"), ("前台", "reception"),
    ("庭园", "atrium"), ("上空", "atrium"),
]

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

# 公共空间（指南 4.2：卫生间/楼梯间/电梯间/走廊为公共，大厅/出入口/无障碍出入口也属于公共）
ROOM_PUBLIC_TYPES = {
    "toilet", "staircase", "elevator_hall", "corridor", "lobby",
    "entrance", "accessible_entrance", "atrium",
    "elevator_lobby", "stair_lobby",
}

# 无障碍可达性（指南 4.2：楼梯对视障禁用；电梯/出入口/走廊/大厅均无障碍）
ACCESSIBLE_TYPES = {
    "classroom", "lab", "office", "meeting", "equipment", "storage",
    "library", "medical", "counseling", "activity", "reception",
    "corridor", "lobby", "entrance", "accessible_entrance",
    "elevator_hall", "atrium", "toilet",
}
NON_ACCESSIBLE_TYPES = {"staircase", "infrastructure"}

# 拓扑建模时是否需要从走廊接入（指南 4.2：是否有独立出入口）
# 走廊/门厅/大厅/出入口/楼梯/电梯厅均视为公共接入点，其他房间通过门接入走廊
INDEPENDENT_ENTRANCE_TYPES = {
    "corridor", "lobby", "entrance", "accessible_entrance",
    "staircase", "elevator_hall", "atrium",
    "elevator_lobby", "stair_lobby",
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


def bezier_mid(bz):
    """三次贝塞尔曲线中点（t=0.5，四重线性插值）。
    用于门摆弧弧中点的统一计算（审查 E5：收敛 detect_doors/parse_floor 两处重复实现）。"""
    p1, p2, p3, p4 = bz
    def lerp(a, b, t=0.5):
        return (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t)
    q1, q2, q3 = lerp(p1, p2), lerp(p2, p3), lerp(p3, p4)
    r1, r2 = lerp(q1, q2), lerp(q2, q3)
    return lerp(r1, r2)


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
    提取房间标签文本（大字号、排除图签区），合并多行中文（如 学生/社团/活动区）；
    同时收集 A-ANNO-150-TXT 英文编号（如 II-WR-03）作为 roomCode。

    返回 (names, codes):
      names = [(chinese_text, (cx, cy)_pt], ...]   → 空间名（用于房间探测）
      codes = [(code_text, (cx, cy)_pt], ...]       → 编号（后与房间匹配）
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
            if x1 > TITLE_BLOCK_X:
                continue
            raw.append({"text": txt, "size": size,
                        "bbox": (x0, y0, x1, y1),
                        "cx": (x0 + x1) / 2, "cy": (y0 + y1) / 2,
                        "w": x1 - x0, "h": y1 - y0})

    def _blocked(t):
        if any(k in t for k in ("归档记录", "出图日期", "图号", "版本号",
                                 "图名", "会  签", "会 签", "会签",
                                 "验证签字", "教育建筑", "产业发展研究院",
                                 "初中学部", "教学楼", "项目名称", "工程名称",
                                 "出图", "设计", "校对", "审核", "审定",
                                 "BIAD", "A20-", "平面图", "学校",
                                 "建设单位", "设计单位", "图纸",
                                 "2m范围内", "门窗洞口", "范围内", "年   月   日",
                                 "年 月 日", "年 月")):
            return True
        if re.fullmatch(r"[0-9.\s%㎡()（）*≥<=-]+", t):
            return True
        if any(k in t for k in ("面积", "分区", "排烟", "平面", "教学", "项目", "设计")):
            return True
        if re.search(r"[0-9a-zA-Z]{3,}", t):
            return True
        if len(t) > 12:
            return True
        return False

    # A-ANNO-150-TXT 英文编号（II-WR-03 / II-LAB-01 / R1001 等）
    _ANNO_CODE_RE = re.compile(r"^(II-[\w\d]+-[\d]+|R\d{3,})$")

    names = []  # 中文空间名
    codes = []  # 英文编号
    for r in raw:
        t = r["text"]
        if _blocked(t):
            continue
        if _ANNO_CODE_RE.match(t):
            codes.append((t, (r["cx"], r["cy"])))
        elif bool(re.search(r"[一-鿿]", t)) and r["size"] >= LABEL_MIN_SIZE:
            names.append(r)

    # 合并垂直堆叠的多行中文标签（行间距 < 2.2 倍行高，水平重叠）；
    # 不跨语义类合并——管井（风井/水井/排风井）与房间标签各自成串，
    # 避免「水井排风井」与「女卫生间」拼接成「水井排风井女卫生间」。
    # 开放空间(走廊/门厅/大厅/活动/出入口)与封闭空间(房间/管井/电梯/楼梯间等)
    # 也分属不同语义类，绝不拼接——避免「走道」+「历史地理资料室」合成「走道历史地理资料室」。
    _UTIL_TAGS = {"风井", "水井", "排风井", "强电井", "弱电井", "管井"}
    _OPEN_TAGS = {"走道", "走廊", "过道", "门厅", "大厅", "活动", "社团",
                  "庭园", "上空", "门厅无障碍出入口", "无障碍出入口",
                  "出入口", "人防主出入口"}
    def _cat(t):
        if t in _UTIL_TAGS: return "util"
        if t in _OPEN_TAGS: return "open"
        return "room"
    names.sort(key=lambda r: (r["cx"], r["cy"]))
    used = [False] * len(names)
    merged = []
    for i, r in enumerate(names):
        if used[i]:
            continue
        group = [r]
        used[i] = True
        r_cat = _cat(r["text"])
        for j in range(i + 1, len(names)):
            r2 = names[j]
            if used[j]:
                continue
            # 不同语义类不合并
            if r_cat != _cat(r2["text"]):
                continue
            if abs(r2["cx"] - r["cx"]) < max(r["w"], r2["w"], 20) * 0.6 + 10 and \
               abs(r2["cy"] - r["cy"]) < (r["h"] + r2["h"]) * 2.2:
                group.append(r2)
                used[j] = True
        group.sort(key=lambda g: g["cy"])
        text = "".join(g["text"] for g in group)
        cx = sum(g["cx"] for g in group) / len(group)
        cy = sum(g["cy"] for g in group) / len(group)
        # 携带该标签组的最大字号，供后续「同一空间内字号最大者作为语义层」规则使用
        gsize = max(g["size"] for g in group)
        merged.append((text, (cx, cy), gsize))
    return merged, codes


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


# ------------------------------------------------------------ 门属性（指南 §3.2）

# 门开向探测：沿摆弧鼓出侧法向逐级外推，判断门扇扫入哪个房间
DOOR_PROBE_STEPS_M = (0.5, 0.9, 1.4)

# 图纸不可判、必须现场勘测补充的门属性（指南 §6.2）
DOOR_SURVEY_FIELDS = ("hasThreshold", "isGlass", "isAutomatic")

DOOR_SUBTYPE = {
    "swing": "hinged",        # 平开门
    "fire": "fire_hinged",    # 防火平开门
    "opening": "opening",     # 无门扇洞口
}

# 判定「外开」的参照：门扇扫入的若是这些通行空间，则为外开（对视障用户风险更高）
DOOR_CIRCULATION_TYPES = {
    "corridor", "lobby", "entrance", "accessible_entrance", "atrium",
    "elevator_hall", "elevator_lobby", "stair_lobby",
}


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


# ---- 墙体厚度与材质（指南 §3.1「CAD 中墙体是双线表示」/ §3.3 thickness+material）

WALL_THICKNESS_MIN_M = 0.06      # 小于此值视为同一条线的重复描边
WALL_THICKNESS_MAX_M = 0.60      # 大于此值不再是同一道墙的两个墙面
WALL_PAIR_ANGLE_TOL = math.radians(5)
WALL_PAIR_OVERLAP_RATIO = 0.30   # 两线沿轴向的重叠比例下限


def _point_line_distance(p, a, b):
    """点到**无限长直线** ab 的距离（区别于点到线段）。"""
    dx, dy = b[0] - a[0], b[1] - a[1]
    L = math.hypot(dx, dy)
    if L < 1e-12:
        return math.hypot(p[0] - a[0], p[1] - a[1])
    return abs((p[0] - a[0]) * dy - (p[1] - a[1]) * dx) / L


def estimate_wall_thickness(wall_segs):
    """双线配对求墙厚：为每条墙面线找最近的平行对侧墙面线，间距即墙厚。

    配对条件：夹角 <5°、垂距落在 [0.06, 0.60] m、沿轴向重叠 ≥30%。
    返回与 wall_segs 等长的 list[float|None]（米），未配对为 None。
    """
    n = len(wall_segs)
    result = [None] * n
    if n == 0:
        return result
    tmin = WALL_THICKNESS_MIN_M / SCALE      # pt
    tmax = WALL_THICKNESS_MAX_M / SCALE
    lines = [LineString([a, b]) for a, b in wall_segs]
    tree = STRtree(lines)
    for i, (a, b) in enumerate(wall_segs):
        li = lines[i]
        ang_i = seg_angle(a, b)
        len_i = seg_len(a, b)
        if len_i < 1e-9:
            continue
        ux, uy = (b[0] - a[0]) / len_i, (b[1] - a[1]) / len_i
        lo_i, hi_i = sorted((a[0] * ux + a[1] * uy, b[0] * ux + b[1] * uy))
        best = None
        for j in tree.query(li.buffer(tmax)):
            j = int(j)
            if j == i:
                continue
            c, d = wall_segs[j]
            if angle_diff(ang_i, seg_angle(c, d)) > WALL_PAIR_ANGLE_TOL:
                continue
            # 垂距（取对侧两端点到本线所在直线距离的均值）
            dist = 0.5 * (_point_line_distance(c, a, b)
                          + _point_line_distance(d, a, b))
            if dist < tmin or dist > tmax:
                continue
            # 轴向重叠比例
            lo_j, hi_j = sorted((c[0] * ux + c[1] * uy, d[0] * ux + d[1] * uy))
            ov = min(hi_i, hi_j) - max(lo_i, lo_j)
            if ov <= 0 or ov < WALL_PAIR_OVERLAP_RATIO * min(len_i,
                                                             seg_len(c, d)):
                continue
            if best is None or dist < best:
                best = dist
        if best is not None:
            result[i] = round(best * SCALE, 3)
    return result


def wall_material_from_thickness(t_m):
    """按墙厚启发式推断材质（图纸未标材质，仅由常见做法反推，须现场核实）。"""
    if t_m is None:
        return None
    if t_m >= 0.30:
        return "concrete"        # 剪力墙 / 结构墙
    if t_m >= 0.18:
        return "brick"           # 240 砌体墙
    return "partition"           # 轻质隔墙


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


def snap(geom, target, tol):
    try:
        return shp_snap(geom, target, tol)
    except Exception:
        return geom


def classify_room_type(label):
    # 合班教室：始终为封闭教室，不得落入 corridor/lobby/activity 等开放类型
    if label and ("合班教室" in label or ( "合班" in label and "教室" in label)):
        return "classroom"
    if label and "合班" in label and not any(
            k in label for k in ("走道", "走廊", "门厅", "大厅")):
        return "classroom"
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

def _bbox_area_m2(b):
    return (b[2] - b[0]) * (b[3] - b[1]) * SCALE * SCALE


def _bbox_aspect(b):
    w, h = abs(b[2] - b[0]), abs(b[3] - b[1])
    lo, hi = min(w, h), max(w, h)
    return (hi / lo) if lo > 1e-6 else 999.0


def detect_stair_boxes(items_by_layer):
    """统一楼梯 bbox 检测：STAIR + A-FLOR-STRS 合并聚类 + 面积/长宽比过滤。

    返回 list[(x0,y0,x1,y1)]（pt）。早期注入 staircase room、门洞范围判定、
    最终 geometry 共用同一套结果，消除多路径参数不一致问题。
    """
    pts = []
    for lname in ("STAIR", "A-FLOR-STRS"):
        si = items_by_layer.get(lname, {"lines": [], "quads": []})
        for seg in si.get("lines", []):
            pts.append(seg)
        for q in si.get("quads", []):
            if len(q) >= 3:
                pts.append((q[0], q[2]))
    if not pts:
        return []
    boxes = bbox_clusters(pts, gap_pt=STAIR_CLUSTER_GAP_M * PT_PER_M)
    out = []
    for b in boxes:
        area = _bbox_area_m2(b)
        if area < STAIR_AREA_MIN_M2 or area > STAIR_AREA_MAX_M2:
            continue
        if _bbox_aspect(b) > STAIR_MAX_ASPECT:
            continue
        out.append(b)
    return out


def detect_elevator_boxes(items_by_layer):
    """电梯井 bbox：A-FLOR-EVTR 聚类 + 面积过滤。"""
    evtr = items_by_layer.get(LAYER_ELEVATOR, {"lines": [], "quads": [], "curves": []})
    pts = list(evtr.get("lines", []))
    for q in evtr.get("quads", []):
        if len(q) >= 2:
            pts.append(q[:2])
    if not pts:
        return []
    boxes = bbox_clusters(pts, gap_pt=2 * PT_PER_M)
    out = []
    for b in boxes:
        area = _bbox_area_m2(b)
        if ELEV_AREA_MIN_M2 <= area <= ELEV_AREA_MAX_M2:
            out.append(b)
    return out


def detect_elevator_doors(window_groups, evtr_boxes, floor_no,
                          gap_m=1.5, max_width_m=3.0):
    """把「电梯井外墙上的窗户」识别为电梯门元素（需求⑱）。

    现实建筑中电梯井道外墙的采光/检修窗即电梯门所在位置；此处将
    电梯 bbox buffer 范围内的 window 组识别为电梯门，归属对应电梯。

    参数：
      window_groups: parse_floor 的 window_groups（pt 坐标系，含 axis/center/length_pt）
      evtr_boxes:   电梯井 bbox 列表（pt：(x0,y0,x1,y1)）
      floor_no:     楼层号
      gap_m:        窗户距电梯墙的最大距离（米）
      max_width_m:  电梯门最大宽度（米，超出视为建筑窗不识别）

    返回: [{index, elev_index, center_m, axis_m, width_m}]（米制坐标）
    """
    if not window_groups or not evtr_boxes:
        return []
    gap_pt = gap_m / SCALE
    max_w_pt = max_width_m / SCALE
    elev_polys = []
    for bxd in evtr_boxes:
        x0, y0, x1, y1 = bxd
        elev_polys.append((x0, y0, x1, y1))
    out = []
    for i, wg in enumerate(window_groups):
        a, b = wg["axis"]
        # 窗组中心（pt）
        mx = (a[0] + b[0]) / 2.0
        my = (a[1] + b[1]) / 2.0
        # 窗宽（pt）
        w_pt = wg["length_pt"]
        if w_pt > max_w_pt:
            continue
        # 找最近的电梯 bbox：中心到 bbox 矩形的最短距离
        best_d, best_bi = float("inf"), None
        for bi, (x0, y0, x1, y1) in enumerate(elev_polys):
            dx = max(0.0, max(x0 - mx, mx - x1))
            dy = max(0.0, max(y0 - my, my - y1))
            d = math.hypot(dx, dy)
            if d < best_d:
                best_d, best_bi = d, bi
        if best_bi is None or best_d > gap_pt:
            continue
        _mid_pt = ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)
        _c = pt2m(_mid_pt)
        out.append({
            "index": len(out),
            "elev_index": best_bi,
            "center_m": [round(_c[0], 3), round(_c[1], 3)],
            "axis_m": [[round(pt2m(a)[0], 3), round(pt2m(a)[1], 3)],
                       [round(pt2m(b)[0], 3), round(pt2m(b)[1], 3)]],
            "width_m": round(w_pt * SCALE, 3),
        })
    return out


def attach_elevator_door_nodes(nodes, edges, elevator_doors, elevators,
                               floor_no, link_radius_m=15.0):
    """把电梯门元素接入拓扑：生成 TD 节点，连对应电梯 TF 与最近公共节点。

    规则：
      - 每个电梯门生成独立 doorway 节点（doorType="elevator"，label=所属电梯）；
      - 连到对应电梯的 facility(TF) 节点（按 elev_index 匹配）；
      - 连到距门 ≤link_radius_m 的开放空间（intersection / facility_entrance /
        facility / doorway 均可，取最近者，保证门可达）；
      - 编号从当前最大 TD 序号之后续号（不与既有门冲突）。
    """
    if not elevator_doors:
        return nodes, edges
    # 当前最大 TD 序号
    max_td = 0
    for n in nodes:
        if n.get("type") == "doorway":
            try:
                max_td = max(max_td, int(n["id"].split("-")[-1]))
            except ValueError:
                pass
    # 电梯 TF：facilityType=elevator。按坐标最近匹配（reconcile 重排后 index
    # 不可靠），并回填 elevatorId（需求⑳：门归属一律用元素 ID）。
    # TF 自身 label 即电梯编号，但归属字段统一用 ID。
    tf_nodes = [n for n in nodes
                if n.get("type") == "facility" and n.get("facilityType") == "elevator"]
    elev_by_centroid = {}
    for n in tf_nodes:
        n.setdefault("elevatorId", None)  # 由调用方按坐标回填
    # 开放空间候选（不含纯管井门——规则 5 已剔除连接，但节点仍可作挂接点）
    cand = [n for n in nodes if n.get("type") in
            ("intersection", "facility_entrance", "doorway")]
    new_nodes, new_edges = [], []
    node_id_set = {n["id"] for n in nodes}
    edge_id_set = {e["id"] for e in edges}
    seq = max_td
    # 边序号：从既有最大 TE 序号 +1 起，单调递增（避免死循环）
    max_te = 0
    for e in edges:
        try:
            max_te = max(max_te, int(e["id"].split("-")[-1]))
        except (ValueError, IndexError):
            pass
    edge_seq = max_te
    # 电梯门 → 电梯 TF 匹配：优先 elevatorId（Feature 格式，归属用 ID），
    # 否则坐标最近（兼容 detect 原始 dict，规避 reconcile 重排后的 index 错位）
    def _match_tf(ed):
        _ep = ed.get("properties", {}) if "elev_index" not in ed else {}
        ec = list(ed["geometry"]["coordinates"]) if "elev_index" not in ed \
            else list(ed["center_m"])
        if _ep.get("elevatorId"):
            for n in nodes:
                if n.get("type") == "facility" \
                   and n.get("facilityType") == "elevator" \
                   and n.get("elevatorId") == _ep["elevatorId"]:
                    return n
            # TF 未带 elevatorId：按坐标最近回退（并回填）
        best_n, best_d = None, float("inf")
        for n in tf_nodes:
            d = math.hypot(ec[0] - n["coordinates"][0],
                           ec[1] - n["coordinates"][1])
            if d < best_d:
                best_d, best_n = d, n
        return best_n

    for i, ed in enumerate(elevator_doors):
        # 兼容两种格式：detect 原始 dict（elev_index）或 build_geojson Feature
        if "elev_index" in ed:
            ei = ed["elev_index"]
            center = ed["center_m"]
            el_label = (elevators[ei]["properties"]["label"]
                        if 0 <= ei < len(elevators) else f"电梯{floor_no}F-{ei + 1}")
            el_id = (elevators[ei]["id"]
                     if 0 <= ei < len(elevators) else None)
        else:
            _ep = ed.get("properties", {})
            ei = _ep.get("elevatorIndex", 0)
            center = list(ed["geometry"]["coordinates"])
            el_label = _ep.get("elevatorLabel", f"电梯{floor_no}F-{ei + 1}")
            el_id = _ep.get("elevatorId")  # 归属元素 ID（需求⑳）
        seq += 1
        td_id = obj_id(f"F{floor_no}", OBJ_TYPE["topo_doorway"], seq)
        while td_id in node_id_set:
            seq += 1
            td_id = obj_id(f"F{floor_no}", OBJ_TYPE["topo_doorway"], seq)
        node_id_set.add(td_id)
        nd = {
            "id": td_id,
            "type": "doorway",
            "doorType": "elevator",
            "label": f"电梯门（{el_label}）",
            "coordinates": list(center),
            "rooms": [el_id] if el_id else [],
            "elevatorId": el_id,
            "elevatorLabel": el_label,
            "elevatorIndex": ei,
            "blindAccessible": True,
            "wheelchairAccessible": True,
        }
        new_nodes.append(nd)
        # 连对应电梯 TF（需求⑳：用 elevatorId 归属匹配，回退坐标最近）
        tf_node = _match_tf(ed)
        if tf_node:
            tf_id = tf_node["id"]
            # 回填 TF 的 elevatorId（需求⑳：归属用元素 ID）
            if el_id and tf_node.get("elevatorId") is None:
                tf_node["elevatorId"] = el_id
            d = math.hypot(center[0] - tf_node["coordinates"][0],
                           center[1] - tf_node["coordinates"][1])
            edge_seq += 1
            eid = obj_id(f"F{floor_no}", OBJ_TYPE["topo_edge"], edge_seq)
            while eid in edge_id_set:
                edge_seq += 1
                eid = obj_id(f"F{floor_no}", OBJ_TYPE["topo_edge"], edge_seq)
            edge_id_set.add(eid)
            new_edges.append({
                "id": eid, "from": tf_id, "to": td_id,
                "distance": round(d, 2),
                "estimatedTime": round(d / BLIND_WALK_SPEED, 1),
                "accessibilityLevel": 0, "riskLevel": 1,
                "walkable": True, "wheelchairAccessible": True,
                "blindAccessible": True,
                "type": "elevator_door",
            })
        # 连最近公共节点
        best = None
        best_d = link_radius_m
        for c in cand:
            d = math.hypot(center[0] - c["coordinates"][0],
                           center[1] - c["coordinates"][1])
            if d < best_d:
                best_d, best = d, c
        if best is not None:
            edge_seq += 1
            eid = obj_id(f"F{floor_no}", OBJ_TYPE["topo_edge"], edge_seq)
            while eid in edge_id_set:
                edge_seq += 1
                eid = obj_id(f"F{floor_no}", OBJ_TYPE["topo_edge"], edge_seq)
            edge_id_set.add(eid)
            new_edges.append({
                "id": eid, "from": td_id, "to": best["id"],
                "distance": round(best_d, 2),
                "estimatedTime": round(best_d / BLIND_WALK_SPEED, 1),
                "accessibilityLevel": 0, "riskLevel": 1,
                "walkable": True, "wheelchairAccessible": True,
                "blindAccessible": True,
                "type": "elevator_door",
            })
    return nodes + new_nodes, edges + new_edges


def _tf_coord(nodes, nid):
    for n in nodes:
        if n["id"] == nid:
            return n["coordinates"]
    return [0.0, 0.0]


def _keep_walkable_pieces(geom, min_piece_m2):
    """过滤差集后的微小碎片，返回 Polygon 或 MultiPolygon（pt 坐标）。"""
    if geom is None or geom.is_empty:
        return None
    pieces = list(geom.geoms) if geom.geom_type == "MultiPolygon" else [geom]
    keep = [g for g in pieces if g.area * SCALE * SCALE >= min_piece_m2]
    if not keep:
        return None
    if len(keep) == 1:
        return keep[0]
    return MultiPolygon(keep)


def _walkable_geojson(geom):
    """Walkable Polygon → GeoJSON MultiPolygon 坐标（m），带内环（柱洞）。"""
    if geom is None or geom.is_empty:
        return None
    polys = list(geom.geoms) if geom.geom_type == "MultiPolygon" else [geom]

    def _ring(coords):
        return [list(pt2m((x, y))) for x, y in coords]

    return {
        "type": "MultiPolygon",
        "coordinates": [[_ring(p.exterior.coords)] + [_ring(h.coords)
                       for h in p.interiors] for p in polys],
    }


def _resolve_open_closed_overlaps(rooms, min_area_m2=1.0):
    """开放空间（走廊/门厅/活动/中庭/前室）不得覆盖封闭房间。

    build_rooms 分水岭阶段可能把「走道」标签投到包含教室的连通域，
    导致走廊 polygon 误吞教室；后续 Walkable 生成会把教室作为障碍扣除，
    在重叠区留下空洞（用户截图红圈处无骨架/无指纹）。
    本函数在 Walkable 生成前做后处理：从每个开放空间 polygon 中扣除
    其与所有封闭房间的重叠部分，保留最大残片，并同步更新 coords_m/centroid_m。
    """
    from shapely.geometry import MultiPolygon
    from shapely.ops import unary_union

    _OPEN = set(_OPEN_ID_KEY) | {"elevator_lobby", "stair_lobby"}
    closed = [(r, r["polygon_pt"]) for r in rooms
              if r.get("roomType") not in _OPEN
              and r.get("polygon_pt") is not None
              and not r["polygon_pt"].is_empty]
    if not closed:
        return 0
    closed_union = unary_union([p for _, p in closed]).buffer(-0.01 / SCALE)
    if closed_union.is_empty:
        return 0
    n = 0
    min_pt = min_area_m2 / (SCALE * SCALE)
    for r in rooms:
        if r.get("roomType") not in _OPEN:
            continue
        poly = r.get("polygon_pt")
        if poly is None or poly.is_empty:
            continue
        inter = poly.intersection(closed_union)
        if inter.is_empty or inter.area < min_pt:
            continue
        clipped = poly.difference(closed_union)
        pieces = []
        if clipped.geom_type == "Polygon":
            pieces = [clipped]
        elif clipped.geom_type == "MultiPolygon":
            pieces = [g for g in clipped.geoms if not g.is_empty]
        pieces = [g for g in pieces if g.area >= min_pt]
        if not pieces:
            continue
        # 保留最大块，避免走廊被切成多块同名房间；若残片仍>=min_area_m2则接受
        biggest = max(pieces, key=lambda g: g.area)
        if abs(biggest.area - poly.area) > min_pt:
            r["polygon_pt"] = biggest
            r["coords_m"] = [list(pt2m((x, y))) for x, y in biggest.exterior.coords]
            r["centroid_m"] = list(pt2m((biggest.centroid.x, biggest.centroid.y)))
            n += 1
    return n


def generate_walkable_polygons(rooms, wall_segs, stair_boxes, evtr_boxes,
                               col_boxes, wall_buffer_m=0.12,
                               col_buffer_m=0.10,
                               stair_buffer_m=0.55,
                               elev_buffer_m=0.25,
                               min_piece_m2=0.5):
    """T1: 为公共空间（corridor/lobby/activity/atrium）生成 Walkable Polygon。

    障碍物 = 柱子 bbox(buffer) ∪ 楼梯井/电梯井 bbox(buffer) ∪
            封闭房间多边形(楼梯间/电梯厅/管井) ∪ 墙线 buffer；
    walkable = 公共空间多边形 \\ 障碍物。
    输入均为 pt 坐标（polygon_pt / 墙线段 / 井道/柱 bbox）。
    结果写入各 room dict 的 walkable_poly_pt，并返回 {下标: Shapely 多边形}。
    验收口径：Walkable Polygon 不含任何柱子/墙体/井道内部区域。
    """
    from shapely.geometry import MultiPolygon
    _open = set(_OPEN_ID_KEY) | {"elevator_lobby", "stair_lobby"}
    obstacles = []
    # 建筑结构柱（混凝土截面 bbox），外扩安全间距防贴柱
    for (x0, y0, x1, y1) in col_boxes:
        if (x1 - x0) * (y1 - y0) > 0:
            obstacles.append(box(x0, y0, x1, y1).buffer(col_buffer_m / SCALE))
    # 楼梯井：加大缓冲（0.55m），避免 walkable 贴楼梯甚至绕出井外
    for (x0, y0, x1, y1) in stair_boxes:
        if (x1 - x0) * (y1 - y0) > 0:
            obstacles.append(box(x0, y0, x1, y1).buffer(stair_buffer_m / SCALE))
    # 电梯井
    for (x0, y0, x1, y1) in evtr_boxes:
        if (x1 - x0) * (y1 - y0) > 0:
            obstacles.append(box(x0, y0, x1, y1).buffer(elev_buffer_m / SCALE))
    # 封闭房间：楼梯间再外扩挖空
    _stair_like = {"staircase", "infrastructure"}
    for r in rooms:
        if r.get("roomType") not in _open and r.get("polygon_pt") is not None:
            poly = r["polygon_pt"]
            if r.get("roomType") in _stair_like:
                try:
                    poly = poly.buffer(0.35 / SCALE)
                except Exception:
                    pass
            obstacles.append(poly)
    # 墙体：结构墙线并集外扩 WALL_BUFFER=0.12m
    if wall_segs:
        obstacles.append(unary_union(
            [LineString([a, b]) for a, b in wall_segs]).buffer(wall_buffer_m / SCALE))
    obstacle = unary_union(obstacles) if obstacles else None

    out = {}
    for i, r in enumerate(rooms):
        if r.get("roomType") not in _open:
            continue
        poly = r.get("polygon_pt")
        if poly is None:
            continue
        wp = poly.difference(obstacle) if obstacle is not None else poly
        wp = _keep_walkable_pieces(wp, min_piece_m2)
        out[i] = wp
        r["walkable_poly_pt"] = wp
    return out


def _lobby_largest_poly(geom):
    """返回 geom 中面积最大的 Polygon 部分；空/无面积返回 None。"""
    if geom is None or getattr(geom, "is_empty", True):
        return None
    if geom.geom_type == "Polygon":
        return geom if geom.area > 0 else None
    if geom.geom_type == "MultiPolygon":
        return max(geom.geoms, key=lambda g: g.area) if geom.geoms else None
    return None


def _make_lobby_room(rid, kind, poly, src, floor_no):
    """由切出的前室多边形 poly(pt 坐标) 构造一个新的房间 dict。"""
    room_type = "elevator_lobby" if kind == "elevator" else "stair_lobby"
    label = "电梯前室" if kind == "elevator" else "楼梯前室"
    return {
        "id": rid,
        "label": label,
        "roomType": room_type,
        "code": (src.get("code", "") if isinstance(src, dict) else ""),
        "polygon_pt": poly,
        "walkable_poly_pt": poly,
        "coords_m": [list(pt2m((x, y))) for x, y in poly.exterior.coords],
        "centroid_m": list(pt2m((poly.centroid.x, poly.centroid.y))),
        "floor": floor_no,
    }


def split_lobby_pockets(rooms, stair_boxes, evtr_boxes,
                        band_m=2.0, area_min_m2=1.5, area_max_m2=14.0,
                        floor_no=None):
    """T2.5: 在可通行区(走廊/门厅)中切出紧邻电梯井/楼梯井的小前室。

    旧逻辑把整间走廊误标为前室(可达 150m²)，不符合"前室<10m² 且紧邻井道"
    的常识。本函数改用几何切分：
      对每口电梯井/楼梯井，取其缓冲 band_m 与相邻开放空间的交集口袋，
      该口袋即真实前室(通常 2~12m²)；原开放空间被口袋吃掉的部分收缩、
      其余仍保留为走廊/门厅。

    只针对电梯(elevator)与楼梯(staircase)井，不处理其它 utility infrastructure（风井/管道井）。
    新建房间追加到 rooms 列表，由 build_geojson 自动落到 geometry/semantic。
    返回新建前室数量。
    """
    def boxes_to_polys(boxes):
        out = []
        for (x0, y0, x1, y1) in (boxes or []):
            if (x1 - x0) > 0 and (y1 - y0) > 0:
                out.append(box(x0, y0, x1, y1))
        return out

    elev_geoms = boxes_to_polys(evtr_boxes)
    stair_geoms = boxes_to_polys(stair_boxes)
    # 补充井道房间多边形（比 bbox 更精确），并区分电梯/楼梯
    for r in rooms:
        rt = r.get("roomType")
        if rt in ("elevator_hall", "staircase") and r.get("polygon_pt") is not None:
            (elev_geoms if rt == "elevator_hall" else stair_geoms).append(
                r["polygon_pt"])

    if not (elev_geoms or stair_geoms):
        return 0

    OPEN = set(_OPEN_ID_KEY) | {"elevator_lobby", "stair_lobby", "lobby"}
    band_pt = band_m / SCALE
    # 面积阈值：输入为 m²，shapely 多边形面积为 pt²，换算 1 m² = 1/SCALE² pt²
    _inv = 1.0 / (SCALE * SCALE)
    amin = area_min_m2 * _inv
    amax = area_max_m2 * _inv
    claimed = []
    new_rooms = []
    seq = [0]

    for r in rooms:
        if r.get("roomType") not in OPEN:
            continue
        rp = r.get("polygon_pt")
        if rp is None or getattr(rp, "is_empty", True):
            continue
        base = rp
        for c in claimed:
            base = base.difference(c)   # 避免同一口袋被两个开放空间重复认领
        if getattr(base, "is_empty", True) or base.area < amin:
            continue
        pockets = []
        is_elev = False
        for kind, geoms in (("elevator", elev_geoms), ("stair", stair_geoms)):
            for g in geoms:
                if base.distance(g) > band_pt + 0.5 / SCALE:
                    continue
                pkt = base.intersection(g.buffer(band_pt))
                pkt = _lobby_largest_poly(pkt)
                if pkt is None or pkt.area < amin:
                    continue
                pockets.append((kind, pkt))
                if kind == "elevator":
                    is_elev = True
        if not pockets:
            continue
        u = unary_union([p for _, p in pockets])
        u = _lobby_largest_poly(u)   # 同一开放空间贴两口井时取最大口袋
        if u is None or u.area < amin or u.area > amax:
            continue
        kind = "elevator" if is_elev else "stair"
        seq[0] += 1
        rid = "F{}-{}L-{:02d}".format(
            floor_no if floor_no is not None else "X",
            ("EL" if kind == "elevator" else "ST"), seq[0])
        new_rooms.append(_make_lobby_room(rid, kind, u, r, floor_no))
        claimed.append(u)
        # 收缩原开放空间（去掉口袋区域），其余保留为走廊/门厅
        shrunk = _lobby_largest_poly(rp.difference(u))
        if shrunk is not None and not getattr(shrunk, "is_empty", True) \
                and shrunk.area > amin:
            r["polygon_pt"] = shrunk
            r["walkable_poly_pt"] = shrunk
            r["coords_m"] = [list(pt2m((x, y))) for x, y in shrunk.exterior.coords]
            r["centroid_m"] = list(pt2m((shrunk.centroid.x, shrunk.centroid.y)))
            # roomType 保持原类（走廊/门厅），不再整间标前室
        else:
            # 原空间几乎全被口袋吃掉 → 直接改标为前室
            r["roomType"] = "elevator_lobby" if kind == "elevator" else "stair_lobby"
            r["polygon_pt"] = u
            r["walkable_poly_pt"] = u
            r["coords_m"] = [list(pt2m((x, y))) for x, y in u.exterior.coords]
            r["centroid_m"] = list(pt2m((u.centroid.x, u.centroid.y)))
    rooms.extend(new_rooms)
    return len(new_rooms)





# ── 射线投票参数 ──
_HEBAN_AXIS_TOL = 2.0          # pt：轴向判定容差
_HEBAN_MIN_SEG_LEN = 5.0       # pt：最小有效墙段长度
_HEBAN_H_SPAN = 180.0          # pt：水平射线跨度
_HEBAN_V_SPAN = 140.0          # pt：垂直射线跨度
_HEBAN_RAY_SAMPLES = 401       # 射线采样数
_HEBAN_RAY_BIN = 3.0           # pt：投票分箱大小
_HEBAN_MIN_SUPPORT = 0.50      # 最低支持率


def _heban_real_polygon_v2(label_pt_pt, all_segs, furn_segs, closures):
    """
    合班教室真实闭合墙体识别 v2 —— 语义种子 + 多方向射线投票。

    替代 v1 的多尺度形态学闭运算方案。核心思路：
      1) 从标签点（语义种子）向四个方向发射大量射线；
      2) 每条射线碰撞最近墙体，统计碰撞坐标的众数；
      3) 门洞缺口仅影响少数射线，不改变众数；
      4) 四个方向的众数坐标构成房间矩形边界。

    参数同 _heban_real_polygon，返回 shapely Polygon 或 None。
    """
    from collections import Counter

    seed_x, seed_y = label_pt_pt

    # 合并所有墙体来源（结构墙 + 家具线 + 封口线）
    all_lines = list(all_segs) + list(furn_segs) + list(closures)
    if not all_lines:
        return None

    tol = _HEBAN_AXIS_TOL
    min_len = _HEBAN_MIN_SEG_LEN
    vert = []   # [(x, y_min, y_max), ...]
    horz = []   # [(y, x_min, x_max), ...]

    for (x1, y1), (x2, y2) in all_lines:
        if abs(x1 - x2) <= tol and abs(y1 - y2) > min_len:
            x = (x1 + x2) / 2.0
            vert.append((x, min(y1, y2), max(y1, y2)))
        elif abs(y1 - y2) <= tol and abs(x1 - x2) > min_len:
            y = (y1 + y2) / 2.0
            horz.append((y, min(x1, x2), max(x1, x2)))

    if len(vert) < 5 or len(horz) < 5:
        return None

    # ── 射线投票辅助函数 ──

    def _cluster_mode(values):
        """对射线碰撞坐标做分箱投票，返回 (均值坐标, 支持数)。"""
        if not values:
            return None, 0
        hist = Counter(int(round(v / _HEBAN_RAY_BIN)) for v in values)
        best_bin, support = hist.most_common(1)[0]
        center = best_bin * _HEBAN_RAY_BIN
        in_cluster = [v for v in values
                      if abs(v - center) <= _HEBAN_RAY_BIN * 0.60]
        return sum(in_cluster) / len(in_cluster), support

    def _vote_vertical(side):
        """向水平方向发射射线，投票找左/右墙。"""
        values = []
        for i in range(_HEBAN_RAY_SAMPLES):
            y = seed_y - _HEBAN_V_SPAN + 2.0 * _HEBAN_V_SPAN * i / (_HEBAN_RAY_SAMPLES - 1)
            candidates = [
                x for x, y0, y1 in vert
                if y0 - tol <= y <= y1 + tol
                and ((side == "left" and x < seed_x - tol)
                     or (side == "right" and x > seed_x + tol))
            ]
            if candidates:
                values.append(max(candidates) if side == "left" else min(candidates))
        coord, support = _cluster_mode(values)
        if coord is None:
            return None
        ratio = support / len(values) if values else 0
        if ratio < _HEBAN_MIN_SUPPORT:
            return None
        return coord

    def _vote_horizontal(side):
        """向垂直方向发射射线，投票找上/下墙。"""
        values = []
        for i in range(_HEBAN_RAY_SAMPLES):
            x = seed_x - _HEBAN_H_SPAN + 2.0 * _HEBAN_H_SPAN * i / (_HEBAN_RAY_SAMPLES - 1)
            candidates = [
                y for y, x0, x1 in horz
                if x0 - tol <= x <= x1 + tol
                and ((side == "top" and y < seed_y - tol)
                     or (side == "bottom" and y > seed_y + tol))
            ]
            if candidates:
                values.append(max(candidates) if side == "top" else min(candidates))
        coord, support = _cluster_mode(values)
        if coord is None:
            return None
        ratio = support / len(values) if values else 0
        if ratio < _HEBAN_MIN_SUPPORT:
            return None
        return coord

    # ── 四方向投票 ──
    left = _vote_vertical("left")
    right = _vote_vertical("right")
    top = _vote_horizontal("top")
    bottom = _vote_horizontal("bottom")

    if any(v is None for v in (left, right, top, bottom)):
        return None

    if not (left < right and top < bottom):
        return None

    poly = Polygon([
        (left, top), (right, top),
        (right, bottom), (left, bottom),
    ])
    if not poly.is_valid:
        poly = poly.buffer(0)
    if poly.is_empty:
        return None

    area_m2 = float(poly.area) * (SCALE ** 2)
    if not (30.0 <= area_m2 <= 300.0):
        return None

    return poly


def inject_heban_classroom_rooms(rooms, doors, labels_with_pt, floor_no,
                                  all_segs=(), furn_segs=(), closures=()):
    """
    合班教室注入（隔离版）：只服务合班自身，不影响其他空间。

    约束：
      1) 优先用局部闭合墙体识别得到真实多边形；失败才回退到 3m 占位方块；
      2) 只「追加」门归属，从不删除其它 room id；
      3) 不抢已明确归属其它封闭房间的门；
      4) 不修改其它房间的 roomType / walkable。
    """
    if not labels_with_pt:
        return 0

    OPEN = {"corridor", "lobby", "activity", "atrium",
            "elevator_lobby", "stair_lobby", "entrance", "accessible_entrance"}
    room_by_id = {r["id"]: r for r in rooms}

    def _lab(r):
        return r.get("label") or ""

    def _door_free_for_heban(dr):
        """门未被其它封闭房间独占时才可挂合班。"""
        for rid in dr.get("rooms") or []:
            r = room_by_id.get(rid)
            if r is None:
                continue
            if "合班" in _lab(r):
                continue
            rt = r.get("roomType") or ""
            if rt not in OPEN and rt != "infrastructure":
                # 已属于教室/办公/卫生间等封闭空间 → 不抢
                return False
        return True

    targets = [r for r in rooms if "合班" in _lab(r)]

    if not targets:
        for entry in labels_with_pt:
            label = entry[0]
            pt_pt = entry[1]
            if "合班" not in label:
                continue
            # 标签是否已落在某个已有封闭房间内？若是则不注入，避免叠房间
            cx_m, cy_m = pt2m(pt_pt)
            skip = False
            for r in rooms:
                if r.get("roomType") in OPEN:
                    continue
                poly = r.get("polygon_pt")
                if poly is None or getattr(poly, "is_empty", True):
                    continue
                try:
                    if poly.contains(Point(pt_pt[0], pt_pt[1])):
                        skip = True
                        break
                except Exception:
                    pass
            if skip:
                print(f"[F{floor_no}] 合班标签已在其它封闭房间内，跳过注入")
                continue

            def m2pt(xm, ym):
                return (xm / SCALE + ORIGIN_X, ORIGIN_Y - ym / SCALE)

            # 优先识别真实闭合墙体多边形；失败才回退 3m 占位方块
            real_poly = _heban_real_polygon_v2(pt_pt, all_segs, furn_segs, closures)
            if real_poly is not None:
                poly = real_poly
                source = "heban_inject_real"
                print(f"[F{floor_no}] 合班教室识别真实闭合墙体: "
                      f"面积≈{float(poly.area) * (SCALE ** 2):.1f}m²")
            else:
                # 极小占位（3m×3m），仅作拓扑质心，降低压盖邻室风险
                half = 1.5
                corners_m = [
                    (cx_m - half, cy_m - half),
                    (cx_m + half, cy_m - half),
                    (cx_m + half, cy_m + half),
                    (cx_m - half, cy_m + half),
                ]
                corners_pt = [m2pt(x, y) for x, y in corners_m]
                poly = Polygon(corners_pt)
                source = "heban_inject"
                print(f"[F{floor_no}] 合班教室真实墙体识别失败，回退 3m 占位")
            # 若与其它封闭房间相交，尝试差集；失败则仍用原多边形（拓扑用）
            try:
                others = []
                for r in rooms:
                    if r.get("roomType") in OPEN:
                        continue
                    op = r.get("polygon_pt")
                    if op is not None and not getattr(op, "is_empty", True):
                        others.append(op)
                if others:
                    diff = poly.difference(unary_union(others))
                    if not diff.is_empty:
                        if diff.geom_type == "Polygon":
                            poly = diff
                        elif diff.geom_type == "MultiPolygon" and diff.geoms:
                            poly = max(diff.geoms, key=lambda g: g.area)
            except Exception:
                pass

            seq = sum(1 for r in rooms if "-RM-" in str(r.get("id", ""))) + 1
            rid = obj_id(f"F{floor_no}", OBJ_TYPE["room"], seq)
            used_ids = {r["id"] for r in rooms}
            while rid in used_ids:
                seq += 1
                rid = obj_id(f"F{floor_no}", OBJ_TYPE["room"], seq)

            coords_m = [list(pt2m((x, y))) for x, y in poly.exterior.coords]
            room = {
                "id": rid,
                "label": label if "教室" in label else "合班教室",
                "roomType": "classroom",
                "polygon_pt": poly,
                "centroid_pt": (pt_pt[0], pt_pt[1]),
                "coords_m": coords_m,
                "centroid_m": [cx_m, cy_m],
                "area_m2": round(float(poly.area) * (SCALE ** 2), 2),
                "synthetic": True,
                "source": source,
            }
            rooms.append(room)
            room_by_id[rid] = room
            targets.append(room)
            print(f"[F{floor_no}] 注入合班教室(隔离): {rid} @ "
                  f"({cx_m:.1f},{cy_m:.1f}) 占位≈{room['area_m2']}m²")

    if not targets:
        return 0

    for room in targets:
        poly_pt = room.get("polygon_pt")
        cand = []
        for dr in doors:
            if not _door_free_for_heban(dr):
                continue
            dpt = dr.get("center")
            if not dpt:
                continue
            # 用门到房间多边形边界的距离（米），替代质心距离。
            # 门应落在房间墙面上，边界距离天然区分"墙上门"与"隔壁走廊门"。
            if poly_pt is not None and not poly_pt.is_empty:
                bdist_pt = poly_pt.exterior.distance(Point(dpt[0], dpt[1]))
                bdist_m = bdist_pt * SCALE
            else:
                dm = pt2m(dpt)
                bdist_m = math.hypot(dm[0] - room["centroid_m"][0],
                                     dm[1] - room["centroid_m"][1])
            if bdist_m > 1.0:  # 门中心距墙面超过 1m → 不是该房间的门
                continue
            pri = 0 if dr.get("kind") == "fire" else (
                1 if dr.get("kind") == "opening" else 2)
            cand.append((pri, bdist_m, dr))
        cand.sort(key=lambda x: (x[0], x[1]))  # 按 (优先级, 边界距离) 排序，不比较 dict
        picked = []
        for pri, d, dr in cand:
            if len(picked) >= 6:  # 上限放宽到 6，边界距离判据足以防误挂
                break
            picked.append((d, dr))
        for d, dr in picked:
            rooms_list = dr.setdefault("rooms", [])
            if room["id"] not in rooms_list:
                rooms_list.append(room["id"])  # 只追加，不删原有
        if not picked:
            print(f"[F{floor_no}] 警告: 合班 {room['id']} 无可用门"
                  f"（近门均已属其它封闭房间或不在墙上）")
        else:
            print(f"[F{floor_no}] 合班 {room['id']} 关联门 {len(picked)} 扇"
                  f"（只追加, 最近 {picked[0][0]:.1f}m）")
    return len(targets)


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

        # --- 2) 剔除伪楼梯（形状离谱；无编号按 STAIR_MAX_ASPECT，有编号仍拒极端细长条）---
        dropped = {"1": [], "2": []}
        if kind == "ST":
            def keep(b, c):
                w, h = abs(b[2] - b[0]), abs(b[3] - b[1])
                lo, hi = min(w, h), max(w, h)
                aspect = (hi / lo) if lo > 1e-6 else 999.0
                # 有编号：仅剔除极端细长条（防走廊+踏步被吸进）
                if c:
                    return aspect <= STAIR_MAX_ASPECT_CODED
                return aspect <= STAIR_MAX_ASPECT

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


# 防火门常开判定用：功能房间类型 + 公共/开放空间类型
_FIRE_OPEN_FUNC = FUNCTIONAL_ROOM_TYPES | {"room"}
_FIRE_OPEN_PUBLIC = ROOM_PUBLIC_TYPES | {"elevator_lobby", "stair_lobby",
                                          "entrance", "accessible_entrance"}


def _compute_fire_door_normally_open(dr, rooms_by_id):
    """防火门常开判定：连接功能房间(教室/办公室等)与开放空间(走廊/门厅等)→常开；其余→常闭"""
    func = False
    public = False
    for rid in dr.get("rooms") or []:
        r = rooms_by_id.get(rid)
        if r is None:
            continue
        rt = r.get("roomType", "")
        if rt in _FIRE_OPEN_FUNC:
            func = True
        if rt in _FIRE_OPEN_PUBLIC:
            public = True
    return func and public

def build_geojson(f1, f2):
    facility_report = reconcile_facilities(f1, f2)
    for kind, info in facility_report.items():
        for fl in ("1", "2"):
            if info["added"][fl]:
                print(f"[F{fl}] {kind} 依图纸编号补齐: {info['added'][fl]}")
            if info["dropped"].get(fl):
                print(f"[F{fl}] {kind} 剔除无编号伪设施: {info['dropped'][fl]} 个")

    def floor_block(floor_no, data):

        # T0.5 开放/封闭房间多边形重叠裁剪
        # 根因：走廊 polygon 可能误吞教室等封闭空间；后续 Walkable 会把教室
        # 当障碍扣除，在重叠区留下无骨架/无指纹的空洞。
        try:
            n_ov = _resolve_open_closed_overlaps(data["rooms"])
            if n_ov:
                print(f"[F{floor_no}] 开放/封闭房间重叠裁剪: {n_ov} 个")
        except Exception as e:
            print(f"    [WARN] 开放/封闭房间重叠裁剪失败: {e}")

        # T1.5 沿建筑外轮廓裁剪：室内导航不得出现户外走道/可通行区
        # 根因：
        #   1) 走廊房间多边形可能因自由域延伸出外墙；
        #   2) 旧方案用「墙+全部房间」生成建筑外轮廓，公共空间多边形本身的外溢
        #      会被吸入轮廓，导致再内缩仍无法贴合真实外墙；
        #   3) 只裁 walkable 不够——渲染走道图层(layer_corridor 等)画的是 coords_m，
        #      必须同步裁剪公共空间多边形本身，否则渲染层仍画出墙外走道。
        # 对策：仅用墙线生成建筑外轮廓（不受公共空间多边形外溢影响），
        #      用较大 close_r 弥合门洞/缺口，再内缩回到墙线位置。
        # 对照《公共空间识别方案》：Walkable ⊂ 建筑内部，须先确定 P_floor 再扣障碍。
        try:
            _flo = {
                "walls": [{"geometry": {"type": "LineString",
                            "coordinates": [list(pt2m(a)), list(pt2m(b))]}}
                          for (a, b) in data["wall_segs"]],
                # 关键：不传入房间多边形，避免外溢的公共空间 polygon 把轮廓撑大
                "rooms": [],
            }
            # close_r=14@0.1m ≈ 1.4m 弥合门洞/外墙缺口；再内缩 1.2m 回到墙线外侧
            _ol = building_outline(_flo, cell=0.1, wall_hw=1, close_r=14)
            if _ol:
                _areas = [Polygon(p).area for p in _ol]
                _mx = max(_areas) if _areas else 0.0
                _thr = max(150.0, 0.05 * _mx)
                # 仅保留含「封闭房间」中心的轮廓块，剔除纯户外/庭院孤岛
                _open_types = set(_OPEN_ID_KEY) | {"elevator_lobby", "stair_lobby"}
                _room_centers = [Polygon(r["coords_m"]).centroid
                                  for r in data["rooms"]
                                  if r.get("coords_m") and
                                  r.get("roomType") not in _open_types]
                _kept_blocks = []
                for _p, _a in zip(_ol, _areas):
                    if _a < _thr:
                        continue
                    _pg = Polygon(_p)
                    if _room_centers and not any(_pg.covers(_c) for _c in _room_centers):
                        continue
                    _kept_blocks.append(_pg)
                if not _kept_blocks:
                    _kept_blocks = [Polygon(p) for p, a in zip(_ol, _areas) if a >= _thr]
                _mask_m = unary_union(_kept_blocks)
                # 内缩：抵消 close_r 膨胀并留 0.2m 外墙余量，mask 落在真实外墙外侧
                _mask_m = _mask_m.buffer(-1.2)
                if _mask_m.is_empty:
                    print(f"    [WARN] 外轮廓内缩后为空，跳过裁剪")
                else:
                    # mask 从 m 单位转回 pt 坐标（walkable/polygon 均为 pt 单位）。
                    # 手写循环而非 shapely.transform：shapely 2.x 会把整段 coords
                    # 一次性传入 func，lambda 仅返回首对导致变换不生效。
                    def _m_to_pt(g):
                        if g is None or g.is_empty:
                            return g
                        if g.geom_type == "Polygon":
                            ext = [(x / SCALE + ORIGIN_X, ORIGIN_Y - y / SCALE)
                                   for x, y in g.exterior.coords]
                            ints = [[(x / SCALE + ORIGIN_X, ORIGIN_Y - y / SCALE)
                                     for x, y in ring]
                                    for ring in g.interiors]
                            return Polygon(ext, ints)
                        if g.geom_type == "MultiPolygon":
                            return MultiPolygon([_m_to_pt(p) for p in g.geoms])
                        return g

                    def _largest_poly(g):
                        if g is None or g.is_empty:
                            return None
                        if g.geom_type == "Polygon":
                            return g
                        if g.geom_type == "MultiPolygon":
                            parts = [p for p in g.geoms if not p.is_empty]
                            return max(parts, key=lambda p: p.area) if parts else None
                        return None

                    _mask = _m_to_pt(_mask_m)
                    n_clip_wp = 0
                    n_clip_open = 0
                    for r in data["rooms"]:
                        # 1) 裁 walkable
                        wp = r.get("walkable_poly_pt")
                        if wp is not None:
                            before_a = wp.area
                            clipped = _keep_walkable_pieces(
                                wp.intersection(_mask), 0.5)
                            r["walkable_poly_pt"] = clipped
                            if clipped is None or abs(
                                    (clipped.area if clipped else 0) - before_a) > 1e-3:
                                n_clip_wp += 1
                        # 2) 裁公共空间房间多边形本身（走道/门厅等）
                        #    否则渲染层仍会画出墙外走道
                        if (r.get("roomType") in _open_types
                                and r.get("polygon_pt") is not None):
                            inter = r["polygon_pt"].intersection(_mask)
                            kept = _largest_poly(inter)
                            if kept is not None and kept.area > 1e-3:
                                if abs(kept.area - r["polygon_pt"].area) > 1e-3:
                                    n_clip_open += 1
                                r["polygon_pt"] = kept
                                r["coords_m"] = [
                                    list(pt2m((x, y)))
                                    for x, y in kept.exterior.coords]
                                r["centroid_m"] = list(
                                    pt2m((kept.centroid.x, kept.centroid.y)))
                            else:
                                # 整块在户外 → 清空 walkable + polygon，
                                # 否则 T1 会基于原 polygon 重新生成外溢 walkable，
                                # 渲染层也会继续画出墙外走道。
                                r["walkable_poly_pt"] = None
                                r["polygon_pt"] = None
                                r["coords_m"] = []
                    print(f"[F{floor_no}] 外轮廓裁剪: walkable {n_clip_wp} 个, "
                          f"开放空间多边形 {n_clip_open} 个")
                    # 保存 mask 供 T1 后二次裁剪 walkable（T1.5 执行时 walkable 尚未生成）
                    data["building_mask_pt"] = _mask
        except Exception as e:
            print(f"    [WARN] 沿轮廓裁剪失败: {e}")

        # --- T2.5: 前室切分（电梯前室/楼梯前室） ---
        # 在 T1.5 裁剪之后、T1(walkable) 之前执行：把整间走廊误标成大前室
        # 的旧逻辑移除，改为几何切分——走廊 ∩ 缓冲(电梯/楼梯井, 2m) = 真实
        # 小前室(2~12m²)，原走廊缩掉口袋后仍为走廊。T1 随后会基于切分后的
        # polygon_pt 正常生成 walkable（前室与走廊都算可通行）。
        try:
            n_lobby = split_lobby_pockets(
                data["rooms"], data["stair_boxes"], data["evtr_boxes"],
                band_m=2.0, floor_no=floor_no)
            if n_lobby:
                print(f"[F{floor_no}] 前室切分: 新建 {n_lobby} 个电梯/楼梯前室")
        except Exception as e:
            print(f"    [WARN] 前室切分失败: {e}")

        # --- T1: 公共空间 Walkable Polygon ---
        # 移到 T1.5 之后：walkable 基于裁剪后 polygon_pt 生成 → 天然在室内，
        # 无需二次裁剪；同时 T1.5 步骤中"裁 walkable"的代码因 walkable 尚未
        # 生成（NULL）自然跳过，无害。
        try:
            generate_walkable_polygons(
                data["rooms"], data["wall_segs"],
                data["stair_boxes"], data["evtr_boxes"], data["col_boxes"])

            # T1 后基于 T1.5 的 mask 二次裁剪 walkable：
            # T1.5 在 T1 之前执行，当时 walkable 尚未生成，只能裁 polygon；
            # 若 polygon 完全落在户外未被裁掉（已在上文清空），或 T1 新生成的
            # walkable 因数值误差越过 mask，此处再做一次防御性裁剪。
            _mask_after = data.get("building_mask_pt")
            if _mask_after is not None and not _mask_after.is_empty:
                n_clip_t1 = 0
                for r in data["rooms"]:
                    wp = r.get("walkable_poly_pt")
                    if wp is None or getattr(wp, "is_empty", True):
                        continue
                    clipped = _keep_walkable_pieces(
                        wp.intersection(_mask_after), 0.5)
                    if clipped is None or getattr(clipped, "is_empty", True):
                        if wp.area > 1e-3:
                            n_clip_t1 += 1
                        r["walkable_poly_pt"] = None
                    elif abs(clipped.area - wp.area) > 1e-3:
                        r["walkable_poly_pt"] = clipped
                        n_clip_t1 += 1
                    else:
                        r["walkable_poly_pt"] = clipped
                if n_clip_t1:
                    print(f"[F{floor_no}] Walkable Polygon 后裁剪: {n_clip_t1} 处")

            # 二次挖空楼梯井（含室外楼梯 bbox），杜绝 walkable 绕楼梯外溢
            n_punch = 0
            stair_obs = []
            for (x0, y0, x1, y1) in data.get("stair_boxes") or []:
                if (x1 - x0) * (y1 - y0) > 0:
                    stair_obs.append(box(x0, y0, x1, y1).buffer(0.6 / SCALE))
            for r in data["rooms"]:
                if r.get("roomType") == "staircase" and r.get("polygon_pt") is not None:
                    try:
                        stair_obs.append(r["polygon_pt"].buffer(0.4 / SCALE))
                    except Exception:
                        stair_obs.append(r["polygon_pt"])
            if stair_obs:
                stair_u = unary_union(stair_obs)
                _open_punch = set(_OPEN_ID_KEY) | {"elevator_lobby", "stair_lobby"}
                for r in data["rooms"]:
                    if r.get("roomType") not in _open_punch:
                        continue
                    wp = r.get("walkable_poly_pt")
                    if wp is None or getattr(wp, "is_empty", True):
                        continue
                    try:
                        clipped = _keep_walkable_pieces(wp.difference(stair_u), 0.5)
                        if clipped is None or getattr(clipped, "is_empty", True):
                            r["walkable_poly_pt"] = None
                            n_punch += 1
                        elif abs(clipped.area - wp.area) > 1e-3:
                            r["walkable_poly_pt"] = clipped
                            n_punch += 1
                        else:
                            r["walkable_poly_pt"] = clipped
                    except Exception:
                        pass
            n_wp = sum(1 for r in data["rooms"]
                       if r.get("roomType") in
                       (set(_OPEN_ID_KEY) | {"elevator_lobby", "stair_lobby"})
                       and r.get("walkable_poly_pt") is not None)
            print(f"[F{floor_no}] Walkable Polygon: 公共空间 {n_wp} 个"
                  + (f"（楼梯挖空 {n_punch} 处）" if n_punch else ""))
        except Exception as e:
            print(f"    [WARN] Walkable Polygon 生成失败: {e}")

        walls = []
        try:
            wall_th = estimate_wall_thickness(data["wall_segs"])
        except Exception as e:
            print(f"    [WARN] 墙厚估算失败: {e}")
            wall_th = [None] * len(data["wall_segs"])
        for i, (a, b) in enumerate(data["wall_segs"]):
            t_m = wall_th[i]
            walls.append({
                "type": "Feature",
                "id": obj_id(f"F{floor_no}", OBJ_TYPE["wall"], i + 1),
                "geometry": {"type": "LineString",
                             "coordinates": [list(pt2m(a)), list(pt2m(b))]},
                "properties": {
                    "type": "wall",
                    # 指南 §3.3：thickness 由 CAD 双线间距实测，material 按厚度反推
                    "thickness": t_m,
                    "material": wall_material_from_thickness(t_m),
                    "materialSource": ("inferred_from_thickness"
                                       if t_m is not None else None),
                    "sourceLayer": LAYER_WALL,
                },
            })
        n_th = sum(1 for t in wall_th if t is not None)
        print(f"[F{floor_no}] 墙厚配对: {n_th}/{len(wall_th)} "
              f"({n_th * 100.0 / max(1, len(wall_th)):.0f}%)")
        rooms_g, rooms_s = [], []
        for r in data["rooms"]:
            # 跳过被 T1.5 判定为完全在户外的公共空间（coords_m 已清空）
            if not r.get("coords_m"):
                continue
            # 需求⑳+1：功能房间统一 type="room"，用途落到 roomSubType；
            # 公共/设施型（走廊/楼梯/卫生间/电梯厅/管井等）保持独立 type。
            _rt = r["roomType"]
            _is_func = _rt in FUNCTIONAL_ROOM_TYPES
            _type_out = "room" if _is_func else _rt
            rooms_g.append({
                "type": "Feature",
                "id": r["id"],
                "geometry": {"type": "Polygon", "coordinates": [r["coords_m"]]},
                "properties": {"type": _type_out,
                               "roomType": _rt,
                               "roomSubType": _rt,
                               "label": r["label"], "roomId": r["id"],
                               "code": r.get("code", ""),
                               "centroid": r["centroid_m"],
                               "public": r["roomType"] in ROOM_PUBLIC_TYPES,
                               "accessible": r["roomType"] not in NON_ACCESSIBLE_TYPES,
                               "hasIndependentEntrance": r["roomType"] in INDEPENDENT_ENTRANCE_TYPES,
                               "walkablePolygon": _walkable_geojson(
                                   r.get("walkable_poly_pt")),
                               "floor": int(floor_no)},
            })
            rooms_s.append({
                "id": r["id"], "type": _type_out,
                "roomSubType": _rt if _is_func else None,
                "label": r["label"],
                "centroid": r["centroid_m"], "geometryId": r["id"],
                "public": r["roomType"] in ROOM_PUBLIC_TYPES,
                "accessible": r["roomType"] not in NON_ACCESSIBLE_TYPES,
                "hasIndependentEntrance": r["roomType"] in INDEPENDENT_ENTRANCE_TYPES,
                "floor": int(floor_no),
            })
        doors = []
        rooms_by_id = {r["id"]: r for r in data["rooms"]}
        for i, dr in enumerate(data["doors"]):
            cx, cy = pt2m(dr["center"])
            kind = dr["kind"]
            width_m = round(dr["width_pt"] * SCALE, 2)
            swing = door_swing_attributes(dr, rooms_by_id,
                                          DOOR_CIRCULATION_TYPES)
            dr["swing_attrs"] = swing      # 供拓扑层门口节点复用
            doors.append({
                "type": "Feature",
                "id": obj_id(f"F{floor_no}", OBJ_TYPE["door"], i + 1),
                "geometry": {"type": "Point", "coordinates": [cx, cy]},
                "properties": {
                    "type": "door",
                    "doorType": kind,
                    "doorSubType": DOOR_SUBTYPE.get(kind, kind),
                    "width_m": width_m,
                    "mergedCount": dr.get("merged", 1),
                    "rooms": dr["rooms"],
                    # 指南 §3.2 开向（内开/外开 + 左开/右开），由摆弧几何推导
                    "openDirection": swing["openDirection"],
                    "openDirectionSource": swing["openDirectionSource"],
                    "hingeSide": swing["hingeSide"],
                    "swingIntoRoom": swing["swingIntoRoom"],
                    # 指南 §3.2 门宽判轮椅可通行（≥0.8m）
                    "wheelchairAccessible": width_m >= 0.8,
                    # 以下图纸不可判，须现场勘测补充（指南 §6.2）
                    "hasThreshold": None,
                    "isGlass": None,
                    "isAutomatic": None,
                    "surveyRequired": list(DOOR_SURVEY_FIELDS),
                    "sourceLayer": ("window" if kind == "swing"
                                    else "DOOR_FIRE" if kind == "fire"
                                    else "window+geometry"),
                    # 防火门常开/常闭：教室/办公室↔开放空间(走廊/门厅等)=常开，其余=常闭
                    "isNormallyOpen": (_compute_fire_door_normally_open(
                        dr, rooms_by_id) if kind == "fire" else None),
                },
            })
        windows = []
        for i, wg in enumerate(data["window_groups"]):
            a, b = wg["axis"]
            windows.append({
                "type": "Feature",
                "id": obj_id(f"F{floor_no}", OBJ_TYPE["window"], i + 1),
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
                "id": obj_id(f"F{floor_no}", OBJ_TYPE["stair"], i + 1),
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
                "id": obj_id(f"F{floor_no}", OBJ_TYPE["elevator"], i + 1),
                "geometry": {"type": "Polygon", "coordinates": [coords]},
                "properties": {"type": "elevator",
                               "code": code,
                               "label": code or f"电梯{floor_no}F-{i + 1}",
                               "centroid": list(cen)},
            })
        # 需求⑱：电梯门元素（电梯井外墙窗户识别，归属对应电梯）
        # 归属用电梯元素 ID（需求⑳：归属一律用元素 ID，不用 label）。
        # ⚠️ 不能直接用 detect 阶段的 elev_index——reconcile_facilities 会按
        # 编号重排 evtr_boxes，index 会错位；改用「电梯门中心 ↔ 电梯质心最近」
        # 匹配重排后的 elevators，确保归属到正确的电梯 ID。
        elevator_doors = []
        for i, ed in enumerate(data.get("elevator_doors") or []):
            ec = list(ed["center_m"])
            best_ei, best_d = None, float("inf")
            for ei, el in enumerate(elevators):
                elc = el["properties"]["centroid"]
                d = math.hypot(ec[0] - elc[0], ec[1] - elc[1])
                if d < best_d:
                    best_d, best_ei = d, ei
            if best_ei is None:
                continue
            el_feat = elevators[best_ei]
            el_id = el_feat["id"]
            el_label = el_feat["properties"]["label"]
            elevator_doors.append({
                "type": "Feature",
                "id": obj_id(f"F{floor_no}", OBJ_TYPE["door"], i + 1),
                "geometry": {"type": "Point",
                             "coordinates": list(ed["center_m"])},
                "properties": {
                    "type": "elevator_door",
                    "doorType": "elevator",
                    "doorSubType": "elevator",
                    "width_m": ed["width_m"],
                    # 归属元素 ID（需求⑳：不用 label）
                    "rooms": [el_id],
                    "elevatorId": el_id,
                    "elevatorLabel": el_label,
                    "elevatorIndex": best_ei,
                    "axis": ed["axis_m"],
                    "wheelchairAccessible": True,
                    "surveyRequired": list(DOOR_SURVEY_FIELDS),
                    "sourceLayer": "window+elevator",
                },
            })
        columns = []
        for i, bxd in enumerate(data["col_boxes"]):
            x0, y0, x1, y1 = bxd
            corners = [pt2m((x0, y0)), pt2m((x1, y0)), pt2m((x1, y1)), pt2m((x0, y1))]
            coords = [list(c) for c in corners] + [list(corners[0])]
            columns.append({
                "type": "Feature",
                "id": obj_id(f"F{floor_no}", OBJ_TYPE["column"], i + 1),
                "geometry": {"type": "Polygon", "coordinates": [coords]},
                "properties": {"type": "column", "sourceLayer": "COLUMN"},
            })

        # --- 风险节点（指南 §6.3）---
        # 楼梯口 r=10；电梯口 r=1（§5.3 电梯边同值）；
        # 外开门 r=2（门扇扫入走廊，视障不可预判，属图纸可推导的风险）；
        # 玻璃门 r=5 / 自动门 r=3 图纸不可判，列入 surveyRequired 待现场补录。
        risk_nodes = [{
            "id": obj_id(f"F{floor_no}", OBJ_TYPE["stair_risk"], i + 1),
            "type": "stair_entrance",
            "riskLevel": 10, "label": s["properties"]["label"],
            "coordinates": s["properties"]["centroid"],
        } for i, s in enumerate(stairs)]
        for i, e in enumerate(elevators):
            risk_nodes.append({
                "id": f"F{floor_no}-ER-{i + 1:04d}",
                "type": "elevator_entrance",
                "riskLevel": 1, "label": e["properties"]["label"],
                "coordinates": e["properties"]["centroid"],
            })
        _od = 0
        for d in doors:
            if d["properties"].get("openDirection") != "outward":
                continue
            _od += 1
            risk_nodes.append({
                "id": f"F{floor_no}-DR-{_od:04d}",
                "type": "outward_door",
                "riskLevel": 2,
                "label": f"外开门（{d['properties'].get('hingeSide') or '?'}）",
                "coordinates": d["geometry"]["coordinates"],
                "doorId": d["id"],
            })
        a11y_elevators = [{
            "id": obj_id(f"F{floor_no}", OBJ_TYPE["elev_a11y"], i + 1),
            "label": e["properties"]["label"],
            "coordinates": e["properties"]["centroid"], "floor": int(floor_no),
        } for i, e in enumerate(elevators)]

        # --- 拓扑层（指南 第五章）：节点三类（room/intersection/doorway/facility）+ 边
        doors_for_topo = []
        rooms_by_id_for_topo = {r["id"]: r for r in data["rooms"]}
        for i, dr in enumerate(data["doors"]):
            cx, cy = pt2m(dr["center"])
            sw = dr.get("swing_attrs") or {}
            doors_for_topo.append({
                "id": obj_id(f"F{floor_no}", OBJ_TYPE["door"], i + 1),
                "center_m": [cx, cy],
                "kind": dr["kind"],
                "width_pt": dr["width_pt"],
                "rooms": dr["rooms"],
                "openDirection": sw.get("openDirection"),
                "hingeSide": sw.get("hingeSide"),
                "isNormallyOpen": (_compute_fire_door_normally_open(
                    dr, rooms_by_id_for_topo)
                    if dr["kind"] == "fire" else None),
            })
        # 额外 facility_entrance 节点：仅真正公共出入口/门厅等。
        # 合班教室是封闭大教室，不进 extra_nodes（避免当公共入口）。
        extra_nodes = []
        used_labels = {r["label"] for r in data["rooms"]}
        _PUBLIC_EXTRA_KW = ("门厅", "出入口", "传达", "前台")
        # 明确排除的封闭空间关键词（即使未匹配到房间多边形也不当公共入口）
        _ENCLOSED_EXTRA_SKIP = ("合班", "教室", "图书", "资料室", "办公室",
                                "会议室", "实验室", "卫生间", "楼梯", "电梯")
        for entry in data.get("labels_all_with_pt", []):
            label, pt_pt = entry[0], entry[1]
            if label in used_labels:
                continue
            if any(k in label for k in _ENCLOSED_EXTRA_SKIP):
                continue
            if not any(k in label for k in _PUBLIC_EXTRA_KW):
                continue
            cx, cy = pt2m(pt_pt)
            extra_nodes.append({
                "label": label,
                "coordinates": [cx, cy],
                "facilityType": "accessible_entrance" if "无障碍" in label
                                else "entrance",
            })
        skeleton_fc = {"type": "FeatureCollection", "features": []}
        walkable_fc = {"type": "FeatureCollection", "features": []}
        # 收集 walkable（米制）供骨架
        walkable_by_rid = {}
        for r in data["rooms"]:
            wp = r.get("walkable_poly_pt")
            if wp is None or getattr(wp, "is_empty", True):
                continue
            # pt → m
            def _pt_poly_to_m(g):
                if g is None or g.is_empty:
                    return g
                if g.geom_type == "Polygon":
                    ext = [list(pt2m((x, y))) for x, y in g.exterior.coords]
                    # shapely 2.x 的 g.interiors 是 LinearRing，需 .coords 迭代
                    holes = [[list(pt2m((x, y))) for x, y in ring.coords]
                             for ring in g.interiors]
                    return Polygon(ext, holes)
                if g.geom_type == "MultiPolygon":
                    return MultiPolygon([_pt_poly_to_m(p) for p in g.geoms])
                return g
            wp_m = _pt_poly_to_m(wp)
            walkable_by_rid[r["id"]] = wp_m
            walkable_fc["features"].append({
                "type": "Feature",
                "id": r["id"] + "-WP",
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [
                        [list(c) for c in wp_m.exterior.coords]
                    ] if wp_m.geom_type == "Polygon" else [],
                },
                "properties": {
                    "type": "walkable",
                    "roomId": r["id"],
                    "roomType": r.get("roomType"),
                    "label": r.get("label"),
                },
            })

        use_sk = bool(USE_SKELETON and _HAS_SKELETON and walkable_by_rid)
        if use_sk:
            try:
                topo = build_skeleton_topology(
                    int(floor_no), data["rooms"], doors_for_topo,
                    stairs, elevators,
                    walkable_by_room_id=walkable_by_rid,
                    extra_nodes=extra_nodes,
                    resolution=SKELETON_RESOLUTION,
                    obj_type=OBJ_TYPE,
                    manual_skeleton=_load_manual_skeleton().get(str(floor_no)),
                )
                nodes = topo["nodes"]
                edges = topo["edges"]
                skeleton_fc["features"] = topo.get("skeleton_features") or []
                meta = topo.get("skeleton_meta") or {}
                print(f"[F{floor_no}] 骨架拓扑: TI={meta.get('junction_count',0)} "
                      f"段={meta.get('segment_count',0)} "
                      f"节点={len(nodes)} 边={len(edges)}")
            except Exception as e:
                print(f"    [WARN] 骨架拓扑失败，回退质心拓扑: {e}")
                topo = build_floor_topology(
                    floor_no, data["rooms"], doors_for_topo,
                    stairs, elevators, extra_nodes=extra_nodes)
                nodes, edges = topo["nodes"], topo["edges"]
        else:
            if USE_SKELETON and not _HAS_SKELETON:
                print(f"[F{floor_no}] 未找到 skeleton 包，使用质心拓扑")
            topo = build_floor_topology(
                floor_no, data["rooms"], doors_for_topo,
                stairs, elevators, extra_nodes=extra_nodes)
            nodes, edges = topo["nodes"], topo["edges"]

        # 需求⑱：电梯门 TD 节点（电梯井外墙窗户识别）接入拓扑
        nodes, edges = attach_elevator_door_nodes(
            nodes, edges, elevator_doors, elevators, int(floor_no))

        # 节点级风险等级（指南 §6.3）——骨架/质心两条拓扑路径统一施加
        assign_node_risk_levels(nodes, data["rooms"])

        return {
            "geometry": {
                "walls": walls, "rooms": rooms_g, "doors": doors,
                "stairs": stairs, "elevators": elevators, "columns": columns,
                "windowSegments": windows,
                "elevatorDoors": elevator_doors,
            },
            "semantic": {"rooms": rooms_s},
            "topology": {"nodes": nodes, "edges": edges},
            "skeleton": skeleton_fc,
            "walkable_regions": walkable_fc,
            "accessibility": {
                "elevators": a11y_elevators,
                "riskNodes": risk_nodes,
                # 以下三层本套图纸无对应 CAD 图层（无无障碍设计专篇），
                # 按指南 §6.2 属必须现场勘测补充的要素，保持空数组并声明来源。
                "ramps": [], "tactilePaths": [], "groundMaterialChanges": [],
                "surveyRequired": {
                    "reason": "本套施工图无无障碍设计专篇图层（坡道/盲道/地面材质均未标注）",
                    "pendingLayers": ["ramps", "tactilePaths",
                                      "groundMaterialChanges"],
                    "pendingNodeAttributes": ["isGlass(玻璃门 r=5)",
                                              "isAutomatic(自动门 r=3)",
                                              "hasThreshold(门槛高度)"],
                    "guideRef": "docs/03-地图构建指南.md §6.2 / §七",
                },
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

        ns1 = len(f1.get("stair_boxes", []))
        ns2 = len(f2.get("stair_boxes", []))
        for kind, key, blind_ok in (
                ("staircase", "stair_boxes", False),
                ("elevator", "evtr_boxes", True)):
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
                # 拓扑设施节点顺序：先楼梯(1..ns)后电梯(ns+1..)，引用对应 TF 编号
                if kind == "staircase":
                    nid_src = obj_id("F1", OBJ_TYPE["topo_facility"], i + 1)
                    nid_dst = obj_id("F2", OBJ_TYPE["topo_facility"], best + 1)
                else:
                    nid_src = obj_id("F1", OBJ_TYPE["topo_facility"], ns1 + i + 1)
                    nid_dst = obj_id("F2", OBJ_TYPE["topo_facility"], ns2 + best + 1)
                edges.append({
                    "id": obj_id("FX", OBJ_TYPE["cross_edge"], len(edges) + 1),
                    "code": code,
                    "from": nid_src,
                    "to": nid_dst,
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
        "generator": "src/parsing/parse_cad_pdf.py",
        "notes": "仅解析 PDF 默认开启图层；window 已剔除标识矢量笔画；"
                 "门洞分三类——摆弧门(swing, window 层)、防火门(fire, DOOR_FIRE)、"
                 "无摆弧开口(opening, 墙缝几何+window 矢量编号块确认)；"
                 "拓扑图按指南第五章规范构建（room/doorway/intersection/facility）。",
        "idConvention": "统一对象编号：F{floor}-{TYPE_ABBR}-{seq:04d}；"
                        "楼层 F1/F2，跨层 FX；缩写见 topology.OBJ_TYPE"
                        "(W墙/RM房间/D门/ST楼梯/EL电梯/C柱/WN窗段/"
                        "TR拓扑房间节点/TD拓扑门口节点/TI拓扑交叉口节点/"
                        "TF拓扑设施节点/TEN拓扑出入口节点/TE拓扑边/"
                        "SR楼梯风险节点/EA无障碍电梯节点/XE跨层边)。",
        "floors": {
            "1": floor_block("1", f1),
            "2": floor_block("2", f2),
        },
        "crossFloorEdges": cross_floor_edges(f1, f2),
    }


def main(argv=None):
    import argparse
    global USE_SKELETON
    ap = argparse.ArgumentParser(description="PathAI CAD PDF → GeoJSON")
    ap.add_argument("--use-skeleton", dest="use_skeleton", action="store_true",
                    default=None, help="启用中轴骨架拓扑（默认看 USE_SKELETON）")
    ap.add_argument("--no-skeleton", dest="no_skeleton", action="store_true",
                    help="禁用骨架拓扑，使用质心最近邻")
    ap.add_argument("--no-manual-skeleton", dest="no_manual_skeleton",
                    action="store_true",
                    help="忽略手绘骨架 JSON，强制走自动中轴骨架")
    args = ap.parse_args(argv)
    if args.no_skeleton:
        USE_SKELETON = False
    elif args.use_skeleton:
        USE_SKELETON = True
    if args.no_manual_skeleton:
        global MANUAL_SKELETON
        MANUAL_SKELETON = {}

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
