# -*- coding: utf-8 -*-
from pathlib import Path
"""比例尺校准：轴网间距(8400mm标注) + 窗组长度 vs 窗编号(如 M2GW6124=6.1m)"""
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))
import parse_cad_pdf as P
import fitz
import math
import collections

doc = fitz.open(P.PDF_F1)
page = doc[0]
items = P.extract_layer_items(page, {"AXIS", "window", "WALL", "A-FLOR-STRS",
                                     "STAIR", "A-FLOR-EVTR", "A-METAL-S",
                                     "A-TECH-SANT", "COLUMN", "柱子-刚结构"})
doc.close()

# --- 轴网：AXIS 层长直线，统计水平线 y 间距 / 垂直线 x 间距的众数
axis = items["AXIS"]
hx, hy = [], []
for a, b in axis["lines"]:
    L = math.hypot(b[0] - a[0], b[1] - a[1])
    if L < 200:
        continue
    ang = abs(math.degrees(math.atan2(b[1] - a[1], b[0] - a[0])) % 180)
    if ang < 2 or ang > 178:
        hy.append((a[1] + b[1]) / 2)
    elif 88 < ang < 92:
        hx.append((a[0] + b[0]) / 2)

def spacing_mode(vals):
    vals = sorted(vals)
    diffs = [round(vals[i + 1] - vals[i], 1) for i in range(len(vals) - 1)]
    diffs = [d for d in diffs if d > 20]
    cnt = collections.Counter(diffs)
    return cnt.most_common(8)

print("AXIS 水平线 y 间距众数(pt):", spacing_mode(hy))
print("AXIS 垂直线 x 间距众数(pt):", spacing_mode(hx))

# --- 窗组长度：底部窗墙 y 1270-1290, x 1200-1750（标注 M2GW5924=5.9m / M2GW6124=6.1m）
struct = []
for ln in ("WALL", "A-FLOR-STRS", "STAIR", "A-FLOR-EVTR", "A-METAL-S",
           "A-TECH-SANT", "COLUMN", "柱子-刚结构"):
    li = items.get(ln)
    if li:
        struct.extend(P.wall_segments(li))
struct = P.merge_collinear(struct)
win = items["window"]
wg, _, _ = P.classify_window_layer(win["lines"], win["quads"], win["curves"])
print("\n底部窗墙窗组长度:")
for g in wg:
    cx, cy = g["center"]
    if 1200 <= cx <= 1750 and 1260 <= cy <= 1300:
        L = g["length_pt"]
        print(f"  center=({cx:.1f},{cy:.1f}) len={L:.1f}pt  "
              f"@0.0644={L * 0.0644:.2f}m  @0.0545={L * 0.0545:.2f}m")
