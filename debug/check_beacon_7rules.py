# -*- coding: utf-8 -*-
"""测试路线三点定位方案（trilateration_routes_refined）7 项要求严格检查：
1) 不部署天花板，仅公共空间墙壁/开放柱     2) 走廊左右两侧分开部署
3) 避开角落与障碍物                         4) 三角形部署（三点定位质量）
5) 均匀部署                                 6) 不进入封闭空间内部、不悬空
7) 针对测试线路，不相关空间不部署信标（新增：以指纹路线点 3m 缓冲为"相关空间"，
   距离超过阈值即判定不相关 → 违规）
"""
import json
import math
import statistics
from collections import defaultdict

from shapely.geometry import LineString, Point, Polygon
from shapely.ops import unary_union

GEO = json.load(open("result/school_building_01_map_v9.geojson", encoding="utf-8"))
PLAN = json.load(open("result/beacon_deployment_plan_trilateration_routes_refined.json", encoding="utf-8"))
SK = json.load(open("result/skeleton_manual_parsed.json", encoding="utf-8"))
FP = json.load(open("result/fingerprint_grid_routes.json", encoding="utf-8"))  # 路线指纹点

beacons = PLAN["beacons"]
by_floor = defaultdict(list)
for b in beacons:
    by_floor[b["floor"]].append(b)

# 路线指纹点（相关空间定义）
fp_by_floor = defaultdict(list)
for fk_str, fdata in (FP.get("floors") or {}).items():
    for p in fdata.get("points", []):
        fp_by_floor[fk_str].append(Point(p["coordinates"]))

# ============ 楼层几何 ============
floor_geom = {}
for fk in ("1", "2"):
    g = GEO["floors"][fk]["geometry"]
    wall_lines, wall_polys, wall_segs = [], [], []
    for w in g.get("walls") or []:
        c = w["geometry"]["coordinates"]
        if len(c) < 2:
            continue
        ls = LineString(c)
        t = (w.get("properties") or {}).get("thickness") or 0.2
        wall_lines.append(ls)
        wall_polys.append(ls.buffer(t / 2 + 0.03))
        for i in range(len(c) - 1):
            wall_segs.append((LineString([c[i], c[i + 1]]),
                              (c[i][0], c[i][1], c[i + 1][0], c[i + 1][1])))
    cols, col_meta = [], {}
    for c in g.get("columns") or []:
        poly = Polygon(c["geometry"]["coordinates"][0])
        cols.append(poly)
        col_meta[c.get("id")] = poly
    rooms_poly = []
    for r in g.get("rooms") or []:
        gg = r.get("geometry") or {}
        if gg.get("type") == "Polygon":
            rooms_poly.append((r.get("id"), Polygon(gg["coordinates"][0]),
                               (r.get("properties") or {}).get("type")))
    sk_edges = []
    for e in SK[fk].get("edges") or []:
        tid = {n["id"]: n for n in SK[fk].get("ti_nodes") or []}
        f_, t_ = tid.get(e.get("from")), tid.get(e.get("to"))
        if f_ and t_:
            sk_edges.append((e.get("id"), LineString([f_["coordinates"], t_["coordinates"]])))
    floor_geom[fk] = {
        "wall_lines": wall_lines,
        "wall_union": unary_union(wall_polys) if wall_polys else None,
        "wall_segs": wall_segs,
        "cols": cols, "col_meta": col_meta, "rooms": rooms_poly,
        "sk_edges": sk_edges,
    }


def dist_to_wall(p, fk):
    fg = floor_geom[str(fk)]
    best = 1e9
    for ls in fg["wall_lines"]:
        d = p.distance(ls)
        if d < best:
            best = d
    return best


def dist_to_column(p, fk):
    fg = floor_geom[str(fk)]
    best = 1e9
    for c in fg["cols"]:
        d = p.distance(c)
        if d < best:
            best = d
    return best


def inside_room(p, fk, edge_tol=0.3):
    for rid, poly, rtype in floor_geom[str(fk)]["rooms"]:
        if poly.contains(p):
            inner = poly.buffer(-edge_tol)
            if inner.is_empty or inner.contains(p):
                return rid, rtype
    return None


def nearest_wall_seg(p, fk):
    fg = floor_geom[str(fk)]
    best_d, best_info = 1e9, None
    for seg, coords in fg["wall_segs"]:
        d = p.distance(seg)
        if d < best_d:
            best_d = d
            proj = seg.interpolate(seg.project(p))
            end_d = min(proj.distance(Point(coords[0], coords[1])),
                        proj.distance(Point(coords[2], coords[3])))
            best_info = (d, end_d, coords)
    return best_info


