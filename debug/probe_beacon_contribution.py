# -*- coding: utf-8 -*-
"""验证：剔除路线外 BK-Q-* 补点后，测试路线指纹点 >=3 覆盖是否变化。
用与 gen_trilateration_plan_routes.py 一致的射线穿墙 RSSI 模型。"""
import json
import math
from collections import defaultdict

from shapely.geometry import LineString, Point
from shapely.strtree import STRtree

GEO = json.load(open("result/school_building_01_map_v9.geojson", encoding="utf-8"))
PLAN = json.load(open("result/beacon_deployment_plan_trilateration_routes_refined.json", encoding="utf-8"))
FP = json.load(open("result/fingerprint_grid_routes.json", encoding="utf-8"))

TX_POWER = -10
RSSI_REF_1M = -50
N = 3.5
WALL_ATTEN = {"brick": 12, "concrete": 15, "partition": 8, "glass": 6, None: 12}
VISIBLE = -85
D_MAX = 11.0

# 墙体线段（按楼层）+ STRtree
wall_trees = {}
for fk in ("1", "2"):
    segs = []
    for w in GEO["floors"][fk]["geometry"].get("walls") or []:
        c = w["geometry"]["coordinates"]
        if len(c) < 2:
            continue
        t = (w.get("properties") or {}).get("thickness") or 0.2
        mat = (w.get("properties") or {}).get("material")
        segs.append((LineString(c), WALL_ATTEN.get(mat, 12), t))
    wall_trees[fk] = STRtree([s[0] for s in segs]), segs


def visible(p, b, fk):
    """射线穿墙判定：返回 RSSI；<VISIBLE 或超距不可见"""
    d = p.distance(b)
    if d > D_MAX:
        return None
    # 穿墙衰减
    wall_loss = 0.0
    hit = wall_trees[fk][0].query(LineString([p, b]))
    for i in hit:
        seg, atten, t = wall_trees[fk][1][i]
        inter = seg.intersection(LineString([p, b]))
        if inter.is_empty:
            continue
        if hasattr(inter, "length"):
            wall_loss += atten * inter.length / (t if t else 0.2) * 0.05
        else:
            wall_loss += atten * 0.5
    rssi = RSSI_REF_1M - 10 * N * math.log10(max(d, 0.1)) - wall_loss
    return rssi


beacons = PLAN["beacons"]
by_floor = defaultdict(list)
for b in beacons:
    by_floor[b["floor"]].append(b)

# 路线外补点（routeDist > 6m）
fp_by_floor = defaultdict(list)
for fk_str, fdata in (FP.get("floors") or {}).items():
    for p in fdata.get("points", []):
        fp_by_floor[fk_str].append(Point(p["coordinates"]))

outside = set()
for b in beacons:
    fps = fp_by_floor.get(str(b["floor"]), [])
    d = min(Point(b["coordinates"]).distance(q) for q in fps) if fps else 1e9
    if d > 6.0:
        outside.add(b["beaconId"])

print(f"路线外补点: {len(outside)}")
print(sorted(outside)[:40])


def coverage(bset, fk):
    """返回 (ge3, total, pct)"""
    ge3 = 0
    total = 0
    for fp in fp_by_floor.get(str(fk), []):
        total += 1
        cnt = 0
        for b in bset:
            if b["floor"] != int(fk):
                continue
            if visible(fp, Point(b["coordinates"]), fk) is not None:
                cnt += 1
        if cnt >= 3:
            ge3 += 1
    return ge3, total, (ge3 / total * 100 if total else 0)


for fk in ("1", "2"):
    full = [b for b in beacons if b["floor"] == int(fk)]
    reduced = [b for b in full if b["beaconId"] not in outside]
    g1, t1, p1 = coverage(full, fk)
    g2, t2, p2 = coverage(reduced, fk)
    print(f"\nF{fk}: 全量 {len(full)} 信标 -> >=3覆盖 {g1}/{t1} = {p1:.2f}%")
    print(f"F{fk}: 剔除{len(full)-len(reduced)} 个路线外补点 -> >=3覆盖 {g2}/{t2} = {p2:.2f}%")
    print(f"  覆盖变化: {p2 - p1:+.2f} 个百分点")
