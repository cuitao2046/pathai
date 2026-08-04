# -*- coding: utf-8 -*-
"""追踪：两坐标点之间是否为红色（连通）；找底部墙上的缺口位置"""
import sys
sys.path.insert(0, r"E:\code\pathai\src")
import parse_cad_pdf as P
import fitz
import cv2
import numpy as np

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
win = items[P.LAYER_WINDOW]
wg, wdc, _ = P.classify_window_layer(win["lines"], win["quads"], win["curves"])
wg = [g for g in wg if any(P.point_to_seg_dist(g["center"], a, b)[0] < 6.0 for a, b in struct)]

def near_wall(bz, tol=6.0):
    p1, p4 = bz[0], bz[3]
    return any(P.point_to_seg_dist(p1, a, b)[0] < tol or
               P.point_to_seg_dist(p4, a, b)[0] < tol for a, b in struct)

fire = items[P.LAYER_DOOR_FIRE]
doors = P.detect_doors([b for b in wdc if near_wall(b)], fire["lines"],
                       [b for b in fire["curves"] if near_wall(b)])
closures = []
for g in wg:
    if g["length_pt"] > 2.0:
        closures.extend(P.opening_closures(g["axis"]))
for dr in doors:
    closures.extend(P.opening_closures(dr["axis"]))

walls, minx, miny, W, H, Z = P.rasterize_walls(struct, closures)
free = cv2.bitwise_not(walls)
n, cc = cv2.connectedComponents(free, connectivity=4)
m2 = (P.SCALE / Z) ** 2
areas = np.bincount(cc.ravel(), minlength=n)
small = np.where(areas < P.ABSORB_CELL_M2 / m2)[0]
small = small[small != 0]
lut = np.zeros(n, np.uint8)
lut[small] = 255
walls = cv2.bitwise_or(walls, lut[cc])
free = cv2.bitwise_not(walls)
n, cc = cv2.connectedComponents(free, connectivity=4)

def to_px(p):
    return (int(round((p[0] - minx) * Z)), int(round((p[1] - miny) * Z)))

if floor == 1:
    A = (1464.0, 1212.8)   # 音乐教室 label
    B = (1464.0, 1600.0)   # 正下方室外
else:
    A = (900.0, 1300.0)
    B = (900.0, 1600.0)

pa, pb = to_px(A), to_px(B)
print("comp at A:", cc[pa[1], pa[0]], " comp at B:", cc[pb[1], pb[0]])
# 沿 A->B 垂直路径的墙体分布
col = walls[pa[1]:pb[1], pa[0]]
runs = []
in_wall = False
for i, v in enumerate(col):
    if v and not in_wall:
        start = i
        in_wall = True
    elif not v and in_wall:
        runs.append((start, i - 1))
        in_wall = False
if in_wall:
    runs.append((start, len(col) - 1))
print("wall runs on A->B path (pt y):")
for s, e in runs:
    y0 = (pa[1] + s) / Z + miny
    y1 = (pa[1] + e) / Z + miny
    print(f"  y {y0:.1f} .. {y1:.1f}  ({(e - s + 1)} px)")
# 扫描指定水平墙线的覆盖缺口
def h_gaps(y_pt, xa_pt, xb_pt, band_half=6, min_gap_px=2):
    py = int(round((y_pt - miny) * Z))
    x0px, x1px = to_px((xa_pt, 0))[0], to_px((xb_pt, 0))[0]
    band = walls[py - band_half:py + band_half + 1, x0px:x1px]
    cover = band.max(axis=0)
    gaps = []
    in_gap = False
    for i, v in enumerate(cover):
        if not v and not in_gap:
            start = i
            in_gap = True
        elif v and in_gap:
            if i - start >= min_gap_px:
                gaps.append((start, i - 1))
            in_gap = False
    if in_gap:
        gaps.append((start, len(cover) - 1))
    out = []
    for s, e in gaps:
        xa = (x0px + s) / Z + minx
        xb = (x0px + e) / Z + minx
        out.append((xa, xb, (e - s + 1) / Z * P.SCALE))
    return out

if floor == 1:
    for yw in (1190.0, 1281.0, 1431.0):
        print(f"gaps on wall y~{yw}, x 1000..1750:")
        for xa, xb, wm in h_gaps(yw, 1000.0, 1750.0):
            if wm > 0.15:
                print(f"  x {xa:7.1f} .. {xb:7.1f}  ({wm:.2f} m)")
