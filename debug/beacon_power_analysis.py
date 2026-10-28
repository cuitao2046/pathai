# -*- coding: utf-8 -*-
"""一次性分析：不同广播功率档位（覆盖半径）下，路线指纹点的 >=3 信标可见率。

只按同层欧氏距离估算（不考虑墙体衰减），用于选档参考，不作为验收结论。
"""
from __future__ import annotations

import json
import math
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

deploy = json.loads((ROOT / "result/ble_deployment.json").read_text(encoding="utf-8"))
beacons = deploy["beacons"] if isinstance(deploy, dict) else deploy
grid = json.loads((ROOT / "result/fingerprint_grid_routes.json").read_text(encoding="utf-8"))

by_floor: dict[int, list[tuple[float, float]]] = {}
for beacon in beacons:
    by_floor.setdefault(int(beacon["floor"]), []).append(tuple(beacon["coordinates"]))

RADII = {"2档 -16dBm/10m": 10.0, "3档 -12dBm/15m": 15.0, "4档 -8dBm/22m": 22.0}

for name, radius in RADII.items():
    total = ge3 = ge4 = ge5 = 0
    nearest_max = 0.0
    per_floor: dict[int, list[int]] = {}
    for floor_id, floor in grid["floors"].items():
        pts = floor["points"]
        anchors = by_floor.get(int(floor_id), [])
        counts: list[int] = []
        for pt in pts:
            px, py = pt["coordinates"]
            dists = [math.hypot(px - ax, py - ay) for ax, ay in anchors]
            near = min(dists) if dists else float("inf")
            nearest_max = max(nearest_max, near)
            c = sum(1 for d in dists if d <= radius)
            counts.append(c)
            total += 1
            ge3 += c >= 3
            ge4 += c >= 4
            ge5 += c >= 5
        per_floor[int(floor_id)] = [
            sum(1 for c in counts if c >= 3),
            len(counts),
        ]
    print(
        f"{name}: 点 {total}, ≥3 {ge3}({ge3 / total:.1%}), "
        f"≥4 {ge4}({ge4 / total:.1%}), ≥5 {ge5}({ge5 / total:.1%}); "
        f"各层≥3 {per_floor}; 最近信标最大距离 {nearest_max:.2f}m"
    )

# 第三近信标距离分布：衡量「掉一个信标就只剩 2 个」的风险
third: list[float] = []
for floor_id, floor in grid["floors"].items():
    anchors = by_floor.get(int(floor_id), [])
    for pt in floor["points"]:
        px, py = pt["coordinates"]
        ds = sorted(math.hypot(px - ax, py - ay) for ax, ay in anchors)
        third.append(ds[2] if len(ds) >= 3 else 999.0)
third.sort()
count = len(third)


def pct(p: float) -> float:
    return third[min(count - 1, int(p * count))]


print(
    "第三近信标距离：median {:.2f}m / p90 {:.2f}m / p99 {:.2f}m / max {:.2f}m".format(
        third[count // 2], pct(0.90), pct(0.99), third[-1]
    )
)
for threshold in (6, 7, 8, 9, 10):
    bad = sum(1 for d in third if d > threshold)
    print(f"  第三近 > {threshold}m: {bad} 点 ({bad / count:.1%})")

