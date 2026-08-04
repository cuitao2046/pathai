# -*- coding: utf-8 -*-
from pathlib import Path
"""调试：导出栅格墙图与封口线，检查房间泄漏点"""
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
import fitz
import cv2
import numpy as np
from parse_cad_pdf import (extract_layer_items, extract_room_labels,
                           wall_segments, merge_collinear, classify_window_layer,
                           detect_doors, opening_closures,
                           LAYERS_STRUCT, LAYER_WINDOW, LAYER_DOOR_FIRE,
                           get_default_on_layers, point_to_seg_dist,
                           PDF_F1, PDF_F2)

floor = int(sys.argv[1]) if len(sys.argv) > 1 else 1
pdf = PDF_F1 if floor == 1 else PDF_F2

doc = fitz.open(pdf)
page = doc[0]
on_layers = get_default_on_layers(doc)
items = extract_layer_items(page, set(LAYERS_STRUCT) | {LAYER_WINDOW, LAYER_DOOR_FIRE})
labels = extract_room_labels(page)
doc.close()

struct_segs = []
for lname in LAYERS_STRUCT:
    li = items.get(lname)
    if li:
        struct_segs.extend(wall_segments(li))
struct_segs = merge_collinear(struct_segs)

win = items[LAYER_WINDOW]
window_groups, win_door_curves, _ = classify_window_layer(
    win["lines"], win["quads"], win["curves"])
window_groups = [wg for wg in window_groups
                 if any(point_to_seg_dist(wg["center"], a, b)[0] < 6.0
                        for a, b in struct_segs)]

def near_wall(bz, tol=6.0):
    p1, p4 = bz[0], bz[3]
    for a, b in struct_segs:
        if point_to_seg_dist(p1, a, b)[0] < tol:
            return True
        if point_to_seg_dist(p4, a, b)[0] < tol:
            return True
    return False

fire = items[LAYER_DOOR_FIRE]
win_arcs = [bz for bz in win_door_curves if near_wall(bz)]
fire_arcs = [bz for bz in fire["curves"] if near_wall(bz)]
doors = detect_doors(win_arcs, fire["lines"], fire_arcs)

closures = []
for wg in window_groups:
    if wg["length_pt"] > 2.0:
        closures.extend(opening_closures(wg["axis"]))
for dr in doors:
    closures.extend(opening_closures(dr["axis"]))

segs = list(struct_segs) + list(closures)
PX = 1.6
xs = [p[0] for s in segs for p in s]
ys = [p[1] for s in segs for p in s]
margin = 20.0
minx, miny = min(xs) - margin, min(ys) - margin
W = int((max(xs) - min(xs) + 2 * margin) * PX) + 1
H = int((max(ys) - min(ys) + 2 * margin) * PX) + 1

def to_px(p):
    return (int(round((p[0] - minx) * PX)), int(round((p[1] - miny) * PX)))

img = np.zeros((H, W), np.uint8)
for a, b in struct_segs:
    cv2.line(img, to_px(a), to_px(b), 255, thickness=2)
kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
walls = cv2.morphologyEx(img, cv2.MORPH_CLOSE, kernel)
walls = cv2.dilate(walls, np.ones((2, 2), np.uint8))

# 叠加显示：墙=白，封口线=红，标签点=蓝
vis = cv2.cvtColor(walls, cv2.COLOR_GRAY2BGR)
for a, b in closures:
    cv2.line(vis, to_px(a), to_px(b), (0, 0, 255), thickness=2)
for text, (lx, ly) in labels:
    cv2.circle(vis, to_px((lx, ly)), 3, (255, 0, 0), -1)

scale = 4000.0 / max(W, H)
small = cv2.resize(vis, (int(W * scale), int(H * scale)))
out = str(Path(__file__).resolve().parent.parent / "result" / "_debug_raster_f{floor}.png")
cv2.imwrite(out, small)
print("saved", out, "full size", W, H)

# 连通域统计
free = cv2.bitwise_not(walls)
n, cc = cv2.connectedComponents(free, connectivity=4)
border = set(np.unique(np.concatenate([cc[0, :], cc[-1, :], cc[:, 0], cc[:, -1]])))
areas = []
for cid in range(1, n):
    if cid in border:
        continue
    areas.append(int(np.sum(cc == cid)))
areas.sort(reverse=True)
m2 = 0.04 * 0.04
print("封闭连通域:", len(areas),
      " 面积m2 top20:", [round(a * m2, 1) for a in areas[:20]])
