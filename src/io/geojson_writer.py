# -*- coding: utf-8 -*-
"""GeoJSON 组装输出（B1 拆分自 src/parsing/parse_cad_pdf.py）。

迁移自 parse_cad_pdf.py 的 GeoJSON 组装链与相关常量：
    _read_manual_skeleton / estimate_wall_thickness /
    wall_material_from_thickness / _keep_walkable_pieces / _walkable_geojson /
    _resolve_open_closed_overlaps / generate_walkable_polygons /
    _lobby_largest_poly / _make_lobby_room / split_lobby_pockets /
    reconcile_facilities / _compute_fire_door_normally_open / build_geojson

共享常量 FUNCTIONAL_ROOM_TYPES/_OPEN_ID_KEY 在 src/semantics/room_types.py，
LAYER_WALL 在 src/parsing/pdf_layers.py（解析与 IO 模块共用，避免循环导入）。
"""
import json
import math
from pathlib import Path

from shapely.geometry import LineString, MultiPolygon, Polygon, box
from shapely.ops import unary_union
from shapely.strtree import STRtree

# 全局常量唯一来源（比例/原点/路由规则，见 docs/code-review-2026-08-12.md D1-D4）
from src.common.constants import (DOOR_DEFAULT_PENALTY, DOOR_PENALTY,
                                  ORIGIN_X, ORIGIN_Y, SCALE)
# 图纸级配置（B5/D6 外置）：场馆元信息 + 图签区坐标，可经 CLI/实例覆盖
from src.common.drawing_config import DrawingConfig
# 复用建筑外轮廓提取（栅格化+弥合门洞+外部泛洪+Moore 追踪），
# 用于 walkable 沿外墙裁剪，避免走廊多边形延伸到户外紧贴墙体的位置。
from src.geometry.contour import building_outline
# B1：几何工具下沉到 src/geometry/geo_utils.py
from src.geometry.geo_utils import (_point_line_distance, angle_diff, pt2m,
                                    seg_angle, seg_len)
# B10：穿墙几何判定唯一来源（C4：routeExtras 计算复用，渲染端不再重算）
from src.geometry.segments import segments_properly_cross
# B1：墙体图层名共享常量下沉到 src/parsing/pdf_layers.py
from src.parsing.pdf_layers import LAYER_WALL
from src.semantics.door_swing import door_swing_attributes
# B1：共享常量（开放空间 id 前缀 / 功能房间类型）下沉到 src/semantics/room_types.py
from src.semantics.room_types import FUNCTIONAL_ROOM_TYPES, _OPEN_ID_KEY
from src.semantics.stair_elevator import (
    STAIR_MAX_ASPECT, STAIR_MAX_ASPECT_CODED, attach_elevator_door_nodes)
# 拓扑建模（指南 第五章）
from src.topology import (OBJ_TYPE, assign_node_risk_levels,
                          build_floor_topology, obj_id)

try:
    from src.skeleton.pipeline import build_skeleton_topology
    _HAS_SKELETON = True
except ImportError:
    _HAS_SKELETON = False
    build_skeleton_topology = None  # type: ignore


# ---------------------------------------------------------------- 配置

# D5 跨层边魔法数字（审查 D5）：抽成命名常量，注明单位与语义。
# 楼梯/电梯跨层边（crossFloorEdges）统一取值，与 validate_geojson 的断言一致。
CROSS_FLOOR_DISTANCE_M = 4.2        # 跨层几何距离（米，占位值，不参与寻路）
CROSS_FLOOR_EST_TIME = {"staircase": 60.0, "elevator": 15.0}   # 跨层预估时间（秒）
CROSS_FLOOR_ACCESS = {"staircase": 999, "elevator": 0}   # 可达等级：999=含楼梯对视障禁用
CROSS_FLOOR_RISK = {"staircase": 10, "elevator": 1}      # 风险等级：10=楼梯口

# 路径自动适配：以本文件位置推导项目根目录，不依赖固定盘符/路径
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
RESULT_DIR = _PROJECT_ROOT / "result"
# 手绘骨架 JSON：由 src/import_manual_skeleton.py 导出。存在时优先于自动中轴骨架，
# 仅替代 TI 节点 / TI-TI 边 / 骨架线；TR/TD/TF/TEN 仍自动生成并挂接到手动 TI。
MANUAL_SKELETON_PATH = str(RESULT_DIR / "skeleton_manual_parsed.json")
# build_geojson 的 cfg 默认实例：场馆元信息用外置默认值（B5）
DEFAULT_CONFIG = DrawingConfig()