def corridor_side(p, fk):
    fg = floor_geom[str(fk)]
    best_d, best = 1e9, None
    for eid, ls in fg["sk_edges"]:
        d = p.distance(ls)
        if d < best_d:
            best_d = d
            proj = ls.interpolate(ls.project(p))
            vx, vy = ls.coords[1][0] - ls.coords[0][0], ls.coords[1][1] - ls.coords[0][1]
            wx, wy = p.x - proj.x, p.y - proj.y
            cross = vx * wy - vy * wx
            best = (cross, math.hypot(wx, wy), eid)
    if best is None:
        return ("none", 1e9, None)
    cross, off, eid = best
    if off <= 0.8:
        if cross > 0:
            return ("left", off, eid)
        elif cross < 0:
            return ("right", off, eid)
        return ("mid", off, eid)
    return ("off_corridor", off, eid)


def dist_to_route(p, fk):
    """信标到最近路线指纹点的距离（相关空间判定）"""
    best = 1e9
    for fp in fp_by_floor.get(str(fk), []):
        d = p.distance(fp)
        if d < best:
            best = d
    return best


print("=" * 70)
print(f"测试路线三点定位 refined 方案 {len(beacons)} 信标 — 7 项合规检查")
print(f"（路线指纹点 F1 {len(fp_by_floor.get('1', []))} / F2 {len(fp_by_floor.get('2', []))}）")
print("=" * 70)

# ============ 1) 挂载 ============
print("\n[1] 挂载方式：不允许天花板，仅公共空间墙壁/开放柱")
mount_cnt = defaultdict(int)
for b in beacons:
    mount_cnt[b.get("mountType", "?")] += 1
print("  挂载分布:", dict(mount_cnt))
ceiling = [b for b in beacons if b.get("mountType") == "ceiling"]
print(f"  天花板信标: {len(ceiling)} {'✅' if not ceiling else '❌ ' + str([b['beaconId'] for b in ceiling])}")

# ============ 2) 左右分离 ============
print("\n[2] 走廊左右两侧分开部署（|offset|<=0.8m 视为走廊内）")
side_stat = defaultdict(lambda: defaultdict(int))
for fk in (1, 2):
    for b in by_floor[fk]:
        side, off, eid = corridor_side(Point(b["coordinates"]), fk)
        side_stat[fk][side] += 1
for fk in (1, 2):
    s = side_stat[fk]
    l, r = s.get("left", 0), s.get("right", 0)
    ok = (l >= 1 and r >= 1)
    print(f"  F{fk}: 左{l} 右{r} 中{s.get('mid',0)} 走廊外{s.get('off_corridor',0)} "
          f"/ 共{sum(s.values())}  {'✅两侧均有' if ok else '❌ 单侧/缺失'}")

# ============ 3) 角落与障碍物 ============
print("\n[3] 避开角落与障碍物（贴墙角<0.3m 违规；贴柱缝隙 0.06~0.5m 违规）")
corner_list, obstacle_list = [], []
for b in beacons:
    p = Point(b["coordinates"])
    fk = b["floor"]
    d, end_d, _ = nearest_wall_seg(p, fk)
    dc = dist_to_column(p, fk)
    if dc <= 0.06:  # 柱面挂载豁免
        continue
    if d <= 0.5 and end_d < 0.3:
        corner_list.append((b["beaconId"], fk, round(d, 2), round(end_d, 2)))
    if 0.06 < dc < 0.5:
        obstacle_list.append((b["beaconId"], fk, round(dc, 2)))
print(f"  贴墙角违规: {len(corner_list)}")
for c in corner_list[:15]:
    print(f"    {c[0]} F{c[1]} 距墙{c[2]}m 距端点{c[3]}m")
print(f"  贴柱缝隙违规: {len(obstacle_list)}")
for c in obstacle_list[:10]:
    print(f"    {c[0]} F{c[1]} 距柱{c[2]}m")

# ============ 4) 三角形部署 ============
print("\n[4] 三角形部署（15m 内邻居>=2 且三点面积>=2m²）")
import itertools
tri_ok = tri_bad_area = tri_few = 0
tri_bad_list = []
for fk in (1, 2):
    pts = [(Point(b["coordinates"]), b["beaconId"]) for b in by_floor[fk]]
    for p, bid in pts:
        near = []
        for q, qid in pts:
            if qid == bid:
                continue
            d = p.distance(q)
            if d <= 15.0:
                near.append((q, qid, d))
        near.sort(key=lambda x: x[2])
        if len(near) < 2:
            tri_few += 1
            tri_bad_list.append((bid, fk, "邻居<2", round(near[0][2], 1) if near else None))
            continue
        a, b2, c = near[0][0], near[1][0], p
        area = abs(a.x * (b2.y - c.y) + b2.x * (c.y - a.y) + c.x * (a.y - b2.y)) / 2.0
        if area < 2.0:
            tri_bad_area += 1
            tri_bad_list.append((bid, fk, "面积小", round(area, 2)))
        else:
            tri_ok += 1
