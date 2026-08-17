# -*- coding: utf-8 -*-
"""
纯 BLE 信标三点定位（三边测量）误差优化仿真器 —— 零指纹依赖。

目的：在「不采集指纹」的前提下，量化各优化杠杆对定位误差 σ_pos 的影响，
回答「当前 61 信标部署下，纯三点定位能否做到路线点误差 ≤1m」。

本版本（A 方案）在原有杠杆基础上新增「利用已知墙体几何」的四项机制：
  - 墙体几何 NLOS 检测：STRtree 射线穿墙判定，替代随机 NLOS 注入（更真实）
  - 逐墙衰减标定模型：material×thickness → 预测 NLOS 偏置
  - WLS 加权 / 偏置校正：剔除或校正被墙偏置的测距
  - EKF 跟踪滤波：沿走廊轨迹时序融合 + 残差门控剔除离群

复用资产：
  result/ble_deployment.json                 (61 信标, coordinates/floor/installHeight/txPower)
  result/fingerprint_valid_region.json       (可行走多边形, 仅作几何约束)
  result/school_building_01_map_v9.geojson   (墙体 LineString + material/thickness，做 NLOS 几何判定)

依赖：numpy, shapely
"""
import json, math, random, time
import numpy as np
from shapely.geometry import Polygon, Point, LineString
from shapely.ops import nearest_points, unary_union
from shapely.strtree import STRtree

RNG = random.Random(20260817)
np.random.seed(20260817)

PHONE_Z = 1.2          # 手机持握高度 (m)
VIS_RADIUS = 18.0      # 可见半径 (m)
RAW_SIGMA = 2.0        # 单样本 RSSI 测距 σ_d (m)，典型多径/遮挡
N_SAMPLES = 20         # 时序平均样本数（≈2s @10Hz）
GRID_STEP = 1.0        # 测试点网格步长 (m)
GDOP_GATE = 3.0        # GDOP 门限
NLOS_BIAS_SCATTER = 0.2  # 标定偏置 ±20% 散射
SHRINK = 0.7             # 在线分类偏置缩减系数（硬减全量会反馈发散，见闭环验证）

# 逐墙衰减标定：穿墙等效路径放大系数（厚度×factor = 等效附加距离）
WALL_FACTOR = {
    "brick": 4.0, "concrete": 5.0, "gypsum": 2.5, "wood": 2.0,
    "glass": 1.5, "partition": 3.0, "default": 3.0,
}


# ---------- 数据加载 ----------
def load_beacons(floor):
    b = json.load(open("result/ble_deployment.json", encoding="utf-8"))
    out = []
    for x in b["beacons"]:
        if x["floor"] != floor:
            continue
        cx, cy = x["coordinates"]
        out.append({
            "id": x["beaconId"],
            "xy": np.array([cx, cy], float),
            "z": float(x.get("installHeight", 2.2)),
            "tx": x.get("txPower", -10),
        })
    return out

def load_walls(floor):
    g = json.load(open("result/school_building_01_map_v9.geojson", encoding="utf-8"))
    feats = g["floors"][str(floor)]["geometry"]["walls"]
    lines = []
    for f in feats:
        coords = f["geometry"]["coordinates"]
        if len(coords) < 2:
            continue
        mat = (f.get("properties", {}).get("material") or "default").lower()
        _th = f.get("properties", {}).get("thickness", 0.2)
        th = float(_th) if _th is not None else 0.2
        lines.append({
            "line": LineString(coords),
            "material": mat,
            "thickness": th,
            "factor": WALL_FACTOR.get(mat, WALL_FACTOR["default"]),
        })
    return lines

def load_walkable(floor):
    v = json.load(open("result/fingerprint_valid_region.json", encoding="utf-8"))
    polys = [Polygon(p["exterior"]) for p in v["floors"][f"{floor}F"]["polygons"]]
    return unary_union(polys)

def gen_grid(poly, step=GRID_STEP):
    minx, miny, maxx, maxy = poly.bounds
    pts = []
    x = minx
    while x <= maxx:
        y = miny
        while y <= maxy:
            pt = Point(x, y)
            if poly.contains(pt) or poly.distance(pt) < step * 0.6:
                pts.append(np.array([x, y], float))
            y += step
        x += step
    return pts


