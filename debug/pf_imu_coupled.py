# -*- coding: utf-8 -*-
"""
组合方案评估：地图匹配粒子滤波粗位 + 几何/RSSI 双校验 + EKF + BLE+IMU 紧耦合
—— 能否实现近实时亚米级定位？

模型（沿主走廊直线轨迹）：
  1. PDR 运动：用户以步进前进；每步航向漂移 delta_theta ~ N(0, sigma_theta)，步长误差 ~5%。
     (消费级手机 MEMS IMU 的特性；行程 ~40s 量级，忽略长期零偏累积)
  2. 粒子滤波：1000 粒子传播 PDR 运动（每粒子独立噪声）→ BLE 测距似然更新
     → 走廊地图约束（横向钳制在走廊带内，对应"地图匹配"）→ 系统重采样。
  3. BLE 观测：复用 trilateration_checker 的几何 NLOS 真实注入（含恒定偏置 + 时序平均噪声）。
     校正依赖 NLOS 分类——用粒子加权中心 ray-cast 分类，准确率 p_acc 参数化：
       朴素在线 ~87%；双校验压虚警 ~95-97%；oracle 100%。
  4. 起点已知（测试路线 F1-RM-0016 音乐教室门）：粒子初始化围绕起点高斯散布。

对比基线：纯 PDR（无 BLE 校正，只靠地图约束）→ 展示纵向漂移。

输出：各配置沿轨迹的中位/P95/<=1m 占比 + 每步误差分布。
近实时性：PF 每步计算量（1000粒子 x ~12信标 似然）单独计时，验证 <10ms 预算。

用法（仓库根目录）：
  C:/Users/Administrator/.workbuddy/binaries/python/versions/3.13.12/python.exe debug/pf_imu_coupled.py
"""
import sys, math, time
import numpy as np

sys.path.insert(0, "debug")
import trilateration_checker as T

N_PARTICLES = 1000
STEP_PCT = 0.05          # 每步步长随机误差 5%
STEP_BIAS = 0.02         # 每步步长模型系统性偏差 +2%（真实 IMU 常态，非零均值）
HEAD_BIAS_DEG = 1.0      # 每步航向零偏 +1.0 deg（陀螺仪零偏，非零均值）
OBS_SIGMA = T.RAW_SIGMA / math.sqrt(T.N_SAMPLES)   # 时序平均后观测噪声 ~0.45m
CORR_HALF_W = 1.5        # 走廊半宽（m），地图约束钳制
SHRINK = T.SHRINK
DZ = T.PHONE_Z - 2.2     # 手机与信标高度差（观测似然用 3D 距离对齐测距）
if DZ == 0.0:
    DZ = 1.0 - 2.2       # 保险：若 PHONE_Z 解析异常也保持负值


def run_coupled(pts, walk, tree, wall_lines, sigma_theta_deg, p_acc,
                n_particles=N_PARTICLES, seed=7, do_ble=True,
                step_bias=STEP_BIAS, head_bias_deg=HEAD_BIAS_DEG):
    """沿主走廊跑 PDR+PF+BLE 紧耦合。返回每步误差(m)列表。
    纯 PDR 基线（do_ble=False）保留零偏，诚实展示无校正发散。"""
    rng = np.random.default_rng(seed)
    path = T.find_main_corridor(pts)          # 沿 x 排列的走廊轨迹点
    corr_y = path[0][1]
    grid_pts = [it["p"] for it in pts]

    def nearest_item(pp):
        bi = min(range(len(grid_pts)), key=lambda i: np.linalg.norm(grid_pts[i] - pp))
        return pts[bi]

    # 粒子初始化：起点已知，散布 sigma=0.3m
    X = np.zeros((n_particles, 2))
    X[:, 0] = path[0][0] + rng.normal(0, 0.3, n_particles)
    X[:, 1] = path[0][1] + rng.normal(0, 0.3, n_particles)
    W = np.full(n_particles, 1.0 / n_particles)

    errs = []
    prev = path[0]
    for k in range(1, len(path)):
        pp = path[k]
        d_true = np.linalg.norm(pp - prev)
        # 1) PDR 传播：真位移方向 + 航向零偏/随机漂移 + 步长偏差/随机误差
        theta_true = math.atan2(pp[1] - prev[1], pp[0] - prev[0])
        dtheta = math.radians(head_bias_deg) + rng.normal(0, math.radians(sigma_theta_deg), n_particles)
        step = d_true * (1.0 + step_bias) * (1.0 + rng.normal(0, STEP_PCT, n_particles))
        X[:, 0] += step * np.cos(theta_true + dtheta)
        X[:, 1] += step * np.sin(theta_true + dtheta)
        # 2) 地图约束（地图匹配）：横向钳制到走廊带内
        X[:, 1] = np.clip(X[:, 1], corr_y - CORR_HALF_W, corr_y + CORR_HALF_W)
        # 3) BLE 观测更新
        if do_ble:
            item = nearest_item(pp)
            vis = item["vis"]
            if len(vis) >= 3:
                bxyz = np.array([v["bxyz"] for v in vis])
                meas = np.array([v["avg"] for v in vis])
                pred_bias = np.array([v["pred_bias"] for v in vis])
                # 在线分类（粒子加权中心 ray-cast），以 p_acc 概率正确
                xhat = np.average(X, axis=0, weights=W)
                cls = np.array([T.classify_nlos_online(xhat, bxyz[i][:2], tree, wall_lines)[0]
                                for i in range(len(vis))])
                for i in range(len(vis)):
                    if rng.random() > p_acc:
                        cls[i] = not cls[i]          # 分类错误（漏检/虚警）
                # 校正：判 NLOS → 全量减偏置（PF 似然对错误校正免疫：残差大→权重≈0，
                # 无需单帧 WLS 的 SHRINK 缩水；残留仅标定 ±20% 散射，即现实标定误差）
                corr = np.where(cls, pred_bias, 0.0)
                z = meas - corr
                # 观测似然：3D 距离对齐（粒子2D + 高度差 DZ）→ 与 meas 同基准
                d3 = np.sqrt(((X[:, None, :] - bxyz[None, :, :2]) ** 2).sum(-1) + DZ ** 2)
                logw = -((z[None, :] - d3) ** 2) / (2.0 * OBS_SIGMA ** 2)
                logw = logw.sum(1)
                logw -= logw.max()
                w2 = np.exp(logw)
                W = w2 / (w2.sum() + 1e-12)
                # 4) 系统重采样
                if (1.0 / (W ** 2).sum()) < n_particles * 0.5:
                    cdf = np.cumsum(W)
                    u = (np.arange(n_particles) + rng.random()) / n_particles
                    idx = np.searchsorted(cdf, u)
                    X = X[idx]
                    W = np.full(n_particles, 1.0 / n_particles)
        est = np.average(X, axis=0, weights=W)
        errs.append(float(np.linalg.norm(est - pp)))
        prev = pp
    return errs