def _read_manual_skeleton():
    """读取整楼手绘骨架 JSON；文件不存在/解析失败返回 {}（视为无手动骨架）。

    审查 B4：从模块级全局缓存（_load_manual_skeleton + MANUAL_SKELETON）改为
    每次调用独立读取，消除「多次调用 build_geojson 行为不确定」与
    「热更新 JSON 需重启进程」两类问题。
    """
    p = Path(MANUAL_SKELETON_PATH)
    if not p.exists():
        print(f"[manual-skeleton] 未找到 {MANUAL_SKELETON_PATH}，使用自动中轴骨架")
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        print(f"[manual-skeleton] 已加载手绘骨架: {MANUAL_SKELETON_PATH} "
              f"(楼层: {list(data.keys())})")
        return data
    except Exception as e:
        print(f"[WARN] 手绘骨架 JSON 解析失败，回退自动骨架: {e}")
        return {}

# 比例尺/原点已统一到 src/common/constants.py（SCALE/ORIGIN_X/ORIGIN_Y），
# 校准依据见该模块注释：轴网 8400mm = 158.8pt，与窗编号 M2GW5924 互证。

# Phase2+ 骨架导航管线开关（T3–T8）。True 时用中轴拓扑替代质心最近邻。
USE_SKELETON = True
SKELETON_RESOLUTION = 0.08  # 米/像素；0.05 更精但更慢


def set_use_skeleton(flag):
    """骨架管线开关（替代原 main() 的 global USE_SKELETON 赋值）。"""
    global USE_SKELETON
    USE_SKELETON = bool(flag)


# --- 楼梯/电梯井编号空间容差（编号文本到设施 bbox 的最大容差(米)）---
FACILITY_CODE_NEAR_M = 2.0

# 公共空间（指南 4.2：卫生间/楼梯间/电梯间/走廊为公共，大厅/出入口/无障碍出入口也属于公共）
ROOM_PUBLIC_TYPES = {
    "toilet", "staircase", "elevator_hall", "corridor", "lobby",
    "entrance", "accessible_entrance", "atrium",
    "elevator_lobby", "stair_lobby",
}

# 无障碍可达性（指南 4.2：楼梯对视障禁用）
NON_ACCESSIBLE_TYPES = {"staircase", "infrastructure"}

# 拓扑建模时是否需要从走廊接入（指南 4.2：是否有独立出入口）
# 走廊/门厅/大厅/出入口/楼梯/电梯厅均视为公共接入点，其他房间通过门接入走廊
INDEPENDENT_ENTRANCE_TYPES = {
    "corridor", "lobby", "entrance", "accessible_entrance",
    "staircase", "elevator_hall", "atrium",
    "elevator_lobby", "stair_lobby",
}

# ---- 墙体厚度与材质（指南 §3.1「CAD 中墙体是双线表示」/ §3.3 thickness+material）

WALL_THICKNESS_MIN_M = 0.06      # 小于此值视为同一条线的重复描边
WALL_THICKNESS_MAX_M = 0.60      # 大于此值不再是同一道墙的两个墙面
WALL_PAIR_ANGLE_TOL = math.radians(5)
WALL_PAIR_OVERLAP_RATIO = 0.30   # 两线沿轴向的重叠比例下限

# ------------------------------------------------------------ 门属性（指南 §3.2）

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

# 防火门常开判定用：功能房间类型 + 公共/开放空间类型
_FIRE_OPEN_FUNC = FUNCTIONAL_ROOM_TYPES | {"room"}
_FIRE_OPEN_PUBLIC = ROOM_PUBLIC_TYPES | {"elevator_lobby", "stair_lobby",
                                          "entrance", "accessible_entrance"}


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