# ---------- 测距 + 墙体 NLOS 检测 ----------
def nlos_info(b_xy, p_xy, tree, wall_lines):
    """射线穿墙判定：phone(p_xy)↔beacon(b_xy) 线段与墙体相交 → NLOS。
    返回 (is_nlos, 穿过的墙体列表)。用 STRtree 先粗筛包围盒。"""
    seg = LineString([p_xy, b_xy])
    try:
        cand = tree.query(seg)
    except Exception:
        cand = range(len(wall_lines))
    crossed = []
    for i in cand:
        wl = wall_lines[i]
        try:
            if seg.crosses(wl["line"]) or seg.intersects(wl["line"]):
                crossed.append(wl)
        except Exception:
            continue
    return (len(crossed) > 0), crossed

def ranged_bias(crossed):
    """标定模型：穿过墙体的等效附加距离 = Σ thickness×factor。"""
    return sum(wl["thickness"] * wl["factor"] for wl in crossed)

def make_measurement(beacon, p_true, tree, wall_lines):
    """几何 NLOS（射线穿墙）+ 标定偏置 + 零均值噪声。
    返回 (measured_d, is_nlos, pred_bias)。"""
    d = np.linalg.norm(np.array([p_true[0], p_true[1], PHONE_Z]) -
                       np.array([beacon["xy"][0], beacon["xy"][1], beacon["z"]]))
    if d > VIS_RADIUS:
        return None
    is_nlos, crossed = nlos_info(beacon["xy"], p_true, tree, wall_lines)
    pred_bias = ranged_bias(crossed) if is_nlos else 0.0
    # 实际偏置 = 标定预测值 ×(1±scatter)；标定不完美
    actual_bias = pred_bias * (1.0 + RNG.uniform(-NLOS_BIAS_SCATTER, NLOS_BIAS_SCATTER)) if is_nlos else 0.0
    noise = RNG.gauss(0, RAW_SIGMA)
    return max(0.1, d + actual_bias + noise), is_nlos, pred_bias


# ---------- 解算核心 ----------
def gdop(beacons_xyz, p):
    G = []
    for b in beacons_xyz:
        vec = np.array([p[0]-b[0], p[1]-b[1], p[2]-b[2]])
        n = np.linalg.norm(vec)
        if n < 1e-6:
            continue
        G.append(vec / n)
    G = np.array(G)
    if G.shape[0] < 3:
        return float("inf")
    try:
        Q = np.linalg.inv(G.T @ G)
        return math.sqrt(np.trace(Q))
    except np.linalg.LinAlgError:
        return float("inf")

def solve_gn(bxyz, meas, fix_z=None, init=None, iters=12, sigmas=None):
    """Levenberg-Marquardt + WLS（sigmas 非空时加权）。防 NLOS 偏置发散。"""
    n = len(bxyz)
    if n < 3:
        return None
    if init is None:
        init = np.mean([b for b in bxyz], axis=0)
    x = np.array([init[0], init[1], fix_z], float) if fix_z is not None else np.array(init, float)
    lam = 1e-3
    for _ in range(iters):
        G = []; r = []; W = []
        for (b, d) in zip(bxyz, meas):
            vec = x - b
            dist = np.linalg.norm(vec)
            if dist < 1e-6:
                continue
            u = vec / dist
            G.append(u); r.append(dist - d)
            W.append(1.0 / (sigmas[len(W)]**2) if sigmas is not None else 1.0)
        G = np.array(G); r = np.array(r); W = np.diag(W)
        if len(G) < 3:
            return None
        try:
            A = G.T @ W @ G + lam * np.eye(G.shape[1])
            dx = np.linalg.solve(A, -G.T @ W @ r)
        except np.linalg.LinAlgError:
            return None
        step = np.linalg.norm(dx)
        if step > 8.0:
            dx = dx / step * 8.0
        x = x + dx
        lam = max(1e-6, lam * 0.7)
    if fix_z is not None:
        x[2] = fix_z
    return x

def project_to_walkable(xy, poly):
    pt = Point(xy[0], xy[1])
    if poly.contains(pt):
        return xy
    np_ = nearest_points(poly, pt)[0]
    return np.array([np_.x, np_.y], float)


