# -*- coding: utf-8 -*-
"""统计 merge_collinear 桥接的间隙尺寸分布（直方图）"""
import sys
sys.path.insert(0, r"E:\code\pathai\src")
import parse_cad_pdf as P
import fitz
import math
import collections


def merged_gaps(segs, angle_tol_deg=2.0, axis_tol=2.0, gap_tol=30.0):
    """复刻 merge_collinear，但返回被桥接的间隙尺寸列表"""
    buckets = collections.defaultdict(list)
    for a, b in segs:
        ang = math.degrees(P.norm_angle(P.seg_angle(a, b)))
        key = (round(ang / angle_tol_deg) * angle_tol_deg) % 180.0
        buckets[key].append((a, b))
    gaps = []
    for key, group in buckets.items():
        ang = math.radians(key)
        ux, uy = math.cos(ang), math.sin(ang)
        nx, ny = -uy, ux
        band_map = {}
        for a, b in group:
            pos = ((a[0] + b[0]) / 2) * nx + ((a[1] + b[1]) / 2) * ny
            lo, hi = sorted((a[0] * ux + a[1] * uy, b[0] * ux + b[1] * uy))
            bk = None
            for k in band_map:
                if abs(k - pos) <= axis_tol:
                    bk = k
                    break
            if bk is None:
                bk = pos
                band_map[bk] = []
            band_map[bk].append((lo, hi))
        for pos, ivs in band_map.items():
            ivs.sort()
            cur_lo, cur_hi = ivs[0]
            for lo, hi in ivs[1:]:
                if lo - cur_hi <= gap_tol:
                    if lo - cur_hi > 0.5:  # 真正的间隙（非重叠）
                        gaps.append(lo - cur_hi)
                    cur_hi = max(cur_hi, hi)
                else:
                    cur_lo, cur_hi = lo, hi
    return gaps


for pdf, tag in ((P.PDF_F1, "F1"), (P.PDF_F2, "F2")):
    doc = fitz.open(pdf)
    page = doc[0]
    on_layers = P.get_default_on_layers(doc)
    wanted = [l for l in P.LAYERS_STRUCT + P.LAYERS_FURNITURE if l in on_layers]
    items = P.extract_layer_items(page, set(wanted))
    doc.close()
    segs = []
    for lname in wanted:
        segs.extend(P.wall_segments(items[lname]))
    gaps = merged_gaps(segs)
    hist = collections.Counter()
    for g in gaps:
        hist[min(int(g // 2 * 2), 30)] += 1
    print(f"[{tag}] 桥接间隙 {len(gaps)} 个, 尺寸分布(pt):")
    for k in sorted(hist):
        print(f"   {k:2d}-{k + 2:2d}pt ({k * P.SCALE:.2f}-{(k + 2) * P.SCALE:.2f}m): "
              f"{'#' * hist[k]} {hist[k]}")
