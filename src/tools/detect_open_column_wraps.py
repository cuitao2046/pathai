#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
detect_open_column_wraps.py — 孤立柱墙壁识别（蓝牙信标柱面部署的前置分析）。

目标: 蓝牙信标可部署在「孤立柱子」的墙壁上（四周无遮挡）。本脚本完全基于
现有 GeoJSON 的 columns + walls + 开放空间(rooms + walkable_regions) 做前置分析:
  算法一: 识别「四周开放」的柱 —— 质心环形探测区(不填满柱内)落在开放空间的
          面积占比 + 8 方向射线落点检验 + 封闭房间占比, 判定可自由部署的柱。
  算法二: 识别「包裹这些柱」的墙 —— 对每个开放柱, 找贴柱(d<=d_max)且短段
          (len<=l_max) 的墙, 并按柱心方位角 4 象限覆盖检验是否真正「包裹」。

墙去重: 同一道墙在 CAD 转出时可能被拆成两条近平行线(双线墙)或近似重合的
短段; 先去重再算贴柱距离, 避免同一面墙被重复计入包柱墙。简单可靠策略:
「线到线距离 < 0.02m 且方向平行(<5°)」聚类合并为一条代表段
(STRtree 近邻查询 + 并查集聚类, 代表段 = 以簇内最长段为基准线投影取并集)。

输出:
  result/open_column_wraps.json          (默认, 可 --out 覆盖)
  result/school_building_01_map_v9_columns.geojson  (--write-back 时, 仅当输入
                                                   为默认 GeoJSON 时复制一份,
                                                   不动原文件; 其他输入则原地写)

用法:
  python src/tools/detect_open_column_wraps.py
  python src/tools/detect_open_column_wraps.py --r 1.2 --openness-min 0.65 --debug
  python src/tools/detect_open_column_wraps.py --write-back
