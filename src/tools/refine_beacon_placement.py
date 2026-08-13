#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
refine_beacon_placement.py — 三点定位信标方案的「放置质量后处理」。

目标: 让部署真正满足 4 条质量规则(评审发现原方案由贪心补点生成, 未强制这些约束):
  R1 左右两侧分开部署  —— 每条走廊沿长轴两侧各 >=1 个 wall 信标, 缺侧补点
  R2 避开墙角/障碍物   —— 信标距真墙角(非共线墙交点) >= 0.8m; 距柱体 >= 0.5m
  R3 三角部署          —— 每个覆盖点的 3 个最近可见信标非共线(最小三角面积 >= 0.6 m^2),
                          对退化点定向补点打破共线
  R4 部署均匀          —— Lloyd 式原位松弛 + 间距过大处补点, NN 间距 CV 尽量 < 0.5

硬约束(不可破坏): 每个目标采样点的可见信标数 >= 3 不下降。
  - 全楼方案: 目标点 = walkable 区域 1m 网格(与 gen_trilateration_plan.py 同口径)
  - 路线方案: 目标点 = --target fingerprint_grid_routes.json 的 753 个路线点
  为满足 R1/R3 必要时可增加信标布点(--max-new 上限)。

流程:
  1. 评审基线(四规则量化指标)
  2. 原位松弛: 每信标沿最近墙滑动 + 局部网格候选, 打分(均匀+三角), 接受不降覆盖的移动
  3. 统一代价补点: 走廊缺侧(R1) / 退化三角(R3) / 超长间距(R4) 三类候选在
     同一收益尺度(W_CORR/W_TRI/W_GAP)下竞争共享配额 —— 取代原先 R1/R3/R4
     顺序执行 + 各自抢配额的结构, 优先级由收益大小自动决定
  4. 收尾原位松弛: 统一补点可能拉大局部间距, 再松弛一轮
  5. 最终评审 + 前后对比, 写 refined 计划与评审 JSON

性能: 三角奖励仅查询「退化点」空间索引(小半径), 最近墙用 STRtree 查询,
      全楼 324 信标方案整体运行约 1 分钟以内。

输出:
  result/beacon_deployment_plan_trilateration_refined.json  (默认, 可 --out 覆盖)
  result/trilateration_placement_review.json               (评审报告, 前后对比)

用法:
  python src/tools/refine_beacon_placement.py                                    # 全楼方案
  python src/tools/refine_beacon_placement.py --plan result/beacon_deployment_plan_trilateration_routes.json \
      --target result/fingerprint_grid_routes.json --out result/beacon_deployment_plan_trilateration_routes_refined.json
  python src/tools/refine_beacon_placement.py --review-only                       # 只评审不修改
