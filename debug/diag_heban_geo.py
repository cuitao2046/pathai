# -*- coding: utf-8 -*-
"""Diagnose the 合班教室 neighborhood: what wall gaps / doors exist near its label."""
import math, sys, json
sys.path.insert(0, "src")
import parse_cad_pdf as P
import fitz

SCALE = P.SCALE

floor_no = 1
doc = fitz.open(P.PDF_F1)
page = doc[0]
on = P.get_default_on_layers(doc)
wanted = list({P.LAYER_WALL, P.LAYER_WINDOW, P.LAYER_DOOR_FIRE, P.LAYER_STAIR,
               P.LAYER_ELEVATOR, *P.LAYER_COLUMNS, *P.LAYERS_STRUCT,
               *P.LAYERS_FURNITURE} - set(P.LAYERS_IGNORE))
active = [l for l in wanted if l in on]
items = P.extract_layer_items(page, set(active))
labels = P.extract_room_labels(page)
room_names, room_codes = labels

heban = [e for e in room_names if "合班" in e[0]]
print("合班标签:", [(e[0], [round(x, 1) for x in e[1]]) for e in heban])

struct_segs = []
for lname in P.LAYERS_STRUCT:
    li = items.get(lname)
    if li:
        struct_segs.extend(P.wall_segments(li))
_, opening_gaps = P.merge_collinear(struct_segs, gap_tol=P.MAX_OPENING_WIDTH_PT, record_gaps=True)
struct_segs2, wall_gaps = P.merge_collinear(struct_segs, record_gaps=True)
print(f"total struct_segs={len(struct_segs)} opening_gaps={len(opening_gaps)} wall_gaps(<=30pt)={len(wall_gaps)}")

# also near gaps from a wider (non-bridged) scan up to 150pt
_, wide_gaps = P.merge_collinear(struct_segs, gap_tol=150.0, record_gaps=True)

R_M = 12.0
for e in heban:
    text, (lx, ly) = e[0], e[1]
    print(f"\n=== 合班标签 {text} @ pt({lx:.1f},{ly:.1f}) (m=({P.pt2m((lx,ly))[0]:.2f},{P.pt2m((lx,ly))[1]:.2f})) ===")
    r_pt = R_M / SCALE
    for name, gaps in (("opening_gaps(<=80pt)", opening_gaps),
                       ("wide_gaps(<=150pt)", wide_gaps)):
        near = [g for g in gaps
                if math.hypot(g["center"][0]-lx, g["center"][1]-ly) < r_pt]
        print(f"  [{name}] near(<= {R_M}m): {len(near)}")
        for g in sorted(near, key=lambda g: math.hypot(g["center"][0]-lx, g["center"][1]-ly)):
            cx, cy = g["center"]
            w = g["gap"] * SCALE
            ll = g["left_len"] * SCALE
            rl = g["right_len"] * SCALE
            print(f"    gap w={w:.2f}m  center=({cx:.1f},{cy:.1f})  left_len={ll:.2f}m right_len={rl:.2f}m")

# doors near heban
print("\n--- doors near heban ---")
# reuse detect pipeline lightly: build window groups + doors
from parse_cad_pdf import (cluster_window_glyph_codes, extract_dk_text_labels,
                           find_wall_openings, detect_doors, dedupe_doorways)
win_lines = []
for li in items.get(P.LAYER_WINDOW, []):
    win_lines.extend(P.window_lines(li)) if hasattr(P, "window_lines") else None
# simpler: get window stroke lines via items
win_items = items.get(P.LAYER_WINDOW, [])
# collect short strokes as (a,b)
strokes = []
for it in win_items:
    # it is a drawing dict from get_drawings
    pass
# Fallback: detect doors through parse_floor-equivalent minimal path is heavy;
# instead just report gaps. Done above.
print("(door detection skipped; gap scan sufficient for planning)")
doc.close()
