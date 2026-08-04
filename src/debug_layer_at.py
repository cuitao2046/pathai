# -*- coding: utf-8 -*-
from pathlib import Path
"""检查指定区域内各图层的线条分布 + 已检测门的位置"""
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))
import parse_cad_pdf as P
import fitz
import math
import collections

floor = int(sys.argv[1]) if len(sys.argv) > 1 else 1
pdf = P.PDF_F1 if floor == 1 else P.PDF_F2
doc = fitz.open(pdf)
page = doc[0]
on_layers = P.get_default_on_layers(doc)
all_layers = {v["name"] for v in doc.get_ocgs().values()}
items = P.extract_layer_items(page, all_layers)
doc.close()

if floor == 1:
    R = (1230, 1100, 1720, 1300)  # 音乐教室 row top
else:
    R = (800, 1100, 1400, 1500)

print(f"region {R} (default-ON layers marked *):")
for lname in sorted(all_layers):
    li = items.get(lname)
    if not li:
        continue
    cnt = 0
    for a, b in li["lines"]:
        cx, cy = (a[0] + b[0]) / 2, (a[1] + b[1]) / 2
        if R[0] <= cx <= R[2] and R[1] <= cy <= R[3]:
            cnt += 1
    for q in li["quads"]:
        cx = sum(p[0] for p in q) / 4
        cy = sum(p[1] for p in q) / 4
        if R[0] <= cx <= R[2] and R[1] <= cy <= R[3]:
            cnt += 4
    if cnt:
        mark = "*" if lname in on_layers else " "
        print(f"  {mark} {lname:20s} {cnt}")

# 区域内 window 图层弧线（门候选）
win = items.get(P.LAYER_WINDOW, {"lines": [], "quads": [], "curves": []})
print("\nwindow 图层弧线 in region:")
for bz in win["curves"]:
    xs = [p[0] for p in bz]
    ys = [p[1] for p in bz]
    cx, cy = (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2
    if R[0] <= cx <= R[2] and R[1] <= cy <= R[3]:
        r = max(max(xs) - min(xs), max(ys) - min(ys))
        print(f"  bbox_center=({cx:.1f},{cy:.1f}) r={r:.1f}pt={r * P.SCALE:.2f}m")
