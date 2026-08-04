# -*- coding: utf-8 -*-
from pathlib import Path
"""诊断：每个房间标签点命中的连通域状态（BORDER=泄漏 / SIZE=面积超限 / ok）"""
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))
import parse_cad_pdf as P
import fitz
import cv2
import numpy as np

floor = int(sys.argv[1]) if len(sys.argv) > 1 else 1
pdf = P.PDF_F1 if floor == 1 else P.PDF_F2

doc = fitz.open(pdf)
page = doc[0]
items = P.extract_layer_items(page, set(P.LAYERS_STRUCT) | {P.LAYER_WINDOW, P.LAYER_DOOR_FIRE})
labels = P.extract_room_labels(page)
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

Z = P.RENDER_ZOOM
segs = struct + closures
xs = [p[0] for s in segs for p in s]
ys = [p[1] for s in segs for p in s]
margin = 20.0
minx, miny = min(xs) - margin, min(ys) - margin
W = int((max(xs) - minx + margin) * Z) + 1
H = int((max(ys) - miny + margin) * Z) + 1

def to_px(p):
    return (int(round((p[0] - minx) * Z)), int(round((p[1] - miny) * Z)))

walls = np.zeros((H, W), np.uint8)
for a, b in struct:
    cv2.line(walls, to_px(a), to_px(b), 255, 2)
for a, b in closures:
    cv2.line(walls, to_px(a), to_px(b), 255, 3)
walls = cv2.morphologyEx(walls, cv2.MORPH_CLOSE,
                         cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (9, 9)))
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
areas = np.bincount(cc.ravel(), minlength=n)
border = set(np.unique(np.concatenate([cc[0, :], cc[-1, :], cc[:, 0], cc[:, -1]])))

print(f"F{floor} label diagnostics:")
for t, (lx, ly) in labels:
    px, py = to_px((lx, ly))
    cid = cc[py, px] if 0 <= px < W and 0 <= py < H else 0
    if cid == 0:
        best, ba = 0, 0
        for r in (6, 12, 24, 40):
            sub = cc[max(0, py - r):py + r + 1, max(0, px - r):px + r + 1]
            vals = sub[sub > 0]
            if len(vals):
                ids, cnt = np.unique(vals, return_counts=True)
                top = ids[np.argmax(cnt)]
                if cnt.max() > ba:
                    best, ba = top, cnt.max()
            if best:
                break
        cid = best
    a = areas[cid] * m2 if cid else 0
    tag = "BORDER" if cid in border else ("ok" if P.MIN_ROOM_AREA_M2 <= a <= P.MAX_ROOM_AREA_M2 else "SIZE")
    if cid == 0:
        tag = "WALL-NB"
    print(f"  {t:16s} ({lx:7.1f},{ly:7.1f}) area={a:8.1f}m2  {tag}")
