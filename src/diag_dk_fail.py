# -*- coding: utf-8 -*-
"""详细打印被拒绝的"疑似 DK"块（n 在 6~40 之间）的笔画特征，定位漏识原因。"""
import sys
sys.path.insert(0, r"E:\code\pathai\src")
import fitz, math
from parse_cad_pdf import (PDF_F1, PDF_F2, get_default_on_layers,
                           extract_layer_items, seg_len,
                           cluster_window_glyph_codes, is_dk_block,
                           _stroke_features)

PDFS = {"F1": PDF_F1, "F2": PDF_F2}
for fid, pdf in PDFS.items():
    doc = fitz.open(pdf); page = doc[0]; on = get_default_on_layers(doc)
    items = extract_layer_items(page, set(on)); doc.close()
    win = items.get("window", {"lines": []})
    blocks = cluster_window_glyph_codes(win["lines"], min_strokes=1, with_strokes=True)
    print(f"\n===== {fid} 疑似DK(6<=n<=40)被拒块 =====")
    cand = []
    for blk in blocks:
        cx, cy, w, h, n, segs = blk
        if not (6 <= n <= 40):
            continue
        xs = [p[0] for s in segs for p in s]; ys = [p[1] for s in segs for p in s]
        bbox = (min(xs), min(ys), max(xs), max(ys))
        ok, reason = is_dk_block(segs, bbox)
        if ok:
            continue
        # 统计特征
        feats = [f for s in segs for f in [_stroke_features(s[0], s[1])] if f]
        n_vert = sum(1 for f in feats if f["is_vert"] and f["L"] >= 2.0)
        n_diag = sum(1 for f in feats if not f["is_vert"] and not f["is_horiz"])
        cand.append((cx, cy, w, h, n, reason, n_vert, n_diag, len(feats)))
    cand.sort(key=lambda r: r[0])
    for cx, cy, w, h, n, reason, nv, nd, nf in cand:
        print(f"  ({cx:7.1f},{cy:7.1f}) w={w:4.0f} h={h:4.0f} n={n:2d} "
              f"reason={reason:20s} vert(L>=2)={nv} diag={nd} feat={nf}")