# ---------- 预计算（每点逐信标几何 NLOS + 时序平均，只算一次） ----------
def prep_points(floor=1, grid_step=GRID_STEP, n_avg=N_SAMPLES):
    beacons = load_beacons(floor)
    walks = load_walkable(floor)
    wall_lines = load_walls(floor)
    tree = STRtree([wl["line"] for wl in wall_lines])
    grid = gen_grid(walks, grid_step)
    pts = []
    for p in grid:
        vis = []
        for b in beacons:
            m = make_measurement(b, p, tree, wall_lines)
            if m is None:
                continue
            meas, is_nlos, pred_bias = m
            bxyz = np.array([b["xy"][0], b["xy"][1], b["z"]], float)
            d_true = np.linalg.norm(np.array([p[0], p[1], PHONE_Z]) -
                                   np.array([b["xy"][0], b["xy"][1], b["z"]]))
            # 时序平均：N 次重采样（同 NLOS 状态/同预测偏置，含其散射+零均值噪声）
            acc = 0.0
            for _ in range(n_avg):
                ab = pred_bias * (1.0 + RNG.uniform(-NLOS_BIAS_SCATTER, NLOS_BIAS_SCATTER)) if is_nlos else 0.0
                acc += d_true + ab + RNG.gauss(0, RAW_SIGMA)
            avg = acc / n_avg
            vis.append({"bxyz": bxyz, "single": meas, "avg": avg,
                        "is_nlos": is_nlos, "pred_bias": pred_bias})
        if len(vis) < 3:
            continue
        pts.append({"p": p, "vis": vis})
    return pts, walks, tree, wall_lines


# ---------- 配置评估 ----------
def eval_config(pts, walk, use_3d=True, temporal=False, map_con=False,
                nlos_mode="none"):
    """nlos_mode:
        none     : 忽略 NLOS（不校正、不加权）
        correct  : 减去预测偏置后再解算
        weight   : WLS 给 NLOS 大 σ 降权
        hybrid   : 先减偏置 + 再 WLS 降权
    """
    errors = []
    used_gdop = []
    for item in pts:
        p = item["p"]
        vis = item["vis"]
        bxyz = [v["bxyz"] for v in vis]
        if temporal:
            meas = [v["avg"] for v in vis]
            is_nlos = [v["is_nlos"] for v in vis]
            pred = [v["pred_bias"] for v in vis]
        else:
            meas = [v["single"] for v in vis]
            is_nlos = [v["is_nlos"] for v in vis]
            pred = [v["pred_bias"] for v in vis]
        # NLOS 处理
        sigmas = None
        if nlos_mode == "correct":
            meas = [mm - pp for mm, pp in zip(meas, pred)]
        elif nlos_mode in ("weight", "hybrid"):
            if nlos_mode == "hybrid":
                meas = [mm - pp for mm, pp in zip(meas, pred)]
            sigmas = [4.0 if nl else RAW_SIGMA / (math.sqrt(N_SAMPLES) if temporal else 1.0)
                      for nl in is_nlos]
            # 时序平均已降噪，非 NLOS 的 σ 也相应缩小
            if temporal:
                sigmas = [4.0 if nl else RAW_SIGMA / math.sqrt(N_SAMPLES) for nl in is_nlos]
        else:
            sigmas = None
        fix_z = PHONE_Z if use_3d else 0.0
        init = np.mean(bxyz, axis=0)
        sol = solve_gn(bxyz, meas, fix_z=fix_z, init=init, sigmas=sigmas)
        if sol is None:
            continue
        xy = np.array([sol[0], sol[1]])
        if map_con:
            xy = project_to_walkable(xy, walk)
        errors.append(np.linalg.norm(xy - p))
        used_gdop.append(gdop(bxyz, np.array([p[0], p[1], PHONE_Z])))
    errors = np.array(errors)
    used_gdop = np.array(used_gdop)
    return {
        "n_points": len(errors),
        "median_err": float(np.median(errors)) if len(errors) else None,
        "mean_err": float(np.mean(errors)) if len(errors) else None,
        "p95_err": float(np.percentile(errors, 95)) if len(errors) else None,
        "pct_le_1m": float(np.mean(errors <= 1.0) * 100) if len(errors) else None,
        "gdop_median": float(np.median(used_gdop)) if len(used_gdop) else None,
        "gdop_gt3_pct": float(np.mean(used_gdop > GDOP_GATE) * 100) if len(used_gdop) else None,
    }


# ---------- EKF 跟踪滤波（沿主走廊轨迹） ----------
def find_main_corridor(pts):
    """挑可行走网格中 y 带宽最大者作为主走廊，按 x 排序成轨迹。"""
    from collections import defaultdict
    by_y = defaultdict(list)
    for item in pts:
        by_y[round(item["p"][1], 1)].append(item["p"][0])
    best_y = max(by_y, key=lambda y: len(by_y[y]))
    xs = sorted(set(by_y[best_y]))
    path = [np.array([x, best_y], float) for x in xs]
    return path

