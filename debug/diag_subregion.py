#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""诊断：L=3m 靶标配置下，未覆盖点的瓶颈在哪（LOS 锚点数 / 最优张角 / 扇区多样性）"""
import json
import math
from pathlib import Path
import shapely.geometry as sg
import importlib.util as _ilu
_spec = _ilu.spec_from_file_location("sp", Path(__file__).parent / "subregion_two_point.py")
sp = _ilu.module_from_spec(_spec)
_spec.loader.exec_module(sp)
cc = sp.cc

valid_polys = cc.load_valid_region()
bld = json.loads(sp.BUILDING.read_text(encoding="utf-8"))

L = 3.0
for fl in ["1F", "2F"]:
    anchors, occ, bb, segs = cc.build_floor(bld["floors"][fl[0]])
    targets = sp.make_wall_targets(segs, valid_polys[fl], L)
    merged = list(dict.fromkeys(anchors + targets))
    valid = valid_polys[fl]
    # 靶标→子区间桶（同主脚本）
    buckets = {}
    for a in merged:
        tx, ty = sp.tile_of(a[0], a[1], bb)
        buckets.setdefault((tx, ty), []).append(a)
    b = valid.bounds
    reasons = {"no_cand": 0, "los_lt2": 0, "no_good_phi": 0}
    phi_best_list = []
    samples = []
    nx = int(math.ceil((b[2] - b[0]) / 1.2)) + 1
    ny = int(math.ceil((b[3] - b[1]) / 1.2)) + 1
    for ix in range(nx):
        x = b[0] + ix * 1.2
        for iy in range(ny):
            y = b[1] + iy * 1.2
            if not valid.contains(sg.Point(x, y)):
                continue
            P = (x, y)
            ptx, pty = sp.tile_of(x, y, bb)
            cand = []
            for dtx in (-1, 0, 1):
                for dty in (-1, 0, 1):
                    cand.extend(buckets.get((ptx + dtx, pty + dty), ()))
            cand = [a for a in cand if math.hypot(a[0] - x, a[1] - y) <= sp.RADIUS]
            if len(cand) < 2:
                reasons["no_cand"] += 1
                continue
            sectors = {}
            for a in cand:
                ang = math.atan2(a[1] - y, a[0] - x)
                sk = int(((ang + math.pi) / (math.pi / 6))) % 12
                sectors.setdefault(sk, []).append((math.hypot(a[0] - x, a[1] - y), a))
            diverse = []
            for sk, lst in sectors.items():
                lst.sort()
                diverse.extend(a for _, a in lst[:2])
            los = [a for a in diverse if cc.los_clear(P, a, occ, bb)]
            if len(los) < 2:
                reasons["los_lt2"] += 1
                if len(samples) < 6:
                    samples.append((round(x, 1), round(y, 1), "los", len(cand), len(los)))
                continue
            best_phi = 0
            for i in range(len(los)):
                for j in range(i + 1, len(los)):
                    ph = cc.subtended(P, los[i], los[j])
                    best_phi = max(best_phi, ph)
            phi_best_list.append(best_phi)
            if not (sp.PHI_MIN <= best_phi <= sp.PHI_MAX):
                reasons["no_good_phi"] += 1
                if len(samples) < 10:
                    samples.append((round(x, 1), round(y, 1), "phi", round(math.degrees(best_phi), 1), len(los)))
    phi_best_list.sort()
    n = len(phi_best_list)
    print(f"== {fl} (L=3m, 1.2m 粗网格) ==")
    print("  未覆盖原因:", reasons)
    if n:
        def pct(q):
            return round(math.degrees(phi_best_list[min(n - 1, int(n * q))]), 1)
        print(f"  有≥2 LOS 锚点的点 {n} 个，其中最优张角分位数: p10={pct(0.1)} p25={pct(0.25)} p50={pct(0.5)} p75={pct(0.75)}")
    print("  样例(坐标, 瓶颈, 详情):", samples)
