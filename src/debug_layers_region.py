# -*- coding: utf-8 -*-
from pathlib import Path
"""检查男卫生间区域内各图层的线段分布（定位厕位隔断所在图层）"""
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))
import parse_cad_pdf as P
import fitz

pdf = P.PDF_F1
R = (1500, 780, 1760, 1010)  # 男卫1 区域 (x0,y0,x1,y1) pt
doc = fitz.open(pdf)
page = doc[0]
on_layers = P.get_default_on_layers(doc)
all_layers = {v["name"] for v in doc.get_ocgs().values()}
items = P.extract_layer_items(page, all_layers)
doc.close()

print(f"region {R}:")
for lname in sorted(all_layers):
    li = items.get(lname)
    if not li:
        continue
    segs = []
    for a, b in li["lines"]:
        cx, cy = (a[0] + b[0]) / 2, (a[1] + b[1]) / 2
        if R[0] <= cx <= R[2] and R[1] <= cy <= R[3]:
            segs.append((a, b))
    nq = 0
    for q in li["quads"]:
        cx = sum(p[0] for p in q) / 4
        cy = sum(p[1] for p in q) / 4
        if R[0] <= cx <= R[2] and R[1] <= cy <= R[3]:
            nq += 1
    if segs or nq:
        mark = "*" if lname in on_layers else " "
        lens = sorted(P.seg_len(a, b) for a, b in segs)
        med = lens[len(lens) // 2] if lens else 0
        print(f"  {mark} {lname:18s} lines={len(segs):4d} (中位长 {med:.1f}pt) quads={nq}")
        # 打印竖直/水平短线样本（隔断候选）
        if lname in ("WALL", "A-FLOR-STRS", "A-TECH-SANT", "A-METAL-S"):
            shorts = [(a, b) for a, b in segs
                      if 5 < P.seg_len(a, b) < 45]
            for a, b in shorts[:12]:
                print(f"      ({a[0]:.0f},{a[1]:.0f})->({b[0]:.0f},{b[1]:.0f}) "
                      f"len={P.seg_len(a, b):.1f}")
