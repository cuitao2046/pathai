"""P0 布点增强：按「有效范围 5m」保证 F1 全路线点 ≥3 信标覆盖，并消除 GDOP>3 退化点。

仅修改 F1（F2 已达标，按需求不动）；新增信标沿用 BK-TR-F1-0XX 编号与 route_fill schema。
覆盖缺口 → 最缺优先贪心，沿廊+横廊二维偏移网格搜索候选位（绝不原地堆叠）。
GDOP 退化 → 选对该点 GDOP 降幅最大的跨走廊偏移补点。进度守卫防止死循环。依赖：numpy。
运行：python -m src.tools.improve_p0_coverage
"""
import json
import math
import os
import re

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DEP = os.path.join(ROOT, "result", "ble_deployment.json")
ROUTE = os.path.join(ROOT, "result", "fingerprint_grid_routes.json")

R_COVER = 5.0          # 用户要求的「降级有效范围」：在此半径内须有 ≥3 信标
NEED = 3               # 覆盖所需最少信标数
R_GDOP = 3.0           # GDOP 退化阈值
MIN_SPACING = 2.5      # 新信标与既有/新信标的最小间距（避免人为制造过密对）
UUID = "B9407F30-F5F8-466E-AFF9-25556B57FE6D"
GDOP_INF = 999.0       # 奇异（共线/重合）时返回的有限大值，便于统计


def gdop(p, A):
    """3 枚最近信标对点 p 的几何精度因子（越小越好）。"""
    A = np.asarray(A, dtype=float)
    d = np.linalg.norm(A - p, axis=1)
    if d.min() < 1e-6:
        return GDOP_INF
    G = (A - p) / d[:, None]                       # 3x2 单位观测向量
    try:
        Q = np.linalg.inv(G.T @ G)
    except np.linalg.LinAlgError:
        return GDOP_INF
    return math.sqrt(Q[0, 0] + Q[1, 1])


def load():
    dep = json.load(open(DEP, encoding="utf-8"))
    rt = json.load(open(ROUTE, encoding="utf-8"))
    return dep, rt


def tangent_at(pts, i):
    a = pts[max(0, i - 1)]
    b = pts[min(len(pts) - 1, i + 1)]
    t = b - a
    n = np.linalg.norm(t)
    return t / n if n > 1e-9 else np.array([1.0, 0.0])


def normal_at(pts, i):
    t = tangent_at(pts, i)
    return np.array([-t[1], t[0]])


def offset_grid():
    """沿廊(a) × 横廊(c) 二维候选偏移网格。"""
    along = [0, 2.0, 3.0, 4.0, -2.0, -3.0, -4.0]
    cross = [0, 1.5, 2.2, 3.0, -1.5, -2.2, -3.0]
    out = []
    for a in along:
        for c in cross:
            if a == 0 and c == 0:
                continue
            out.append((a, c))
    return out


def coverage_stats(existing, pts, r, need):
    covered = sum(1 for p in pts
                  if sum(1 for c in existing if np.linalg.norm(c - p) <= r) >= need)
    return covered, len(pts)


def gdop_stats(existing, pts, thr):
    degen = 0
    worst = 0.0
    E = np.asarray(existing)
    for p in pts:
        D = np.linalg.norm(E - p, axis=1)
        order = np.argsort(D)[:3]
        g = gdop(p, E[order])
        worst = max(worst, g)
        if g > thr:
            degen += 1
    return degen, len(pts), worst