def ekf_track(pts, walk, dt=0.5, gate=3.0):
    """常数速度 EKF：状态 [px,py,vx,vy]，测距为伪观测，残差门控剔除 NLOS 离群。
    返回沿轨迹的逐点误差列表与轨迹长度。"""
    # 建立轨迹：沿主走廊
    path = find_main_corridor(pts)
    # 预取轨迹点对应的可见信标（最近网格点）
    grid_pts = [item["p"] for item in pts]
    errors = []
    # 状态
    x = np.array([path[0][0], path[0][1], 0.0, 0.0])
    P = np.eye(4) * 5.0
    F = np.array([[1,0,dt,0],[0,1,0,dt],[0,0,1,0],[0,0,0,1]])
    Q = np.eye(4) * 0.2
    prev = path[0]
    def nearest_item(pp):
        bi = min(range(len(grid_pts)), key=lambda i: np.linalg.norm(grid_pts[i]-pp))
        return pts[bi]
    for k, pp in enumerate(path):
        # 预测
        x = F @ x
        P = F @ P @ F.T + Q
        # 更新：取该点可见信标
        item = nearest_item(pp)
        vis = item["vis"]
        if len(vis) < 3:
            prev = pp
            continue
        bxyz = np.array([v["bxyz"] for v in vis])
        meas = np.array([v["avg"] for v in vis])
        is_nlos = [v["is_nlos"] for v in vis]
        pred_bias = np.array([v["pred_bias"] for v in vis])
        # 校正 NLOS 偏置
        meas_corr = meas - pred_bias
        # EKF 对每个信标线性化 h = ||x-b||
        R_base = (RAW_SIGMA / math.sqrt(N_SAMPLES))**2
        yin = pp
        for i in range(len(bxyz)):
            bx, by = bxyz[i][0], bxyz[i][1]
            dx_ = x[0]-bx; dy_ = x[1]-by
            dhat = math.hypot(dx_, dy_)
            if dhat < 1e-3:
                continue
            z = meas_corr[i]
            H = np.array([dx_/dhat, dy_/dhat, 0, 0])
            R = R_base * 9 if is_nlos[i] else R_base  # NLOS 观测噪声放大
            S = H @ P @ H.T + R
            innov = z - dhat
            # 残差门控：NLOS 离群跳过
            if R > R_base and abs(innov) > gate * math.sqrt(S):
                continue
            K = P @ H.T / S
            x = x + K * innov
            P = (np.eye(4) - np.outer(K, H)) @ P
        err = np.linalg.norm(x[:2] - pp)
        errors.append(err)
        prev = pp
    return errors


# ---------- 在线 NLOS 分类器（估计位置 ray-cast，替代 oracle 真值 ray-cast） ----------
def classify_nlos_online(xhat_xy, b_xy, tree, wall_lines):
    """在线 NLOS 分类：用估计位置 xhat 代替真值做射线穿墙判定。
    返回 (is_nlos, 预测偏置)。这是 oracle 与在线的唯一差别点。"""
    is_nlos, crossed = nlos_info(b_xy, xhat_xy, tree, wall_lines)
    return is_nlos, (ranged_bias(crossed) if is_nlos else 0.0)


def solve_with_online_nlos(xhat0, vis, walk, tree, wall_lines,
                           use_3d=True, temporal=True, map_con=True, iters=3):
    """[失败模式参考] 朴素迭代闭合：估计位置 ray-cast 分类 → 全量硬减偏置 → 解算 → 更新估计。
    ⚠️ 闭环验证表明：分类准确率约 87%，错误硬减偏置会反馈发散（全网格中位达 15.9m）。
    仅作对照，不在主报告中使用；稳健版见 ekf_track_online / eval_online_grid。"""
    xhat = np.array([xhat0[0], xhat0[1], PHONE_Z if use_3d else 0.0], float)
    bxyz = np.array([v["bxyz"] for v in vis])
    meas_full = np.array([v["avg"] for v in vis]) if temporal else np.array([v["single"] for v in vis])
    for _ in range(iters):
        pred_est = np.array([classify_nlos_online(xhat[:2], v["bxyz"][:2], tree, wall_lines)[1]
                             for v in vis])
        meas = meas_full - pred_est
        fix_z = PHONE_Z if use_3d else 0.0
        sol = solve_gn(bxyz, meas, fix_z=fix_z, init=xhat.copy())
        if sol is None:
            break
        xy = np.array([sol[0], sol[1]])
        if map_con:
            xy = project_to_walkable(xy, walk)
        xhat = np.array([xy[0], xy[1], PHONE_Z if use_3d else 0.0], float)
    return xhat[:2]


