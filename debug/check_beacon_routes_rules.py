# -*- coding: utf-8 -*-
"""测试路线三点定位信标方案（trilateration_routes_refined）6 项要求检查：
1) 不部署天花板，仅公共空间墙壁/柱子     2) 走廊左右两侧分开部署
3) 避开角落与障碍物                      4) 三角形部署（三点定位质量）
5) 均匀部署                              6) 不进入封闭空间、不悬空
+ 指定柱子 F1-C-0238/F1-C-0192/F1-C-0206 是否被利用
"""
import json
import math
from collections import defaultdict

from shapely.geometry import LineString, Point, Polygon
from shapely.ops import unary_union

GEO = json.load(open("result/school_building_01_map_v9.geojson", encoding="utf-8"))
PLAN = json.load(open("result/beacon_deployment_plan_trilateration_routes_refined.json", encoding="utf-8"))
SK = json.load(open("result/skeleton_manual_parsed.json", encoding="utf-8"))

beacons = PLAN["beacons"]
by_floor = defaultdict(list)
for b in beacons:
    by_floor[b["floor"]].append(b)

# ============ 楼层几何 ============
floor_geom = {}
for fk in ("1", "2"):
    g = GEO["floors"][fk]["geometry"]
    wall_lines, wall_polys = [], []
    wall_segs = []  # (LineString, (x1,y1,x2,y2)) 单段
    for w in g.get("walls") or []:
        c = w["geometry"]["coordinates"]
        if len(c) < 2:
            continue
        ls = LineString(c)
        t = (w.get("properties") or {}).get("thickness") or 0.2
        wall_lines.append(ls)
        wall_polys.append(ls.buffer(t / 2 + 0.03))
        for i in range(len(c) - 1):
            wall_segs.append((LineString([c[i], c[i + 1]]), (c[i][0], c[i][1], c[i + 1][0], c[i + 1][1])))
    cols = []
    col_meta = {}
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
    # 走廊骨架边（TI-TI）
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


def inside_room(p, fk):
    for rid, poly, rtype in floor_geom[str(fk)]["rooms"]:
        if poly.contains(p):
            return rid, rtype
    return None


def nearest_wall_seg(p, fk):
    """最近墙线段及投影点到端点距离"""
    fg = floor_geom[str(fk)]
    best_d, best_info = 1e9, None
    for seg, coords in fg["wall_segs"]:
        d = p.distance(seg)
        if d < best_d:
            best_d = d
            proj = seg.interpolate(seg.project(p))
            end_d = min(proj.distance(Point(coords[0], coords[1])), proj.distance(Point(coords[2], coords[3])))
            best_info = (d, end_d, coords)
    return best_info


def corridor_side(p, fk):
    """基于最近走廊骨架边判断左右侧：返回 (side, offset_m, edge_id)"""
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


# ============ 1) 挂载 ============
print("=" * 62)
print("1) 挂载方式：天花板/墙壁/柱子/门套")
mount_cnt = defaultdict(int)
for b in beacons:
    mount_cnt[b.get("mountType", "?")] += 1
print("挂载分布:", dict(mount_cnt))
print("天花板信标:", mount_cnt.get("ceiling", 0), "（要求 0）✅" if not mount_cnt.get("ceiling") else "❌")

# ============ 2) 左右分离 ============
print("\n" + "=" * 62)
print("2) 走廊左右两侧分开部署（基于走廊骨架边判断，|offset|<=0.8m 视为在走廊内）")
side_stat = defaultdict(lambda: defaultdict(int))
side_detail = defaultdict(list)
for fk in (1, 2):
    for b in by_floor[fk]:
        p = Point(b["coordinates"])
        side, off, eid = corridor_side(p, fk)
        side_stat[fk][side] += 1
        side_detail[fk].append((b["beaconId"], side, round(off, 2)))
for fk in (1, 2):
    s = side_stat[fk]
    total = sum(s.values())
    l, r = s.get("left", 0), s.get("right", 0)
    ok = (l >= 1 and r >= 1)
    print(f"F{fk}: 左 {l}  右 {r}  中 {s.get('mid',0)}  走廊外 {s.get('off_corridor',0)} / 共{total}  "
          f"{'✅两侧均有' if ok else '❌ 单侧或缺失'}")
    if not ok:
        print("   单侧信标:", [x[0] for x in side_detail[fk] if x[1] in ("left", "right")][:12])

