# -*- coding: utf-8 -*-
"""调试合并步骤：打印指定区域内小单元的已标注邻居(4px半径)"""
import sys
import cv2
import numpy as np
import fitz

sys.path.insert(0, r"E:\code\pathai\src")
import parse_cad_pdf as P

CX, CY = 1644, 850   # 男卫1
HALF = 130


def main():
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
    absorb_px = P.ABSORB_CELL_M2 / m2_per_px
    areas_all = stats[:, cv2.CC_STAT_AREA].astype(np.int64)
    border_ids = set(np.unique(np.concatenate([
        cc[0, :], cc[-1, :], cc[:, 0], cc[:, -1]])))

    owner = np.full((H, W), -1e9, np.float32)
    region_ids = np.where(areas_all >= absorb_px)[0]
    region_ids = region_ids[region_ids != 0]
    region_mask = np.isin(cc, region_ids)
    owner[region_mask] = cc[region_mask].astype(np.float32)
    border_arr = np.fromiter((i in border_ids for i in range(n)),
                             dtype=bool, count=n)
    owner[np.isin(cc, np.where(border_arr)[0])] = -1.0
    k3 = np.ones((3, 3), np.uint8)
    for _ in range(80):
        fill_mask = (owner == 0)
        if not fill_mask.any():
            break
        grown = cv2.dilate((owner != 0).astype(np.uint8), k3) > 0
        frontier = grown & fill_mask
        if not frontier.any():
            break
        nbr_max = cv2.dilate(owner, k3)
        owner[frontier] = nbr_max[frontier]

    # 标签 -> cid
    probes = [(t, (int(round((x - minx) * Z)), int(round((y - miny) * Z))))
              for t, (x, y) in labels if not P.LABEL_SKIP_RE.search(t)]

    def comp_at(px, py):
        if 0 <= px < W and 0 <= py < H and owner[py, px] > 0:
            return int(owner[py, px])
        for r in (6, 12, 24, 40, 60, 90):
            x0, x1 = max(0, px - r), min(W, px + r + 1)
            y0, y1 = max(0, py - r), min(H, py + r + 1)
            sub = owner[y0:y1, x0:x1]
            vals = sub[sub > 0]
            if len(vals):
                ids, cnt = np.unique(vals, return_counts=True)
                return int(ids[np.argmax(cnt)])
        return 0

    label_of_cid = {}
    seen = set()
    for t, (px, py) in probes:
        cid = comp_at(px, py)
        if cid == 0 or cid in border_ids or cid in seen:
            continue
        seen.add(cid)
        label_of_cid[cid] = t
    labeled_cids = set(label_of_cid)

    # 窗口内候选单元邻居分析
    px_c, py_c = int((CX - minx) * Z), int((CY - miny) * Z)
    half = int(HALF * Z)
    x0w, x1w = max(0, px_c - half), min(W, px_c + half)
    y0w, y1w = max(0, py_c - half), min(H, py_c + half)
    k25 = np.ones((25, 25), np.uint8)
    print("窗口内 <9m2 单元的邻居(12px 半径)：")
    ids_in_win = np.unique(cc[y0w:y1w, x0w:x1w])
    for cid_u in ids_in_win:
        if cid_u == 0 or cid_u in border_ids:
            continue
        a = areas_all[cid_u] * m2_per_px
        if a >= P.MERGE_REGION_M2 or a < 0.05:
            continue
        l, t = int(stats[cid_u, 0]), int(stats[cid_u, 1])
        w_, h_ = int(stats[cid_u, 2]), int(stats[cid_u, 3])
        x0, x1 = max(0, l - 26), min(W, l + w_ + 26)
        y0, y1 = max(0, t - 26), min(H, t + h_ + 26)
        mask_u = (cc[y0:y1, x0:x1] == cid_u)
        dil_u = cv2.dilate(mask_u.astype(np.uint8), k25) > 0
        band = dil_u & ~mask_u
        owner_sub = owner[y0:y1, x0:x1]
        nbrs = {int(v) for v in np.unique(owner_sub[band & (owner_sub > 0)])}
        nl = nbrs & labeled_cids
        tag = label_of_cid.get(cid_u, "")
        names = [f"{label_of_cid.get(v, v)}" for v in nl]
        neg = "有室外" if (owner_sub[band] == -1).any() else ""
        print(f"  cid={cid_u} area={a:.2f}m2 label={tag or '-'} "
              f"已标注邻居={names} 邻居={sorted(nbrs)[:8]} {neg}")


if __name__ == "__main__":
    main()
