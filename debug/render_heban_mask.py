# -*- coding: utf-8 -*-
"""Render a wall-mask crop around the 合班 label, overlaying candidate wide gaps (no door closure)."""
import sys, math
sys.path.insert(0, "src")
import numpy as np
import parse_cad_pdf as P
import fitz
import cv2

floor_no = 1
doc = fitz.open(P.PDF_F1); page = doc[0]
on = P.get_default_on_layers(doc)
wanted = list({P.LAYER_WALL, P.LAYER_WINDOW, P.LAYER_DOOR_FIRE, P.LAYER_STAIR,
               P.LAYER_ELEVATOR, *P.LAYER_COLUMNS, *P.LAYERS_STRUCT,
               *P.LAYERS_FURNITURE} - set(P.LAYERS_IGNORE))
active = [l for l in wanted if l in on]
items = P.extract_layer_items(page, set(active))
labels = P.extract_room_labels(page); room_names, _ = labels
struct = []
for l in P.LAYERS_STRUCT:
    li = items.get(l)
    if li: struct.extend(P.wall_segments(li))
furn = []
for l in P.LAYERS_FURNITURE:
    if l in P.LAYERS_IGNORE: continue
    li = items.get(l)
    if li: furn.extend(P.wall_segments(li))
struct = P.merge_collinear(struct)
furn = P.merge_collinear(furn)
all_segs = struct + furn
# raw wall mask, NO closures (so openings stay open -> we can see the heban leak)
walls, walls_furn, minx, miny, W, H, Z = P.rasterize_walls(all_segs, [], furn_segs=furn)

heban = [e for e in room_names if "合班" in e[0]][0]
hlx, hly = heban[1]
hx, hy = (hlx - minx) * Z, (hly - miny) * Z
# candidate wide gaps on raw struct
struct_raw = []
for l in P.LAYERS_STRUCT:
    li = items.get(l)
    if li: struct_raw.extend(P.wall_segments(li))
_, cg = P.merge_collinear(struct_raw, gap_tol=150.0, record_gaps=True)

half_m = 18.0
half_px = int(half_m / (P.SCALE / Z))
cx_px, cy_px = int(hx), int(hy)
x0, x1 = max(0, cx_px-half_px), min(W, cx_px+half_px)
y0, y1 = max(0, cy_px-half_px), min(H, cy_px+half_px)
crop = walls[y0:y1, x0:x1].copy()
img = np.zeros((crop.shape[0], crop.shape[1], 3), np.uint8)
img[crop > 0] = (255, 255, 255)
# heban label (green)
cv2.circle(img, (cx_px - x0, cy_px - y0), 7, (0, 255, 0), -1)
# candidate wide gaps within 12m
r_pt = 12.0 / P.SCALE
n = 0
for g in cg:
    if g["gap"] < 30.0: continue
    cx, cy = g["center"]
    if math.hypot(cx - hlx, cy - hly) > r_pt: continue
    px, py = int((cx - minx) * Z - x0), int((cy - miny) * Z - y0)
    col = (0, 0, 255) if g["gap"] * P.SCALE > 4.0 else (0, 165, 255)
    cv2.circle(img, (px, py), 6, col, -1)
    n += 1
cv2.imwrite("debug/_heban_mask_crop.png", img)
print("saved debug/_heban_mask_crop.png  crop", img.shape[1], "x", img.shape[0])
print("heban label px", (cx_px - x0, cy_px - y0), "walls", (W, H))
print("wide gaps (>=1.59m) within 12m:", n)
doc.close()
