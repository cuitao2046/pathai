# -*- coding: utf-8 -*-
"""
信标现场安装放样卡生成器（A4 可打印 HTML + 回填 CSV + 反算校验）。

解决的问题：现场无法直接得到设计坐标（米），需要一套**只用既有特征**（门洞中心、
墙角、柱角、楼梯/电梯口）的可执行参考系，逐枚信标量出安装位置。

两种放样方式（按信标所处空间自动选择）：
  - room_two_dist（房间/门厅内）双距交会：从两个基准点各量一段水平距离，
    两弧交点即信标地面投影；再用第三个基准点直量校核。
  - corridor_chain（走廊贴墙）链式：从一个墙面基准点沿墙量 L，再垂直离墙量 o，
    即得投影点；直量 sqrt(L^2+o^2) 校核。
  - door_anchor（信标就在门洞中心 0.25 m 内）直接以门洞中心为基准。

约束：
  - 所有距离均为**水平距离**（卷尺拉平），测量距离 ≤ 8 m；
  - 基准点到信标的连线不得穿墙（LineString.crosses 判定，避开贴墙共线误判）；
  - 反算偏差 <0.3 m 可用 / 0.3~0.5 m 局部复核 / >0.5 m 停工重测。

用法（仓库根目录）：
  python src/tools/gen_beacon_install_cards.py
  python src/tools/gen_beacon_install_cards.py --site school-building-01 --version 2026-10-30.1
  python src/tools/gen_beacon_install_cards.py --verify result/beacon_install_survey.csv
  python src/tools/gen_beacon_install_cards.py --selfcheck

输出：
  result/beacon_install_cards.html       A4 安装卡（每页 4 张）
  result/beacon_install_survey.csv      现场回填表（预填设计值）
  result/beacon_install_verify.csv      --verify 的反算报告
"""
from __future__ import annotations

import argparse
import csv
import html
import json
import math
import sys
from dataclasses import dataclass, field
from pathlib import Path

import segno
from shapely.geometry import LineString, Point, Polygon, MultiPolygon, box
from shapely.strtree import STRtree

BASE_DIR = Path(__file__).resolve().parents[2]
MAP_DIR = BASE_DIR / "backend" / "maps_data"
RESULT_DIR = BASE_DIR / "result"

MAX_MEASURE = 8.0          # 卷尺量程（米）
MIN_MEASURE = 0.5          # 过近基准点误差大
SHRINK = 0.18              # 可见性判定时线段两端收缩量（避开贴墙共线）
DOOR_ANCHOR_TOL = 0.25     # 距门洞中心该值内直接以门洞为基准
WALL_SEARCH = 1.0          # 链式放样允许的最大离墙距离
WALL_SNAP = 0.15           # 离墙量小于该值视为贴墙安装（不量 o）
MIN_ALONG = 0.5            # 链式放样沿墙最短量距（过短锚点退化，不可执行）
CHECK_MIN = 1.0            # 校核点最小量距（过近同一构件无校核意义）
THRESH_OK, THRESH_WARN = 0.3, 0.5

METHOD_LABEL = {
    "room_two_dist": "房间双距交会",
    "corridor_chain": "走廊链式（沿墙+离墙）",
    "door_anchor": "门洞中心基准",
    "fallback": "人工复核",
}


def _fail(msg: str) -> None:
    print(f"[ERROR] {msg}", file=sys.stderr)
    raise SystemExit(1)


# ---------------------------------------------------------------- 数据加载
def load_site(site: str, version: str) -> dict:
    site_dir = MAP_DIR / site
    if not site_dir.is_dir():
        _fail(f"场所地图目录不存在：{site_dir}")
    if version == "latest":
        candidates = []
        for file in sorted(site_dir.glob("*.json")):
            data = json.loads(file.read_text(encoding="utf-8"))
            key = (data.get("publishedAt", ""), data.get("mapVersion", file.stem))
            candidates.append((key, data))
        if not candidates:
            _fail(f"场所无已发布地图包：{site_dir}")
        return max(candidates, key=lambda item: item[0])[1]
    file = site_dir / f"{version}.json"
    if not file.is_file():
        _fail(f"地图包不存在：{file}")
    return json.loads(file.read_text(encoding="utf-8"))


def _polygon(feature: dict):
    g = feature["geometry"]
    if g["type"] == "Polygon":
        return Polygon(g["coordinates"][0], g["coordinates"][1:])
    return MultiPolygon([(c[0], c[1:]) for c in g["coordinates"]])


def _exterior_rings(geom):
    if geom.geom_type == "Polygon":
        return [geom.exterior]
    return [p.exterior for p in geom.geoms]


def _turn_deg(a, b, c) -> float:
    v1 = (a[0] - b[0], a[1] - b[1])
    v2 = (c[0] - b[0], c[1] - b[1])
    ang = math.degrees(math.atan2(v1[0] * v2[1] - v1[1] * v2[0],
                                  v1[0] * v2[0] + v1[1] * v2[1]))
    return abs(ang)


def corners(geom, min_turn: float = 20.0) -> list[tuple[float, float]]:
    """返回多边形显著转角顶点（过滤共线点）。"""
    out = []
    for ring in _exterior_rings(geom):
        pts = list(ring.coords)[:-1]
        n = len(pts)
        if n < 3:
            continue
        for i in range(n):
            if _turn_deg(pts[i - 1], pts[i], pts[(i + 1) % n]) >= min_turn:
                out.append((pts[i][0], pts[i][1]))
    return out


def compass(dx: float, dy: float, diag: float = 0.35) -> str:
    """向量方位描述：东/西/南/北/东北…"""
    if abs(dx) < 1e-9 and abs(dy) < 1e-9:
        return ""
    ang = math.degrees(math.atan2(dy, dx))
    names = ["东", "东北", "北", "西北", "西", "西南", "南", "东南"]
    idx = int(round(ang / 45.0)) % 8
    if abs(dx) > 1e-9 and abs(dy) > 1e-9 and abs(dx) < diag * abs(dy):
        return names[2] if dy > 0 else names[6]
    if abs(dy) > 1e-9 and abs(dx) > 1e-9 and abs(dy) < diag * abs(dx):
        return names[0] if dx > 0 else names[4]
    return names[idx]


def fmt(v: float, nd: int = 2) -> str:
    return f"{v:.{nd}f}"


@dataclass
class Ref:
    rid: str
    kind: str          # door / elevator_door / room_corner / column_corner
    x: float
    y: float
    desc: str
    priority: int      # 0 门洞 / 1 墙角 / 2 柱角


