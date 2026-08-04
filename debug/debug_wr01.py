"""debug: 定位 F1 首层 卫生间 II-WR-01 附近的 DK 门洞识别情况。

直接打开首层 PDF，在原始 PDF pt 坐标空间里：
  1) 找文本 "WR-01"/"II-WR" 的位置；
  2) 收集其附近(±90pt)的 window 组、DK 块、以及全部矢量编号块(含 is_dk_block 诊断)；
  3) 复现“DK块→最近 window 组(<13pt)→转为门洞”的判断，定位漏检原因。
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
import parse_cad_pdf as P  # noqa: E402
import fitz  # noqa: E402

PDF = P.PDF_F1
doc = fitz.open(PDF)
page = doc[0]

# 1) 文本定位 WR-01 / II-WR
print("=== 文本中 WR 相关标注 ===")
text_locs = []
d = page.get_text("dict")
for block in d["blocks"]:
    if block.get("type") != 0:
        continue
    for line in block.get("lines", []):
        txt = "".join(s["text"] for s in line["spans"]).strip()
        if "WR" in txt.upper():
            x0, y0, x1, y1 = line["bbox"]
            cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
            text_locs.append((txt, cx, cy))
            print(f"  {txt!r:20s} @ ({cx:8.1f}, {cy:8.1f})")

# 2) 提取 window 图层
on = P.get_default_on_layers(doc)
wanted = [P.LAYER_WINDOW]
active = [l for l in wanted if l in on]
items = P.extract_layer_items(page, set(active))
win = items.get(P.LAYER_WINDOW, {"lines": [], "quads": [], "curves": []})
window_groups, win_door_curves, removed = P.classify_window_layer(
    win["lines"], win["quads"], win["curves"])

# 全部矢量编号块 + DK 判定诊断
blocks = P.cluster_window_glyph_codes(win["lines"], with_strokes=True)
dk_blocks = P.recognize_dk_glyph_blocks(win["lines"])

print(f"\nwindow 组总数: {len(window_groups)} | DK 块总数: {len(dk_blocks)} | 矢量编号块总数: {len(blocks)}")

# 3) 对每个 WR 文本位置，收集周边信息
R = 90.0  # 搜索半径 pt
for txt, tx, ty in text_locs:
    print(f"\n========== 目标 {txt!r} @ ({tx:.1f},{ty:.1f}) 半径 {R}pt 内 ==========")
    near_wg = [(wg["center"], wg["length_pt"]) for wg in window_groups
               if abs(wg["center"][0] - tx) < R and abs(wg["center"][1] - ty) < R]
    print(f"  附近 window 组: {len(near_wg)}")
    for c, ln in near_wg:
        # 该组是否会被某 DK 块(<13pt)转为门洞?
        hit = [dk for dk in dk_blocks if ((dk[0]-c[0])**2+(dk[1]-c[1])**2)**0.5 < 13.0]
        print(f"    window组 center=({c[0]:.1f},{c[1]:.1f}) len={ln:.1f}pt  -> 命中DK块数={len(hit)}")
    near_dk = [dk for dk in dk_blocks
               if abs(dk[0] - tx) < R and abs(dk[1] - ty) < R]
    print(f"  附近 DK 块: {len(near_dk)}")
    for dk in near_dk:
        print(f"    DK @ ({dk[0]:.1f},{dk[1]:.1f})")
    # 全部矢量编号块诊断
    near_all = []
    for blk in blocks:
        cx, cy, w, h, n, segs = blk
        if abs(cx - tx) < R and abs(cy - ty) < R:
            xs = [p[0] for s in segs for p in s]
            ys = [p[1] for s in segs for p in s]
            bbox = (min(xs), min(ys), max(xs), max(ys))
            ok, reason = P.is_dk_block(segs, bbox)
            near_all.append((cx, cy, ok, reason))
    print(f"  附近全部矢量编号块: {len(near_all)}")
    for cx, cy, ok, reason in near_all:
        print(f"    块 @ ({cx:.1f},{cy:.1f}) DK={ok} reason={reason}")

doc.close()
