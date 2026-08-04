# -*- coding: utf-8 -*-
from pathlib import Path
"""可视化泄漏：巨型连通域（室外）染红，定位房间泄漏点"""
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
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
walls, minx, miny, W, H, Z = P.rasterize_walls(struct, closures)

def to_px(p):
    return (int(round((p[0] - minx) * Z)), int(round((p[1] - miny) * Z)))

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
border = set(np.unique(np.concatenate([cc[0, :], cc[-1, :], cc[:, 0], cc[:, -1]])))

# 染色：墙=黑，室外巨组=红，其他自由=白
vis = np.zeros((H, W, 3), np.uint8)
vis[free > 0] = (255, 255, 255)
for cid in border:
    vis[cc == cid] = (0, 0, 255)
# 标签点蓝点
for t, (lx, ly) in labels:
    cv2.circle(vis, to_px((lx, ly)), 4, (255, 0, 0), -1)

clips = {
    1: [("row_music", (1000, 1050, 1750, 1450)),
        ("heban", (1250, 1400, 1850, 1850)),
        ("ziyuan", (400, 1450, 950, 1900)),
        ("toilet_br", (1700, 2350, 2000, 2600))],
    2: [("f2_center", (800, 900, 1700, 1600)),
        ("f2_chem", (400, 1100, 1200, 1800))],
}
for name, (x0, y0, x1, y1) in clips[floor]:
    p0 = to_px((x0, y0))
    p1 = to_px((x1, y1))
    crop = vis[p0[1]:p1[1], p0[0]:p1[0]]
    scale = 1400.0 / max(crop.shape[:2])
    crop = cv2.resize(crop, (int(crop.shape[1] * scale), int(crop.shape[0] * scale)),
                      interpolation=cv2.INTER_NEAREST)
    out = str(Path(__file__).resolve().parent.parent / "result" / "_leak_f{floor}_{name}.png")
    cv2.imwrite(out, crop)
    print("saved", out)