@dataclass
class Room:
    rid: str
    rtype: str
    label: str
    code: str
    poly: object
    centroid: tuple[float, float]

    @property
    def is_corridor(self) -> bool:
        return self.rtype == "corridor"

    @property
    def display(self) -> str:
        name = self.label or self.rid
        if self.code:
            return f"{name} {self.code}"
        return f"{name}（{self.rid}）"


@dataclass
class FloorModel:
    floor: str
    rooms: list[Room] = field(default_factory=list)
    walls: list[LineString] = field(default_factory=list)
    wall_tree: object = None
    refs: list[Ref] = field(default_factory=list)

    def visible(self, a, b, shrink: float = SHRINK) -> bool:
        """a→b 连线是否不被墙体横穿（收缩两端，避免贴墙共线误判）。"""
        seg = LineString([a, b])
        length = seg.length
        if length <= 2 * shrink + 0.05:
            return True
        s = LineString([seg.interpolate(shrink), seg.interpolate(length - shrink)])
        for idx in self.wall_tree.query(s):
            if s.crosses(self.walls[idx]):
                return False
        return True

    def room_at(self, pt) -> Room | None:
        buf = pt.buffer(0.08)
        hit = [r for r in self.rooms if r.poly.intersects(buf)]
        if not hit:
            return None
        non_corr = [r for r in hit if not r.is_corridor]
        return non_corr[0] if non_corr else hit[0]

    def nearest_room(self, pt) -> Room | None:
        return min(self.rooms, key=lambda r: r.poly.distance(pt)) if self.rooms else None


def build_floor(floor: str, geo: dict) -> FloorModel:
    model = FloorModel(floor=floor)
    for feat in geo.get("rooms") or []:
        props = feat.get("properties") or {}
        geom = _polygon(feat)
        c = props.get("centroid") or [geom.centroid.x, geom.centroid.y]
        model.rooms.append(Room(
            rid=props.get("roomId") or feat.get("id", ""),
            rtype=props.get("roomType") or "",
            label=props.get("label") or "",
            code=props.get("code") or "",
            poly=geom,
            centroid=(c[0], c[1]),
        ))
    for feat in geo.get("walls") or []:
        model.walls.append(LineString(feat["geometry"]["coordinates"]))
    model.wall_tree = STRtree(model.walls)

    room_name = {r.rid: r.display for r in model.rooms}

    # 基准点 1：门洞中心（含电梯门）
    for feat in geo.get("doors") or []:
        props = feat.get("properties") or {}
        xy = feat["geometry"]["coordinates"]
        rooms = props.get("rooms") or []
        names = " / ".join(room_name.get(r, r) for r in rooms)
        width = props.get("width_m")
        desc = f"门洞中心 {feat['id']}"
        if width:
            desc += f"（宽 {width:.2f} m"
            desc += f"，连接 {names}）" if names else "）"
        elif names:
            desc += f"（连接 {names}）"
        model.refs.append(Ref(feat["id"], "door", xy[0], xy[1], desc, 0))
    for feat in geo.get("elevatorDoors") or []:
        props = feat.get("properties") or {}
        xy = feat["geometry"]["coordinates"]
        label = props.get("elevatorLabel") or props.get("elevatorId") or ""
        desc = f"电梯 {label} 门中心 {feat['id']}".strip()
        model.refs.append(Ref(feat["id"], "elevator_door", xy[0], xy[1], desc, 0))

    # 基准点 2：房间/门厅/前室的内角（走廊角优先级更低，单独标记）
    for room in model.rooms:
        if room.is_corridor:
            continue
        for xy in corners(room.poly):
            d = compass(xy[0] - room.centroid[0], xy[1] - room.centroid[1])
            model.refs.append(Ref(
                f"{room.rid}@{fmt(xy[0], 1)},{fmt(xy[1], 1)}",
                "room_corner", xy[0], xy[1],
                f"{room.display} {d}角", 1))

    # 基准点 3：方柱角
    for feat in geo.get("columns") or []:
        geom = _polygon(feat)
        c = geom.centroid
        for xy in corners(geom, min_turn=30.0):
            d = compass(xy[0] - c.x, xy[1] - c.y)
            model.refs.append(Ref(
                f"{feat['id']}@{fmt(xy[0], 1)},{fmt(xy[1], 1)}",
                "column_corner", xy[0], xy[1],
                f"方柱 {feat['id']} {d}角", 2))
    return model


# ---------------------------------------------------------------- 放样方案
@dataclass
class Plan:
    beacon_id: str
    floor: str
    x: float
    y: float
    method: str
    space: str = ""
    refs: list[Ref] = field(default_factory=list)     # 两距：2 个；校核：第 3 个
    dists: list[float] = field(default_factory=list)
    check_ref: Ref | None = None
    check_d: float = 0.0
    anchor: Ref | None = None
    anchor_xy: tuple[float, float] | None = None
    along: float = 0.0
    offset: float | None = None       # None = 附近无墙面基准
    dir_vec: tuple[float, float] = (1.0, 0.0)
    nrm_vec: tuple[float, float] = (0.0, 1.0)
    note: str = ""


def _angle_between(px, py, a, b) -> float:
    v1 = (a[0] - px, a[1] - py)
    v2 = (b[0] - px, b[1] - py)
    n1 = math.hypot(*v1)
    n2 = math.hypot(*v2)
    if n1 < 1e-9 or n2 < 1e-9:
        return 0.0
    cos = max(-1.0, min(1.0, (v1[0] * v2[0] + v1[1] * v2[1]) / (n1 * n2)))
    return math.degrees(math.acos(cos))


def plan_room(px, py, model: FloorModel, cands: list[tuple[Ref, float]], space: str) -> Plan | None:
    usable = [(r, d) for r, d in cands if MIN_MEASURE <= d <= MAX_MEASURE]
    best = None
    for i in range(len(usable)):
        for j in range(i + 1, len(usable)):
            r1, d1 = usable[i]
            r2, d2 = usable[j]
            # 两基准点自身间距过小（如同一根柱子的两个角）时交会精度极差
            if math.hypot(r1.x - r2.x, r1.y - r2.y) < 1.5:
                continue
            ang = _angle_between(px, py, (r1.x, r1.y), (r2.x, r2.y))
            if not (30.0 <= ang <= 150.0):
                continue
            score = max(d1, d2) + 0.8 * (r1.priority + r2.priority) + 0.02 * abs(ang - 90.0)
            if best is None or score < best[0]:
                best = (score, r1, d1, r2, d2)
    if best is None:
        return None
    _, r1, d1, r2, d2 = best
    plan = Plan("", model.floor, px, py, "room_two_dist", space,
                refs=[r1, r2], dists=[d1, d2])
    # 第三个基准点用于直量校核（须为不同构件，且量距不过短）
    bases = {r1.rid.split("@")[0], r2.rid.split("@")[0]}
    for ref, d in usable:
        if ref is r1 or ref is r2:
            continue
        if ref.rid.split("@")[0] in bases or d < CHECK_MIN:
            continue
        if not model.visible((px, py), (ref.x, ref.y)):
            continue
        if plan.check_ref is None or d < plan.check_d:
            plan.check_ref, plan.check_d = ref, d
    return plan


