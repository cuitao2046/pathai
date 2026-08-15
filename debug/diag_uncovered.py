#!/usr/bin/env python3
# 诊断直角坐标法未覆盖点的成因
import importlib.util, math, json
from pathlib import Path
import shapely.geometry as g

spec = importlib.util.spec_from_file_location("cc", "E:/code/pathai/debug/coverage_compare.py")
cc = importlib.util.module_from_spec(spec); spec.loader.exec_module(cc)

bld = json.load(open(cc.BUILDING))
for fl_key in ["1F", "2F"]:
    fk = fl_key[0]
    anchors, occ, bb, segs = cc.build_floor(bld["floors"][fk])
    vp = cc.load_valid_region()[fl_key]
    unc = []
    b = vp.bounds
    nx = int(math.ceil((b[2]-b[0])/cc.GRID))+1
    ny = int(math.ceil((b[3]-b[1])/cc.GRID))+1
    for ix in range(nx):
        x = b[0]+ix*cc.GRID
        for iy in range(ny):
            y = b[1]+iy*cc.GRID
            if not vp.contains(g.Point(x, y)):
                continue
            P = (x, y); best = None
            for (x1,y1,x2,y2) in segs:
                if (math.hypot(x1-x,y1-y) > cc.R_PERP and math.hypot(x2-x,y2-y) > cc.R_PERP):
                    continue
                dx, dy = x2-x1, y2-y1; L2 = dx*dx+dy*dy
                if L2 == 0: continue
                t = max(0, min(1, ((x-x1)*dx+(y-y1)*dy)/L2))
                fx, fy = x1+t*dx, y1+t*dy; d = math.hypot(x-fx, y-fy)
                if d > cc.R_PERP: continue
                if best and d >= best[0]: continue
                if not cc.los_clear(P, (fx, fy), occ, bb): continue
                best = (d, fx, fy)
            if best is None:
                unc.append((x, y))
    # 到最近墙(忽略视线) 的距离
    mind = []
    for (x, y) in unc:
        dmin = 1e9
        for (x1,y1,x2,y2) in segs:
            dx, dy = x2-x1, y2-y1; L2 = dx*dx+dy*dy
            if L2 == 0: continue
            t = max(0, min(1, ((x-x1)*dx+(y-y1)*dy)/L2))
            fx, fy = x1+t*dx, y1+t*dy
            dmin = min(dmin, math.hypot(x-fx, y-fy))
        mind.append(dmin)
    print(f"{fl_key}: 未覆盖点 {len(unc)}")
    if mind:
        print(f"  到最近墙(无视线)距离 min={min(mind):.1f}m mean={sum(mind)/len(mind):.1f}m max={max(mind):.1f}m")
    print("  样例:", [(round(x,1), round(y,1)) for x, y in unc[:5]])
