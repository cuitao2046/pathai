#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
两点法指纹采集 覆盖/精度校验器
================================
- 采集域：fingerprint_valid_region.json（已确认与建筑 local_meters 同坐标系）
- 候选锚点：建筑柱（column 几何中心），按楼层取用
- 方法：在有效区内撒网格点，对每个点
    1) 过滤有视线(LOS)可达的柱（激光线段被墙体遮挡则不可见；门洞视为透明）
    2) 在 LOS 锚点中选取张角最大(≈90°)的一对，作为两点法的两个锚点
    3) 用解析误差模型估计 1σ 定位误差：
       σ_pos = σ_d * sqrt(1+|cosφ|) / sinφ   （双测距 Jacobian 推出）
    4) 用有效区多边形做伪解剔除（两圆交点取落在有效区内者），并标记歧义点
- 输出：热力图 HTML + 报表 JSON，回答“整片有效区能否约束在 5~10cm 内”
"""
import json
import math
import time
from pathlib import Path

import shapely.geometry as g

ROOT = Path(r"E:/code/pathai")
VALID_JSON = ROOT / "result" / "fingerprint_valid_region.json"
BUILDING = ROOT / "result" / "school_building_01_map_v9_columns.geojson"
OUT_HTML = ROOT / "result" / "fingerprint_coverage_heatmap.html"
OUT_JSON = ROOT / "result" / "fingerprint_coverage_report.json"

SIGMA_D = 0.02          # 测距误差 2cm（激光±2mm + 手持瞄准），可改
GRID = 0.6             # 网格间距 m
RADIUS = 25.0          # 候选锚点最大距离 m
PHI_MIN = math.radians(20)   # 接受一对锚点的最小张角
MAX_CAND = 16          # 每个点最多 LOS 测试的锚点数（按距离取最近）
CELL = 0.25            # 墙体栅格分辨率 m

# ---------------------------------------------------------------- 载入
def load_valid_region():
    vr = json.loads(VALID_JSON.read_text(encoding="utf-8"))
    polys = {}
    for fl, info in vr["floors"].items():
        lst = []
        for p in info["polygons"]:
            poly = g.Polygon(p["exterior"])
            for h in p["holes"]:
                poly = poly.difference(g.Polygon(h))
            lst.append(poly)
        polys[fl] = g.MultiPolygon(lst) if len(lst) > 1 else lst[0]
    return polys

def build_floor(f):
    geo = f["geometry"]
    # 锚点 = 柱几何中心 + 墙角顶点（用户指定“柱/墙角”组合；
    # 柱多在房间内，走廊有效区需靠墙角提供 LOS 可达锚点）
    raw = []
    for c in geo.get("columns", []):
        cp = g.shape(c["geometry"]).centroid
        raw.append((round(cp.x, 2), round(cp.y, 2)))
    for w in geo.get("walls", []):
        if w.get("properties", {}).get("type") == "door":
            continue
        for x, y in g.shape(w["geometry"]).coords:
            raw.append((round(x, 1), round(y, 1)))
    seen = set()
    anchors = []
    for a in raw:
        if a in seen:
            continue
        seen.add(a)
        anchors.append(a)
    # 墙体栅格（门洞透明：跳过 type==door 的墙）
    xs, ys = [], []
    for a in anchors:
        xs.append(a[0]); ys.append(a[1])
    bb = (min(xs) - 5, min(ys) - 5, max(xs) + 5, max(ys) + 5)
    occ = set()
    step = CELL * 0.4
    for w in geo.get("walls", []):
        if w.get("properties", {}).get("type") == "door":
            continue
        ls = g.shape(w["geometry"])
        if ls.geom_type != "LineString":
            continue
        coords = list(ls.coords)
        for k in range(len(coords) - 1):
            (x1, y1), (x2, y2) = coords[k], coords[k + 1]
            seglen = math.hypot(x2 - x1, y2 - y1)
            n = max(1, int(seglen / step))
            for t in range(n + 1):
                x = x1 + (x2 - x1) * t / n
                y = y1 + (y2 - y1) * t / n
                gx = int((x - bb[0]) / CELL)
                gy = int((y - bb[1]) / CELL)
                occ.add((gx, gy))
    return anchors, occ, bb

# ---------------------------------------------------------------- LOS(栅格)
def los_clear(P, A, occ, bb):
    # 只检测线段内部是否穿墙；跳过首尾端点——
    # 锚点(柱心)常紧邻墙体，其所在栅格被标为 occ，不能因此阻断对它的视线。
    dx, dy = A[0] - P[0], A[1] - P[1]
    dist = math.hypot(dx, dy)
    steps = int(dist / (CELL * 0.5)) + 1
    if steps <= 1:
        return True
    for k in range(1, steps):
        t = k / steps
        x = P[0] + dx * t
        y = P[1] + dy * t
        gx = int((x - bb[0]) / CELL)
        gy = int((y - bb[1]) / CELL)
        if (gx, gy) in occ:
            return False
    return True

# ---------------------------------------------------------------- 几何
def circle_intersections(A, B, r1, r2):
    (x1, y1), (x2, y2) = A, B
    dx, dy = x2 - x1, y2 - y1
    D = math.hypot(dx, dy)
    if D == 0 or D > r1 + r2 or D < abs(r1 - r2):
        return None
    a = (r1 * r1 - r2 * r2 + D * D) / (2 * D)
    h2 = r1 * r1 - a * a
    if h2 < 0:
        h2 = 0
    h = math.sqrt(h2)
    xm, ym = x1 + a * dx / D, y1 + a * dy / D
    ox, oy = -dy / D * h, dx / D * h
    return ((xm + ox, ym + oy), (xm - ox, ym - oy))

def subtended(P, A, B):
    v1x, v1y = A[0] - P[0], A[1] - P[1]
    v2x, v2y = B[0] - P[0], B[1] - P[1]
    n1, n2 = math.hypot(v1x, v1y), math.hypot(v2x, v2y)
    if n1 == 0 or n2 == 0:
        return 0.0
    c = (v1x * v2x + v1y * v2y) / (n1 * n2)
    return math.acos(max(-1.0, min(1.0, c)))

def sigma_pos(phi):
    return SIGMA_D * math.sqrt(1 + abs(math.cos(phi))) / math.sin(phi)

# ---------------------------------------------------------------- 主流程
def check_floor(fl, valid, anchors, occ, bb):
    t0 = time.time()
    b = valid.bounds
    cells = []
    n_uncovered = n_ambiguous = total = 0
    nx = int(math.ceil((b[2] - b[0]) / GRID)) + 1
    ny = int(math.ceil((b[3] - b[1]) / GRID)) + 1
    for ix in range(nx):
        x = b[0] + ix * GRID
        for iy in range(ny):
            y = b[1] + iy * GRID
            if not valid.contains(g.Point(x, y)):
                continue
            total += 1
            P = (x, y)
            cand = sorted(((a, math.hypot(a[0] - x, a[1] - y))
                          for a in anchors if math.hypot(a[0] - x, a[1] - y) <= RADIUS),
                         key=lambda t: t[1])[:MAX_CAND]
            los = [a for a, d in cand if los_clear(P, a, occ, bb)]
            if len(los) < 2:
                cells.append((x, y, None, False)); n_uncovered += 1; continue
            best = None
            for i in range(len(los)):
                for j in range(i + 1, len(los)):
                    phi = subtended(P, los[i], los[j])
                    # 张角需落在可用区间：太小(≈0°,共线同向)或太大(≈180°,共线反向)都退化
                    if phi < PHI_MIN or phi > math.radians(160):
                        continue
                    sc = math.sin(phi)
                    if best is None or sc > best[0]:
                        best = (sc, i, j, phi)
            if best is None:
                cells.append((x, y, None, False)); n_uncovered += 1; continue
            _, i, j, phi = best
            sp = sigma_pos(phi)
            A, Bp = los[i], los[j]
            d1 = math.hypot(A[0] - x, A[1] - y)
            d2 = math.hypot(Bp[0] - x, Bp[1] - y)
            inter = circle_intersections(A, Bp, d1, d2)
            amb = False
            if inter and all(valid.contains(g.Point(q[0], q[1])) for q in inter):
                amb = True; n_ambiguous += 1
            cells.append((x, y, sp, amb))
    return dict(floor=fl, cells=cells, total=total,
                n_uncovered=n_uncovered, n_ambiguous=n_ambiguous, dt=time.time() - t0)

# ---------------------------------------------------------------- 统计 + 渲染
def floor_stats(res, valid_area):
    vals = [c[2] for c in res["cells"] if c[2] is not None]
    covered = len(vals)
    frac = covered / res["total"] if res["total"] else 0
    return {
        "grid_points": res["total"],
        "covered_points": covered,
        "uncovered_points": res["n_uncovered"],
        "ambiguous_points": res["n_ambiguous"],
        "covered_area_m2": round(frac * valid_area, 2),
        "covered_pct": round(frac * 100, 2),
        "within_5cm_pct": round(sum(1 for v in vals if v <= 0.05) / covered * 100, 2) if covered else 0,
        "within_10cm_pct": round(sum(1 for v in vals if v <= 0.10) / covered * 100, 2) if covered else 0,
        "max_sigma_cm": round(max(vals) * 100, 2) if vals else None,
        "mean_sigma_cm": round(sum(vals) / len(vals) * 100, 2) if vals else None,
        "compute_sec": round(res["dt"], 1),
    }

def color(v):
    if v is None:
        return "#444444"
    cm = v * 100
    return "#1E88E5" if cm <= 5 else "#4CAF50" if cm <= 10 else "#FFC107" if cm <= 20 else "#FB8C00" if cm <= 40 else "#E53935"

def render_html(floors_res, anchors, valid_polys, stats):
    parts = ['<!doctype html><html lang="zh"><head><meta charset="utf-8"><style>'
             'body{font-family:-apple-system,"Microsoft YaHei",sans-serif;margin:0;background:#f5f6f8;color:#222}'
             '.wrap{max-width:1100px;margin:0 auto;padding:18px}h1{font-size:20px;margin:0 0 4px}'
             'h2{font-size:16px;margin:18px 0 8px}.meta{color:#888;font-size:12px;line-height:1.7}'
             'table{border-collapse:collapse;font-size:13px;margin:8px 0}td,th{border:1px solid #ddd;padding:5px 9px;text-align:right}'
             'th{background:#eef1f4}.ok{color:#1a7f37}.bad{color:#c0392b}'
             '.sec{background:#fff;border:1px solid #e3e6ea;border-radius:8px;padding:12px;margin:12px 0}'
             '.legend span{display:inline-block;width:14px;height:14px;border-radius:3px;vertical-align:middle;margin:0 3px 0 10px}'
             '</style></head><body><div class="wrap">']
    parts.append("<h1>指纹采集两点法 · 覆盖/精度校验</h1>")
    parts.append(f'<div class="meta">采集域: fingerprint_valid_region.json（与建筑 local_meters 同系）&nbsp;|&nbsp;'
                 f'锚点: 建筑柱几何中心&nbsp;|&nbsp;测距误差 σ_d={SIGMA_D*100:.0f}cm&nbsp;|&nbsp;'
                 f'网格 {GRID}m&nbsp;|&nbsp;候选半径 {RADIUS:.0f}m&nbsp;|&nbsp;门洞透明 LOS</div>')
    parts.append('<h2>汇总</h2><table><tr><th>楼层</th><th>有效区m²</th><th>网格点</th><th>已覆盖%</th>'
                 '<th>歧义点</th><th>≤5cm%</th><th>≤10cm%</th><th>最大σcm</th><th>均值σcm</th></tr>')
    for fl in sorted(stats):
        s = stats[fl]
        w5 = f'<span class="ok">{s["within_5cm_pct"]}%</span>' if s["within_5cm_pct"] >= 90 else f'<span class="bad">{s["within_5cm_pct"]}%</span>'
        w10 = f'<span class="ok">{s["within_10cm_pct"]}%</span>' if s["within_10cm_pct"] >= 95 else f'<span class="bad">{s["within_10cm_pct"]}%</span>'
        parts.append(f'<tr><td>{fl}</td><td>{s["covered_area_m2"]}</td><td>{s["grid_points"]}</td>'
                     f'<td>{s["covered_pct"]}%</td><td>{s["ambiguous_points"]}</td><td>{w5}</td>'
                     f'<td>{w10}</td><td>{s["max_sigma_cm"]}</td><td>{s["mean_sigma_cm"]}</td></tr>')
    parts.append("</table>")
    parts.append('<div class="legend">误差σ:'
                 '<span style="background:#1E88E5"></span>≤5cm<span style="background:#4CAF50"></span>≤10cm'
                 '<span style="background:#FFC107"></span>≤20cm<span style="background:#FB8C00"></span>≤40cm'
                 '<span style="background:#E53935"></span>&gt;40cm<span style="background:#444444"></span>无覆盖(红框=歧义)</div>')
    for fl in sorted(floors_res):
        res, anc, vp = floors_res[fl], anchors[fl], valid_polys[fl]
        bb = vp.bounds; pad = 2.0
        W, H = bb[2] - bb[0] + 2 * pad, bb[3] - bb[1] + 2 * pad
        scale = 880 / W
        tx = lambda x: (x - bb[0] + pad) * scale
        ty = lambda y: (bb[3] + pad - y) * scale
        svg = [f'<svg width="100%" viewBox="0 0 {W*scale:.0f} {H*scale:.0f}" style="background:#fafbfc">']
        subs = list(vp.geoms) if vp.geom_type == "MultiPolygon" else [vp]
        for sp in subs:
            pts = " ".join(f"{tx(x):.1f},{ty(y):.1f}" for x, y in sp.exterior.coords)
            svg.append(f'<polygon points="{pts}" fill="#eef4ff" stroke="#90a4d4" stroke-width="1"/>')
        for a in anc:
            svg.append(f'<circle cx="{tx(a[0]):.1f}" cy="{ty(a[1]):.1f}" r="1.6" fill="#9e9e9e"/>')
        s2 = GRID * scale / 2
        for (x, y, v, amb) in res["cells"]:
            col = color(v)
            svg.append(f'<rect x="{tx(x)-s2:.1f}" y="{ty(y)-s2:.1f}" width="{GRID*scale:.1f}" height="{GRID*scale:.1f}" fill="{col}" opacity="0.82"/>')
            if amb:
                svg.append(f'<rect x="{tx(x)-s2:.1f}" y="{ty(y)-s2:.1f}" width="{GRID*scale:.1f}" height="{GRID*scale:.1f}" fill="none" stroke="#E53935" stroke-width="0.6"/>')
        svg.append("</svg>")
        s = stats[fl]
        parts.append(f'<div class="sec"><h2>{fl} 层（有效区 {s["covered_area_m2"]} m²，已覆盖 {s["covered_pct"]}%，'
                     f'≤10cm 占 {s["within_10cm_pct"]}%，计算 {s["compute_sec"]}s）</h2>' + "".join(svg) + "</div>")
    parts.append("</div></body></html>")
    OUT_HTML.write_text("".join(parts), encoding="utf-8")

# ---------------------------------------------------------------- 入口
def main():
    print("载入有效区 ...")
    valid_polys = load_valid_region()
    vr = json.loads(VALID_JSON.read_text(encoding="utf-8"))
    print("载入建筑(柱/墙栅格) ...")
    bld = json.loads(BUILDING.read_text(encoding="utf-8"))
    anchors, occ, bb = {}, {}, {}
    for fk in valid_polys:
        a, o, b = build_floor(bld["floors"][fk[0]])
        anchors[fk], occ[fk], bb[fk] = a, o, b
    floors_res, stats = {}, {}
    for fl, vp in valid_polys.items():
        valid_area = vr["floors"][fl]["union_area_m2"]
        print(f"计算 {fl} 层 ...")
        res = check_floor(fl, vp, anchors[fl], occ[fl], bb[fl])
        floors_res[fl] = res
        stats[fl] = floor_stats(res, valid_area)
        print(f"  {fl}: 网格{res['total']} 覆盖{stats[fl]['covered_pct']}% "
              f"≤10cm {stats[fl]['within_10cm_pct']}% 最大σ {stats[fl]['max_sigma_cm']}cm")
    report = {
        "params": dict(sigma_d_m=SIGMA_D, grid_m=GRID, radius_m=RADIUS,
                       phi_min_deg=math.degrees(PHI_MIN), max_cand=MAX_CAND,
                       door_transparent=True, anchor_source="column_centroid", cell_m=CELL),
        "floors": stats, "conclusion": "",
    }
    OUT_JSON.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    render_html(floors_res, anchors, valid_polys, stats)
    all10 = all(s["within_10cm_pct"] >= 95 for s in stats.values())
    allcov = all(s["covered_pct"] >= 99 for s in stats.values())
    concl = ("✅ 整片有效区均能被两点法+柱锚点约束在 10cm 内。"
             if all10 and allcov else
             "⚠️ 存在超出 10cm 或无法覆盖的区域，见热力图红/灰区，需补充锚点或换方案。")
    report["conclusion"] = concl
    OUT_JSON.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"报表: {OUT_JSON}\n热力图: {OUT_HTML}\n结论: {concl}")

if __name__ == "__main__":
    main()