def _chain_on_wall(model: FloorModel, px: float, py: float, off: float,
                   wall: LineString, space: str) -> Plan | None:
    """在指定墙面上做链式放样；锚点或沿墙量退化时返回 None。"""
    coords = list(wall.coords)
    (x1, y1), (x2, y2) = coords[0], coords[-1]
    vx, vy = x2 - x1, y2 - y1
    norm = math.hypot(vx, vy)
    if norm < 1e-6:
        return None
    ux, uy = vx / norm, vy / norm
    t_q = wall.project(Point(px, py))
    q = wall.interpolate(t_q)
    qx, qy = q.x, q.y
    # 法向：由墙面指向信标
    if off > 0.02:
        nx, ny = (px - qx) / off, (py - qy) / off
    else:
        nx, ny = -uy, ux

    # 锚点：同一面墙上的门洞中心 > 柱角，沿墙量须 ≥ MIN_ALONG
    anchors = []
    for ref in model.refs:
        if ref.kind not in ("door", "elevator_door", "column_corner"):
            continue
        if wall.distance(Point(ref.x, ref.y)) > 0.35:
            continue
        t_a = wall.project(Point(ref.x, ref.y))
        if not (-1.0 <= t_a <= wall.length + 1.0):
            continue
        along = t_a - t_q
        if abs(along) < MIN_ALONG or abs(along) > MAX_MEASURE:
            continue
        anchors.append((abs(along), ref, t_a))
    if anchors:
        anchors.sort(key=lambda item: (item[1].priority, item[0]))
        anchor, t_a = anchors[0][1], anchors[0][2]
    else:
        # 墙端点（墙角）：取离投影点较远的一端，避免沿墙量为 0
        ends = [((x1, y1), wall.project(Point(x1, y1))),
                ((x2, y2), wall.project(Point(x2, y2)))]
        (ex, ey), t_a = max(ends, key=lambda e: abs(e[1] - t_q))
        if abs(t_a - t_q) < MIN_ALONG or abs(t_a - t_q) > MAX_MEASURE:
            return None
        anchor = Ref(f"{model.floor}-WALL-END", "wall_end", ex, ey,
                     f"墙角（图示位置 {fmt(ex, 1)}, {fmt(ey, 1)}）", 3)

    # 链式放样的起点取基准特征在墙面上的投影点（卷尺沿墙面拉）
    a_pt = wall.interpolate(t_a)
    anchor_xy = (a_pt.x, a_pt.y)
    along = t_q - t_a

    plan = Plan("", model.floor, px, py, "corridor_chain", space,
                anchor=anchor, anchor_xy=anchor_xy,
                along=along, offset=off,
                dir_vec=(ux, uy), nrm_vec=(nx, ny))

    # 校核：优先取第三个可见基准点独立量距，否则退化为从起点斜量
    best = None
    anchor_base = anchor.rid.split("@")[0]
    for ref in model.refs:
        if ref is anchor or ref.rid.split("@")[0] == anchor_base:
            continue
        d = math.hypot(ref.x - px, ref.y - py)
        if d < CHECK_MIN or d > MAX_MEASURE:
            continue
        if not model.visible((px, py), (ref.x, ref.y)):
            continue
        score = d + 0.6 * ref.priority
        if best is None or score < best[0]:
            best = (score, ref, d)
    if best:
        plan.check_ref, plan.check_d = best[1], best[2]
    else:
        plan.check_d = math.hypot(along, off)
    return plan


def plan_chain(px, py, model: FloorModel, space: str) -> Plan | None:
    pt = Point(px, py)
    walls = []
    for idx in model.wall_tree.query(pt.buffer(WALL_SEARCH + 0.5)):
        wall = model.walls[idx]
        d = wall.distance(pt)
        if d <= WALL_SEARCH:
            walls.append((d, wall))
    walls.sort(key=lambda item: item[0])
    for d, wall in walls[:4]:
        plan = _chain_on_wall(model, px, py, d, wall, space)
        if plan is not None:
            return plan
    return None


def nearest_wall_offset(model: FloorModel, px: float, py: float) -> float | None:
    pt = Point(px, py)
    best = None
    for idx in model.wall_tree.query(pt.buffer(WALL_SEARCH)):
        d = model.walls[idx].distance(pt)
        if d > WALL_SEARCH:
            continue
        if best is None or d < best:
            best = d
    return best


def build_plan(beacon: dict, model: FloorModel) -> Plan:
    px, py = float(beacon["x"]), float(beacon["y"])
    pt = Point(px, py)
    wall_off = nearest_wall_offset(model, px, py)
    room = model.room_at(pt)
    space = room.display if room else (model.nearest_room(pt).display if model.rooms else "")

    cands = []
    for ref in model.refs:
        d = math.hypot(ref.x - px, ref.y - py)
        if d > MAX_MEASURE + 0.5:
            continue
        if not model.visible((px, py), (ref.x, ref.y)):
            continue
        cands.append((ref, d))
    cands.sort(key=lambda item: (item[0].priority, item[1]))

    # 1) 门洞中心近邻
    door_hit = [(r, d) for r, d in cands if r.kind in ("door", "elevator_door") and d <= DOOR_ANCHOR_TOL]
    if door_hit:
        ref, d = min(door_hit, key=lambda item: item[1])
        plan = Plan(beacon["beaconId"], model.floor, px, py, "door_anchor", space,
                    refs=[ref], dists=[d])
        plan.offset = wall_off
        plan.check_ref = next((r for r, _ in cands if r is not ref), None)
        if plan.check_ref:
            plan.check_d = math.hypot(plan.check_ref.x - px, plan.check_ref.y - py)
        plan.note = f"设计点距门洞中心 {fmt(d)} m（≤0.25 m，按门洞中心施工）"
        return plan

    # 2) 房间内双距 > 3) 走廊链式 > 4) 走廊内双距兜底
    room_plan = None
    if room is not None and not room.is_corridor:
        room_plan = plan_room(px, py, model, cands, space)
    chain_plan = plan_chain(px, py, model, space)

    if room_plan:
        plan = room_plan
    elif chain_plan:
        plan = chain_plan
    elif room is not None:
        plan = plan_room(px, py, model, cands, space)
        if plan is None:
            plan = _fallback_plan(beacon, model, px, py, space, cands)
            return plan
    else:
        plan = _fallback_plan(beacon, model, px, py, space, cands)
        return plan

    plan.beacon_id = beacon["beaconId"]
    if plan.method == "room_two_dist":
        plan.offset = wall_off
    return plan


