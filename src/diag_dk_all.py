# -*- coding: utf-8 -*-
"""枚举 window 图层所有矢量文字块（不丢弃小簇），逐个调用 is_dk_block，
输出 DK / 拒绝原因 / bbox / 笔画数，用于排查漏识的洞口(DK)标注。"""
import sys
sys.path.insert(0, r"E:\code\pathai\src")
from parse_cad_pdf import (PDF_F1, PDF_F2, get_default_on_layers,
                           extract_layer_items, seg_len,
                           cluster_window_glyph_codes, is_dk_block,
                           recognize_dk_glyph_blocks)

PDFS = {"F1": PDF_F1, "F2": PDF_F2}

for fid, pdf in PDFS.items():
    doc = fitz.open(pdf) if (fitz := __import__("fitz")) else None
    page = doc[0]
    on = get_default_on_layers(doc)
    items = extract_layer_items(page, set(on))
    doc.close()
    win = items.get("window", {"lines": []})
    # 完整簇（含 < min_strokes 的），用低阈值重聚类
    all_blocks = cluster_window_glyph_codes(win["lines"], min_strokes=1, with_strokes=True)
    dk = set(recognize_dk_glyph_blocks(win["lines"]))
    print(f"\n===== {fid}: 全部矢量文字块 {len(all_blocks)} 个, 已识别 DK {len(dk)} 个 =====")
    rows = []
    for blk in all_blocks:
        cx, cy, w, h, n, segs = blk
        xs = [p[0] for s in segs for p in s]
        ys = [p[1] for s in segs for p in s]
        bbox = (min(xs), min(ys), max(xs), max(ys))
        ok, reason = is_dk_block(segs, bbox)
        is_in_dk = (round(cx, 1), round(cy, 1)) in {(round(a,1),round(b,1)) for a,b in dk}
        rows.append((cx, cy, w, h, n, ok, reason, is_in_dk))
    # 排序：先按 y 再按 x，便于对照图纸
    rows.sort(key=lambda r: (round(r[1] / 50), r[0]))
    for cx, cy, w, h, n, ok, reason, indk in rows:
        tag = "DK" if ok else ("(dk-set)" if indk else "")
        print(f"  ({cx:7.1f},{cy:7.1f}) size=({w:4.0f}x{h:4.0f}) n={n:2d} "
              f"{'OK ' if ok else 'NO '}{reason:22s} {tag}")
