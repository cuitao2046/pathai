# -*- coding: utf-8 -*-
"""检查指定 pt 坐标附近门的 arc_mid 与各房间多边形的包含关系"""
import sys
import math
import fitz
from shapely.geometry import Point

sys.path.insert(0, r"E:\code\pathai\src")
import parse_cad_pdf as P

CX, CY, R = 1227, 946, 60.0  # 乐器存放室门附近

doc = fitz.open(P.PDF_F1)
page = doc[0]
on_layers = P.get_default_on_layers(doc)
wanted = list({P.LAYER_WALL, P.LAYER_WINDOW, P.LAYER_DOOR_FIRE, P.LAYER_STAIR,
               P.LAYER_ELEVATOR, *P.LAYER_COLUMNS, *P.LAYERS_STRUCT,
               *P.LAYERS_FURNITURE})
active = [l for l in wanted if l in on_layers]
items = P.extract_layer_items(page, set(active))
labels = P.extract_room_labels(page)
doc.close()

struct_segs = []
for lname in P.LAYERS_STRUCT:
    li = items.get(lname)
    if li:
        struct_segs.extend(P.wall_segments(li))
struct_segs = P.merge_collinear(struct_segs)
furn_segs = []
for lname in P.LAYERS_FURNITURE:
    li = items.get(lname)
    if li:
        furn_segs.extend(P.wall_segments(li))
furn_segs = P.merge_collinear(furn_segs)
all_segs = struct_segs + furn_segs

win = items.get(P.LAYER_WINDOW, {"lines": [], "quads": [], "curves": []})
window_groups, win_door_curves, _ = P.classify_window_layer(
    win["lines"], win["quads"], win["curves"])
window_groups = [wg for wg in window_groups
                 if any(P.point_to_seg_dist(wg["center"], a, b)[0] < 6.0
                        for a, b in all_segs)]

def near_wall(bz, tol=6.0):
    p1, p4 = bz[0], bz[3]
    for a, b in all_segs:
        if P.point_to_seg_dist(p1, a, b)[0] < tol:
            return True
        if P.point_to_seg_dist(p4, a, b)[0] < tol:
            return True
    return False

fire = items.get(P.LAYER_DOOR_FIRE, {"lines": [], "quads": [], "curves": []})
win_arcs = [bz for bz in win_door_curves if near_wall(bz)]
fire_arcs = [bz for bz in fire["curves"] if near_wall(bz)]
doors = P.detect_doors(win_arcs, fire["lines"], fire_arcs, struct_segs=all_segs)
closures = []
for wg in window_groups:
    if wg["length_pt"] > 2.0:
        closures.extend(P.opening_closures(wg["axis"]))
for dr in doors:
    closures.extend(P.opening_closures(dr["axis"]))

labeled_polys = P.build_rooms(all_segs, closures, furn_segs=furn_segs,
                              label_points=labels)

near = [dr for dr in doors
        if math.hypot(dr["center"][0] - CX, dr["center"][1] - CY) < R]
for dr in near:
    print(f"door center=({dr['center'][0]:.0f},{dr['center'][1]:.0f}) "
          f"w={dr['width_pt'] * P.SCALE:.2f}m kind={dr['kind']} "
          f"arc_mid=({dr['arc_mid'][0]:.0f},{dr['arc_mid'][1]:.0f})")
    am = Point(dr["arc_mid"])
    for text, poly in labeled_polys:
        d_center = poly.exterior.distance(Point(dr["center"]))
        d_am = poly.exterior.distance(am)
        if d_center < 40 or d_am < 40:
            print(f"    {text:12s} contains_arc_mid={poly.contains(am)} "
                  f"d_ext(arc_mid)={d_am:.1f}pt d_ext(center)={d_center:.1f}pt")