def _fallback_plan(beacon: dict, model: FloorModel, px: float, py: float,
                   space: str, cands: list[tuple[Ref, float]]) -> Plan:
    """兜底：给最近的可见基准点，现场人工判断。"""
    plan = Plan(beacon["beaconId"], model.floor, px, py, "fallback", space)
    if cands:
        plan.refs = [cands[0][0]]
        plan.dists = [cands[0][1]]
    plan.note = "自动放样失败，请对照渲染图人工确认"
    return plan


def build_plans(pkg: dict) -> tuple[dict[str, FloorModel], list[tuple[dict, Plan]]]:
    models = {}
    for floor, fdata in (pkg.get("floors") or {}).items():
        models[str(floor)] = build_floor(str(floor), fdata.get("geometry") or {})
    plans = []
    for beacon in sorted(pkg.get("beacons") or [], key=lambda b: b["beaconId"]):
        model = models.get(str(beacon["floorId"]))
        if model is None:
            continue
        plan = build_plan(beacon, model)
        plan.beacon_id = beacon["beaconId"]
        plans.append((beacon, plan))
    return models, plans


# ---------------------------------------------------------------- 文字说明
def instructions(plan: Plan, height: float) -> list[str]:
    h = fmt(height, 1)
    if plan.method == "room_two_dist":
        lines = [
            f"① 从「{plan.refs[0].desc}」地面点，向信标方向量 <b>{fmt(plan.dists[0])} m</b>；",
            f"② 从「{plan.refs[1].desc}」地面点，量 <b>{fmt(plan.dists[1])} m</b>；"
            f"两弧交点（图中★一侧）即信标<b>地面投影点</b>；",
        ]
        if plan.check_ref:
            lines.append(f"③ 校核：从「{plan.check_ref.desc}」量 <b>{fmt(plan.check_d)} m</b>，"
                         f"与投影点偏差应 ≤0.15 m；")
        if plan.offset is None:
            lines.append(f"④ 中心高 <b>{h} m</b>（周边 1 m 内无墙面，按图示位置就位）。")
        elif plan.offset < WALL_SNAP:
            lines.append(f"④ 贴墙安装（以墙面为基准，无离墙量），中心高 <b>{h} m</b>。")
        else:
            lines.append(f"④ 贴墙安装，离墙 <b>{fmt(plan.offset)} m</b>，中心高 <b>{h} m</b>。")
        return lines
    if plan.method == "corridor_chain":
        a = plan.anchor.desc if plan.anchor else "基准点"
        along_c = compass(plan.dir_vec[0] * plan.along, plan.dir_vec[1] * plan.along)
        side = compass(plan.nrm_vec[0], plan.nrm_vec[1])
        snap = plan.offset is not None and plan.offset < WALL_SNAP
        lines = [
            f"① 从「{a}」（墙面投影点）起，沿墙面<strong>{along_c}</strong>量 <b>{fmt(abs(plan.along))} m</b>；",
        ]
        if snap:
            lines.append("② 该点即信标<b>地面投影点</b>（贴墙安装，以墙面为基准，不量离墙）；")
        else:
            lines.append(f"② 从该点垂直墙面<strong>{side}</strong>量 <b>{fmt(plan.offset)} m</b>，"
                         f"即信标<b>地面投影点</b>；")
        if plan.check_ref:
            lines.append(f"③ 校核：从「{plan.check_ref.desc}」直量 <b>{fmt(plan.check_d)} m</b>，"
                         f"与投影点偏差应 ≤0.15 m；")
        if snap:
            lines.append(f"④ 信标中心高 <b>{h} m</b>（贴墙安装）。")
        else:
            lines.append(f"④ 信标中心高 <b>{h} m</b>（贴墙安装，保持离墙 {fmt(plan.offset)} m）。")
        return lines
    if plan.method == "door_anchor":
        ref = plan.refs[0]
        return [
            f"① 以「{ref.desc}」为基准，信标安装在门洞中心正上方；",
            f"② 中心高 <b>{h} m</b>，探头朝走廊（{fmt(plan.dists[0])} m 为设计点与门洞中心偏差）；",
            f"③ 校核：从基准点直量至信标投影点，偏差应 ≤0.25 m。",
        ]
    return [
        "自动放样失败：请对照交互渲染图与设计坐标人工确认；",
        f"设计坐标：X={fmt(plan.x)} m，Y={fmt(plan.y)} m，中心高 {h} m。",
    ]


# ---------------------------------------------------------------- 局部平面图
PLAN_W, PLAN_H = 86.0, 56.0


def _star(cx: float, cy: float, r: float) -> str:
    pts = []
    for i in range(10):
        ang = math.radians(-90 + i * 36)
        rr = r if i % 2 == 0 else r * 0.42
        pts.append(f"{cx + rr * math.cos(ang):.2f},{cy + rr * math.sin(ang):.2f}")
    return " ".join(pts)


