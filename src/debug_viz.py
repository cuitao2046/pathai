# -*- coding: utf-8 -*-
"""调试可视化：检查墙线/房间多边形/标签/门洞的空间关系"""
import sys
sys.path.insert(0, r"E:\code\pathai\src")
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Polygon as MplPolygon
from parse_cad_pdf import (parse_floor, PDF_F1, PDF_F2, extract_room_labels)
import fitz

floor = int(sys.argv[1]) if len(sys.argv) > 1 else 1
pdf = PDF_F1 if floor == 1 else PDF_F2
data = parse_floor(pdf, floor)

doc = fitz.open(pdf)
labels = extract_room_labels(doc[0])
doc.close()

fig, ax = plt.subplots(figsize=(24, 16))
for a, b in data["wall_segs"]:
    ax.plot([a[0], b[0]], [a[1], b[1]], 'k-', lw=0.5)
for r in data["rooms"]:
    poly = r["polygon_pt"]
    xs, ys = poly.exterior.xy
    ax.add_patch(MplPolygon(list(zip(xs, ys)), closed=True,
                            facecolor='orange', alpha=0.3, edgecolor='red', lw=0.8))
for text, (lx, ly) in labels:
    ax.plot(lx, ly, 'b.', ms=3)
    ax.text(lx, ly, text, fontsize=6, color='blue')
for dr in data["doors"]:
    ax.plot(dr["center"][0], dr["center"][1], 'rs', ms=4)
for wg in data["window_groups"]:
    a, b = wg["axis"]
    ax.plot([a[0], b[0]], [a[1], b[1]], 'g-', lw=1.2)
ax.set_aspect('equal')
ax.invert_yaxis()
out = rf"E:\code\pathai\result\_debug_parse_f{floor}.png"
fig.savefig(out, dpi=110, bbox_inches='tight')
print("saved", out)
print("rooms:", len(data["rooms"]), "doors:", len(data["doors"]),
      "windows:", len(data["window_groups"]), "labels:", len(labels))