def main():
    dep, rt = load()
    beacons = dep["beacons"]

    f1_existing = [np.array(b["coordinates"], dtype=float)
                   for b in beacons if int(b["floor"]) == 1]
    f1_pts = [np.array(p["coordinates"], dtype=float)
              for p in rt["floors"]["1"]["points"]]

    before_cov = coverage_stats(f1_existing, f1_pts, R_COVER, NEED)
    before_gd = gdop_stats(f1_existing, f1_pts, R_GDOP)

    idx_re = re.compile(r"BK-TR-F1-(\d+)")
    idxs = [int(idx_re.search(b["beaconId"]).group(1))
            for b in beacons if idx_re.search(b["beaconId"])]
    minor_used = {b.get("minor") for b in beacons if isinstance(b.get("minor"), int)}
    next_idx = (max(idxs) if idxs else 0) + 1
    next_minor = max(minor_used) + 1

    new = []
    new_coords = []

    def make_beacon(c):
        nonlocal next_idx, next_minor
        next_idx += 1
        m = next_minor
        while m in minor_used:
            m += 1
        next_minor = m + 1
        minor_used.add(m)
        return {
            "beaconId": f"BK-TR-F1-{next_idx:03d}",
            "uuid": UUID,
            "major": 1,
            "minor": m,
            "coordinates": [round(float(c[0]), 3), round(float(c[1]), 3)],
            "floor": 1,
            "locationDesc": "P0 补点(5m覆盖/降GDOP)",
            "mountType": "wall",
            "installHeight": 2.2,
            "txPower": -10,
            "broadcastInterval": 300,
            "batteryModel": "CR2477",
            "expectedLifespan": 5,
            "semanticTag": "trilateration_route_fill",
            "sourceNodeId": None,
            "sourceNodeType": "route_fill",
            "riskLevel": "low",
            "subType": "fill",
        }

    def all_coords():
        return f1_existing + new_coords

    def placeable(c):
        return all(np.linalg.norm(c - o) >= MIN_SPACING for o in all_coords())

    def cover_count(p):
        return sum(1 for c in all_coords() if np.linalg.norm(c - p) <= R_COVER)

    grid = offset_grid()

    def candidates(p, i):
        t = tangent_at(f1_pts, i)
        n = normal_at(f1_pts, i)
        for a, c in grid:
            yield p + a * t + c * n

    # ---- PASS 1：覆盖补点（最缺优先贪心，二维偏移网格，绝不堆叠） ----
    while True:
        unc = [i for i in range(len(f1_pts)) if cover_count(f1_pts[i]) < NEED]
        if not unc:
            break
        unc.sort(key=lambda i: cover_count(f1_pts[i]))
        placed = False
        for i in unc:
            for cand in candidates(f1_pts[i], i):
                if placeable(cand):
                    new_coords.append(cand)
                    new.append(make_beacon(cand))
                    placed = True
                    break
            if placed:
                break
        if not placed:
            break
        if len(new) > 300:
            break

    # ---- PASS 2：GDOP 退化点补点（选对该点 GDOP 降幅最大的偏移） ----
    for _ in range(5):
        E = np.asarray(all_coords())
        degen = []
        for i, p in enumerate(f1_pts):
            D = np.linalg.norm(E - p, axis=1)
            if gdop(p, E[np.argsort(D)[:3]]) > R_GDOP:
                degen.append(i)
        if not degen:
            break
        for i in degen:
            p = f1_pts[i]
            E = np.asarray(all_coords())
            D = np.linalg.norm(E - p, axis=1)
            base = gdop(p, E[np.argsort(D)[:3]])
            best = None
            best_g = base
            for cand in candidates(p, i):
                if not placeable(cand):
                    continue
                comb = np.vstack([E, cand])
                Dc = np.linalg.norm(comb - p, axis=1)
                g = gdop(p, comb[np.argsort(Dc)[:3]])
                if g < best_g:
                    best_g = g
                    best = cand
            if best is not None:
                new_coords.append(best)
                new.append(make_beacon(best))
        if len(new) > 450:
            break

    beacons.extend(new)
    json.dump(dep, open(DEP, "w", encoding="utf-8"), ensure_ascii=False, indent=2)

    # 读回刚写出的文件再统计，保证打印与落盘一致（避免内存集合统计偏差）
    saved = json.load(open(DEP, encoding="utf-8"))
    saved_f1 = [np.array(b["coordinates"], dtype=float)
                for b in saved["beacons"] if int(b["floor"]) == 1]
    after_cov = coverage_stats(saved_f1, f1_pts, R_COVER, NEED)
    after_gd = gdop_stats(saved_f1, f1_pts, R_GDOP)

    print(f"新增 F1 信标: {len(new)}  (总 F1 {len(f1_existing) + len(new)}, 总 {len(beacons)})")
    print(f"F1 覆盖@5m:  before {before_cov[0]}/{before_cov[1]} "
          f"({before_cov[0]/before_cov[1]*100:.1f}%)  ->  after {after_cov[0]}/{after_cov[1]} "
          f"({after_cov[0]/after_cov[1]*100:.1f}%)")
    print(f"F1 GDOP>3:   before {before_gd[0]}/{before_gd[1]} (worst {before_gd[2]:.1f})"
          f"  ->  after {after_gd[0]}/{after_gd[1]} (worst {after_gd[2]:.1f})")


if __name__ == "__main__":
    main()