def render_plan_svg(plan: Plan, model: FloorModel) -> str:
    px, py = plan.x, plan.y
    key_pts = [(px, py)]
    for ref in plan.refs:
        key_pts.append((ref.x, ref.y))
    if plan.check_ref:
        key_pts.append((plan.check_ref.x, plan.check_ref.y))
    if plan.anchor_xy:
        key_pts.append(plan.anchor_xy)
    if plan.anchor:
        key_pts.append((plan.anchor.x, plan.anchor.y))
    if plan.method == "corridor_chain" and plan.offset is not None:
        # 沿墙投影点
        qx, qy = px - plan.nrm_vec[0] * plan.offset, py - plan.nrm_vec[1] * plan.offset
        key_pts.append((qx, qy))

    search = box(px - 7.0, py - 7.0, px + 7.0, py + 7.0)
    walls = []
    for idx in model.wall_tree.query(search):
        wall = model.walls[idx]
        if wall.distance(Point(px, py)) <= 7.0:
            walls.append(wall)
    rooms = [r for r in model.rooms if r.poly.distance(Point(px, py)) <= 7.0]

    # 视窗只由测量点决定：先按卡片可用区（扣掉四周边距）反算比例，再居中；
    # 墙/房间超出部分由 SVG 自动裁剪。
    xs = [p[0] for p in key_pts]
    ys = [p[1] for p in key_pts]
    minx, maxx, miny, maxy = min(xs), max(xs), min(ys), max(ys)
    cx0, cy0 = (minx + maxx) / 2, (miny + maxy) / 2
    w0, h0 = max(maxx - minx, 1e-6), max(maxy - miny, 1e-6)
    # 边距须容纳基准点标签外扩（3.4 mm）与信标 ID 文字（中心下 6.4 mm）
    margin = 7.5
    s = min((PLAN_W - 2 * margin) / w0, (PLAN_H - 2 * margin) / h0, 43.0)
    minx, maxx = cx0 - PLAN_W / (2 * s), cx0 + PLAN_W / (2 * s)
    miny, maxy = cy0 - PLAN_H / (2 * s), cy0 + PLAN_H / (2 * s)
    ox = oy = 0.0

    def mm(x, y):
        return ox + (x - minx) * s, oy + (maxy - y) * s

    out = [f'<svg class="plan" viewBox="0 0 {PLAN_W:.0f} {PLAN_H:.0f}" '
           f'xmlns="http://www.w3.org/2000/svg">']

    # 房间底色
    for room in rooms:
        polys = [room.poly] if room.poly.geom_type == "Polygon" else list(room.poly.geoms)
        for poly in polys:
            pts = " ".join(f"{mm(*c)[0]:.2f},{mm(*c)[1]:.2f}" for c in poly.exterior.coords)
            fill = "#f4f6f8" if room.is_corridor else "#fbfbfb"
            out.append(f'<polygon points="{pts}" fill="{fill}" stroke="#d8d8d8" stroke-width="0.2"/>')

    # 墙体
    for wall in walls:
        (x1, y1), (x2, y2) = mm(*wall.coords[0]), mm(*wall.coords[-1])
        out.append(f'<line x1="{x1:.2f}" y1="{y1:.2f}" x2="{x2:.2f}" y2="{y2:.2f}" '
                   f'stroke="#37474f" stroke-width="0.85" stroke-linecap="round"/>')

    # 尺寸线
    def dim(a, b, text, dash="2,1.2", color="#c0392b"):
        (x1, y1), (x2, y2) = mm(*a), mm(*b)
        out.append(f'<line x1="{x1:.2f}" y1="{y1:.2f}" x2="{x2:.2f}" y2="{y2:.2f}" '
                   f'stroke="{color}" stroke-width="0.35" stroke-dasharray="{dash}"/>')
        mx, my = (x1 + x2) / 2, (y1 + y2) / 2
        out.append(f'<text x="{mx:.2f}" y="{my - 1.1:.2f}" font-size="2.6" fill="{color}" '
                   f'text-anchor="middle" paint-order="stroke" stroke="#fff" stroke-width="1.3">{text}</text>')

    if plan.method in ("room_two_dist", "door_anchor"):
        labels = [f"{d:.2f}" for d in plan.dists]
        for ref, lab in zip(plan.refs, labels):
            dim((ref.x, ref.y), (px, py), lab)
        if plan.check_ref and plan.method == "room_two_dist":
            dim((plan.check_ref.x, plan.check_ref.y), (px, py), f"校{plan.check_d:.2f}", "1,1", "#2e7d32")
    elif plan.method == "corridor_chain" and plan.anchor_xy and plan.offset is not None:
        ax, ay = plan.anchor_xy
        qx, qy = px - plan.nrm_vec[0] * plan.offset, py - plan.nrm_vec[1] * plan.offset
        dim((ax, ay), (qx, qy), f"L={abs(plan.along):.2f}")
        if plan.offset >= WALL_SNAP:
            dim((qx, qy), (px, py), f"o={plan.offset:.2f}")
        if plan.check_ref:
            dim((plan.check_ref.x, plan.check_ref.y), (px, py),
                f"校{plan.check_d:.2f}", "1,1", "#2e7d32")
        out.append(f'<circle cx="{mm(qx, qy)[0]:.2f}" cy="{mm(qx, qy)[1]:.2f}" r="0.9" '
                   f'fill="#c0392b"/>')
        # 基准点与墙面投影点
        cx, cy = mm(plan.anchor.x, plan.anchor.y)
        color = "#1565c0" if plan.anchor.kind in ("door", "elevator_door") else "#6a1b9a"
        out.append(f'<circle cx="{cx:.2f}" cy="{cy:.2f}" r="1.5" fill="{color}"/>')
        out.append(f'<text x="{cx:.2f}" y="{cy + 0.85:.2f}" font-size="2.2" fill="#fff" '
                   f'text-anchor="middle">1</text>')
        px1, py1 = mm(ax, ay)
        out.append(f'<circle cx="{px1:.2f}" cy="{py1:.2f}" r="1.1" fill="#fff" '
                   f'stroke="{color}" stroke-width="0.55"/>')
        dx, dy = cx - px1, cy - py1
        nl = math.hypot(dx, dy) or 1.0
        out.append(f'<text x="{px1 + dx / nl * 3.4:.2f}" y="{py1 + dy / nl * 3.4:.2f}" '
                   f'font-size="2.2" fill="#455a64" text-anchor="middle" '
                   f'paint-order="stroke" stroke="#fff" stroke-width="1.2">'
                   f'{html.escape(plan.anchor.rid.split("@")[0])}</text>')

    # 基准点
    for i, ref in enumerate(plan.refs, start=1):
        cx, cy = mm(ref.x, ref.y)
        color = "#1565c0" if ref.kind in ("door", "elevator_door") else "#6a1b9a"
        out.append(f'<circle cx="{cx:.2f}" cy="{cy:.2f}" r="1.5" fill="{color}"/>')
        out.append(f'<text x="{cx:.2f}" y="{cy + 0.85:.2f}" font-size="2.2" fill="#fff" '
                   f'text-anchor="middle">{i}</text>')
        bx, by = mm(px, py)
        dx, dy = cx - bx, cy - by
        nl = math.hypot(dx, dy) or 1.0
        lx, ly = cx + dx / nl * 3.4, cy + dy / nl * 3.4
        out.append(f'<text x="{lx:.2f}" y="{ly:.2f}" font-size="2.2" fill="#455a64" '
                   f'text-anchor="middle" paint-order="stroke" stroke="#fff" stroke-width="1.2">'
                   f'{html.escape(ref.rid.split("@")[0])}</text>')
    if plan.check_ref:
        cx, cy = mm(plan.check_ref.x, plan.check_ref.y)
        out.append(f'<circle cx="{cx:.2f}" cy="{cy:.2f}" r="1.2" fill="#2e7d32"/>')
        out.append(f'<text x="{cx:.2f}" y="{cy + 0.7:.2f}" font-size="2" fill="#fff" '
                   f'text-anchor="middle">3</text>')

    # 信标
    bx, by = mm(px, py)
    out.append(f'<polygon points="{_star(bx, by, 3.0)}" fill="#d32f2f" stroke="#fff" stroke-width="0.4"/>')
    out.append(f'<text x="{bx:.2f}" y="{by + 6.4:.2f}" font-size="2.4" fill="#d32f2f" '
               f'text-anchor="middle" font-weight="700" paint-order="stroke" stroke="#fff" stroke-width="1.2">'
               f'{html.escape(plan.beacon_id)}</text>')

    # 比例尺与指北针
    bar_m = 2.0 if 2.0 * s <= 22 else 1.0
    bx0, by0 = 2.5, PLAN_H - 2.5
    out.append(f'<line x1="{bx0}" y1="{by0}" x2="{bx0 + bar_m * s:.2f}" y2="{by0}" '
               f'stroke="#263238" stroke-width="0.9"/>')
    out.append(f'<text x="{bx0 + bar_m * s / 2:.2f}" y="{by0 - 1.2:.2f}" font-size="2.2" '
               f'fill="#263238" text-anchor="middle">{bar_m:.0f} m</text>')
    out.append(f'<text x="{PLAN_W - 3:.2f}" y="6" font-size="3.2" fill="#263238" '
               f'text-anchor="middle" font-weight="700">N</text>')
    out.append(f'<line x1="{PLAN_W - 3:.2f}" y1="7.2" x2="{PLAN_W - 3:.2f}" y2="12" '
               f'stroke="#263238" stroke-width="0.6"/>')
    out.append(f'<polygon points="{PLAN_W - 3:.2f},6.8 {PLAN_W - 4:.2f},9 {PLAN_W - 2:.2f},9" fill="#263238"/>')
    out.append("</svg>")
    return "".join(out)