"""
from __future__ import annotations
import argparse
import json
import math
from datetime import datetime
from pathlib import Path

from shapely.geometry import LineString, Point, shape
from shapely.ops import unary_union
from shapely.strtree import STRtree

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_GEO = ROOT / "result" / "school_building_01_map_v9.geojson"
DEFAULT_OUT = ROOT / "result" / "open_column_wraps.json"
DEFAULT_GEO_WRITEBACK = ROOT / "result" / "school_building_01_map_v9_columns.geojson"

# ---- 开放空间类型(properties.type): 四周无遮挡的可通行区域 ----
OPEN_SPACE_TYPES = {
    "corridor", "lobby", "atrium", "elevator_lobby", "stair_lobby", "activity",
}

# ---- 默认阈值(均可由 CLI 覆盖) ----
DEFAULT_R = 1.0            # 环形探测半径(m)
DEFAULT_OPENNESS_MIN = 0.6  # 开放度下限
DEFAULT_N_OPEN_MIN = 6      # 8 向射线中落入开放空间的方向数下限
DEFAULT_D_MAX = 0.15        # 贴柱墙最大距离(m)
DEFAULT_L_MAX = 2.0         # 包柱墙最大单段长度(m)
CLOSED_RATIO_MAX = 0.25     # 封闭房间占环形探测区比例上限(固定, 非 CLI 参数)

# ---- 墙去重 / 射线容差 ----
DEDUP_TOL = 0.02            # 线到线距离 < 0.02m 视为同一道墙(双线墙/近似重合)
DEDUP_ANGLE_TOL_DEG = 5.0   # 方向平行容差(度)
RAY_PROBE = 0.01            # 射线落点小缓冲(m), 吸收浮点边界误差
N_DIRS = 8                  # 方向抽样数(每 45°)


def seg_angle(seg):
    """线段整体方向角, 归一化到 [0, pi)（平行判定用, 与方向无关）。"""
    a, b = seg.coords[0], seg.coords[-1]
    return math.atan2(b[1] - a[1], b[0] - a[0]) % math.pi


def dedupe_walls(walls, wall_ids, tol=DEDUP_TOL, angle_tol_deg=DEDUP_ANGLE_TOL_DEG):
    """墙段去重: 把「线到线距离 < tol 且方向平行」的短段合并为一条代表段。

    双线墙/近似重合段在 CAD 转出时常见, 不去重会导致同一面墙被重复计入包柱墙。
    策略(简单可靠):
      1. STRtree 对每段做 tol 缓冲近邻查询, 候选内精确算线线距离;
      2. 距离 < tol 且角度差 < angle_tol_deg -> 并查集归并到同一簇;
      3. 代表段 = 以簇内最长段为基准线, 把所有端点投影到基准方向, 取投影
         区间并集生成一条覆盖整簇的线段(长度=墙的有效延展)。
    注: 代表段落在最长段所在直线上, 与簇内其他线最多相差 tol(0.02m),
        相对 d_max(0.15m) 可忽略。

    返回 [{"id": 主id(簇内最长段), "geom": LineString, "mergedIds": [原id,...]}]
    """
    n = len(walls)
    parent = list(range(n))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    angs = [seg_angle(w) for w in walls]
    tree = STRtree(walls)
    atol = math.radians(angle_tol_deg)
    for i in range(n):
        for j in tree.query(walls[i].buffer(tol)):
            if j <= i:
                continue
            if walls[i].distance(walls[j]) >= tol:
                continue
            diff = abs(angs[i] - angs[j])
            diff = min(diff, math.pi - diff)
            if diff < atol:
                union(i, j)

    groups = {}
    for i in range(n):
        groups.setdefault(find(i), []).append(i)

    reps = []
    for idxs in groups.values():
        base = max(idxs, key=lambda i: walls[i].length)
        s = walls[base]
        a = s.coords[0]
        b = s.coords[-1]
        dx, dy = b[0] - a[0], b[1] - a[1]
        ln = math.hypot(dx, dy) or 1e-9
        ux, uy = dx / ln, dy / ln
        ts = []
        for i in idxs:
            for cx, cy in walls[i].coords:
                ts.append((cx - a[0]) * ux + (cy - a[1]) * uy)
        tmin, tmax = min(ts), max(ts)
        reps.append({
            "id": wall_ids[base],
            "geom": LineString([(a[0] + ux * tmin, a[1] + uy * tmin),
                                (a[0] + ux * tmax, a[1] + uy * tmax)]),
            "mergedIds": [wall_ids[i] for i in idxs],
        })
    return reps


def build_open_closed(fdict, open_types):
    """构造楼层开放空间 O 与封闭房间 R。

    O = 开放类型房间(OPEN_SPACE_TYPES) unary_union;
        若 walkable_regions 有 features, 其多边形一并并入 O(兼容只给了
        walkable 区域的旧数据; 无 features 则跳过不报错)。
    R = 其余类型房间(room/toilet/staircase/infrastructure 等) unary_union。
    返回 (O, R), 均可能为 None(对应集合为空)。
    """
    fg = fdict.get("geometry", {})
    open_polys, closed_polys = [], []
    for f in fg.get("rooms", []):
        pr = f.get("properties", {})
        rtype = pr.get("type") or pr.get("roomType")
        try:
            g = shape(f["geometry"])
        except Exception:
            continue
        if g.is_empty:
            continue
        (open_polys if rtype in open_types else closed_polys).append(g)

    O = unary_union(open_polys) if open_polys else None
    wr = fdict.get("walkable_regions")
    if wr and wr.get("features"):
        wr_polys = [shape(f["geometry"]) for f in wr["features"]
                    if f.get("geometry", {}).get("type") == "Polygon"]
        if wr_polys:
            wr_union = unary_union(wr_polys)
            O = unary_union([O, wr_union]) if O is not None else wr_union
    R = unary_union(closed_polys) if closed_polys else None
    return O, R


def quadrant_of(px, py, x, y):
    """点 (x,y) 相对柱心 (px,py) 的方位象限(0=NE, 1=NW, 2=SW, 3=SE)。"""
    ang = math.degrees(math.atan2(y - py, x - px)) % 360.0
    return int(ang // 90)


def classify_floor(fdict, params, floor_label, debug=False):
    """单楼层分析: 算法一找开放柱, 算法二找包柱墙。

    返回 (floor_rec, col_roles, wall_wrap_map):
      floor_rec     报告 JSON 的 floors[fl] 结构
      col_roles     {柱id: "freestanding_open" | "enclosed"} 供 --write-back
      wall_wrap_map {墙id: [柱id, ...]} 供 --write-back
    """
    fg = fdict.get("geometry", {})
    # ---- 1. 提取柱 / 墙 ----
    col_feats = [f for f in fg.get("columns", [])
                 if f.get("geometry", {}).get("type") == "Polygon"]
    wall_feats = [f for f in fg.get("walls", [])
                  if f.get("geometry", {}).get("type") == "LineString"]

    cols, col_ids = [], []
    for f in col_feats:
        try:
            g = shape(f["geometry"])
        except Exception:
            continue
        if g.is_empty or g.area <= 0:
            continue
        cols.append(g)
        col_ids.append(f.get("id") or f"anon-col-{len(cols)}")

    walls, wall_ids = [], []
    for f in wall_feats:
        try:
            g = shape(f["geometry"])
        except Exception:
            continue
        if g.is_empty or g.length <= 0:
            continue
        walls.append(g)
        wall_ids.append(f.get("id") or f"anon-wall-{len(walls)}")
    reps = dedupe_walls(walls, wall_ids)

    # ---- 2. 开放空间 O / 封闭房间 R ----
    O, R = build_open_closed(fdict, params["openSpaceTypes"])
    closed_ratio_max = params.get("closedRatioMax", CLOSED_RATIO_MAX)

    open_cols, rejected = [], []
    col_roles = {}
    wall_wrap = {}  # 原始墙 id -> [包裹的柱 id, ...]
    total_wrap = 0
    wrapped_count = 0

    for c, cid in zip(cols, col_ids):
        p = c.centroid
        # 环形探测区: 不要 buffer 填满柱内(0.05m 内圈保留柱体本身)
        annulus = c.buffer(params["r"]).difference(c.buffer(0.05))
        a_a = annulus.area
        if a_a <= 1e-12:
            rejected.append({"id": cid, "reason": "openness_low"})
            col_roles[cid] = "enclosed"
            continue
        a_o = annulus.intersection(O).area if O is not None else 0.0
        a_r = annulus.intersection(R).area if R is not None else 0.0
        openness = a_o / (a_a + 1e-9)
        closed_ratio = a_r / (a_a + 1e-9)

        # 方向抽样: 8 向射线落点(带 0.01m 小缓冲吸收边界误差)落入 O 即算开放
        n_open = 0
        for k in range(N_DIRS):
            ang = math.radians(k * 360.0 / N_DIRS)
            pt = Point(p.x + params["r"] * math.cos(ang),
                       p.y + params["r"] * math.sin(ang))
            if O is not None and O.intersects(pt.buffer(RAY_PROBE)):
                n_open += 1

        is_open = (openness >= params["opennessMin"]
                   and n_open >= params["nOpenMin"]
                   and closed_ratio <= closed_ratio_max)
        if not is_open:
            if openness < params["opennessMin"]:
                reason = "openness_low"
            elif n_open < params["nOpenMin"]:
                reason = "rays_low"
            else:
                reason = "closed_ratio_high"
            rejected.append({"id": cid, "reason": reason})
            col_roles[cid] = "enclosed"
            continue

        col_roles[cid] = "freestanding_open"
        # ---- 算法二: 找包裹该开放柱的墙 ----
        wrap_walls = []
        quads = set()
        for rep in reps:
            if rep["geom"].length > params["lMax"]:
                continue
            d = rep["geom"].distance(c)  # 等价于与 c.buffer(d_max) 相交(含容差)
            if d > params["dMax"]:
                continue
            mid = rep["geom"].interpolate(rep["geom"].length / 2)
            q = quadrant_of(p.x, p.y, mid.x, mid.y)
            quads.add(q)
            entry = {
                "id": rep["id"],
                "distM": round(d, 3),
                "lengthM": round(rep["geom"].length, 3),
                "quadrant": q,
            }
            if len(rep["mergedIds"]) > 1:
                entry["mergedFrom"] = rep["mergedIds"]
            wrap_walls.append(entry)
            for wid in rep["mergedIds"]:
                wall_wrap.setdefault(wid, []).append(cid)

        wrapped = len(quads) >= 3
        if wrapped:
            wrapped_count += 1
        total_wrap += len(wrap_walls)
        wrap_walls.sort(key=lambda e: (e["quadrant"], e["id"]))
        open_cols.append({
            "id": cid,
            "centroid": [round(p.x, 3), round(p.y, 3)],
            "openness": round(openness, 3),
            "nOpen": n_open,
            "closedRatio": round(closed_ratio, 3),
            "wrapped": wrapped,
            "quadrantCount": len(quads),
            "wrapWalls": wrap_walls,
        })
        if debug:
            print(f"  [OPEN] {cid}  openness={openness:.3f}  nOpen={n_open}  "
                  f"closedRatio={closed_ratio:.3f}  wrapped={wrapped}  "
                  f"quads={len(quads)}  wrapWalls={len(wrap_walls)}", flush=True)
            for e in wrap_walls:
                print(f"      {e['id']}  dist={e['distM']}m  len={e['lengthM']}m  "
                      f"q={e['quadrant']}", flush=True)

    open_cols.sort(key=lambda e: e["id"])
    rejected.sort(key=lambda e: e["id"])
    floor_rec = {
        "totalColumns": len(cols),
        "openColumns": open_cols,
        "rejectedColumns": rejected,
        "wrapWallSegments": total_wrap,
        "wrappedColumns": wrapped_count,
    }
    print(f"  F{floor_label}: 柱={len(cols)}  开放柱={len(open_cols)}  "
          f"被拒柱={len(rejected)}  包柱墙段={total_wrap}  "
          f"被包裹开放柱={wrapped_count}", flush=True)
    return floor_rec, col_roles, wall_wrap


def write_back(geo, geo_path, col_roles, wall_wrap):
    """写回 GeoJSON(复制一份, 不动原文件)。

    规则: 输入为默认 GeoJSON -> 输出到 result/school_building_01_map_v9_columns.geojson;
          输入为其他文件 -> 原地覆盖写。
    标注: 开放柱 properties.columnRole="freestanding_open";
          其余柱 properties.columnRole="enclosed";
          包裹开放柱的墙 properties.wrapsColumnId(=单柱)/wrapsColumnIds(=多柱)。
    """
    if geo_path.resolve() == DEFAULT_GEO.resolve():
        out_path = DEFAULT_GEO_WRITEBACK
    else:
        out_path = geo_path
    copy = json.loads(json.dumps(geo))
    for fdict in copy["floors"].values():
        fg = fdict.get("geometry", {})
        for f in fg.get("columns", []):
            role = col_roles.get(f.get("id"))
            if role:
                f["properties"]["columnRole"] = role
        for f in fg.get("walls", []):
            cols = wall_wrap.get(f.get("id"))
            if cols:
                f["properties"]["wrapsColumnId"] = cols[0]
                if len(cols) > 1:
                    f["properties"]["wrapsColumnIds"] = cols
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(copy, fh, ensure_ascii=False, indent=2)
    return out_path


def main():
    ap = argparse.ArgumentParser(
        description="孤立柱墙壁识别: 开放柱 + 包柱墙(蓝牙信标柱面部署前置分析)")
    ap.add_argument("--geo", default=str(DEFAULT_GEO), help="输入 GeoJSON")
    ap.add_argument("--out", default=str(DEFAULT_OUT), help="输出报告 JSON")
    ap.add_argument("--write-back", action="store_true",
                    help="写回 GeoJSON(默认输入则复制到 v9_columns.geojson)")
    ap.add_argument("--r", type=float, default=DEFAULT_R, help="环形探测半径(m)")
    ap.add_argument("--openness-min", type=float, default=DEFAULT_OPENNESS_MIN,
                    help="开放度下限")
    ap.add_argument("--n-open-min", type=int, default=DEFAULT_N_OPEN_MIN,
                    help="8 向射线落入开放空间的方向数下限")
    ap.add_argument("--d-max", type=float, default=DEFAULT_D_MAX,
                    help="贴柱墙最大距离(m)")
    ap.add_argument("--l-max", type=float, default=DEFAULT_L_MAX,
                    help="包柱墙最大单段长度(m)")
    ap.add_argument("--debug", action="store_true", help="打印每个开放柱判定明细")
    args = ap.parse_args()

    geo_path = Path(args.geo)
    if not geo_path.exists():
        raise SystemExit(f"GeoJSON 不存在: {geo_path}")
    with open(geo_path, encoding="utf-8") as fh:
        geo = json.load(fh)

    params = {
        "r": args.r,
        "opennessMin": args.openness_min,
        "nOpenMin": args.n_open_min,
        "dMax": args.d_max,
        "lMax": args.l_max,
        "closedRatioMax": CLOSED_RATIO_MAX,
        "openSpaceTypes": sorted(OPEN_SPACE_TYPES),
    }

    report = {
        "venueId": geo.get("venueId", ""),
        "venueName": geo.get("venueName", ""),
        "version": geo.get("version", ""),
        "generatedAt": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "params": params,
        "summary": {"totalColumns": 0, "openColumns": 0,
                    "wrapWallSegments": 0, "perFloor": {}},
        "floors": {},
    }
    col_roles_all = {}
    wall_wrap_all = {}

    print("===== 孤立柱墙壁识别 =====", flush=True)
    print(f"输入: {geo_path}", flush=True)
    for fl in sorted(geo["floors"].keys()):
        print(f"----- Floor {fl} -----", flush=True)
        floor_rec, col_roles, wall_wrap = classify_floor(
            geo["floors"][fl], params, fl, debug=args.debug)
        report["floors"][fl] = floor_rec
        col_roles_all.update(col_roles)
        for wid, cols in wall_wrap.items():
            wall_wrap_all.setdefault(wid, []).extend(cols)
        s = report["summary"]
        s["totalColumns"] += floor_rec["totalColumns"]
        s["openColumns"] += len(floor_rec["openColumns"])
        s["wrapWallSegments"] += floor_rec["wrapWallSegments"]
        s["perFloor"][fl] = {
            "totalColumns": floor_rec["totalColumns"],
            "openColumns": len(floor_rec["openColumns"]),
            "wrapWallSegments": floor_rec["wrapWallSegments"],
            "wrappedColumns": floor_rec["wrappedColumns"],
        }

    out_path = Path(args.out)
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(report, fh, ensure_ascii=False, indent=2)
    print(f"\n报告已写入: {out_path}", flush=True)
    print("汇总: " + json.dumps(report["summary"], ensure_ascii=False), flush=True)

    if args.write_back:
        wb_path = write_back(geo, geo_path, col_roles_all, wall_wrap_all)
        print(f"写回 GeoJSON: {wb_path} (原文件未改动)" if wb_path == DEFAULT_GEO_WRITEBACK
              else f"写回 GeoJSON: {wb_path} (原地覆盖)", flush=True)


if __name__ == "__main__":
    main()
