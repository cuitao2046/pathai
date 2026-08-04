# -*- coding: utf-8 -*-
from pathlib import Path
"""探查 PDF：DK 洞口标识 / DOOR_FIRE 线元素 / 公共空间标签的实际存储形式。"""
import re
import sys
import math
import collections

import fitz

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from parse_cad_pdf import (PDF_F1, PDF_F2, get_default_on_layers,
                           extract_layer_items, extract_room_labels,
                           seg_len, seg_midpoint)

PDFS = [("F1", PDF_F1), ("F2", PDF_F2)]


def probe_text(page, tag):
    print(f"\n=== [{tag}] 文本层中的 DK / 洞 / 门 编号 ===")
    d = page.get_text("dict")
    hits = []
    allcodes = collections.Counter()
    for block in d["blocks"]:
        if block.get("type") != 0:
            continue
        for line in block["lines"]:
            txt = "".join(s["text"] for s in line["spans"]).strip()
            if not txt:
                continue
            x0, y0, x1, y1 = line["bbox"]
            size = max(s["size"] for s in line["spans"])
            if re.match(r"^\s*DK", txt, re.I):
                hits.append((txt, round((x0 + x1) / 2, 1), round((y0 + y1) / 2, 1),
                             round(size, 1)))
            m = re.match(r"^([A-Za-z]{1,4})\d", txt.strip())
            if m:
                allcodes[m.group(1).upper()] += 1
    print(f"  DK 开头文本条数: {len(hits)}")
    for h in hits[:40]:
        print("   ", h)
    print("  文本层编号前缀统计:", dict(allcodes.most_common(20)))


def probe_layers(page, tag, on_layers):
    print(f"\n=== [{tag}] 图层元素统计 ===")
    stat = collections.defaultdict(lambda: {"l": 0, "c": 0, "re": 0, "qu": 0})
    for dr in page.get_drawings():
        lay = dr.get("layer") or "(none)"
        for it in dr["items"]:
            stat[lay][it[0]] = stat[lay].get(it[0], 0) + 1
    for lay in sorted(stat):
        on = "ON " if lay in on_layers else "off"
        s = stat[lay]
        print(f"  {on} {lay:24s} line={s.get('l',0):5d} curve={s.get('c',0):5d} "
              f"re={s.get('re',0):4d} qu={s.get('qu',0):5d}")


def probe_fire(items, tag):
    fire = items.get("DOOR_FIRE")
    if not fire:
        print(f"\n=== [{tag}] DOOR_FIRE 图层未开启/不存在 ===")
        return
    print(f"\n=== [{tag}] DOOR_FIRE 图层线元素 ===")
    lines = fire["lines"]
    lens = sorted(round(seg_len(a, b), 1) for a, b in lines)
    print(f"  lines={len(lines)} curves={len(fire['curves'])} quads={len(fire['quads'])}")
    if lens:
        print(f"  线长分布: min={lens[0]} p25={lens[len(lens)//4]} "
              f"median={lens[len(lens)//2]} p75={lens[3*len(lens)//4]} max={lens[-1]}")
        hist = collections.Counter(int(x // 5) * 5 for x in lens)
        print("  线长直方(5pt桶):", dict(sorted(hist.items())[:20]))
    # 曲线半径分布
    rs = []
    for bz in fire["curves"]:
        xs = [p[0] for p in bz]
        ys = [p[1] for p in bz]
        rs.append(round(max(max(xs) - min(xs), max(ys) - min(ys)), 1))
    rs.sort()
    if rs:
        print(f"  弧半径: min={rs[0]} median={rs[len(rs)//2]} max={rs[-1]} n={len(rs)}")


def probe_window_glyphs(items, tag):
    """window 图层短笔画聚类 -> 疑似矢量编号文字块"""
    win = items.get("window")
    if not win:
        return
    print(f"\n=== [{tag}] window 图层短笔画（矢量编号）聚类 ===")
    TINY = 8.0
    shorts = [(a, b) for a, b in win["lines"] if seg_len(a, b) < TINY]
    short_curves = win["curves"]
    print(f"  window lines={len(win['lines'])} (short<8pt: {len(shorts)}) "
          f"curves={len(win['curves'])} quads={len(win['quads'])}")
    # 曲线半径分布（区分门弧 vs 字形笔画）
    rs = []
    for bz in short_curves:
        xs = [p[0] for p in bz]
        ys = [p[1] for p in bz]
        rs.append(max(max(xs) - min(xs), max(ys) - min(ys)))
    rs.sort()
    if rs:
        hist = collections.Counter(int(x // 5) * 5 for x in rs)
        print("  window 曲线尺寸直方(5pt桶):", dict(sorted(hist.items())))
    # 短笔画聚类成文字块
    pts = [seg_midpoint(a, b) for a, b in shorts]
    n = len(pts)
    parent = list(range(n))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    # 网格加速
    CELL = 12.0
    grid = collections.defaultdict(list)
    for i, p in enumerate(pts):
        grid[(int(p[0] // CELL), int(p[1] // CELL))].append(i)
    for (gx, gy), idxs in grid.items():
        neigh = []
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                neigh.extend(grid.get((gx + dx, gy + dy), []))
        for i in idxs:
            for j in neigh:
                if i < j and math.dist(pts[i], pts[j]) < 9.0:
                    union(i, j)
    groups = collections.defaultdict(list)
    for i in range(n):
        groups[find(i)].append(i)
    blocks = []
    for g in groups.values():
        if len(g) < 4:
            continue
        xs = [pts[i][0] for i in g]
        ys = [pts[i][1] for i in g]
        blocks.append((min(xs), min(ys), max(xs), max(ys), len(g)))
    print(f"  短笔画文字块候选: {len(blocks)}")
    ws = sorted(round(b[2] - b[0], 1) for b in blocks)
    hs = sorted(round(b[3] - b[1], 1) for b in blocks)
    if ws:
        print(f"  块宽: median={ws[len(ws)//2]} max={ws[-1]}; "
              f"块高: median={hs[len(hs)//2]} max={hs[-1]}")
    for b in blocks[:15]:
        print(f"    bbox=({b[0]:.0f},{b[1]:.0f})-({b[2]:.0f},{b[3]:.0f}) strokes={b[4]}")


def probe_labels(page, tag):
    print(f"\n=== [{tag}] 房间标签文本 ===")
    labs = extract_room_labels(page)
    print(f"  共 {len(labs)} 条")
    for t, (x, y) in sorted(labs, key=lambda kv: kv[1][1]):
        print(f"    {t:14s} @ ({x:.0f},{y:.0f})")


def main():
    for tag, path in PDFS:
        doc = fitz.open(path)
        page = doc[0]
        on = get_default_on_layers(doc)
        print("=" * 70)
        print(f"{tag}: rotation={page.rotation} rect={page.rect}")
        print(f"默认开启图层: {sorted(on)}")
        probe_layers(page, tag, on)
        probe_text(page, tag)
        wanted = set(on)
        items = extract_layer_items(page, wanted)
        probe_fire(items, tag)
        probe_window_glyphs(items, tag)
        probe_labels(page, tag)
        doc.close()


if __name__ == "__main__":
    main()