# ============ 3) 角落与障碍物 ============
print("\n" + "=" * 62)
print("3) 避开角落与障碍物（距墙段端点<0.3m 视为贴角；距柱<0.2m 但不在柱上视为贴障碍）")
corner_list, obstacle_list = [], []
for b in beacons:
    p = Point(b["coordinates"])
    fk = b["floor"]
    d, end_d, _ = nearest_wall_seg(p, fk)
    if d <= 0.5 and end_d < 0.3:
        corner_list.append((b["beaconId"], fk, round(d, 2), round(end_d, 2)))
    dc = dist_to_column(p, fk)
    if 0.0 < dc < 0.2:
        obstacle_list.append((b["beaconId"], fk, round(dc, 2)))
print(f"贴墙角信标: {len(corner_list)}")
for c in corner_list[:15]:
    print(f"  {c[0]} F{c[1]} 距墙{c[2]}m 距端点{c[3]}m")
print(f"紧贴柱体(距离<0.2m): {len(obstacle_list)}")
for c in obstacle_list[:10]:
    print(f"  {c[0]} F{c[1]} 距柱{c[2]}m")

# ============ 4) 三点定位质量 ============
print("\n" + "=" * 62)
print("4) 三角形部署（三点定位质量）：每信标 15m 内邻居数>=2 且三点构成面积>=2m²")
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
print(f"三点定位达标: {tri_ok}   面积过小(<2m²): {tri_bad_area}   邻居不足(<2): {tri_few}")
for t in tri_bad_list[:20]:
    print(f"  {t[0]} F{t[1]} {t[2]} {t[3]}")

# ============ 5) 均匀分布 ============
print("\n" + "=" * 62)
print("5) 均匀部署（相邻信标间距直方图；<2m 过密、>12m 过疏）")
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
    import statistics
    print(f"F{fk} ({len(pts)}信标) 最近邻间距: 中位{statistics.median(dists):.1f}m "
          f"min{min(dists):.1f}m max{max(dists):.1f}m  {dict(hist)}")

# ============ 6) 悬空 + 封闭空间 ============
print("\n" + "=" * 62)
print("6) 悬空检查（坐标须落在墙/柱上，距墙>0.5m 视为悬空）")
floating = []
on_column = 0
for b in beacons:
    p = Point(b["coordinates"])
    fk = b["floor"]
    dw = dist_to_wall(p, fk)
    dc = dist_to_column(p, fk)
    if dc <= 0.0:
        on_column += 1
        continue
    if dw <= 0.5:
        continue
    floating.append((b["beaconId"], fk, b["coordinates"], round(dw, 2), round(dc, 2),
                     b.get("locationDesc", ""), b.get("snapDist_m")))
print(f"悬空信标: {len(floating)} / {len(beacons)}  （挂柱上 {on_column}）")
for f in sorted(floating, key=lambda x: -x[3])[:30]:
    print(f"  {f[0]} F{f[1]} @{f[2]} 距墙{f[3]}m 距柱{f[4]}m snap={f[6]} | {f[5][:44]}")

print("\n封闭空间内部检查（开放空间 corridor/lobby/activity/atrium 允许部署；"
      "room/staircase 等封闭类型违规）")
OPEN_TYPES = {"corridor", "lobby", "activity", "atrium", "stair_lobby", "elevator_lobby"}
in_room = []
in_open = 0
for b in beacons:
    p = Point(b["coordinates"])
    r = inside_room(p, b["floor"])
    if r:
        if r[1] in OPEN_TYPES:
            in_open += 1
        else:
            in_room.append((b["beaconId"], b["floor"], r[0], r[1], b["coordinates"]))
print(f"封闭空间内信标(违规): {len(in_room)} / {len(beacons)}  （开放空间内 {in_open}，允许）")
for r in in_room[:30]:
    print(f"  {r[0]} F{r[1]} {r[2]}({r[3]}) @{r[4]}")

# ============ 指定柱子利用 ============
print("\n" + "=" * 62)
print("柱子利用检查：F1-C-0238 / F1-C-0192 / F1-C-0206")
for cid in ("F1-C-0238", "F1-C-0192", "F1-C-0206"):
    poly = floor_geom["1"]["col_meta"].get(cid)
    if not poly:
        print(f"  {cid}: 未找到！")
        continue
    ctr = poly.centroid
    near = []
    for b in by_floor[1]:
        d = Point(b["coordinates"]).distance(poly)
        if d < 0.6:
            near.append((b["beaconId"], round(d, 2)))
    print(f"  {cid} @{tuple(round(c,2) for c in ctr.coords[0])}: 附近信标 {near if near else '无（未被利用）'}")