# ---------------------------------------------------------------- 安装卡
def render_card(beacon: dict, plan: Plan, model: FloorModel, idx: int, total: int) -> str:
    height = float(beacon.get("installHeightM") or 2.2)
    mount = beacon.get("mountType") or ""
    mount_cn = {"wall": "壁装", "door_frame": "门套"}.get(mount, mount)
    qr = segno.make_qr(beacon["beaconId"], error="m").svg_inline(
        scale=1, border=0, dark="#000", light="#fff")
    steps = "".join(f"<li>{s}</li>" for s in instructions(plan, height))
    if plan.method == "corridor_chain":
        if plan.offset is not None and plan.offset < WALL_SNAP:
            fields = '<span>实测 L <i>____</i> m</span><span>校核 <i>____</i> m</span>'
        else:
            fields = ('<span>实测 L <i>____</i> m</span><span>实测 o <i>____</i> m</span>'
                      '<span>斜量校核 <i>____</i> m</span>')
    elif plan.method == "room_two_dist":
        fields = ('<span>实测 d1 <i>____</i> m</span><span>实测 d2 <i>____</i> m</span>'
                  '<span>校核 <i>____</i> m</span>')
    else:
        fields = '<span>实测偏移 <i>____</i> m</span><span>校核 <i>____</i> m</span>'
    return f"""
<div class="card">
  <div class="hd">
    <span class="seq">#{idx:02d}/{total}</span>
    <span class="bid">{html.escape(beacon['beaconId'])}</span>
    <span class="dn">{html.escape(beacon.get('deviceName', ''))}</span>
    <span class="tag">{METHOD_LABEL.get(plan.method, plan.method)}</span>
  </div>
  <div class="meta">{int(beacon['floorId'])}F · {html.escape(mount_cn)} · 高 {height:.1f} m ·
    {html.escape(str(beacon.get('locationDesc', '')))[:34]}</div>
  {render_plan_svg(plan, model)}
  <ol class="steps">{steps}</ol>
  <div class="fill">{fields}</div>
  <div class="foot">
    <div class="qr">{qr}</div>
    <div class="sign">
      <div>实测偏差 <i>______</i> m　安装日期 <i>__________</i></div>
      <div>施工人 <i>__________</i>　复核人 <i>__________</i></div>
      <div class="hint">所有距离为水平距离（卷尺拉平）；偏差 &gt;0.5 m 停工重测。</div>
    </div>
  </div>
</div>"""