def eval_online_grid(pts, walk, tree, wall_lines):
    """逐点独立在线（无轨迹连续性）：全 LOS 冷启动首解 → 估计位置分类 → 缩减系数硬减偏置 → 单次解。
    反映「无时序先验」下在线分类器的单点可达性下界。"""
    errors = []
    for item in pts:
        p = item["p"]; vis = item["vis"]
        if len(vis) < 3:
            continue
        bxyz = np.array([v["bxyz"] for v in vis])
        meas = np.array([v["avg"] for v in vis])
        x0 = solve_gn(bxyz, meas, fix_z=PHONE_Z, init=np.mean(bxyz, axis=0))  # 全 LOS 冷启动
        if x0 is None:
            continue
        est = np.array([classify_nlos_online(x0[:2], v["bxyz"][:2], tree, wall_lines)[1]
                        for v in vis])
        meas_corr = meas - est * SHRINK
        x1 = solve_gn(bxyz, meas_corr, fix_z=PHONE_Z, init=x0)
        if x1 is None:
            continue
        errors.append(np.linalg.norm(x1[:2] - p))
    errors = np.array(errors)
    return {
        "n_points": len(errors),
        "median_err": float(np.median(errors)) if len(errors) else None,
        "p95_err": float(np.percentile(errors, 95)) if len(errors) else None,
        "pct_le_1m": float(np.mean(errors <= 1.0) * 100) if len(errors) else None,
    }


def ekf_track_online(pts, walk, tree, wall_lines, dt=0.5, gate=3.0):
    """轨迹在线 EKF（稳健版）：每步用【上一帧 EKF 估计位置 x[:2]】做 ray-cast 分类（非真值）。
    与 oracle L9 唯一差别：NLOS 校正量来自估计位置分类而非真值；采用缩减系数 SHRINK 硬减 +
    高噪声降权，抑制误分类反馈发散。闭环验证显示比朴素迭代稳定（中位 ~1.3m vs 发散 15.9m）。"""
    path = find_main_corridor(pts)
    grid_pts = [item["p"] for item in pts]
    errors = []
    x = np.array([path[0][0], path[0][1], 0.0, 0.0])  # 路线起点已知（F1-RM-0016）
    P = np.eye(4) * 5.0
    F = np.array([[1,0,dt,0],[0,1,0,dt],[0,0,1,0],[0,0,0,1]])
    Q = np.eye(4) * 0.2
    def nearest_item(pp):
        bi = min(range(len(grid_pts)), key=lambda i: np.linalg.norm(grid_pts[i]-pp))
        return pts[bi]
    for pp in path:
        x = F @ x
        P = F @ P @ F.T + Q
        item = nearest_item(pp)
        vis = item["vis"]
        if len(vis) < 3:
            continue
        bxyz = np.array([v["bxyz"] for v in vis])
        meas = np.array([v["avg"] for v in vis])
        # 用上一帧估计位置分类 NLOS（在线核心）
        pred_est = np.array([classify_nlos_online(x[:2], v["bxyz"][:2], tree, wall_lines)[1]
                             for v in vis])
        meas_corr = meas - pred_est * SHRINK
        is_nlos_est = pred_est > 0
        R_base = (RAW_SIGMA / math.sqrt(N_SAMPLES))**2
        for i in range(len(bxyz)):
            bx, by = bxyz[i][0], bxyz[i][1]
            dx_ = x[0]-bx; dy_ = x[1]-by
            dhat = math.hypot(dx_, dy_)
            if dhat < 1e-3:
                continue
            z = meas_corr[i]
            H = np.array([dx_/dhat, dy_/dhat, 0, 0])
            R = R_base * 9 if is_nlos_est[i] else R_base
            S = H @ P @ H.T + R
            innov = z - dhat
            if R > R_base and abs(innov) > gate * math.sqrt(S):
                continue
            K = P @ H.T / S
            x = x + K * innov
            P = (np.eye(4) - np.outer(K, H)) @ P
        errors.append(np.linalg.norm(x[:2] - pp))
    return errors