"""
from __future__ import annotations
import argparse, heapq, json, math, random
from pathlib import Path
from collections import defaultdict

from shapely.geometry import LineString, Point, shape
from shapely.ops import unary_union
from shapely.strtree import STRtree

ROOT = Path(__file__).resolve().parents[2]
GEO = ROOT / "result" / "school_building_01_map_v9.geojson"
DEFAULT_PLAN = ROOT / "result" / "beacon_deployment_plan_trilateration.json"
ROUTE_PLAN = ROOT / "result" / "beacon_deployment_plan_trilateration_routes.json"
ROUTE_TARGET = ROOT / "result" / "fingerprint_grid_routes.json"

# ---- 无线模型(与 gen_trilateration_plan.py 完全同口径) ----
TX_POWER = -10
RSSI_REF_1M = -50
N = 3.5
WALL_ATTEN = {"brick": 12, "concrete": 15, "partition": 8, "glass": 6, None: 12}
VISIBLE = -85
D_MAX = 11.0
OFFSET = 0.25
GRID = 1.0

# ---- 放置质量阈值 ----
CORNER_ANGLE_DEG = 30.0   # 顶点处墙段方向最大夹角差 > 此值为「真墙角」
CORNER_CLEAR = 0.8        # 信标距真墙角最小净距(m)
OBST_CLEAR = 0.5          # 信标距柱体最小净距(m)
ENCLOSED_EDGE = 0.3       # 封闭空间判定的边缘容差(m): 越过此深度视为进入室内
MIN_SPACING = 1.5         # 新增信标与既有信标最小间距(m), 防重合堆积
EPS_AREA = 0.6            # 三点三角面积(m^2)下限, 低于视为退化(共线)
NN_TARGET = 3.5           # 均匀性目标间距(m)
NN_MIN = 2.0              # 过近阈值: 低于则视为堆积
NN_MAX = 7.0              # 过疏阈值: 高于则考虑补点
# ---- 统一代价函数权重(R1/R3/R4 同一收益尺度, 供 unified_add_pass 贪心选择) ----
W_CORR = 12.0             # R1: 消除一条缺侧走廊的收益(硬要求, 权重最高)
W_TRI = 1.0               # R3: 修复一个退化采样点的收益
W_GAP = 3.0               # R4: 削减 1m 超长间距的收益(12.6m 空档收益 16.8 > R1, 优先二分)
RELAX_STEP = 0.3          # 原位松弛候选步长(m)
RELAX_R = 0.6             # 原位松弛候选半径(m)
SLIDE_STEP = 0.3          # 沿墙滑动步长(m)
SLIDE_MAX = 1.2           # 沿墙滑动最大距离(m)
TRI_GAIN_R = 5.0          # 三角奖励查询半径(m, 信标仅移动 <=0.6m, 只影响近处点)
MAX_RELAX_ROUNDS = 5      # 原位松弛最大轮数(每轮 <3s, 保持沙箱 120s 内完成)
MAX_NEW = 40              # 允许新增信标上限(统一代价补点总量封顶)


def tri_area(a, b, c):
    return abs((b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])) / 2.0


def nearest_wall(model, pt, maxd=2.0):
    """STRtree 快速找最近墙段(<=maxd), 返回 (LineString, dist) 或 (None, inf)。"""
    p = Point(pt)
    cand = [i for i in model.tree.query(p.buffer(maxd))]
    if not cand:
        return None, float("inf")
    best_i = min(cand, key=lambda i: p.distance(model.segs[i]))
    d = p.distance(model.segs[best_i])
    if d > maxd:
        return None, d
    return model.segs[best_i], d


class FloorModel:
    """单楼层几何: 墙/真墙角/柱体/走廊 + 目标采样点 + 信标可见性缓存。"""

    def __init__(self, geo_floor, plan_beacons, target=None, seed=0):
        fg = geo_floor["geometry"]
        self.fg = fg
        self.rng = random.Random(seed)
        # 墙
        self.segs, self.atten = [], []
        for w in fg["walls"]:
            c = w["geometry"]["coordinates"]
            if len(c) < 2:
                continue
            self.segs.append(LineString([(x, y) for x, y in c]))
            self.atten.append(WALL_ATTEN.get(w["properties"].get("material"), 12))
        self.tree = STRtree(self.segs)
        # 真墙角
        self.corners = self._true_corners(fg)
        self.ctree = STRtree(self.corners) if self.corners else None
        # 柱体(障碍)
        self.cols = [shape(f["geometry"]) for f in fg.get("columns", [])
                     if f.get("geometry", {}).get("type") == "Polygon"]
        self.coltree = STRtree(self.cols) if self.cols else None
        # 走廊
        self.corridors = [shape(f["geometry"]) for f in fg["rooms"]
                          if (f.get("properties", {}).get("type") or
                              f.get("properties", {}).get("roomType")) == "corridor"]
        self.corridor_axes = [self._long_axis(p) for p in self.corridors]
        # 封闭空间(非公共 / 不可达房间): 信标不得落入其内部
        self.enclosed = []
        for f in fg["rooms"]:
            pr = f.get("properties", {})
            if pr.get("public") is False or pr.get("accessible") is False:
                try:
                    self.enclosed.append(shape(f["geometry"]))
                except Exception:
                    continue
        self.enctree = STRtree(self.enclosed) if self.enclosed else None
        # 目标采样点
        if target is not None:
            self.pts = target
        else:
            wr = geo_floor["walkable_regions"]
            polys = [shape(f["geometry"]) for f in wr["features"]
                     if f.get("geometry", {}).get("type") == "Polygon"]
            union = unary_union(polys)
            self.pts = self._sample_pts(union, GRID)
        self.ptree = STRtree([Point(x, y) for (x, y) in self.pts])
        # 信标
        self.beacons = [(b[0], b[1]) for b in plan_beacons]
        # 可见性缓存: (x,y) -> set(beacon_idx)
        self.cache = {}
        for i, (x, y) in enumerate(self.pts):
            self.cache[(x, y)] = self._visible_ids(x, y)
        # 退化点索引与信标树(懒构建, append/move 后失效)
        self._degen_idx = None
        self._btree = None

    def beacon_tree(self):
        """信标位置 STRtree(懒构建; 移动/新增信标后调用方须置 self._btree=None)。"""
        if self._btree is None:
            self._btree = STRtree([Point(x, y) for (x, y) in self.beacons])
        return self._btree

    # ---------- 几何 ----------
    @staticmethod
    def _true_corners(fg):
        vdir = defaultdict(list)
        for w in fg["walls"]:
            c = w["geometry"]["coordinates"]
            for i in range(len(c) - 1):
                a, b = c[i], c[i + 1]
                d = math.hypot(b[0] - a[0], b[1] - a[1])
                if d < 1e-9:
                    continue
                ang = math.atan2(b[1] - a[1], b[0] - a[0])
                for pt in (a, b):
                    vdir[(round(pt[0], 3), round(pt[1], 3))].append(ang)
        corners = []
        for (vx, vy), angs in vdir.items():
            if len(angs) < 2:
                continue
            mx = 0.0
            for i in range(len(angs)):
                for j in range(i + 1, len(angs)):
                    diff = abs(((angs[i] - angs[j] + math.pi) % (2 * math.pi)) - math.pi)
                    mx = max(mx, math.degrees(diff))
            if mx > CORNER_ANGLE_DEG:
                corners.append(Point(vx, vy))
        return corners

    @staticmethod
    def _long_axis(poly):
        try:
            c = list(poly.minimum_rotated_rectangle.exterior.coords)
        except Exception:
            return None
        edges = sorted([(math.hypot(c[i + 1][0] - c[i][0], c[i + 1][1] - c[i][1]), c[i], c[i + 1])
                        for i in range(len(c) - 1)], reverse=True)
        a, b = edges[0][1], edges[0][2]
        # 长轴单位向量; 参考点用多边形质心(左右判定围绕质心对称, 对破碎/L形多边形稳健)
        ax, ay = b[0] - a[0], b[1] - a[1]
        alen = math.hypot(ax, ay) or 1e-9
        return ((poly.centroid.x, poly.centroid.y), (ax / alen, ay / alen))

    @staticmethod
    def _sample_pts(union, grid):
        minx, miny, maxx, maxy = union.bounds
        pts = []
        x = minx
        while x <= maxx:
            y = miny
            while y <= maxy:
                if union.contains(Point(x, y)):
                    pts.append((x, y))
                y += grid
            x += grid
        return pts

    # ---------- 可见性 ----------
    def _visible_ids(self, x, y):
        out = set()
        for i, (bx, by) in enumerate(self.beacons):
            dx, dy = bx - x, by - y
            d = math.hypot(dx, dy)
            if d > D_MAX or d < 1e-6:
                continue
            ux, uy = dx / d, dy / d
            seg = LineString([(x, y), (bx - ux * OFFSET, by - uy * OFFSET)])
            cand = self.tree.query(seg)
            atten_sum = 0
            for j in cand:
                if seg.intersects(self.segs[j]):
                    atten_sum += self.atten[j]
            rssi = RSSI_REF_1M - 10 * N * math.log10(d) - atten_sum
            if rssi > VISIBLE:
                out.add(i)
        return out

    def move_beacon(self, idx, newpos):
        """移动信标 idx -> newpos, 增量更新受影响采样点的可见集。
        返回 False 表示某点可见数 <3(破坏硬约束), 调用方应放弃该移动。
        注意: 仅当返回 True 时缓存与 beacons 已被更新。"""
        old = self.beacons[idx]
        aff = set()
        for hit in self.ptree.query(Point(old).buffer(D_MAX)):
            aff.add(self.pts[hit])
        for hit in self.ptree.query(Point(newpos).buffer(D_MAX)):
            aff.add(self.pts[hit])
        backup_cache = {}
        ok = True
        x, y = newpos
        for (px, py) in aff:
            s = self.cache[(px, py)]
            has_old = idx in s
            dx, dy = x - px, y - py
            d = math.hypot(dx, dy)
            vis_new = False
            if d <= D_MAX and d >= 1e-6:
                ux, uy = dx / d, dy / d
                seg = LineString([(px, py), (x - ux * OFFSET, y - uy * OFFSET)])
                cand = self.tree.query(seg)
                atten_sum = 0
                for j in cand:
                    if seg.intersects(self.segs[j]):
                        atten_sum += self.atten[j]
                rssi = RSSI_REF_1M - 10 * N * math.log10(d) - atten_sum
                vis_new = rssi > VISIBLE
            new_cnt = len(s) + (1 if vis_new and not has_old else 0) - (1 if has_old and not vis_new else 0)
            if new_cnt < 3:
                ok = False
                break
            backup_cache[(px, py)] = (s, has_old, vis_new)
        if not ok:
            return False
        self.beacons[idx] = newpos
        for (px, py), (s, has_old, vis_new) in backup_cache.items():
            if has_old:
                s.discard(idx)
            if vis_new:
                s.add(idx)
        self._degen_idx = None
        self._btree = None
        return True

    # ---------- 放置质量评估 ----------
    def corner_clear(self, x, y):
        if not self.ctree:
            return True
        i = self.ctree.nearest(Point(x, y))
        return self.corners[i].distance(Point(x, y)) >= CORNER_CLEAR

    def obstacle_clear(self, x, y):
        if not self.coltree:
            return True
        p = Point(x, y)
        i = self.coltree.nearest(p)
        poly = self.cols[i]
        return not (poly.contains(p) or poly.buffer(0.05).contains(p) or poly.distance(p) < OBST_CLEAR)

    def enclosed_clear(self, x, y):
        """信标不得落在封闭空间(非公共/不可达房间)内部。
        门框安装容许贴边, 故用 -ENCLOSED_EDGE 的负缓冲判定「真正进入室内」。"""
        if not self.enctree:
            return True
        p = Point(x, y)
        for i in self.enctree.query(p):
            poly = self.enclosed[i]
            if not poly.contains(p):
                continue
            inner = poly.buffer(-ENCLOSED_EDGE)
            if inner.is_empty or inner.contains(p):
                return False
        return True

    def valid_pos(self, x, y):
        return (self.corner_clear(x, y) and self.obstacle_clear(x, y)
                and self.enclosed_clear(x, y))

    def nn_dist(self, idx):
        """除 idx 外的最近邻距离。"""
        ox, oy = self.beacons[idx]
        best = float("inf")
        for j, (qx, qy) in enumerate(self.beacons):
            if j == idx:
                continue
            d = math.hypot(qx - ox, qy - oy)
            if d < best:
                best = d
        return best

    def corridor_side(self, pt, axis):
        a, (ax, ay) = axis
        cross = ax * (pt[1] - a[1]) - ay * (pt[0] - a[0])
        return 1 if cross > 1e-6 else (-1 if cross < -1e-6 else 0)

    def corridor_sides_present(self, buf=1.2):
        """每走廊两侧(左右)各有多少个信标。返回 [(corr_idx, L, R), ...]"""
        out = []
        for ci, (poly, axis) in enumerate(zip(self.corridors, self.corridor_axes)):
            if axis is None or poly.area < 8.0:
                # 与小走廊缺侧判定(_missing_corridor_sides)口径一致:
                # <8m^2 的走廊段无左右侧概念, 不参与 R1 双侧统计
                continue
            poly_b = poly.buffer(buf)  # 缓冲只算一次, 避免每信标重复
            L = R = 0
            for (bx, by) in self.beacons:
                if poly_b.contains(Point(bx, by)):
                    s = self.corridor_side((bx, by), axis)
                    if s > 0:
                        L += 1
                    elif s < 0:
                        R += 1
            out.append((ci, L, R))
        return out

    # ---------- 退化点(三角) ----------
    def degen_points(self, rebuild=False):
        """返回当前退化采样点坐标列表(缓存)。退化 = 3 最近可见信标三角面积 < EPS_AREA。"""
        if self._degen_idx is None or rebuild:
            idxs = []
            for i, (x, y) in enumerate(self.pts):
                s = self.cache[(x, y)]
                if len(s) < 3:
                    continue
                vis3 = sorted(s, key=lambda k: math.hypot(
                    self.beacons[k][0] - x, self.beacons[k][1] - y))[:3]
                if tri_area(self.beacons[vis3[0]], self.beacons[vis3[1]],
                            self.beacons[vis3[2]]) < EPS_AREA:
                    idxs.append(i)
            self._degen_idx = idxs
        return [self.pts[i] for i in self._degen_idx]

    # ---------- 评审指标 ----------
    def review(self):
        beacons = self.beacons
        n = len(beacons)
        corner_ct = sum(0 if self.corner_clear(x, y) else 1 for (x, y) in beacons)
        obst_ct = sum(0 if self.obstacle_clear(x, y) else 1 for (x, y) in beacons)
        enc_ct = sum(0 if self.enclosed_clear(x, y) else 1 for (x, y) in beacons)
        nn = []
        for j in range(n):
            d = self.nn_dist(j)
            if d < float("inf"):
                nn.append(d)
        if nn:
            m = sum(nn) / len(nn)
            sd = math.sqrt(sum((v - m) ** 2 for v in nn) / len(nn))
            cv = sd / m
        else:
            m = sd = cv = 0.0
        ge3 = tot = 0
        for (x, y) in self.pts:
            tot += 1
            if len(self.cache[(x, y)]) >= 3:
                ge3 += 1
        both = one = none = 0
        total_corr = 0
        for ci, L, R in self.corridor_sides_present():
            total_corr += 1
            if L > 0 and R > 0:
                both += 1
            elif L > 0 or R > 0:
                one += 1
            else:
                none += 1
        degen = len(self.degen_points(rebuild=True))
        return {
            "beacons": n,
            "corner_violations": corner_ct, "corner_rate": round(100.0 * corner_ct / n, 2) if n else 0,
            "obstacle_violations": obst_ct, "obstacle_rate": round(100.0 * obst_ct / n, 2) if n else 0,
            "enclosed_violations": enc_ct, "enclosed_rate": round(100.0 * enc_ct / n, 2) if n else 0,
            "nn_mean_m": round(m, 2), "nn_sd_m": round(sd, 2), "nn_cv": round(cv, 3),
            "nn_min_m": round(min(nn), 2) if nn else 0,
            "nn_max_m": round(max(nn), 2) if nn else 0,
            "corridors_total": total_corr, "corridors_both": both,
            "corridors_one": one, "corridors_none": none,
            "samples": tot, "pct_ge3": round(100.0 * ge3 / tot, 2) if tot else 0,
            "tri_degenerate": degen,
            "tri_degen_rate": round(100.0 * degen / ge3, 2) if ge3 else 0,
        }


def build_model(geo, plan, fl, target=None, seed=0):
    fdict = geo["floors"][fl]
    beacons = [(b["coordinates"][0], b["coordinates"][1])
               for b in plan["beacons"] if str(b["floor"]) == fl]
    tgt = None
    if target is not None:
        tgt = target["floors"].get(fl, {}).get("points", None)
        if tgt is not None:
            tgt = [(p["coordinates"][0], p["coordinates"][1]) for p in tgt
                   if "coordinates" in p]
    return FloorModel(fdict, beacons, target=tgt, seed=seed)


def relax_pass(model, round_i):
    """原位松弛: 每信标在局部网格+沿墙滑动+远离墙角候选里选最优
    (均匀 + 避角边界 + 走廊侧平衡 + 三角改善奖励), 不降覆盖。
    三角奖励仅对退化点评估(TRI_GAIN_R 小半径), 提速明显。"""
    moved = 0
    order = list(range(len(model.beacons)))
    model.rng.shuffle(order)
    # 退化点索引(本轮)
    degen = model.degen_points()
    degen_tree = STRtree([Point(x, y) for (x, y) in degen]) if degen else None
    # 每走廊两侧计数(循环外算一次, 候选评估用增量)
    # 注意: poly.buffer(1.2) 只算一次, 若在信标循环内会对每(走廊,信标)对
    # 重复做多边形缓冲(全楼 204 信标 x 14 走廊 = 上千次 buffer, 耗时巨大)。
    corr_bufs = [poly.buffer(1.2) for poly in model.corridors]
    corr_counts = []
    for ci, poly, axis in zip(range(len(model.corridors)), model.corridors, model.corridor_axes):
        Lc = Rc = 0
        if axis is not None:
            poly_b = corr_bufs[ci]
            for (qx, qy) in model.beacons:
                if poly_b.contains(Point(qx, qy)):
                    s = model.corridor_side((qx, qy), axis)
                    if s > 0:
                        Lc += 1
                    elif s < 0:
                        Rc += 1
        corr_counts.append((Lc, Rc))
    # 信标所属走廊索引与侧(供双侧硬约束)
    corr_of = {}
    corr_side_of = {}
    for idx, (bx, by) in enumerate(model.beacons):
        for ci, poly_b in enumerate(corr_bufs):
            if poly_b.contains(Point(bx, by)):
                s = model.corridor_side((bx, by), model.corridor_axes[ci])
                if s != 0:
                    corr_of[idx] = ci
                    corr_side_of[idx] = (ci, s)
                break

    for idx in order:
        ox, oy = model.beacons[idx]
        cs = corr_side_of.get(idx)
        cands = [(ox, oy)]
        # 远离最近墙角候选(避角关键: 允许一次性滑出 0.8m 净距)
        if model.ctree is not None:
            i = model.ctree.nearest(Point(ox, oy))
            cx, cy = model.corners[i].x, model.corners[i].y
            vx, vy = ox - cx, oy - cy
            vlen = math.hypot(vx, vy) or 1e-9
            ux, uy = vx / vlen, vy / vlen
            for dd in (0.9, 1.2, 1.5, 2.0):
                cands.append((cx + ux * dd, cy + uy * dd))
        # 沿最近墙滑动候选
        w, wd = nearest_wall(model, (ox, oy), maxd=2.0)
        if w is not None:
            proj = w.interpolate(w.project(Point(ox, oy)))
            coords = list(w.coords)
            ang = math.atan2(coords[-1][1] - coords[0][1], coords[-1][0] - coords[0][0])
            for sgn in (-1, 1):
                for k in range(1, int(SLIDE_MAX / SLIDE_STEP) + 1):
                    dd = sgn * k * SLIDE_STEP
                    cands.append((proj.x + dd * math.cos(ang), proj.y + dd * math.sin(ang)))
        # 局部网格候选(RELAX_STEP 步长, RELAX_R 半径)
        nr = int(RELAX_R / RELAX_STEP)
        for dx in range(-nr, nr + 1):
            for dy in range(-nr, nr + 1):
                if dx == 0 and dy == 0:
                    continue
                cands.append((ox + dx * RELAX_STEP, oy + dy * RELAX_STEP))
        valid = [(x, y) for (x, y) in cands if model.valid_pos(x, y)]
        if not valid:
            valid = [(ox, oy)]
        best_c = None
        best_score = None
        for (x, y) in valid:
            d_new = min((math.hypot(x - qx, y - qy)
                         for j, (qx, qy) in enumerate(model.beacons) if j != idx), default=10.0)
            score = -abs(d_new - NN_TARGET) * 0.5
            if d_new < NN_MIN:
                score -= 2.0
            # 避角边界奖励: 距真墙角越远越好(近 0.8 边界仍轻微惩罚)
            if model.ctree is not None:
                jc = model.ctree.nearest(Point(x, y))
                dc = model.corners[jc].distance(Point(x, y))
                if dc < CORNER_CLEAR:
                    score -= (CORNER_CLEAR - dc) * 3.0
                else:
                    score += min(0.6, (dc - CORNER_CLEAR)) * 0.4
            # 走廊侧平衡: 单侧走廊信标移动到缺侧 -> 加分(促成双侧)
            if cs is not None:
                ci, s_old = cs
                axis = model.corridor_axes[ci]
                Lc, Rc = corr_counts[ci]
                in_new = corr_bufs[ci].contains(Point(x, y))
                if Lc > 0 and Rc > 0:
                    # 已双侧走廊: 移动不得破坏双侧(该侧唯一贡献者时强惩罚)
                    s_new = model.corridor_side((x, y), axis) if in_new else 0
                    if not (in_new and s_new == s_old):
                        side_cnt = Lc if s_old > 0 else Rc
                        if side_cnt <= 1:
                            score -= 5.0
                elif axis is not None and in_new:
                    s_new = model.corridor_side((x, y), axis)
                    L2, R2 = Lc, Rc
                    if s_new > 0:
                        L2 += 1
                    elif s_new < 0:
                        R2 += 1
                    if L2 > 0 and R2 > 0:
                        score += 0.4
            # 三角奖励(仅退化点, 小半径; 先 query_nearest 剪枝, 远离退化点的候选跳过)
            if degen_tree is not None:
                nn_idx = degen_tree.query_nearest(Point(x, y))
                if (nn_idx.size and math.hypot(x - degen[nn_idx[0]][0], y - degen[nn_idx[0]][1])
                        <= TRI_GAIN_R):
                    for hit in degen_tree.query(Point(x, y).buffer(TRI_GAIN_R)):
                        px, py = degen[hit]
                        s = model.cache[(px, py)]
                        if len(s) < 3 or idx not in s:
                            continue
                        vis3 = sorted(s, key=lambda k: math.hypot(
                            model.beacons[k][0] - px, model.beacons[k][1] - py))[:3]
                        if idx not in vis3:
                            continue
                        others = [k for k in vis3 if k != idx]
                        a1 = tri_area(model.beacons[others[0]], model.beacons[others[1]], (x, y))
                        a0 = tri_area(model.beacons[vis3[0]], model.beacons[vis3[1]], model.beacons[vis3[2]])
                        if a0 < EPS_AREA and a1 > a0:
                            score += min(0.2, (a1 - a0) / (EPS_AREA * 2))
            if best_score is None or score > best_score:
                best_score = score
                best_c = (x, y)
        if best_c is not None and (best_c[0] != ox or best_c[1] != oy):
            if model.move_beacon(idx, best_c):
                moved += 1
    return moved


def fix_corner_pass(model):
    """确定性避角: 距真墙角 < CORNER_CLEAR 的信标, 沿「墙角->信标」方向 + 沿墙滑动 +
    局部网格(2D)搜索合法位置移开。信标正好在墙角点时沿最近墙段方向滑开。
    候选按「距墙角越远越优先」排序, 取第一个能 move 的位置。"""
    fixed = 0
    for idx in range(len(model.beacons)):
        ox, oy = model.beacons[idx]
        if model.ctree is None or model.corner_clear(ox, oy):
            continue
        i = model.ctree.nearest(Point(ox, oy))
        cx, cy = model.corners[i].x, model.corners[i].y
        vx, vy = ox - cx, oy - cy
        vlen = math.hypot(vx, vy)
        cands = []
        if vlen < 1e-6:
            # 信标恰在墙角点: 沿最近墙段两个方向滑开
            w, wd = nearest_wall(model, (ox, oy), maxd=2.0)
            if w is None:
                continue
            coords = list(w.coords)
            ang = math.atan2(coords[-1][1] - coords[0][1], coords[-1][0] - coords[0][0])
            for sgn in (-1, 1):
                for dd in (0.9, 1.2, 1.6, 2.0):
                    cands.append((ox + sgn * dd * math.cos(ang), oy + sgn * dd * math.sin(ang)))
        else:
            ux, uy = vx / vlen, vy / vlen
            for dd in (0.9, 1.1, 1.4, 1.8, 2.2):
                cands.append((cx + ux * dd, cy + uy * dd))
        # 沿最近墙滑动候选
        w, wd = nearest_wall(model, (ox, oy), maxd=2.0)
        if w is not None:
            proj = w.interpolate(w.project(Point(ox, oy)))
            coords = list(w.coords)
            ang = math.atan2(coords[-1][1] - coords[0][1], coords[-1][0] - coords[0][0])
            for sgn in (-1, 1):
                for dd in (0.9, 1.2, 1.6, 2.0):
                    cands.append((proj.x + sgn * dd * math.cos(ang), proj.y + sgn * dd * math.sin(ang)))
        # 局部 2D 网格
        for dx in (-1.0, -0.6, 0.6, 1.0):
            for dy in (-1.0, -0.6, 0.6, 1.0):
                cands.append((ox + dx, oy + dy))
        # 候选: 贴墙投影 + 合法性; 按「距原位置距离」升序(最小移动优先,
        # 不改变穿墙结构, move_beacon 拒绝率低), 同距离优先距墙角远的
        valid = []
        for cand in cands:
            w2, wd2 = nearest_wall(model, cand, maxd=1.5)
            if w2 is not None:
                proj = w2.interpolate(w2.project(Point(*cand)))
                cand = (proj.x, proj.y)
            if not model.valid_pos(*cand):
                continue
            if model.ctree is not None:
                jc = model.ctree.nearest(Point(*cand))
                dcor = model.corners[jc].distance(Point(*cand))
            else:
                dcor = 1e9
            dmove = math.hypot(cand[0] - ox, cand[1] - oy)
            valid.append((dmove, -dcor, cand))
        if not valid:
            continue
        valid.sort()  # 最小移动优先
        for dmove, ndcor, cand in valid:
            if model.move_beacon(idx, cand):
                fixed += 1
                break
    return fixed


def fix_obstacle_pass(model):
    """确定性避障: 压/近柱体的信标, 沿「柱心->信标」方向移到缓冲外并贴墙。"""
    fixed = 0
    for idx in range(len(model.beacons)):
        ox, oy = model.beacons[idx]
        if model.coltree is None or model.obstacle_clear(ox, oy):
            continue
        p = Point(ox, oy)
        i = model.coltree.nearest(p)
        poly = model.cols[i]
        c = poly.centroid
        vx, vy = ox - c.x, oy - c.y
        vlen = math.hypot(vx, vy) or 1e-9
        ux, uy = vx / vlen, vy / vlen
        best = None
        for dd in (OBST_CLEAR + 0.1, OBST_CLEAR + 0.35, OBST_CLEAR + 0.7):
            cand = (c.x + ux * dd, c.y + uy * dd)
            w, wd = nearest_wall(model, cand, maxd=1.5)
            if w is not None:
                proj = w.interpolate(w.project(Point(*cand)))
                cand = (proj.x, proj.y)
            if model.valid_pos(*cand):
                best = cand
                break
        if best is None:
            continue
        if model.move_beacon(idx, best):
            fixed += 1
    return fixed


def fix_enclosed_pass(model):
    """确定性「迁出封闭空间」: 落在非公共/不可达房间内部的信标, 迁到房间外最近的
    公共侧墙面上。候选 = 房间边界上的采样点沿外法向外推, 取仍能贴墙且合法的位置,
    按「距原位置最近」优先(尽量保持原覆盖结构, move_beacon 拒绝率低)。"""
    fixed = 0
    for idx in range(len(model.beacons)):
        ox, oy = model.beacons[idx]
        if model.enctree is None or model.enclosed_clear(ox, oy):
            continue
        p = Point(ox, oy)
        host = None
        for i in model.enctree.query(p):
            if model.enclosed[i].contains(p):
                host = model.enclosed[i]
                break
        if host is None:
            continue
        cands = []
        ring = host.exterior
        n = max(24, int(ring.length / 0.5))
        for k in range(n):
            bp = ring.interpolate(ring.length * k / n)
            vx, vy = bp.x - ox, bp.y - oy
            vlen = math.hypot(vx, vy) or 1e-9
            ux, uy = vx / vlen, vy / vlen
            # 从边界点继续外推, 越过墙体到达外侧公共空间
            for dd in (0.4, 0.7, 1.0, 1.4):
                cands.append((bp.x + ux * dd, bp.y + uy * dd))
        valid = []
        for cand in cands:
            w, wd = nearest_wall(model, cand, maxd=1.5)
            if w is not None:
                proj = w.interpolate(w.project(Point(*cand)))
                cand = (proj.x, proj.y)
            if not model.valid_pos(*cand):
                continue
            if any(math.hypot(cand[0] - qx, cand[1] - qy) < MIN_SPACING
                   for j, (qx, qy) in enumerate(model.beacons) if j != idx):
                continue
            valid.append((math.hypot(cand[0] - ox, cand[1] - oy), cand))
        if not valid:
            continue
        valid.sort(key=lambda t: t[0])
        for _, cand in valid:
            if model.move_beacon(idx, cand):
                fixed += 1
                break
    return fixed


def _add_beacon_to_cache(model, idx):
    """把新信标 idx 加入受影响采样点的可见集(新增不破坏 >=3)。"""
    bx, by = model.beacons[idx]
    for hit in model.ptree.query(Point(bx, by).buffer(D_MAX)):
        px, py = model.pts[hit]
        s = model.cache[(px, py)]
        d = math.hypot(bx - px, by - py)
        if d > D_MAX or d < 1e-6:
            continue
        ux, uy = (bx - px) / d, (by - py) / d
        seg = LineString([(px, py), (bx - ux * OFFSET, by - uy * OFFSET)])
        cand = model.tree.query(seg)
        atten_sum = 0
        for j in cand:
            if seg.intersects(model.segs[j]):
                atten_sum += model.atten[j]
        rssi = RSSI_REF_1M - 10 * N * math.log10(d) - atten_sum
        if rssi > VISIBLE:
            s.add(idx)


def _missing_corridor_sides(model, buf=1.2):
    """R1: 返回缺侧走廊 {ci: target_side}。target_side=1 缺左侧 / -1 缺右侧;
    双侧都缺的走廊也计入(先补任意一侧)。"""
    missing = {}
    for ci, (poly, axis) in enumerate(zip(model.corridors, model.corridor_axes)):
        if axis is None or poly.area < 8.0:
            continue
        poly_b = poly.buffer(buf)  # 缓冲只算一次, 避免每信标重复
        L = R = 0
        for (bx, by) in model.beacons:
            if poly_b.contains(Point(bx, by)):
                s = model.corridor_side((bx, by), axis)
                if s > 0:
                    L += 1
                elif s < 0:
                    R += 1
        if L > 0 and R > 0:
            continue
        missing[ci] = 1 if L == 0 else -1
    return missing


def _min_dist_to(model, pt):
    """pt 与既有信标的最小距离(STRtree 加速: 7m 缓冲命中集内算精确值)。
    返回 inf 表示 7m 内无信标(对 gap 判断足够)。"""
    btree = model.beacon_tree()
    p = Point(pt)
    hits = btree.query(p.buffer(NN_MAX))
    if hits.size == 0:
        return float("inf")
    best = float("inf")
    for i in hits:
        bx, by = model.beacons[i]
        d = math.hypot(pt[0] - bx, pt[1] - by)
        if d < best:
            best = d
    return best


def _gap_gain(model, pt):
    """R4 分量: 候选若位于「距最近信标 > NN_MAX」的空档, 补点可削减超长间距。
    收益按超出量计(越大越迫切); 单次补中点的边际削减恒为 NN_MAX, 故仅作排序信号。"""
    dmin = _min_dist_to(model, pt)
    if dmin > NN_MAX:
        return W_GAP * (dmin - NN_MAX)
    return 0.0


def _tri_gain_exact(model, degen_pts, degen_tree, pt):
    """R3 分量: 候选加入后能修复(打破共线)的退化采样点数, 精确评估。
    仅查询 TRI_GAIN_R 半径内的退化点; 候选须比退化点当前第 3 近信标更近
    才进入 top3, 且新三角面积 >= EPS_AREA 才视为修复。"""
    if degen_tree is None:
        return 0.0
    bx, by = pt
    nn_idx = degen_tree.query_nearest(Point(bx, by))
    if not (nn_idx.size and math.hypot(bx - degen_pts[nn_idx[0]][0],
                                       by - degen_pts[nn_idx[0]][1]) <= TRI_GAIN_R):
        return 0.0
    gain = 0
    for hit in degen_tree.query(Point(bx, by).buffer(TRI_GAIN_R)):
        px, py = degen_pts[hit]
        s = model.cache[(px, py)]
        if len(s) < 3:
            continue
        vis3 = sorted(s, key=lambda k: math.hypot(
            model.beacons[k][0] - px, model.beacons[k][1] - py))[:3]
        if tri_area(model.beacons[vis3[0]], model.beacons[vis3[1]],
                    model.beacons[vis3[2]]) >= EPS_AREA:
            continue
        if math.hypot(bx - px, by - py) >= math.hypot(
                model.beacons[vis3[2]][0] - px, model.beacons[vis3[2]][1] - py):
            continue
        if tri_area(model.beacons[vis3[0]], model.beacons[vis3[1]], pt) >= EPS_AREA:
            gain += W_TRI
    return gain


def _unified_candidates(model, missing, degen_pts, degen_tree):
    """统一候选池: 三类来源(R1 走廊缺侧 / R3 退化三角 / R4 超长间距)
    各自生成候选并给出同一收益尺度下的分值, 由 unified_add_pass 贪心选择。"""
    cands = []
    seen = set()

    def push(pt, gain):
        key = (round(pt[0], 2), round(pt[1], 2))
        if key in seen or gain <= 0:
            return
        seen.add(key)
        cands.append((pt[0], pt[1], gain))

    # A. R1: 缺侧走廊内网格采样, 每缺侧走廊取离既有信标最远的 top4
    for ci, tside in missing.items():
        poly = model.corridors[ci]
        axis = model.corridor_axes[ci]
        minx, miny, maxx, maxy = poly.bounds
        local = []
        x = minx + 0.5
        while x < maxx:
            y = miny + 0.5
            while y < maxy:
                if (poly.contains(Point(x, y))
                        and model.corridor_side((x, y), axis) == tside
                        and model.valid_pos(x, y)):
                    local.append((x, y))
                y += 0.5
            x += 0.5
        for p in heapq.nlargest(4, local, key=lambda p: _min_dist_to(model, p)):
            push(p, W_CORR + _gap_gain(model, p))
    # B. R3: 退化点垂直方向候选(精确评估修复数)
    for (x, y) in degen_pts:
        s = model.cache[(x, y)]
        if len(s) < 3:
            continue
        vis3 = sorted(s, key=lambda k: math.hypot(
            model.beacons[k][0] - x, model.beacons[k][1] - y))[:3]
        b0, b1 = model.beacons[vis3[0]], model.beacons[vis3[1]]
        dx, dy = b1[0] - b0[0], b1[1] - b0[1]
        dlen = math.hypot(dx, dy) or 1e-9
        nx, ny = -dy / dlen, dx / dlen
        for sgn in (-1, 1):
            qx, qy = x + nx * 3.0 * sgn, y + ny * 3.0 * sgn
            w, wd = nearest_wall(model, (qx, qy), maxd=2.0)
            if w is None:
                continue
            proj = w.interpolate(w.project(Point(qx, qy)))
            if not model.valid_pos(proj.x, proj.y):
                continue
            push((proj.x, proj.y),
                 _tri_gain_exact(model, degen_pts, degen_tree, (proj.x, proj.y))
                 + _gap_gain(model, (proj.x, proj.y)))
    # C. R4: 超长间距最大者的中点沿墙滑动(每个 gap 提供若干贴墙偏移候选)
    gap_order = sorted(range(len(model.beacons)),
                       key=lambda i: model.nn_dist(i), reverse=True)
    for idx in gap_order:
        d0 = model.nn_dist(idx)
        if d0 <= NN_MAX:
            break
        ox, oy = model.beacons[idx]
        j = min((k for k in range(len(model.beacons)) if k != idx),
                key=lambda k: math.hypot(model.beacons[k][0] - ox,
                                         model.beacons[k][1] - oy))
        qx, qy = model.beacons[j]
        mx, my = (ox + qx) / 2, (oy + qy) / 2
        w, wd = nearest_wall(model, (mx, my), maxd=2.0)
        if w is None:
            continue
        t0 = w.project(Point(mx, my))
        for off in (0.0, 0.6, -0.6, 1.2, -1.2):
            t = t0 + off
            if t < 0 or t > w.length:
                continue
            pr = w.interpolate(t)
            c = (pr.x, pr.y)
            if model.valid_pos(*c):
                push(c, W_GAP * (d0 - NN_MAX))
    return cands


def unified_add_pass(model, max_new):
    """统一代价函数贪心补点, 取代 R1/R3/R4 顺序执行 + 各自抢配额的结构。

    每轮: 生成三类候选(走廊缺侧 / 退化三角 / 超长间距), 全部在同一收益尺度
    (W_CORR/W_TRI/W_GAP)下打分, 选全局收益最高的放置; 放置后其余候选收益
    自然衰减, 下一轮重新生成。优先级由收益大小自动决定, 配额全局共享,
    不再需要 EVEN_RESERVE 之类的预留。"""
    added = []
    for _ in range(max_new):
        missing = _missing_corridor_sides(model)
        degen_pts = model.degen_points()
        degen_tree = (STRtree([Point(x, y) for (x, y) in degen_pts])
                      if degen_pts else None)
        cands = _unified_candidates(model, missing, degen_pts, degen_tree)
        cands = [(x, y, g) for (x, y, g) in cands
                 if _min_dist_to(model, (x, y)) >= MIN_SPACING]
        if not cands:
            break
        best = max(cands, key=lambda c: c[2])
        if best[2] <= 0.0:
            break
        model.beacons.append((best[0], best[1]))
        added.append((best[0], best[1]))
        _add_beacon_to_cache(model, len(model.beacons) - 1)
        model._btree = None
    if added:
        model._degen_idx = None
    return added


def clone_plan_entry(entry, new_coord, tag, extra=None):
    out = json.loads(json.dumps(entry))
    out["coordinates"] = [round(new_coord[0], 3), round(new_coord[1], 3)]
    out["plannedCoordinates"] = [round(new_coord[0], 3), round(new_coord[1], 3)]
    out["semanticTag"] = tag
    if extra:
        out.update(extra)
    return out


def normalize_mount_types(plan):
    """把历史遗留的天花板吊装统一改为贴墙安装。
    视障导航要求信标必须部署在公共空间墙面/柱体上(可触及校准 + 可施工),
    上游已不再产生 ceiling, 此处兜底纠正既有方案中的残留项。"""
    n = 0
    for b in plan.get("beacons", []):
        if b.get("mountType") == "ceiling":
            b["mountType"] = "wall"
            b["installHeight"] = 2.2
            b["mountTypeNormalized"] = True
            desc = b.get("locationDesc", "")
            b["locationDesc"] = desc.replace(" / 无墙可吸附-需评估", "")
            n += 1
    if n:
        print(f"mountType 归一: ceiling -> wall 共 {n} 个", flush=True)
    return n


def rebuild_summary(plan):
    """从 beacons 数组实时聚合 summary, 避免手工维护导致的字段陈旧失真
    (原方案 total 写 48 而实际 89, ceilingPendantCount 写 0 而实际有 1)。"""
    beacons = plan.get("beacons", [])
    by_floor, by_sem, by_sub, by_mount = {}, {}, {}, {}
    for b in beacons:
        fl = str(b.get("floor", ""))
        by_floor[fl] = by_floor.get(fl, 0) + 1
        sem = b.get("semanticTag", "unknown")
        by_sem[sem] = by_sem.get(sem, 0) + 1
        sub = f"{sem}/{b.get('subType', 'base')}"
        by_sub[sub] = by_sub.get(sub, 0) + 1
        mt = b.get("mountType", "unknown")
        by_mount[mt] = by_mount.get(mt, 0) + 1
    ceiling = by_mount.get("ceiling", 0)
    plan["summary"] = {
        "total": len(beacons),
        "byFloor": dict(sorted(by_floor.items())),
        "bySemantic": dict(sorted(by_sem.items())),
        "bySubType": dict(sorted(by_sub.items())),
        "byMount": dict(sorted(by_mount.items())),
        "ceilingPendantCount": ceiling,
        "wallLikeCount": len(beacons) - ceiling,
    }
    return plan["summary"]


def main():
    ap = argparse.ArgumentParser(description="三点定位信标方案放置质量后处理(四规则)")
    ap.add_argument("--plan", default=str(DEFAULT_PLAN), help="输入信标方案 JSON")
    ap.add_argument("--target", default=None, help="目标点 JSON(路线方案), 缺省=walkable 1m 网格")
    ap.add_argument("--out", default=None, help="输出 refined 方案 JSON(缺省=输入名_refined.json)")
    ap.add_argument("--review-only", action="store_true", help="只评审不修改")
    ap.add_argument("--max-new", type=int, default=MAX_NEW, help="允许新增信标上限")
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args()

    plan_path = Path(args.plan)
    geo = json.load(open(GEO, encoding="utf-8"))
    plan = json.load(open(plan_path, encoding="utf-8"))
    target = json.load(open(args.target, encoding="utf-8")) if args.target else None

    if args.out:
        out_path = Path(args.out)
    else:
        out_path = plan_path.with_name(plan_path.stem + "_refined.json")

    review = {"inputPlan": plan_path.name,
              "target": Path(args.target).name if args.target else "walkable-grid-1m",
              "params": {"cornerClear": CORNER_CLEAR, "obstacleClear": OBST_CLEAR,
                         "triMinArea": EPS_AREA, "nnTarget": NN_TARGET, "nnMax": NN_MAX},
              "floors": [], "newBeacons": 0}

    refined_plan = json.loads(json.dumps(plan))
    for fl in ["1", "2"]:
        print(f"\n===== Floor {fl} =====", flush=True)
        model = build_model(geo, plan, fl, target, seed=args.seed)
        before = model.review()
        print("基线:", json.dumps(before, ensure_ascii=False), flush=True)

        if not args.review_only:
            n_unified = 0
            # 1. 确定性避角/避障/迁出封闭空间(先修硬性违反, 可多轮)
            for _ in range(3):
                if (fix_corner_pass(model) == 0 and fix_obstacle_pass(model) == 0
                        and fix_enclosed_pass(model) == 0):
                    break
            # 2. Lloyd 式原位松弛(均匀 + 走廊侧平衡 + 三角改善)
            for rnd in range(MAX_RELAX_ROUNDS):
                m = relax_pass(model, rnd)
                if m == 0:
                    break
            # 3. 统一代价函数补点(R1/R3/R4 同一收益尺度竞争共享配额)。
            #    与 relax 交替执行: 松弛会移动信标改变局部 vis3(可能制造新的
            #    退化三角), 交替让补点能修复松弛引入的问题, 直至配额用尽或收敛。
            n_unified = 0
            for rnd in range(3):
                n_unified += len(unified_add_pass(
                    model, max(0, args.max_new - n_unified)))
                if n_unified >= args.max_new:
                    break
                if relax_pass(model, rnd) == 0:
                    break
            # 记录仍超阈的间距: 这些空档受「避墙角/无可贴墙面」几何约束无法补点,
            # 需人工评估(如启用立柱安装或调整测试路线), 不应被 CV 数字掩盖。
            residual = []
            for i in range(len(model.beacons)):
                d = model.nn_dist(i)
                if d > NN_MAX:
                    bx, by = model.beacons[i]
                    residual.append({"coordinates": [round(bx, 2), round(by, 2)],
                                     "nn_m": round(d, 2)})
            residual.sort(key=lambda r: -r["nn_m"])
            evenness_note = {
                "unifiedAdded": n_unified,
                "residualGaps": len(residual),
                "residualDetail": residual[:10],
            }
        else:
            n_unified = 0
            evenness_note = None

        after = model.review()
        print("refined:", json.dumps(after, ensure_ascii=False), flush=True)

        # 同步回 plan: 原信标更新坐标 + 追加新增信标
        orig_entries = [b for b in refined_plan["beacons"] if str(b["floor"]) == fl]
        n_orig = len(orig_entries)
        max_minor = max((b.get("minor", 0) for b in refined_plan["beacons"]), default=0)
        new_minor = max_minor
        seq = 1
        for i, entry in enumerate(orig_entries):
            new_c = model.beacons[i]
            entry["coordinates"] = [round(new_c[0], 3), round(new_c[1], 3)]
            entry["plannedCoordinates"] = [round(new_c[0], 3), round(new_c[1], 3)]
            entry["placementRefined"] = True
        for j in range(n_orig, len(model.beacons)):
            new_minor += 1
            new_c = model.beacons[j]
            entry = {
                "beaconId": f"BK-Q-F{fl}-{seq:03d}",
                "uuid": plan["uuid"],
                "major": int(fl),
                "minor": new_minor,
                "coordinates": [round(new_c[0], 3), round(new_c[1], 3)],
                "plannedCoordinates": [round(new_c[0], 3), round(new_c[1], 3)],
                "floor": int(fl),
                "locationDesc": "放置质量补点(走廊双侧/均匀/三角破共线, 贴墙 2.2m, 非天花板)",
                "mountType": "wall",
                "installHeight": 2.2,
                "txPower": TX_POWER,
                "broadcastInterval": 300,
                "batteryModel": "CR2477",
                "expectedLifespan": 5,
                "semanticTag": "placement_quality_fill",
                "sourceNodeId": None,
                "sourceNodeType": "placement_quality",
                "riskLevel": "low",
                "snapDist_m": 0.0,
                "subType": "quality_fill",
            }
            refined_plan["beacons"].append(entry)
            seq += 1

        n_added = len(model.beacons) - n_orig
        print(f"  F{fl} 新增信标: {n_added} (统一代价补点 {n_unified})", flush=True)
        floor_rec = {"floor": fl, "before": before, "after": after, "added": n_added}
        if evenness_note:
            floor_rec["evenness"] = evenness_note
            if evenness_note["residualGaps"]:
                print(f"  F{fl} 仍超阈间距(>{NN_MAX}m): {evenness_note['residualGaps']} 处 "
                      f"(受避墙角/无贴墙面约束, 需人工评估)", flush=True)
        review["floors"].append(floor_rec)
        review["newBeacons"] += n_added

        keys = ["corner_rate", "obstacle_rate", "nn_cv", "corridors_both",
                "pct_ge3", "tri_degen_rate"]
        print("  F%s 变化: " % fl + " | ".join(
            f"{k}: {before[k]} -> {after[k]}" for k in keys), flush=True)

    if not args.review_only:
        normalize_mount_types(refined_plan)
        rebuild_summary(refined_plan)
        json.dump(refined_plan, open(out_path, "w", encoding="utf-8"),
                  ensure_ascii=False, indent=2)
        review["outputPlan"] = str(out_path)
        review["summary"] = refined_plan["summary"]
        print(f"\nrefined 方案已写入: {out_path}", flush=True)
        print(f"新增信标总数: {review['newBeacons']}", flush=True)
        print(f"summary 已重算: {json.dumps(refined_plan['summary'], ensure_ascii=False)}",
              flush=True)

    review_path = ROOT / "result" / "trilateration_placement_review.json"
    json.dump(review, open(review_path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print(f"评审报告已写入: {review_path}", flush=True)


if __name__ == "__main__":
    main()
