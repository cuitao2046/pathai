# -*- coding: utf-8 -*-
from pathlib import Path
"""调试：放大查看指定 pt 坐标附近的墙图/连通域/家具线结构"""
import sys
import cv2
import numpy as np
import fitz

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
import parse_cad_pdf as P


def inspect(pdf_path, cx, cy, half_pt, out_png, tag):
    doc = fitz.open(pdf_path)
    page = doc[0]
    on_layers = P.get_default_on_layers(doc)
    wanted = list({P.LAYER_WALL, P.LAYER_WINDOW, P.LAYER_DOOR_FIRE, P.LAYER_STAIR,
                   P.LAYER_ELEVATOR, *P.LAYER_COLUMNS, *P.LAYERS_STRUCT,
                   *P.LAYERS_FURNITURE})
    active = [l for l in wanted if l in on_layers]
    items = P.extract_layer_items(page, set(active))
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

    # 门/窗 -> 封口线（复刻 parse_floor 逻辑）
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
    doors = P.detect_doors(win_arcs, fire["lines"], fire_arcs,
                           struct_segs=all_segs)
    closures = []
    for wg in window_groups:
        if wg["length_pt"] > 2.0:
            closures.extend(P.opening_closures(wg["axis"]))
    for dr in doors:
        closures.extend(P.opening_closures(dr["axis"]))

    walls, walls_furn, minx, miny, W, H, Z = P.rasterize_walls(
        all_segs, closures, furn_segs)
    free = cv2.bitwise_not(walls)
    n, cc, stats, _ = cv2.connectedComponentsWithStats(free, connectivity=4)
    m2_per_px = (P.SCALE / Z) ** 2

    # 裁剪窗口
    px_c, py_c = int((cx - minx) * Z), int((cy - miny) * Z)
    half = int(half_pt * Z)
    x0, x1 = max(0, px_c - half), min(W, px_c + half)
    y0, y1 = max(0, py_c - half), min(H, py_c + half)

    vis = np.zeros((y1 - y0, x1 - x0, 3), np.uint8)
    rng = np.random.default_rng(7)
    colors = rng.integers(60, 255, (n, 3), dtype=np.uint8)
    sub_cc = cc[y0:y1, x0:x1]
    vis = colors[sub_cc]
    # 墙=黑, 家具线=红, 封口线=蓝
    wall_sub = walls[y0:y1, x0:x1] > 0
    vis[wall_sub] = (0, 0, 0)
    furn_sub = walls_furn[y0:y1, x0:x1] > 0
    vis[furn_sub] = (0, 0, 255)
    # 打印窗口内各连通域面积
    ids = np.unique(sub_cc)
    print(f"--- {tag} 窗口内连通域 ---")
    for i in ids:
        if i == 0:
            continue
        a = stats[i, cv2.CC_STAT_AREA] * m2_per_px
        if a > 0.05:
            l, t = stats[i, cv2.CC_STAT_LEFT], stats[i, cv2.CC_STAT_TOP]
            print(f"  cid={i} area={a:.2f}m2 bbox_pt=({minx + l / Z:.0f},"
                  f"{miny + t / Z:.0f})")
    cv2.imwrite(out_png, cv2.resize(vis, ((x1 - x0) * 2, (y1 - y0) * 2),
                                    interpolation=cv2.INTER_NEAREST))
    print(f"  输出 {out_png}")


if __name__ == "__main__":
    # F1 男卫生间 @(1644,850) 与 @(1804,2437)；乐器存放室 @(1177,986)
    inspect(P.PDF_F1, 1644, 850, 130, str(Path(__file__).resolve().parent.parent / "result" / "_debug_toilet1.png"), "男卫1")
    inspect(P.PDF_F1, 1804, 2437, 130, str(Path(__file__).resolve().parent.parent / "result" / "_debug_toilet2.png"), "男卫2")
    inspect(P.PDF_F1, 1177, 986, 130, str(Path(__file__).resolve().parent.parent / "result" / "_debug_yueqi.png"), "乐器存放室")
