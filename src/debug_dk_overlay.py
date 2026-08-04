# -*- coding: utf-8 -*-
from pathlib import Path
"""在 PDF 渲染图上叠加每个 window 图层文字块位置+块号+坐标，定位 DK/MGD/MW 等。"""
import sys
import collections
import math

import fitz
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

sys.path.insert(0, str(Path(__file__).resolve().parent))
from parse_cad_pdf import (PDF_F1, get_default_on_layers, extract_layer_items,
                           seg_len)


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


def cluster(segs, link):
    n = len(segs)
    bbs = []
    for (x1, y1), (x2, y2) in segs:
        bbs.append((min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2)))
    uf = UF(n)
    grid = collections.defaultdict(list)
    for i, b in enumerate(bbs):
        cx, cy = (b[0] + b[2]) / 2, (b[1] + b[3]) / 2
        grid[(int(cx // link), int(cy // link))].append(i)
    for (gx, gy), idxs in grid.items():
        neigh = []
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                neigh.extend(grid.get((gx + dx, gy + dy), []))
        for i in idxs:
            for j in neigh:
                if j <= i:
                    continue
                b1, b2 = bbs[i], bbs[j]
                d = max(0.0, max(b1[0], b2[0]) - min(b1[2], b2[2]))
                d2 = max(0.0, max(b1[1], b2[1]) - min(b1[3], b2[3]))
                if math.hypot(d, d2) < link:
                    uf.union(i, j)
    groups = collections.defaultdict(list)
    for i in range(n):
        groups[uf.find(i)].append(i)
    return list(groups.values())


def main():
    doc = fitz.open(PDF_F1)
    page = doc[0]
    page.set_rotation(0)
    on = get_default_on_layers(doc)
    items = extract_layer_items(page, set(on))
    doc.close()
    shorts = [(a, b) for a, b in items["window"]["lines"] if seg_len(a, b) < 8.0]
    groups = cluster(shorts, link=6.0)
    blocks = []
    for g in groups:
        if len(g) < 8:
            continue
        bbs = [(min(p1[0], p2[0]), min(p1[1], p2[1]),
                max(p1[0], p2[0]), max(p1[1], p2[1]))
               for p1, p2 in (shorts[i] for i in g)]
        x0 = min(b[0] for b in bbs)
        y0 = min(b[1] for b in bbs)
        x1 = max(b[2] for b in bbs)
        y1 = max(b[3] for b in bbs)
        blocks.append((x0, y0, x1, y1, len(g)))

    # 加载 PDF 渲染图
    doc = fitz.open(PDF_F1)
    pg = doc[0]
    pg.set_rotation(0)
    pix = pg.get_pixmap(matrix=fitz.Matrix(2.0, 2.0))
    img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, 3)
    doc.close()

    fig, ax = plt.subplots(figsize=(24, 18))
    ax.imshow(img, origin="upper")
    ax.set_xlim(0, pix.width / 2)
    ax.set_ylim(pix.height / 2, 0)
    # PDF pt 1:1.5px (2x zoom) - ax 用缩放后的 px
    S = pix.width / 3370
    for i, (x0, y0, x1, y1, n) in enumerate(sorted(blocks, key=lambda b: (b[1], b[0]))):
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
        rx = x0 * S
        ry = y0 * S
        rw = (x1 - x0) * S
        rh = (y1 - y0) * S
        ax.add_patch(plt.Rectangle((rx, ry), rw, rh, fc="none",
                                   ec="lime", lw=0.7))
        ax.text(rx, ry - 2, f"#{i}", color="lime", fontsize=6)
    ax.set_title("window 矢量编号字块位置（F1）", fontsize=12)
    out = str(Path(__file__).resolve().parent.parent / "result" / "_debug_glyph_overlay_f1.png")
    plt.tight_layout()
    plt.savefig(out, dpi=120)
    print("saved", out)
    print("blocks:", len(blocks))
    for i, (x0, y0, x1, y1, n) in enumerate(blocks):
        print(f"#{i:3d}  bbox=({x0:.0f},{y0:.0f})-({x1:.0f},{y1:.0f}) "
              f"size=({x1-x0:.0f}x{y1-y0:.0f}) strokes={n}")


if __name__ == "__main__":
    main()