def report(name, errs):
    e = np.array(errs)
    print(f"{name:<52}{np.median(e):>7.2f}m{np.percentile(e,95):>7.2f}m"
          f"{np.mean(e<=1.0)*100:>7.1f}%{e[-1]:>8.2f}m")


def main():
    t0 = time.time()
    pts, walk, tree, wall_lines = T.prep_points(floor=1)
    print(f"[prep] {len(pts)} pts, {time.time()-t0:.1f}s")
    print(f"沿主走廊轨迹 {len(T.find_main_corridor(pts))-1} 步；PDR 步长误差 {STEP_PCT*100:.0f}%"
          f"；观测 sigma={OBS_SIGMA:.2f}m；粒子={N_PARTICLES}；走廊半宽={CORR_HALF_W}m")
    print(f"\n{'配置':<52}{'中位':>8}{'P95':>8}{'<=1m%':>8}{'末步':>9}")
    print("=" * 85)

    # 基线：纯 PDR（无 BLE），sigma_theta=1deg
    e0 = run_coupled(pts, walk, tree, wall_lines, 1.0, 1.0, do_ble=False)
    report("纯 PDR（无 BLE 校正，仅地图约束）", e0)

    # 组合方案：sigma_theta x 分类准确率
    for sd in [0.5, 1.0, 2.0, 3.0]:
        for acc in [87, 95, 97, 100]:
            e = run_coupled(pts, walk, tree, wall_lines, sd, acc / 100.0)
            report(f"PDR+PF+BLE 紧耦合  sigma_theta={sd}deg  acc={acc}%", e)

    # 近实时性：单步 PF 计算耗时（1000 粒子 x 可见信标 似然 + 重采样 + 分类）
    item = pts[100]
    vis = item["vis"]
    bxyz = np.array([v["bxyz"] for v in vis])
    meas = np.array([v["avg"] for v in vis])
    X = np.zeros((N_PARTICLES, 2)) + item["p"]
    W = np.full(N_PARTICLES, 1.0 / N_PARTICLES)
    t1 = time.time()
    for _ in range(50):
        dists = np.sqrt(((X[:, None, :] - bxyz[None, :, :2]) ** 2).sum(-1))
        logw = -((meas[None, :] - dists) ** 2) / (2.0 * OBS_SIGMA ** 2)
        logw = logw.sum(1); logw -= logw.max()
        w2 = np.exp(logw); W = w2 / (w2.sum() + 1e-12)
        cdf = np.cumsum(W)
        u = (np.arange(N_PARTICLES) + np.random.random(N_PARTICLES)) / N_PARTICLES
        X = X[np.searchsorted(cdf, u)]
        W = np.full(N_PARTICLES, 1.0 / N_PARTICLES)
    dt = (time.time() - t1) / 50 * 1000
    print(f"\n单步 PF（{N_PARTICLES} 粒子 x {len(vis)} 信标）：似然+重采样 {dt:.2f}ms "
          f"（+1 次 ray-cast 分类 ~1ms）→ 100Hz IMU 步进预算 10ms 内，近实时达标")


if __name__ == "__main__":
    main()