def compute_route_rule_extras(geo):
    """为前端 Dijkstra 预计算路由规则辅助量（C4：随 build_geojson 一次性写入
    顶层 routeExtras，渲染端直接读取，不再每次渲染 O(边×墙) 重算）。

    返回 dict：
    - edge_door_type: edge_id -> doorType(str|None)，从门节点推导；
    - room_best_door: room 节点 id -> 该房间最高优先级门类型(swing>fire>opening)；
    - wall_crossing_titi: 两端均为 intersection 且直线段真正穿墙的 TI<->TI 边 id 集合；
    - infra_doorway_ids: 归属全部为 infrastructure 的门节点 id 集合（纯管井门，规则 5）。

    与 route_rules.py 规则同源（常量唯一来源 src/common/constants.py）；
    穿墙判定复用 geometry/segments.segments_properly_cross（B10 收敛）。
    """
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
            # 楼层限定键：F1/F2 各自独立边编号（E000005 在两层都有）
            edge_door_type_map[f"{fk}:{e['id']}"] = edge_door_type(e)

    # 规则 5：归属全为 infrastructure 的门（纯管井门）→ 前端 Dijkstra 同步剔除
    room_id_to_type = {}
    for n in node_by_id.values():
        if n.get("type") == "room":
            room_id_to_type[n.get("roomId") or n["id"]] = n.get("roomType")
    infra_doorway_ids = set()
    for n in node_by_id.values():
        if n.get("type") != "doorway":
            continue
        rids = n.get("rooms") or []
        if rids and all(room_id_to_type.get(r) == "infrastructure" for r in rids):
            infra_doorway_ids.add(n["id"])

    # 房间最佳门类型（每间房取优先级最高的门）
    # 常开防火门与普通门平等对待（penalty=0）
    best_door = {}
    for n in node_by_id.values():
        if n.get("type") == "doorway":
            for rid in (n.get("rooms") or []):
                t = n.get("doorType")
                # 常开防火门：视为 swing 同级（penalty=0）
                p = 0.0 if (t == "fire" and n.get("isNormallyOpen")) else DOOR_PENALTY.get(t, DOOR_DEFAULT_PENALTY)
                cur = best_door.get(rid)
                cur_p = DOOR_PENALTY.get(cur, DOOR_DEFAULT_PENALTY) if cur else None
                if cur is None or p < cur_p:
                    best_door[rid] = t
    room_best_door = {}
    for n in node_by_id.values():
        if n.get("type") == "room":
            rid = n.get("roomId") or n["id"]
            if rid in best_door:
                room_best_door[n["id"]] = best_door[rid]

    # 穿墙 TI<->TI 边集合（按楼层隔离：F1/F2 投影坐标重叠，跨层墙不得参与判定）
    wall_lines = []
    for fk, fd in geo["floors"].items():
        for w in (fd.get("geometry", {}) or {}).get("walls", []):
            g = w.get("geometry", {})
            if g.get("type") == "LineString" and len(g.get("coordinates", [])) >= 2:
                cs = g["coordinates"]
                wall_lines.append((fk, tuple(cs[0]), tuple(cs[-1])))
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
            for (wf, A, B) in wall_lines:
                if wf != fk:
                    continue  # 跨层墙不参与本层穿墙判定
                if segments_properly_cross(ca, cb, A, B):
                    wall_crossing_titi.add(f"{fk}:{e['id']}")
                    break

    return {
        "edge_door_type": edge_door_type_map,
        "room_best_door": room_best_door,
        # set → 排序 list：保证 JSON 序列化字节确定（跨进程/跨运行一致）
        "wall_crossing_titi": sorted(wall_crossing_titi),
        "infra_doorway_ids": sorted(infra_doorway_ids),
    }


def build_geojson(f1, f2, cfg=None, manual_skeleton=None):
    """组装完整 GeoJSON（拓扑 + walkable + skeleton 等）。

    cfg: Optional[DrawingConfig]——图纸级配置（审查 B5/D6）：场馆元信息、
        图签区坐标等；None 时用 DEFAULT_CONFIG（外置默认值）。
    manual_skeleton: Optional[dict]——整楼手绘骨架（键为楼层号字符串）：
        None=默认按 MANUAL_SKELETON_PATH 从文件读取（无进程级缓存，审查 B4）；
        {} = 明确禁用（等价 --no-manual-skeleton）；dict = 直接使用注入数据。
    """
    if cfg is None:
        cfg = DEFAULT_CONFIG
    if manual_skeleton is None:
        manual_skeleton = _read_manual_skeleton()
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
                    manual_skeleton=manual_skeleton.get(str(floor_no)),
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
                    "distance": CROSS_FLOOR_DISTANCE_M,
                    "estimatedTime": CROSS_FLOOR_EST_TIME[kind],
                    "accessibilityLevel": CROSS_FLOOR_ACCESS[kind],
                    "riskLevel": CROSS_FLOOR_RISK[kind],
                    "walkable": True,
                    "wheelchairAccessible": blind_ok,
                    "blindAccessible": blind_ok,
                })
        return edges

    geo = {
        "venueId": cfg.venue_id,
        "venueName": cfg.venue_name,
        "version": cfg.version,
        "coordinateSystem": "local_meters",
        "scale": SCALE,
        "origin": {"x": ORIGIN_X, "y": ORIGIN_Y, "unit": "pt"},
        "generator": "src/io/geojson_writer.py",
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
    # C4：路由规则辅助量随构建一次性写入（渲染端直接读取，不再重算）
    geo["routeExtras"] = compute_route_rule_extras(geo)
    return geo
