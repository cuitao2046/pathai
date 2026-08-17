"""时效性优化验证：距离预筛 vs 全量 NLOS。轻量版，不跑 prep/ekf。"""
import time
import numpy as np
from shapely.strtree import STRtree
import debug.trilateration_checker as T

walls = T.load_walls(1)
tree = STRtree([wl["line"] for wl in walls])
beacons = T.load_beacons(1)
walk = T.load_walkable(1)
grid = T.gen_grid(walk, T.GRID_STEP)
pts = [grid[len(grid)//2], grid[200], grid[1500], grid[-1]]


def frame_v1(p):
    bxyz, meas = [], []
    for b in beacons:
        is_nlos, crossed = T.nlos_info(b["xy"], p, tree, walls)
        d = np.linalg.norm([p[0]-b["xy"][0], p[1]-b["xy"][1], T.PHONE_Z-b["z"]])
        if d > T.VIS_RADIUS:
            continue
        pb = T.ranged_bias(crossed) if is_nlos else 0.0
        meas.append(d+pb); bxyz.append([b["xy"][0], b["xy"][1], b["z"]])
    if len(bxyz) < 3:
        return None
    return T.solve_gn(np.array(bxyz, float), np.array(meas, float), fix_z=T.PHONE_Z)


def frame_v2(p):
    cand = []
    for b in beacons:
        d = np.linalg.norm([p[0]-b["xy"][0], p[1]-b["xy"][1], T.PHONE_Z-b["z"]])
        if d <= T.VIS_RADIUS:
            cand.append((b, d))
    bxyz, meas = [], []
    for b, d in cand:
        is_nlos, crossed = T.nlos_info(b["xy"], p, tree, walls)
        pb = T.ranged_bias(crossed) if is_nlos else 0.0
        meas.append(d+pb); bxyz.append([b["xy"][0], b["xy"][1], b["z"]])
    if len(bxyz) < 3:
        return None
    return T.solve_gn(np.array(bxyz, float), np.array(meas, float), fix_z=T.PHONE_Z)


# warmup
for _ in range(20):
    frame_v1(pts[0]); frame_v2(pts[0])

print("单帧计算延迟对比 (ms)")
print(f"{'场景':<22}{'v1 全量NLOS':>14}{'v2 距离预筛':>14}")
for p in pts:
    t = time.time()
    for _ in range(100):
        frame_v1(p)
    d1 = (time.time()-t)/100*1000
    t = time.time()
    for _ in range(100):
        frame_v2(p)
    d2 = (time.time()-t)/100*1000
    print(f"点({p[0]:6.0f},{p[1]:6.0f}){d1:>13.1f}ms{d2:>13.1f}ms")
print("注: v2 只对可见(d<=VIS)信标做穿墙判定, 省去远距离信标开销")
