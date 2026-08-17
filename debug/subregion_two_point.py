#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
分区间锚点布设 · 两点法覆盖率仿真
=================================
验证用户假设：把指纹有效区划分成多个采集子区间，每个子区间布设合理锚点（墙面靶标），
能否把两点法覆盖率提升到 95% 以上。

模型：
- 子区间：TILE×TILE 网格切分有效区包围盒；采集点 P 只能使用其所在子区间及 8 邻域子区间内的锚点
- 虚拟锚点：沿墙体（门洞透明）每 L 米采样一个"墙面靶标"位置，保留距有效区 ≤0.8m 者
  （靶标贴在 CAD 已知墙面上，坐标从图读取 + 1 次沿墙激光校核，成本 ≈ 1 次测量/个）
- 基线锚点：建筑柱心 + 墙角顶点（免费，已有）与靶标合并
- 配对规则：LOS 锚点对中 φ∈[30°,150°]，优先"非歧义对"（两圆交点不同时落在有效区内），
  取 sinφ 最大者；σ = σ_d·√(1+|cosφ|)/sinφ
- 扫描 L ∈ {∞(仅基线), 8, 6, 4, 3}，输出覆盖率-锚点数权衡

输出：result/subregion_two_point.json（报表） + result/subregion_two_point.html（推荐配置热力图）
"""
import json
import math
import time
from pathlib import Path

import shapely.geometry as sg
from shapely import affinity

import importlib.util as _ilu
_spec = _ilu.spec_from_file_location("cc", Path(__file__).parent / "coverage_compare.py")
cc = _ilu.module_from_spec(_spec)
_spec.loader.exec_module(cc)

ROOT = Path(r"E:/code/pathai")
VALID_JSON = ROOT / "result" / "fingerprint_valid_region.json"
BUILDING = ROOT / "result" / "school_building_01_map_v9_columns.geojson"
OUT_JSON = ROOT / "result" / "subregion_two_point.json"
OUT_HTML = ROOT / "result" / "subregion_two_point.html"

SIGMA_D = 0.02          # 测距误差 2cm（与三方案对比保持一致）
GRID = 0.6              # 网格 m
TILE = 12.0             # 子区间边长 m
NEAR_VALID = 0.8        # 靶标距有效区上限 m（贴在有效区边界墙上）
PHI_MIN = math.radians(30)   # 放宽到 30°（有靶标后基本用不到这么差的几何）
PHI_MAX = math.radians(150)
MAX_CAND = 20           # 每点参与配对的最近 LOS 锚点数上限
RADIUS = 18.0           # 候选锚点距离上限 m
SWEEP = [None, 8.0, 6.0, 4.0, 3.0]   # None = 仅基线锚点(柱+墙角)
RECOMMEND = 4.0         # 热力图展示配置

ANCHOR_BOOTSTRAP_SEC = 60    # 每靶标布设+坐标校核耗时
TWO_POINT_SEC = 6            # 两点法每采集点耗时(2次瞄准)
RIGHT_ANGLE_SEC = 12         # 直角法每采集点耗时(找垂足+沿墙量距)
FP_POINTS = {"1F": 975, "2F": 467}   # 实际指纹网格点数(成本模型用)


def make_wall_targets(segs, valid, spacing):
    """沿墙段每 spacing 米采样靶标，保留距有效区 ≤ NEAR_VALID 者。"""
    if spacing is None:
        return []
    pts = []
    for (x1, y1, x2, y2) in segs:
        L = math.hypot(x2 - x1, y2 - y1)
        n = max(1, int(L / spacing))
        for k in range(n + 1):
            t = k / n
            x, y = x1 + (x2 - x1) * t, y1 + (y2 - y1) * t
            pts.append((round(x, 2), round(y, 2)))
    # 仅保留贴近有效区的靶标
    keep = []
    mind_check = valid if valid.geom_type == "Polygon" else None
    for p in pts:
        d = valid.distance(sg.Point(p))
        if d <= NEAR_VALID:
            keep.append(p)
    # 去重
    seen, out = set(), []
    for p in keep:
        if p not in seen:
            seen.add(p)
            out.append(p)
    return out


def tile_of(x, y, bb, tile=TILE):
    return (int(math.floor((x - bb[0]) / tile)), int(math.floor((y - bb[1]) / tile)))


ENDPOINT_RELAX = 0.35   # 靶标贴墙：束末 relax 米内不判遮挡（墙格栅格化伪影修正）


def los_clear_anchor(P, A, occ, bb, relax=ENDPOINT_RELAX):
    """锚点(墙面靶标/墙角)视线判定：束末 relax 米内不判遮挡。

    物理依据：靶标贴在墙面朝向采集者一侧，激光束穿过空气直达墙面，末端不可能被
    "靶标所在的墙"遮挡；栅格化(0.25m格)会把束末采样点落进墙格造成伪遮挡。
    现场判据很简单——看得见靶标就有视线。"""
    dx, dy = A[0] - P[0], A[1] - P[1]
    dist = math.hypot(dx, dy)
    steps = int(dist / (cc.CELL * 0.5)) + 1
    if steps <= 1:
        return True
    t_stop = 1.0 - relax / dist if dist > relax else 0.0
    for k in range(1, steps):
        t = k / steps
        if t > t_stop:
            break
        x = P[0] + dx * t
        y = P[1] + dy * t
        gx = int((x - bb[0]) / cc.CELL)
        gy = int((y - bb[1]) / cc.CELL)
        if (gx, gy) in occ:
            return False
    return True


def check_floor(fl, valid, anchors, occ, bb, tile=TILE):
    """对有效区网格逐点做两点法仿真（子区间锚点约束；tile=None 表示仅按半径选锚）。"""
    t0 = time.time()
    # 靶标→子区间桶
    buckets = {}
    if tile is not None:
        for a in anchors:
            tx, ty = tile_of(x=a[0], y=a[1], bb=bb, tile=tile)
            buckets.setdefault((tx, ty), []).append(a)
    b = valid.bounds
    cells = []
    n_uncovered = n_ambiguous = total = 0
    nx = int(math.ceil((b[2] - b[0]) / GRID)) + 1
    ny = int(math.ceil((b[3] - b[1]) / GRID)) + 1
    for ix in range(nx):
        x = b[0] + ix * GRID
        for iy in range(ny):
            y = b[1] + iy * GRID
            if not valid.contains(sg.Point(x, y)):
                continue
            total += 1
            P = (x, y)
            if tile is not None:
                ptx, pty = tile_of(x, y, bb, tile)
                cand = []
                for dtx in (-1, 0, 1):
                    for dty in (-1, 0, 1):
                        cand.extend(buckets.get((ptx + dtx, pty + dty), ()))
            else:
                cand = list(anchors)
            cand = [a for a in cand if math.hypot(a[0] - x, a[1] - y) <= RADIUS]
            # 按方位分 12 个 30° 扇区，每扇区取最近 2 个 → 保证候选朝向多样
            # （否则"最近N个"会被同一面墙的靶标占满，彼此张角≈0° 无法配对）
            sectors = {}
            for a in cand:
                ang = math.atan2(a[1] - y, a[0] - x)
                sk = int(((ang + math.pi) / (math.pi / 6))) % 12
                sectors.setdefault(sk, []).append((math.hypot(a[0] - x, a[1] - y), a))
            diverse = []
            for sk, lst in sectors.items():
                lst.sort()
                diverse.extend(a for _, a in lst[:2])
            cand = diverse[:MAX_CAND * 2]
            los = [a for a in cand if los_clear_anchor(P, a, occ, bb)]
            if len(los) < 2:
                cells.append((x, y, None, False))
                n_uncovered += 1
                continue
            # 全部合规对按 sinφ 降序，优先非歧义
            pairs = []
            for i in range(len(los)):
                for j in range(i + 1, len(los)):
                    phi = cc.subtended(P, los[i], los[j])
                    if phi < PHI_MIN or phi > PHI_MAX:
                        continue
                    pairs.append((math.sin(phi), i, j, phi))
            pairs.sort(reverse=True)
            chosen = None
            for rank, (s, i, j, phi) in enumerate(pairs[:12]):
                A, Bp = los[i], los[j]
                d1 = math.hypot(A[0] - x, A[1] - y)
                d2 = math.hypot(Bp[0] - x, Bp[1] - y)
                inter = cc.circle_intersections(A, Bp, d1, d2)
                amb = bool(inter and all(valid.contains(sg.Point(q[0], q[1])) for q in inter))
                if not amb:
                    chosen = (phi, False)
                    break
                if chosen is None:
                    chosen = (phi, True)   # 记住最优歧义对作兜底
            if chosen is None:
                cells.append((x, y, None, False))
                n_uncovered += 1
                continue
            phi, amb = chosen
            sp = cc.sigma_two_point(phi)
            if amb:
                n_ambiguous += 1
            cells.append((x, y, sp, amb))
    return dict(floor=fl, cells=cells, total=total, n_uncovered=n_uncovered,
                n_ambiguous=n_ambiguous, dt=time.time() - t0)


def stats_of(res):
    vals = [c[2] for c in res["cells"] if c[2] is not None]
    usable = sum(1 for v in vals if v <= 0.10)
    total = res["total"]
    return {
        "grid_points": total,
        "reachable_points": len(vals),
        "reachable_pct": round(len(vals) / total * 100, 2) if total else 0,
        "usable_pct": round(usable / total * 100, 2) if total else 0,
        "ambiguous_points": res["n_ambiguous"],
        "within_5cm_pct_of_reachable": round(sum(1 for v in vals if v <= 0.05) / len(vals) * 100, 2) if vals else 0,
        "max_sigma_cm": round(max(vals) * 100, 2) if vals else None,
        "mean_sigma_cm": round(sum(vals) / len(vals) * 100, 2) if vals else None,
        "compute_sec": round(res["dt"], 1),
    }


def color(v):
    if v is None:
        return "#444444"
    cm = v * 100
    return ("#1E88E5" if cm <= 5 else "#4CAF50" if cm <= 10 else "#FFC107" if cm <= 20
            else "#FB8C00" if cm <= 40 else "#E53935")


def render_html(res_by_fl, anchors_by_fl, valid_polys, stats, sweep, recommend):
    parts = ['<!doctype html><html lang="zh"><head><meta charset="utf-8"><style>'
             'body{font-family:-apple-system,"Microsoft YaHei",sans-serif;margin:0;background:#f5f6f8;color:#222}'
             '.wrap{max-width:1180px;margin:0 auto;padding:18px}h1{font-size:20px;margin:0 0 4px}'
             'h2{font-size:16px;margin:18px 0 8px}.meta{color:#888;font-size:12px;line-height:1.7}'
             'table{border-collapse:collapse;font-size:13px;margin:8px 0}td,th{border:1px solid #ddd;padding:5px 9px;text-align:right}'
             'th{background:#eef1f4}.ok{color:#1a7f37}.bad{color:#c0392b}'
             '.sec{background:#fff;border:1px solid #e3e6ea;border-radius:8px;padding:12px;margin:12px 0}'
             '.mhdr{font-weight:600;color:#534AB7;margin:14px 0 4px}'
             '</style></head><body><div class="wrap">']
    parts.append("<h1>分区间锚点布设 · 两点法覆盖率仿真</h1>")
    parts.append(f'<div class="meta">子区间 {TILE:.0f}m 网格 + 8邻域 &nbsp;|&nbsp; 靶标=沿墙每 L 米(CAD坐标+1次校核) &nbsp;|&nbsp; '
                 f'σ_d={SIGMA_D*100:.0f}cm &nbsp;|&nbsp; φ∈[{math.degrees(PHI_MIN):.0f}°,{math.degrees(PHI_MAX):.0f}°] &nbsp;|&nbsp; '
                 f'配对优先非歧义对 &nbsp;|&nbsp; 网格 {GRID}m</div>')
    parts.append("<h2>子区间方案 × 靶标间距 扫描</h2><table><tr><th>楼层</th><th>方案/L</th><th>锚点数</th><th>网格点</th>"
                 "<th>可达%</th><th>可用%(全有效区≤10cm)</th><th>歧义点</th><th>可达内≤5cm%</th>"
                 "<th>最大σcm</th><th>均值σcm</th></tr>")
    for fl in sorted(stats):
        first = True
        for L in sweep:
            s = stats[fl][L]
            ok = s["usable_pct"] >= 95
            parts.append(f'<tr><td>{fl if first else "---"}</td><td style="text-align:left">{L}</td>'
                         f'<td>{s["anchor_count"]}</td><td>{s["grid_points"]}</td>'
                         f'<td>{"<span class=ok>" if ok else "<span class=bad>"}{s["reachable_pct"]}%</span></td>'
                         f'<td>{"<span class=ok>" if ok else "<span class=bad>"}{s["usable_pct"]}%</span></td>'
                         f'<td>{s["ambiguous_points"]}</td><td>{s["within_5cm_pct_of_reachable"]}%</td>'
                         f'<td>{s["max_sigma_cm"]}</td><td>{s["mean_sigma_cm"]}</td></tr>')
            first = False
    parts.append("</table>")
    parts.append('<div class="legend">误差σ:'
                 '<span style="background:#1E88E5"></span>≤5cm<span style="background:#4CAF50"></span>≤10cm'
                 '<span style="background:#FFC107"></span>≤20cm<span style="background:#444444"></span>无覆盖'
                 '（红框=歧义点：两圆交点均落在有效区内）紫色点=墙面靶标锚点</div>')
    # 推荐配置热力图
    for fl in sorted(res_by_fl):
        vp = valid_polys[fl]
        bb = vp.bounds
        pad = 2.0
        W, H = bb[2] - bb[0] + 2 * pad, bb[3] - bb[1] + 2 * pad
        scale = 880 / W
        tx = lambda x: (x - bb[0] + pad) * scale
        ty = lambda y: (bb[3] + pad - y) * scale
        svg = [f'<svg width="100%" viewBox="0 0 {W*scale:.0f} {H*scale:.0f}" style="background:#fafbfc">']
        subs = list(vp.geoms) if vp.geom_type == "MultiPolygon" else [vp]
        for sp in subs:
            pts = " ".join(f"{tx(x):.1f},{ty(y):.1f}" for x, y in sp.exterior.coords)
            svg.append(f'<polygon points="{pts}" fill="#eef4ff" stroke="#90a4d4" stroke-width="1"/>')
        for a in anchors_by_fl[fl]:
            svg.append(f'<circle cx="{tx(a[0]):.1f}" cy="{ty(a[1]):.1f}" r="1.6" fill="#534AB7" opacity="0.75"/>')
        s2 = GRID * scale / 2
        for (x, y, v, amb) in res_by_fl[fl]["cells"]:
            svg.append(f'<rect x="{tx(x)-s2:.1f}" y="{ty(y)-s2:.1f}" width="{GRID*scale:.1f}" height="{GRID*scale:.1f}" '
                       f'fill="{color(v)}" opacity="0.82"/>')
            if amb:
                svg.append(f'<rect x="{tx(x)-s2:.1f}" y="{ty(y)-s2:.1f}" width="{GRID*scale:.1f}" height="{GRID*scale:.1f}" '
                           f'fill="none" stroke="#E53935" stroke-width="0.6"/>')
        svg.append("</svg>")
        s = stats[fl][recommend]
        parts.append(f'<div class="sec"><div class="mhdr">{fl} · {recommend} （可达 {s["reachable_pct"]}%，'
                     f'全有效区≤10cm可用 {s["usable_pct"]}%，最大σ {s["max_sigma_cm"]}cm，歧义 {s["ambiguous_points"]}）</div>'
                     + "".join(svg) + "</div>")
    parts.append("</div></body></html>")
    OUT_HTML.write_text("".join(parts), encoding="utf-8")


def main():
    print("载入有效区/建筑 ...")
    valid_polys = cc.load_valid_region()
    bld = json.loads(BUILDING.read_text(encoding="utf-8"))
    floors = {}
    for fk in valid_polys:
        anchors, occ, bb, segs = cc.build_floor(bld["floors"][fk[0]])
        floors[fk] = dict(anchors=anchors, occ=occ, bb=bb, segs=segs)
    # 子区间方案：(标签, tile边长或None)
    SCHEMES = [("子区间12m", 12.0), ("子区间24m", 24.0)]
    stats, res_rec, anchors_rec, rec_key = {}, {}, {}, None
    for fl, fdata in floors.items():
        stats[fl] = {}
        for L in SWEEP:
            lkey = "基线" if L is None else f"L={L:g}m"
            targets = make_wall_targets(fdata["segs"], valid_polys[fl], L)
            all_anc = fdata["anchors"] + targets
            seen, merged = set(), []
            for a in all_anc:
                if a not in seen:
                    seen.add(a)
                    merged.append(a)
            for slab, tile in SCHEMES:
                key = f"{slab}/{lkey}"
                print(f"计算 {fl} · {key}（锚点 {len(merged)}，其中靶标 {len(targets)}）...")
                res = check_floor(fl, valid_polys[fl], merged, fdata["occ"], fdata["bb"], tile=tile)
                st = stats_of(res)
                st["anchor_count"] = len(merged)
                st["target_count"] = len(targets)
                st["scheme"] = slab
                st["L"] = lkey
                stats[fl][key] = st
                print(f"  可达 {st['reachable_pct']}% 可用 {st['usable_pct']}% 最大σ {st['max_sigma_cm']}cm "
                      f"歧义 {st['ambiguous_points']} 耗时 {st['compute_sec']}s")
                if L == RECOMMEND and tile == 24.0:
                    res_rec[fl] = res
                    anchors_rec[fl] = merged
                    rec_key = f"子区间24m/L={RECOMMEND:g}m"
    # 汇总与结论（每方案×L 的两楼层最差可用率）
    all_keys = list(next(iter(stats.values())).keys())
    concl = {}
    for key in all_keys:
        use = [stats[fl][key]["usable_pct"] for fl in stats]
        concl[key] = {
            "min_usable_pct": round(min(use), 2),
            "targets_total": sum(stats[fl][key]["target_count"] for fl in stats),
            "ambiguous_total": sum(stats[fl][key]["ambiguous_points"] for fl in stats),
            "meet_95": all(u >= 95 for u in use),
        }
    # 成本模型
    cost = {}
    fp_total = sum(FP_POINTS.values())
    for key, c in concl.items():
        if "基线" not in key:
            t = c["targets_total"]
            cost[key] = {
                "targets_total": t,
                "bootstrap_min": round(t * ANCHOR_BOOTSTRAP_SEC / 60, 1),
                "two_point_collect_min": round(fp_total * TWO_POINT_SEC / 60, 1),
                "two_point_total_min": round((t * ANCHOR_BOOTSTRAP_SEC + fp_total * TWO_POINT_SEC) / 60, 1),
                "right_angle_total_min": round(fp_total * RIGHT_ANGLE_SEC / 60, 1),
            }
    report = {
        "params": dict(sigma_d_m=SIGMA_D, grid_m=GRID, tile_m=TILE, near_valid_m=NEAR_VALID,
                       phi_min_deg=round(math.degrees(PHI_MIN)), phi_max_deg=round(math.degrees(PHI_MAX)),
                       radius_m=RADIUS, max_cand=MAX_CAND, sweep=[("baseline" if L is None else L) for L in SWEEP],
                       schemes=[s for s, _ in SCHEMES],
                       prefer_non_ambiguous=True, door_transparent=True,
                       sector_diversified=True),
        "floors": stats,
        "conclusion": concl,
        "cost_model": cost,
        "fp_points": FP_POINTS,
        "time_assumptions_sec": dict(anchor_bootstrap=ANCHOR_BOOTSTRAP_SEC,
                                     two_point_per_point=TWO_POINT_SEC,
                                     right_angle_per_point=RIGHT_ANGLE_SEC),
    }
    report["summary"] = (
        f"达标配置(两楼层≥95%)：{[k for k, v in concl.items() if v['meet_95']]}；"
        f"基线(柱+墙角，扇区多样化选择)可用率 {concl.get('子区间12m/基线', {}).get('min_usable_pct', 'NA')}%。")
    OUT_JSON.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    render_html(res_rec, anchors_rec, valid_polys, stats, all_keys, rec_key)
    print(f"\n报表: {OUT_JSON}\n热力图: {OUT_HTML}\n{report['summary']}")


if __name__ == "__main__":
    main()