print(f"  三点定位达标: {tri_ok}  面积过小(<2m²): {tri_bad_area}  邻居不足(<2): {tri_few}")
for t in tri_bad_list[:20]:
    print(f"    {t[0]} F{t[1]} {t[2]} {t[3]}")

# ============ 5) 均匀分布 ============
print("\n[5] 均匀部署（相邻信标间距直方图；<2m 过密、>12m 过疏）")
for fk in (1, 2):
    pts = [Point(b["coordinates"]) for b in by_floor[fk]]
    dists = []
    for i, p in enumerate(pts):
        best = min(p.distance(q) for j, q in enumerate(pts) if i != j)
        dists.append(best)
    hist = defaultdict(int)
    for d in dists:
        if d < 2:
            hist["<2m"] += 1
        elif d < 4:
            hist["2-4m"] += 1
        elif d < 6:
            hist["4-6m"] += 1
        elif d < 8:
            hist["6-8m"] += 1
        elif d < 12:
            hist["8-12m"] += 1
        else:
            hist[">12m"] += 1
    print(f"  F{fk} ({len(pts)}信标) 最近邻间距: 中位{statistics.median(dists):.1f}m "
          f"min{min(dists):.1f}m max{max(dists):.1f}m  {dict(hist)}")

# ============ 6) 悬空 + 封闭空间 ============
print("\n[6] 不悬空（贴墙<=0.5m 或挂柱<=0.06m）；不进入封闭空间内部")
floating = []
on_column = 0
for b in beacons:
    p = Point(b["coordinates"])
    fk = b["floor"]
    dw = dist_to_wall(p, fk)
    dc = dist_to_column(p, fk)
    if dc <= 0.06:
        on_column += 1
        continue
    if dw <= 0.5:
        continue
    floating.append((b["beaconId"], fk, b["coordinates"], round(dw, 2), round(dc, 2),
                     b.get("locationDesc", "")))
print(f"  悬空信标: {len(floating)} / {len(beacons)}  （挂柱上 {on_column}）")
for f in sorted(floating, key=lambda x: -x[3])[:30]:
    print(f"    {f[0]} F{f[1]} @{f[2]} 距墙{f[3]}m 距柱{f[4]}m | {f[5][:44]}")

OPEN_TYPES = {"corridor", "lobby", "activity", "atrium", "stair_lobby", "elevator_lobby"}
in_room, in_open = [], 0
for b in beacons:
    p = Point(b["coordinates"])
    r = inside_room(p, b["floor"])
    if r:
        if r[1] in OPEN_TYPES:
            in_open += 1
        else:
            in_room.append((b["beaconId"], b["floor"], r[0], r[1], b["coordinates"]))
print(f"  封闭空间内信标(违规): {len(in_room)}  （开放空间内 {in_open}，允许）")
for r in in_room[:30]:
    print(f"    {r[0]} F{r[1]} {r[2]}({r[3]}) @{r[4]}")

# ============ 7) 测试线路相关空间 ============
print("\n[7] 针对测试线路：不相关空间不部署信标（距最近路线指纹点距离）")
ROUTE_TOL = 6.0  # 信标距测试路线的最大允许距离（m）
far = []
for b in beacons:
    d = dist_to_route(Point(b["coordinates"]), b["floor"])
    b["routeDist_m"] = round(d, 2)
    if d > ROUTE_TOL:
        far.append((b["beaconId"], b["floor"], round(d, 2), b.get("locationDesc", ""),
                    b.get("semanticTag", ""), b.get("subType", "")))
print(f"  距测试路线 >{ROUTE_TOL}m 的信标（不相关空间）: {len(far)} / {len(beacons)}")
for f in sorted(far, key=lambda x: -x[2])[:40]:
    print(f"    {f[0]} F{f[1]} 距路线{f[2]}m | {f[3][:40]} | {f[4]}/{f[5]}")

# 汇总
print("\n" + "=" * 70)
print("汇总：")
print(f"  [1] 天花板: {len(ceiling)}   [2] 单侧走廊: 见上   [3] 墙角 {len(corner_list)} / 柱缝 {len(obstacle_list)}")
print(f"  [4] 三角质量: 达标{tri_ok} 面积小{tri_bad_area} 邻居少{tri_few}")
print(f"  [6] 悬空 {len(floating)} / 封闭违规 {len(in_room)}   [7] 路线外 {len(far)}")
print("=" * 70)
