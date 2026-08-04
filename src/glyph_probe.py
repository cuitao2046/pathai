# -*- coding: utf-8 -*-
"""把 window 图层的短笔画聚成"编号文字块"，渲染成拼图以肉眼确认字形结构。"""
import sys
import math
import collections

import fitz
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

sys.path.insert(0, r"E:\code\pathai\src")
from parse_cad_pdf import (PDF_F1, get_default_on_layers, extract_layer_items,
                           seg_len)

TINY = 8.0
LINK = 6.0


class UF:
    def __init__(self, n):
        self.p = list(range(n))

    def find(self, x):
        while self.p[x] != x:
            self.p[x] = self.p[self.p[x]]
            x = self.p[x]
        return x

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.p[ra] = rb


def bbox_of(seg):
    (x1, y1), (x2, y2) = seg
    return (min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2))


def cluster(segs, link):
    n = len(segs)
    bbs = [bbox_of(s) for s in segs]
    uf = UF(n)
    CELL = max(link * 2, 8.0)
    grid = collections.defaultdict(list)
    for i, b in enumerate(bbs):
        cx, cy = (b[0] + b[2]) / 2, (b[1] + b[3]) / 2
        grid[(int(cx // CELL), int(cy // CELL))].append(i)
    for (gx, gy), idxs in grid.items():
        neigh = []
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                neigh.extend(grid.get((gx + dx, gy + dy), []))
        for i in idxs:
            a = bbs[i]
            for j in neigh:
                if j <= i:
                    continue
                b = bbs[j]
                dx = max(0.0, max(a[0], b[0]) - min(a[2], b[2]))
                dy = max(0.0, max(a[1], b[1]) - min(a[3], b[3]))
                if math.hypot(dx, dy) < link:
                    uf.union(i, j)
    groups = collections.defaultdict(list)
    for i in range(n):
        groups[uf.find(i)].append(i)
    return list(groups.values())


def main():
    doc = fitz.open(PDF_F1)
    page = doc[0]
    on = get_default_on_layers(doc)
    items = extract_layer_items(page, set(on))
    doc.close()
    win = items["window"]
    shorts = [(a, b) for a, b in win["lines"] if seg_len(a, b) < TINY]
    print("short strokes:", len(shorts))
    groups = cluster(shorts, LINK)
    blocks = []
    for g in groups:
        if len(g) < 8:
            continue
        segs = [shorts[i] for i in g]
        xs = [p[0] for s in segs for p in s]
        ys = [p[1] for s in segs for p in s]
        blocks.append({"segs": segs, "bbox": (min(xs), min(ys), max(xs), max(ys))})
    blocks.sort(key=lambda b: (b["bbox"][1], b["bbox"][0]))
    print("blocks:", len(blocks))
    ws = sorted(b["bbox"][2] - b["bbox"][0] for b in blocks)
    hs = sorted(b["bbox"][3] - b["bbox"][1] for b in blocks)
    print("宽 median/max:", round(ws[len(ws) // 2], 1), round(ws[-1], 1))
    print("高 median/max:", round(hs[len(hs) // 2], 1), round(hs[-1], 1))

    n = min(48, len(blocks))
    cols, rows = 6, (n + 5) // 6
    fig, axes = plt.subplots(rows, cols, figsize=(cols * 2.4, rows * 1.5))
    axes = np.atleast_2d(axes)
    for k in range(rows * cols):
        ax = axes[k // cols][k % cols]
        ax.axis("off")
        if k >= n:
            continue
        b = blocks[k]
        x0, y0, x1, y1 = b["bbox"]
        for (ax1, ay1), (ax2, ay2) in b["segs"]:
            ax.plot([ax1, ax2], [-ay1, -ay2], color="k", lw=0.7)
        ax.set_aspect("equal")
        ax.set_title(f"#{k} ({x0:.0f},{y0:.0f}) {x1-x0:.0f}x{y1-y0:.0f}",
                     fontsize=6)
    plt.tight_layout()
    out = r"E:\code\pathai\result\_debug_glyph_blocks.png"
    plt.savefig(out, dpi=170)
    print("saved", out)


if __name__ == "__main__":
    main()