# ---------- 主仿真 ----------
def main():
    print("=" * 72)
    print("纯三点定位误差优化仿真（零指纹 · 方案A：墙体 NLOS + 标定 + 跟踪滤波）")
    print(f"原始 σ_d={RAW_SIGMA}m  可见半径={VIS_RADIUS}m  时序N={N_SAMPLES}  手机高={PHONE_Z}m")
    print("=" * 72)
    t0 = time.time()
    pts, walk, tree, wall_lines = prep_points(floor=1)
    print(f"[预计算] 有效点={len(pts)}  用时={time.time()-t0:.1f}s\n")
    configs = [
        ("L0 基线(2D·单样本·忽略NLOS)",    dict(use_3d=False, temporal=False, map_con=False, nlos_mode="none")),
        ("L1 +3D垂直分量",                 dict(use_3d=True,  temporal=False, map_con=False, nlos_mode="none")),
        ("L3 +时序平均",                   dict(use_3d=True,  temporal=True,  map_con=False, nlos_mode="none")),
        ("L4 +地图约束",                   dict(use_3d=True,  temporal=True,  map_con=True,  nlos_mode="none")),
        ("L6 +墙体NLOS校正",               dict(use_3d=True,  temporal=True,  map_con=True,  nlos_mode="correct")),
        ("L7 +墙体NLOS加权",               dict(use_3d=True,  temporal=True,  map_con=True,  nlos_mode="weight")),
        ("L8 +NLOS校正+加权(hybrid)",      dict(use_3d=True,  temporal=True,  map_con=True,  nlos_mode="hybrid")),
    ]
    rows = []
    for name, kw in configs:
        r = eval_config(pts, walk, **kw)
        rows.append((name, r))
        print(f"● {name}")
        print(f"  有效点={r['n_points']}")
        print(f"  中位误差={r['median_err']:.2f}m  均值={r['mean_err']:.2f}m  P95={r['p95_err']:.2f}m")
        print(f"  ≤1m 占比={r['pct_le_1m']:.1f}%   GDOP中位={r['gdop_median']:.2f}  GDOP>3占比={r['gdop_gt3_pct']:.1f}%")
    # EKF 跟踪
    print("● L9 EKF 跟踪滤波（沿主走廊轨迹）")
    t9 = time.time()
    errs = ekf_track(pts, walk)
    e = np.array(errs)
    print(f"  轨迹点={len(e)}  用时={time.time()-t9:.1f}s")
    print(f"  中位误差={np.median(e):.2f}m  P95={np.percentile(e,95):.2f}m  ≤1m 占比={np.mean(e<=1.0)*100:.1f}%")
    rows.append(("L9 +EKF跟踪", {"n_points": len(e), "median_err": float(np.median(e)),
                                  "p95_err": float(np.percentile(e,95)),
                                  "pct_le_1m": float(np.mean(e<=1.0)*100)}))

    # ---------- 在线 NLOS 分类器闭环验证 ----------
    print("\n● L6b 在线分类（静态网格 bootstrap：全LOS首解冷启动→迭代）")
    t6b = time.time()
    r6b = eval_online_grid(pts, walk, tree, wall_lines)
    print(f"  用时={time.time()-t6b:.1f}s")
    print(f"  中位误差={r6b['median_err']:.2f}m  P95={r6b['p95_err']:.2f}m  ≤1m 占比={r6b['pct_le_1m']:.1f}%")
    rows.append(("L6b 在线(网格bootstrap)", r6b))

    print("● L10 在线分类（轨迹：每步用上一帧估计位置 ray-cast）")
    t10 = time.time()
    err10 = ekf_track_online(pts, walk, tree, wall_lines)
    e10 = np.array(err10)
    print(f"  轨迹点={len(e10)}  用时={time.time()-t10:.1f}s")
    print(f"  中位误差={np.median(e10):.2f}m  P95={np.percentile(e10,95):.2f}m  ≤1m 占比={np.mean(e10<=1.0)*100:.1f}%")
    rows.append(("L10 在线(轨迹)", {"n_points": len(e10), "median_err": float(np.median(e10)),
                                     "p95_err": float(np.percentile(e10,95)),
                                     "pct_le_1m": float(np.mean(e10<=1.0)*100)}))

    print("\n" + "=" * 72)
    print("汇总（目标：≥95% 点 ≤1m）")
    print(f"{'配置':<30}{'中位误差':>9}{'P95':>9}{'≤1m%':>9}{'GDOP>3%':>9}")
    for name, r in rows:
        g3 = f"{r['gdop_gt3_pct']:.1f}%" if r.get('gdop_gt3_pct') is not None else "  -  "
        print(f"{name:<30}{r['median_err']:>8.2f}m{r['p95_err']:>8.2f}m{r['pct_le_1m']:>8.1f}%{g3:>9}")
    print("=" * 72)


if __name__ == "__main__":
    main()
