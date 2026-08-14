# -*- coding: utf-8 -*-
"""7 项检查补充分析：走廊两侧口径对齐 refine（走廊多边形长轴）+ 第 7 条深挖"""
import json
import math
from collections import defaultdict

from shapely.geometry import LineString, Point, Polygon, shape

GEO = json.load(open("result/school_building_01_map_v9.geojson", encoding="utf-8"))
PLAN = json.load(open("result/beacon_deployment_plan_trilateration_routes_refined.json", encoding="utf-8"))
FP = json.load(open("result/fingerprint_grid_routes.json", encoding="utf-8"))

beacons = PLAN["beacons"]
by_floor = defaultdict(list)
for b in beacons:
    by_floor[b["floor"]].append(b)

fp_by_floor = defaultdict(list)
for fk_str, fdata in (FP.get("floors") or {}).items():
    for p in fdata.get("points", []):
        fp_by_floor[fk_str].append(Point(p["coordinates"]))


def long_axis(poly):
    c = list(poly.minimum_rotated_rectangle.exterior.coords)
    edges = sorted([(math.hypot(c[i + 1][0] - c[i][0], c[i + 1][1] - c[i][1]), c[i], c[i + 1])
                    for i in range(len(c) - 1)], reverse=True)
    a, b = edges[0][1], edges[0][2]
    ax, ay = b[0] - a[0], b[1] - a[1]
    alen = math.hypot(ax, ay) or 1e-9
    return ((poly.centroid.x, poly.centroid.y), (ax / alen, ay / alen))


def corridor_side(pt, axis):
    a, (ax, ay) = axis
    cross = ax * (pt[1] - a[1]) - ay * (pt[0] - a[0])
    return 1 if cross > 1e-6 else (-1 if cross < -1e-6 else 0)


# 走廊：roomType=corridor 的开放空间多边形
fg1 = GEO["floors"]["1"]["geometry"]
fg2 = GEO["floors"]["2"]["geometry"]


def get_corridors(fg):
    cors = []
    for f in fg["rooms"]:
        pr = f.get("properties", {})
        if (pr.get("type") or pr.get("roomType")) == "corridor":
            try:
                cors.append((f.get("id"), shape(f["geometry"])))
            except Exception:
                pass
    return cors


cor1, cor2 = get_corridors(fg1), get_corridors(fg2)
axes1 = [(cid, p, long_axis(p)) for cid, p in cor1]
axes2 = [(cid, p, long_axis(p)) for cid, p in cor2]

print("=" * 70)
print("[2-对齐] 走廊两侧分开部署（走廊多边形+长轴口径，|offset|<=1.2m 视为在走廊内）")
for fk, axes, blist in ((1, axes1, by_floor[1]), (2, axes2, by_floor[2])):
    side_cnt = defaultdict(int)
    detail = []
    for b in blist:
        p = Point(b["coordinates"])
        # 找所在走廊（点在走廊多边形 ±1.2m 缓冲内）
        best = None
        for cid, poly, axis in axes:
            d = p.distance(poly)
            if d <= 1.2 and (best is None or d < best[0]):
                best = (d, cid, axis)
        if best is None:
            side_cnt["outside_corridor"] += 1
            detail.append((b["beaconId"], "corridor外", None))
        else:
            d, cid, axis = best
            s = corridor_side((p.x, p.y), axis)
            side_cnt["L" if s > 0 else ("R" if s < 0 else "mid")] += 1
            detail.append((b["beaconId"], "L" if s > 0 else ("R" if s < 0 else "mid"), cid))
    print(f"  F{fk}: {dict(side_cnt)} 共{len(blist)}")
    l = side_cnt.get("L", 0)
    r = side_cnt.get("R", 0)
    print(f"    {'✅ 两侧均有' if l and r else '❌ 缺少一侧'}"
          f"（L={l} R={r} mid={side_cnt.get('mid',0)} 走廊外={side_cnt.get('outside_corridor',0)}）")

print()
print("=" * 70)
print("[7-深挖] 测试路线相关空间分析")
print("  路线指纹点: F1 %d / F2 %d" % (len(fp_by_floor.get("1", [])), len(fp_by_floor.get("2", []))))

# 每信标到最近路线点距离 + 所在走廊是否与路线相交
for fk, axes, blist in ((1, axes1, by_floor[1]), (2, axes2, by_floor[2])):
    fps = fp_by_floor.get(str(fk), [])
    if not fps:
        continue
    print(f"\n  --- F{fk}（{len(blist)} 信标）---")
    # 路线点外接半径
    from shapely.geometry import MultiPoint
    route_hull = MultiPoint(fps).convex_hull
    far = []
    for b in blist:
        p = Point(b["coordinates"])
        d = min(p.distance(q) for q in fps)
        b["routeDist"] = d
        if d > 6.0:
            far.append(b)
    far.sort(key=lambda x: -x["routeDist"])
    print(f"  距路线 >6m: {len(far)}/{len(blist)}")
    for b in far:
        print(f"    {b['beaconId']} F{fk} 距路线{b['routeDist']:.1f}m | {b.get('locationDesc','')[:38]} | {b.get('semanticTag','')}")

# 检查这些远距补点对路线覆盖的贡献：剔除后 pct_ge3 变化
print()
print("=" * 70)
print("[7-贡献] 剔除路线外信标后路线覆盖变化（验证这些信标是否多余）")
