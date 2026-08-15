#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""eval_ble_deployment.py — 评估人工部署信标方案(ble_deployment.json)在测试路线上的
三点定位质量, 与算法基线/优化方案对比。

复用 src/tools/refine_beacon_placement.py 的 FloorModel/build_model/review 口径
(同一无线模型: TxPower=-10, RSSI_ref@1m=-50, n=3.5, 穿墙衰减, 可见>-85dBm,
退化判据 GDOP>3.0 或 目标到3信标三角形最近边距离>1.0m)。

对比方案:
  A. result/ble_deployment.json                                (人工部署 66)
  B. result/beacon_deployment_plan_trilateration_routes.json   (算法基线 60)
  C. result/beacon_deployment_plan_trilateration_routes_refined_max20.json (优化补点)

输出: result/beacon_deployment_evaluation.json(完整) + 终端汇总
"""
from __future__ import annotations
import json, math, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.tools.refine_beacon_placement import (
    build_model, is_degenerate, gdop2d, tri_out_dist, GDOP_MAX, D_OUT_MAX,
)

ROOT = Path(__file__).resolve().parents[1]
GEO = ROOT / "result" / "school_building_01_map_v9.geojson"
TARGET = ROOT / "result" / "fingerprint_grid_routes.json"

PLANS = {
    "A_manual_ble_deploy": ROOT / "result" / "ble_deployment.json",
    "B_baseline_routes60": ROOT / "result" / "beacon_deployment_plan_trilateration_routes.json",
    "C_refined_max20": ROOT / "result" / "beacon_deployment_plan_trilateration_routes_refined_max20.json",
}


def analyze_plan(geo, target, plan_path):
    plan = json.load(open(plan_path, encoding="utf-8"))
    out = {"plan": plan_path.name, "floors": {}}
    for fl in ["1", "2"]:
        model = build_model(geo, plan, fl, target=target)
        rv = model.review()
        # GDOP / 外扩细分
        gdop_ct = dout_ct = both_ct = 0
        worst = []
        for (x, y) in model.pts:
            s = model.cache[(x, y)]
            if len(s) < 3:
                continue
            vis3 = sorted(s, key=lambda k: math.hypot(
                model.beacons[k][0] - x, model.beacons[k][1] - y))[:3]
            b = [model.beacons[i] for i in vis3]
            g = gdop2d((x, y), b[0], b[1], b[2])
            d = tri_out_dist((x, y), b[0], b[1], b[2])
            if g > GDOP_MAX and d > D_OUT_MAX:
                both_ct += 1
            elif g > GDOP_MAX:
                gdop_ct += 1
            elif d > D_OUT_MAX:
                dout_ct += 1
            if is_degenerate((x, y), b[0], b[1], b[2]):
                worst.append({"x": round(x, 2), "y": round(y, 2),
                              "gdop": round(g, 2), "outDist_m": round(d, 2)})
        worst.sort(key=lambda w: -max(w["gdop"] / GDOP_MAX, w["outDist_m"] / D_OUT_MAX))
        rv["gdop_only"] = gdop_ct
        rv["outdist_only"] = dout_ct
        rv["both"] = both_ct
        rv["worstTop"] = worst[:8]
        out["floors"][fl] = rv
    return out


def main():
    geo = json.load(open(GEO, encoding="utf-8"))
    target = json.load(open(TARGET, encoding="utf-8"))
    report = {"target": TARGET.name, "gdopMax": GDOP_MAX, "dOutMax": D_OUT_MAX,
              "plans": {}}
    for key, p in PLANS.items():
        if not p.exists():
            print(f"[skip] {p.name} 不存在")
            continue
        r = analyze_plan(geo, target, p)
        report["plans"][key] = r
        tot_beac = sum(f["beacons"] for f in r["floors"].values())
        tot_ge3 = sum(f["samples"] * f["pct_ge3"] / 100 for f in r["floors"].values())
        tot_samp = sum(f["samples"] for f in r["floors"].values())
        tot_deg = sum(f["tri_degenerate"] for f in r["floors"].values())
        tot_gdop = sum(f["gdop_only"] + f["both"] for f in r["floors"].values())
        tot_dout = sum(f["outdist_only"] + f["both"] for f in r["floors"].values())
        print(f"\n=== {key} ({p.name}) 信标 {tot_beac} ===")
        print(f"  覆盖: >=3可见 {tot_ge3:.0f}/{tot_samp} = {100*tot_ge3/tot_samp:.2f}%")
        print(f"  退化点: {tot_deg} (GDOP>3: {tot_gdop} | 外扩>1m: {tot_dout})")
        for fl, f in r["floors"].items():
            print(f"  F{fl}: 信标{f['beacons']} 覆盖{f['pct_ge3']}% 退化{f['tri_degenerate']}"
                  f"(GDOP {f['gdop_only']+f['both']}/外扩 {f['outdist_only']+f['both']}) "
                  f"墙角违{f['corner_violations']} 柱违{f['obstacle_violations']} "
                  f"NN-CV {f['nn_cv']} 走廊双侧 {f['corridors_both']}/{f['corridors_total']}")

    out_path = ROOT / "result" / "beacon_deployment_evaluation.json"
    json.dump(report, open(out_path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print(f"\n完整报告: {out_path}")


if __name__ == "__main__":
    main()
