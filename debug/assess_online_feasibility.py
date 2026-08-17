# -*- coding: utf-8 -*-
"""
客观评估：0 指纹下纯 BLE 三点定位能否亚米级？

背景：oracle（真值 ray-cast）走廊轨迹 0.29m/100%≤1m 可达；在线（估计位置 ray-cast）
只到 1.33m/35.9%。两者唯一差别是「ray-cast 用的位置 x̂ 是否准确」——这就是鸡生蛋耦合：
在线分类器需要粗定位 x̂，定位精度又依赖分类器是否排除了 NLOS 偏置。

本脚本回答三个决策问题：
  A. 外部粗定位源（地图匹配粒子滤波 / 航迹推算 / 入口校正）精度 e_boot 做到多好，
     在线闭环（分类→校正→WLS→投影）才能 ≤1m？——给出「粗位→最终精度」灵敏度曲线。
  B. 误分类方向分解：漏检（NLOS 判成 LOS，偏置残留）vs 虚警（LOS 判成 NLOS，过校正），
     谁在主导误差累积？这决定补救策略该往哪打。
  C. 测距质量 σ_d × 粗位 e_boot 交互：硬件/配置更好（σ_d 更小）能否补粗位误差的窟窿？

模型说明：
  - 粗定位源用各向同性高斯 e_boot（保守中位；真实走廊地图匹配误差沿走廊方向为主，
    对 NLOS 分类更友好，故本结果是偏悲观的估计）。
  - 每点独立估计（无轨迹连续性），反映最坏情况；SHRINK=0.7 稳健策略与闭环验证一致。
  - 采样：2172 网格点按步长 2 抽样 → ~1095 点/配置，保证曲线平滑且运行可控。

用法（仓库根目录）：
  C:/Users/Administrator/.workbuddy/binaries/python/versions/3.13.12/python.exe debug/assess_online_feasibility.py
"""
import sys, math, time
import numpy as np

sys.path.insert(0, "debug")
import trilateration_checker as T


def classify_accuracy(vis, xhat0, tree, wall_lines):
    """对照真值 NLOS 状态，统计在线分类的 准确率/漏检率/虚警率（逐信标）。"""
    n_tp = n_tn = n_fn = n_fp = 0
    for v in vis:
        bxy = v["bxyz"][:2]
        t_nl = v["is_nlos"]
        e_nl, _ = T.classify_nlos_online(xhat0, bxy, tree, wall_lines)
        if t_nl and e_nl:
            n_tp += 1
        elif t_nl and not e_nl:
            n_fn += 1
        elif (not t_nl) and e_nl:
            n_fp += 1
        else:
            n_tn += 1
    tot = n_tp + n_tn + n_fn + n_fp
    return (n_tp + n_tn) / tot, n_fn / tot, n_fp / tot


def eval_boot(pts, walk, tree, wall_lines, e_boot, shrink=T.SHRINK, downsample=2, seed=7):
    """用外部粗位源（精度 e_boot）→ 在线分类 → 校正 → WLS → 投影，逐点独立。"""
    rng = np.random.default_rng(seed)
    errs, acc, fnr, fpr = [], [], [], []
    for idx, item in enumerate(pts):
        if idx % downsample != 0:
            continue
        p, vis = item["p"], item["vis"]
        if len(vis) < 3:
            continue
        x0 = p + rng.normal(0.0, e_boot, 2)          # 外部粗定位源
        bxyz = np.array([v["bxyz"] for v in vis])
        meas = np.array([v["avg"] for v in vis])
        pred = np.array([T.classify_nlos_online(x0, v["bxyz"][:2], tree, wall_lines)[1]
                         for v in vis])
        meas_c = meas - pred * shrink                 # 稳健缩减校正
        is_est = pred > 0
        sigmas = np.array([4.0 if nl else T.RAW_SIGMA / math.sqrt(T.N_SAMPLES) for nl in is_est])
        sol = T.solve_gn(bxyz, meas_c, fix_z=T.PHONE_Z, init=x0.copy(), sigmas=sigmas)
        if sol is None:
            continue
        xy = T.project_to_walkable(sol[:2], walk)
        errs.append(float(np.linalg.norm(xy - p)))
        a, f, fp_ = classify_accuracy(vis, x0, tree, wall_lines)
        acc.append(a); fnr.append(f); fpr.append(fp_)
    errs = np.array(errs); acc = np.array(acc); fnr = np.array(fnr); fpr = np.array(fpr)
    return dict(
        n=len(errs),
        med=float(np.median(errs)), p95=float(np.percentile(errs, 95)),
        le1=float(np.mean(errs <= 1.0) * 100),
        acc=float(np.mean(acc) * 100), fnr=float(np.mean(fnr) * 100), fpr=float(np.mean(fpr) * 100),
    )


def main():
    t0 = time.time()
    pts, walk, tree, wall_lines = T.prep_points(floor=1)
    print(f"[prep sigma_d={T.RAW_SIGMA}] {len(pts)} pts, {time.time()-t0:.1f}s")

    print("\n=== A. 粗定位源精度 e_boot 敏感性 (sigma_d=%.1fm, SHRINK=%.1f) ===" % (T.RAW_SIGMA, T.SHRINK))
    print(f"{'e_boot(m)':>9}{'n':>6}{'中位(m)':>9}{'P95(m)':>9}{'<=1m%':>8}{'分类acc%':>9}{'漏检fn%':>8}{'虚警fp%':>8}")
    rows_a = []
    for e in [0.0, 0.5, 1.0, 1.5, 2.0, 3.0, 5.0]:
        r = eval_boot(pts, walk, tree, wall_lines, e_boot=e, seed=7)
        rows_a.append((e, r))
        print(f"{e:>9.1f}{r['n']:>6}{r['med']:>8.2f}{r['p95']:>8.2f}{r['le1']:>7.1f}{r['acc']:>8.1f}{r['fnr']:>7.1f}{r['fpr']:>7.1f}")

    print("\n=== B. 测距质量 sigma_d × 粗位 e_boot 交互（独立重prep） ===")
    print(f"{'sigma_d':>8}{'e_boot':>8}{'n':>6}{'中位(m)':>9}{'P95(m)':>9}{'<=1m%':>8}{'acc%':>7}")
    rows_b = []
    for sd in [1.0, 1.5, 2.0]:
        T.RAW_SIGMA = sd
        t1 = time.time()
        pts2, walk2, tree2, walls2 = T.prep_points(floor=1)
        print(f"[prep sigma_d={sd}] {len(pts2)} pts, {time.time()-t1:.1f}s")
        for e in [0.0, 1.0, 2.0]:
            r = eval_boot(pts2, walk2, tree2, walls2, e_boot=e, seed=7)
            rows_b.append((sd, e, r))
            print(f"{sd:>8.1f}{e:>8.1f}{r['n']:>6}{r['med']:>8.2f}{r['p95']:>8.2f}{r['le1']:>7.1f}{r['acc']:>6.1f}")

    # 汇总表便于文档引用
    print("\n=== 汇总 JSON ===")
    out = {"A": [(e, r) for e, r in rows_a], "B": [(s, e, r) for s, e, r in rows_b]}
    print(json.dumps(out, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    import json
    main()