def build_html(pkg: dict, models: dict[str, FloorModel], plans: list[tuple[dict, Plan]]) -> str:
    cards = [render_card(b, p, models[str(b["floorId"])], i + 1, len(plans))
             for i, (b, p) in enumerate(plans)]
    pages = []
    for start in range(0, len(cards), 4):
        chunk = "".join(cards[start:start + 4])
        pages.append(f'<section class="page">{chunk}'
                     f'<div class="pfoot">{html.escape(pkg["siteId"])} · {html.escape(pkg["mapVersion"])}'
                     f' · {start + 1}-{start + len(cards[start:start + 4])} / {len(cards)}</div></section>')
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>信标安装放样卡 · {html.escape(pkg["siteId"])} · {html.escape(pkg["mapVersion"])}</title>
<style>
@page {{ size: A4; margin: 7mm; }}
* {{ box-sizing: border-box; }}
body {{ margin: 0; font-family: "Microsoft YaHei", "PingFang SC", sans-serif; color: #111; }}
.page {{ width: 196mm; height: 283mm; display: grid; grid-template-columns: 1fr 1fr;
        grid-template-rows: 1fr 1fr; gap: 2.5mm; page-break-after: always; position: relative; }}
.page:last-of-type {{ page-break-after: auto; }}
.pfoot {{ position: absolute; bottom: -1mm; right: 2mm; font-size: 6pt; color: #888; }}
.card {{ border: 0.35mm solid #37474f; padding: 1.8mm 2mm; display: flex; flex-direction: column;
        overflow: hidden; }}
.hd {{ display: flex; align-items: baseline; gap: 2mm; }}
.seq {{ font-size: 7pt; color: #607d8b; }}
.bid {{ font-size: 12pt; font-weight: 700; letter-spacing: 0.2pt; }}
.dn {{ font-size: 8pt; color: #37474f; }}
.tag {{ margin-left: auto; font-size: 6.5pt; color: #fff; background: #37474f;
       padding: 0.5mm 1.2mm; border-radius: 1mm; }}
.meta {{ font-size: 6.6pt; color: #455a64; margin: 0.8mm 0 1mm; line-height: 1.25;
        height: 5.6mm; overflow: hidden; }}
.plan {{ width: 100%; height: 52mm; display: block; border: 0.2mm solid #eceff1; }}
.steps {{ margin: 1.2mm 0 1mm; padding-left: 4mm; font-size: 7pt; line-height: 1.34; }}
.steps li {{ margin: 0; }}
.steps b {{ color: #b71c1c; }}
.fill {{ display: flex; gap: 1.5mm; font-size: 6.6pt; color: #263238; margin-top: auto;
        border-top: 0.2mm dashed #b0bec5; padding-top: 1mm; flex-wrap: wrap; }}
.fill i, .sign i {{ font-style: normal; border-bottom: 0.25mm solid #546e7a; padding: 0 2mm; }}
.foot {{ display: flex; gap: 2mm; margin-top: 1mm; align-items: flex-end; }}
.qr {{ width: 15mm; height: 15mm; flex: 0 0 15mm; }}
.qr svg {{ width: 100%; height: 100%; display: block; }}
.sign {{ flex: 1; font-size: 6.4pt; line-height: 1.7; }}
.hint {{ font-size: 5.8pt; color: #78909c; line-height: 1.3; margin-top: 0.6mm; }}
</style>
</head>
<body>
{"".join(pages)}
</body>
</html>
"""


# ---------------------------------------------------------------- 回填 CSV
CSV_FIELDS = [
    "seq", "floor", "beaconId", "deviceName", "method", "space", "height_m",
    "ref1_id", "ref1_desc", "ref1_x_m", "ref1_y_m", "design_d1_m", "meas_d1_m",
    "ref2_id", "ref2_desc", "ref2_x_m", "ref2_y_m", "design_d2_m", "meas_d2_m",
    "ref3_id", "ref3_desc", "design_d3_m", "meas_d3_m",
    "anchor_id", "anchor_desc", "anchor_x_m", "anchor_y_m",
    "travel_x", "travel_y", "nrm_x", "nrm_y",
    "design_L_m", "meas_L_m", "design_o_m", "meas_o_m",
    "design_x_m", "design_y_m", "recalc_x_m", "recalc_y_m", "dev_m", "grade",
    "install_date", "installer", "note",
]


def plan_row(seq: int, beacon: dict, plan: Plan) -> dict:
    row = {k: "" for k in CSV_FIELDS}
    row.update({
        "seq": seq,
        "floor": plan.floor,
        "beaconId": beacon["beaconId"],
        "deviceName": beacon.get("deviceName", ""),
        "method": plan.method,
        "space": plan.space,
        "height_m": beacon.get("installHeightM", 2.2),
        "design_x_m": round(plan.x, 4),
        "design_y_m": round(plan.y, 4),
        "note": plan.note,
    })
    for i, ref in enumerate(plan.refs[:2], start=1):
        row[f"ref{i}_id"] = ref.rid
        row[f"ref{i}_desc"] = ref.desc
        row[f"ref{i}_x_m"] = round(ref.x, 4)
        row[f"ref{i}_y_m"] = round(ref.y, 4)
        row[f"design_d{i}_m"] = round(plan.dists[i - 1], 3)
    if plan.check_ref:
        row["ref3_id"] = plan.check_ref.rid
        row["ref3_desc"] = plan.check_ref.desc
        row["design_d3_m"] = round(plan.check_d, 3)
    if plan.method == "corridor_chain" and plan.anchor_xy:
        sgn = 1.0 if plan.along >= 0 else -1.0
        row.update({
            "anchor_id": plan.anchor.rid,
            "anchor_desc": plan.anchor.desc,
            "anchor_x_m": round(plan.anchor_xy[0], 4),
            "anchor_y_m": round(plan.anchor_xy[1], 4),
            "travel_x": round(plan.dir_vec[0] * sgn, 6) + 0.0,
            "travel_y": round(plan.dir_vec[1] * sgn, 6) + 0.0,
            "nrm_x": round(plan.nrm_vec[0], 6) + 0.0,
            "nrm_y": round(plan.nrm_vec[1], 6) + 0.0,
            "design_L_m": round(abs(plan.along), 3),
            "design_o_m": round(plan.offset, 3),
        })
    return row


def write_csv(path: Path, rows: list[dict], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


# ---------------------------------------------------------------- 反算校验
def _num(row: dict, key: str) -> float | None:
    raw = row.get(key)
    if raw is None:
        return None
    raw = str(raw).strip()
    if raw == "":
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def circle_intersect(c1, r1, c2, r2, prefer):
    """两圆交点，取离 prefer 更近的一个；无交点返回 None。"""
    dx, dy = c2[0] - c1[0], c2[1] - c1[1]
    d = math.hypot(dx, dy)
    if d < 1e-9 or d > r1 + r2 + 1e-6 or d < abs(r1 - r2) - 1e-6:
        return None
    a = (r1 * r1 - r2 * r2 + d * d) / (2 * d)
    h2 = r1 * r1 - a * a
    h = math.sqrt(max(h2, 0.0))
    ux, uy = dx / d, dy / d
    mx, my = c1[0] + a * ux, c1[1] + a * uy
    p1 = (mx - h * uy, my + h * ux)
    p2 = (mx + h * uy, my - h * ux)
    return p1 if math.hypot(p1[0] - prefer[0], p1[1] - prefer[1]) <= \
        math.hypot(p2[0] - prefer[0], p2[1] - prefer[1]) else p2


def recompute(row: dict) -> tuple[tuple[float, float] | None, str]:
    """由实测值反算坐标；返回 (坐标, 备注)。"""
    method = (row.get("method") or "").strip()
    design = (_num(row, "design_x_m") or 0.0, _num(row, "design_y_m") or 0.0)
    if method == "room_two_dist":
        d1, d2 = _num(row, "meas_d1_m"), _num(row, "meas_d2_m")
        if d1 is None or d2 is None:
            return None, "未回填 d1/d2"
        c1 = (_num(row, "ref1_x_m"), _num(row, "ref1_y_m"))
        c2 = (_num(row, "ref2_x_m"), _num(row, "ref2_y_m"))
        if None in c1 or None in c2:
            return None, "基准坐标缺失"
        xy = circle_intersect(c1, d1, c2, d2, design)
        return (xy, "") if xy else (None, "两距无法交会（复核量距）")
    if method == "corridor_chain":
        L, o = _num(row, "meas_L_m"), _num(row, "meas_o_m")
        if L is None or o is None:
            return None, "未回填 L/o"
        ax, ay = _num(row, "anchor_x_m"), _num(row, "anchor_y_m")
        tx, ty = _num(row, "travel_x"), _num(row, "travel_y")
        nx, ny = _num(row, "nrm_x"), _num(row, "nrm_y")
        if None in (ax, ay, tx, ty, nx, ny):
            return None, "参考系字段缺失"
        return (ax + L * tx + o * nx, ay + L * ty + o * ny), ""
    if method == "door_anchor":
        ax, ay = _num(row, "ref1_x_m"), _num(row, "ref1_y_m")
        if ax is None or ay is None:
            return None, "门洞坐标缺失"
        return (ax, ay), ""
    return None, f"未知识别方式 {method or '?'}"


def _grade(dev: float) -> str:
    if dev < THRESH_OK:
        return "OK"
    if dev < THRESH_WARN:
        return "WARN"
    return "FAIL"


def verify(csv_path: Path, out_path: Path) -> int:
    if not csv_path.is_file():
        _fail(f"回填表不存在：{csv_path}")
    with open(csv_path, "r", encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    if not rows:
        _fail("回填表为空")

    report, skipped = [], 0
    for row in rows:
        xy, note = recompute(row)
        design = (_num(row, "design_x_m"), _num(row, "design_y_m"))
        if xy is None:
            skipped += 1
            report.append({**row, "recalc_x_m": "", "recalc_y_m": "", "dev_m": "",
                           "grade": "NA", "note": note})
            continue
        dev = math.hypot(xy[0] - design[0], xy[1] - design[1])
        report.append({**row,
                       "recalc_x_m": round(xy[0], 4),
                       "recalc_y_m": round(xy[1], 4),
                       "dev_m": round(dev, 4),
                       "grade": _grade(dev),
                       "note": note})
    write_csv(out_path, report, CSV_FIELDS)

    graded = [r for r in report if r["grade"] != "NA"]
    n_ok = sum(1 for r in graded if r["grade"] == "OK")
    n_warn = sum(1 for r in graded if r["grade"] == "WARN")
    n_fail = sum(1 for r in graded if r["grade"] == "FAIL")
    print(f"[verify] 共 {len(report)} 枚，已回填 {len(graded)} 枚（跳过 {skipped} 枚）")
    print(f"  OK  (<0.3m)  : {n_ok}")
    print(f"  WARN(0.3~0.5): {n_warn}")
    print(f"  FAIL(>0.5m)  : {n_fail}")
    if n_fail:
        print("[verify] ⚠ 存在 >0.5 m 偏差，须停工复核：")
        for r in graded:
            if r["grade"] == "FAIL":
                print(f"    {r['beaconId']}  {r['dev_m']} m  {r['note']}")
    print(f"[verify] 报告 -> {out_path}")
    return 0


def selfcheck(plans: list[tuple[dict, Plan]]) -> int:
    bad = 0
    for seq, (beacon, plan) in enumerate(plans, start=1):
        row = plan_row(seq, beacon, plan)
        # 用设计值冒充实测值，验证"放样描述 → 反算坐标"闭环
        row["meas_d1_m"] = row["design_d1_m"]
        row["meas_d2_m"] = row["design_d2_m"]
        row["meas_L_m"] = row["design_L_m"]
        row["meas_o_m"] = row["design_o_m"]
        xy, note = recompute(row)
        if xy is None:
            print(f"[selfcheck] {beacon['beaconId']}: 无法反算（{note}）")
            bad += 1
            continue
        dev = math.hypot(xy[0] - plan.x, xy[1] - plan.y)
        tol = DOOR_ANCHOR_TOL if plan.method == "door_anchor" else 0.01
        if dev > tol:
            print(f"[selfcheck] {beacon['beaconId']}: 反算偏差 {dev:.4f} m")
            bad += 1
    if bad:
        print(f"[selfcheck] ✗ {bad} 枚未通过")
        return 1
    print(f"[selfcheck] ✓ {len(plans)} 枚全部通过（设计值反算偏差 <0.01 m）")
    return 0


# ---------------------------------------------------------------- CLI
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="信标现场安装放样卡（A4 + 回填 CSV + 反算校验）")
    parser.add_argument("--site", default="school-building-01")
    parser.add_argument("--version", default="latest")
    parser.add_argument("--out", default="", help="HTML 输出（默认 result/beacon_install_cards.html）")
    parser.add_argument("--csv", default="", help="CSV 输出（默认 result/beacon_install_survey.csv）")
    parser.add_argument("--floor", default="", help="仅生成某层（1 / 2）")
    parser.add_argument("--selfcheck", action="store_true", help="仅做设计值反算自检")
    parser.add_argument("--verify", default="", help="读取现场回填 CSV 做反算校验")
    parser.add_argument("--verify-out", default="", help="校验报告路径")
    args = parser.parse_args(argv)

    if args.verify:
        out = Path(args.verify_out) if args.verify_out else RESULT_DIR / "beacon_install_verify.csv"
        return verify(Path(args.verify), out)

    pkg = load_site(args.site, args.version)
    models, plans = build_plans(pkg)
    if args.floor:
        plans = [(b, p) for b, p in plans if str(b["floorId"]) == str(args.floor)]
        if not plans:
            _fail(f"楼层 {args.floor} 无信标")

    if args.selfcheck:
        return selfcheck(plans)

    html_text = build_html(pkg, models, plans)
    out_html = Path(args.out) if args.out else RESULT_DIR / "beacon_install_cards.html"
    out_html.parent.mkdir(parents=True, exist_ok=True)
    out_html.write_text(html_text, encoding="utf-8")

    rows = [plan_row(i + 1, b, p) for i, (b, p) in enumerate(plans)]
    out_csv = Path(args.csv) if args.csv else RESULT_DIR / "beacon_install_survey.csv"
    write_csv(out_csv, rows, CSV_FIELDS)

    dist: dict[str, int] = {}
    for _, plan in plans:
        dist[plan.method] = dist.get(plan.method, 0) + 1
    pages = (len(plans) + 3) // 4
    print(f"安装卡：{len(plans)} 张 / {pages} 页（{args.site} · {pkg['mapVersion']}）")
    for key, cnt in sorted(dist.items(), key=lambda kv: -kv[1]):
        print(f"  {METHOD_LABEL.get(key, key)}: {cnt}")
    warn = [b["beaconId"] for b, p in plans if p.method == "fallback"]
    if warn:
        print(f"  ⚠ 需人工复核：{', '.join(warn)}")
    max_d = max((max(p.dists) if p.dists else 0.0) for _, p in plans)
    print(f"  最长量距：{max_d:.2f} m（限 {MAX_MEASURE:.0f} m）")
    print(f"输出：{out_html}")
    print(f"回填：{out_csv}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
