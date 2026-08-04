# -*- coding: utf-8 -*-
from pathlib import Path
"""列出区域内已检测门洞 + 未通过 near_wall 的弧线"""
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
import parse_cad_pdf as P
import fitz

floor = int(sys.argv[1]) if len(sys.argv) > 1 else 1
pdf = P.PDF_F1 if floor == 1 else P.PDF_F2
doc = fitz.open(pdf)
page = doc[0]
items = P.extract_layer_items(page, set(P.LAYERS_STRUCT) | {P.LAYER_WINDOW, P.LAYER_DOOR_FIRE})
doc.close()

struct = []
for ln in P.LAYERS_STRUCT:
    li = items.get(ln)
    if li:
        struct.extend(P.wall_segments(li))
struct = P.merge_collinear(struct)

R = (1230, 1080, 1720, 1300) if floor == 1 else (800, 1100, 1400, 1500)

win = items[P.LAYER_WINDOW]
wg, wdc, _ = P.classify_window_layer(win["lines"], win["quads"], win["curves"])
wg = [g for g in wg if any(P.point_to_seg_dist(g["center"], a, b)[0] < 6.0 for a, b in struct)]

def arc_near_dist(bz):
    p1, p4 = bz[0], bz[3]
    d1 = min(P.point_to_seg_dist(p1, a, b)[0] for a, b in struct)
    d4 = min(P.point_to_seg_dist(p4, a, b)[0] for a, b in struct)
    return min(d1, d4), d1, d4

fire = items[P.LAYER_DOOR_FIRE]
print("region", R)
print("--- window 弧线 near_wall 距离 ---")
for bz in wdc:
    xs = [p[0] for p in bz]
    ys = [p[1] for p in bz]
    cx, cy = (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2
    if R[0] <= cx <= R[2] and R[1] <= cy <= R[3]:
        md, d1, d4 = arc_near_dist(bz)
        r = max(max(xs) - min(xs), max(ys) - min(ys))
        print(f"  ({cx:7.1f},{cy:7.1f}) r={r:5.1f}pt  min_d={md:5.2f}pt (p1={d1:.2f}, p4={d4:.2f})")

doors = P.detect_doors([b for b in wdc if arc_near_dist(b)[0] < 6.0],
                       fire["lines"],
                       [b for b in fire["curves"] if arc_near_dist(b)[0] < 6.0])
print("--- 区域内已检测门洞 ---")
for dr in doors:
    cx, cy = dr["center"]
    if R[0] <= cx <= R[2] and R[1] <= cy <= R[3]:
        a, b = dr["axis"]
        print(f"  center=({cx:7.1f},{cy:7.1f}) w={dr['width_pt'] * P.SCALE:.2f}m "
              f"kind={dr['kind']} axis=({a[0]:.1f},{a[1]:.1f})->({b[0]:.1f},{b[1]:.1f}) merged={dr['merged']}")
