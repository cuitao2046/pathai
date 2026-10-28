# -*- coding: utf-8 -*-
"""第一阶段 BLE 三点定位解算（REQ-LOCATION）。

- 只使用地图包信标目录内、与目标楼层一致的信标；未知/跨层信标进 ignoredBeaconIds；
- RSSI→距离：对数路径损耗模型 d = 10^((RSSI@1m - rssi) / (10·n))，n=2 为工程初始值；
  RSSI@1m 取部署档位推算的占位值（-71 dBm），**不使用**信标 txPower（docs/28 §1）；
- 三边最小二乘（线性化正规方程，2×2 手工求解，不引入 numpy）；
- 残差→quality/confidence 映射与阈值均为工程初始值，须经现场标定后冻结（docs/24 §12）。
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from pathai_backend.map_package.schema import BeaconEntry, MapPackage
from pathai_backend.schemas.common import LocationQuality

#: 工程初始值（现场标定后冻结）。
PATH_LOSS_EXPONENT = 2.0
#: 1m 参考 RSSI（iBeacon measured power）。**不是**发射功率：
#: 部署档位为 E5 3 档 -12 dBm（厂商标称覆盖半径约 15 m），按 0 dBm 档典型
#: measured power -59 dBm 随功率下降 12 dB 推算得 -71 dBm；15 m 处约 -94.5 dBm，
#: 与 nRF52 灵敏度（约 -96 dBm）自洽。仍属估算占位，须按 docs/28 现场标定后冻结。
RSSI_AT_1M_DBM = -71.0
RESIDUAL_RELIABLE_M = 1.5
RESIDUAL_DEGRADED_M = 4.0
#: 正规方程行列式下限：信标近似共线时几何退化，判为 lost。
_MIN_DETERMINANT = 1e-6
#: 最少同层可用信标数（三点定位）。
MIN_BEACONS_FOR_TRILATERATION = 3


@dataclass(frozen=True)
class LocationParams:
    """解算参数（docs/28 §5.4）：由服务端配置注入，随 paramsVersion 追溯。"""

    rssi_at_1m: float = RSSI_AT_1M_DBM
    path_loss_n: float = PATH_LOSS_EXPONENT
    residual_reliable_m: float = RESIDUAL_RELIABLE_M
    residual_degraded_m: float = RESIDUAL_DEGRADED_M
    params_version: str = "unset"


DEFAULT_LOCATION_PARAMS = LocationParams()


@dataclass(frozen=True)
class BeaconInput:
    beacon_id: str
    rssi: int


@dataclass(frozen=True)
class LocationEstimate:
    x: float | None
    y: float | None
    floor_id: str | None
    quality: LocationQuality
    confidence: float
    ignored_beacon_ids: tuple[str, ...]


def rssi_to_distance(rssi: int, params: LocationParams = DEFAULT_LOCATION_PARAMS) -> float:
    """对数路径损耗模型：d = 10^((RSSI@1m − rssi) / (10·n))。

    RSSI@1m 取自配置（measured power），**不得**使用设备发射功率 txPower
    （txPower 仅描述广播功率，与距离不是同一量纲；docs/28 §1）。
    """
    return 10.0 ** ((params.rssi_at_1m - rssi) / (10.0 * params.path_loss_n))


def _dedupe_strongest(observations: list[BeaconInput]) -> list[BeaconInput]:
    """同一信标多条观测时保留 RSSI 最强（数值最大）的一条。"""
    best: dict[str, BeaconInput] = {}
    for obs in observations:
        current = best.get(obs.beacon_id)
        if current is None or obs.rssi > current.rssi:
            best[obs.beacon_id] = obs
    return list(best.values())


def _select_floor(
    pkg: MapPackage,
    floor_hint: str | None,
    known: list[tuple[BeaconInput, BeaconEntry]],
) -> str | None:
    if floor_hint is not None:
        return floor_hint if floor_hint in pkg.floors else None
    stats: dict[str, tuple[int, float]] = {}  # floorId → (观测数, RSSI 累计)
    for obs, beacon in known:
        count, total = stats.get(beacon.floorId, (0, 0.0))
        stats[beacon.floorId] = (count + 1, total + obs.rssi)
    if not stats:
        return None
    # 多数信标所在楼层；并列取平均 RSSI 更强者（RSSI 越大越近）
    return max(stats.items(), key=lambda kv: (kv[1][0], kv[1][1] / kv[1][0]))[0]


def _solve_least_squares(
    anchors: list[tuple[float, float]], ranges: list[float]
) -> tuple[float, float] | None:
    """线性化三边交汇最小二乘：以首信标为参考构造 A·x=b，2×2 正规方程求解。"""
    x0, y0 = anchors[0]
    d0 = ranges[0]
    s11 = s12 = s22 = t1 = t2 = 0.0
    for (x, y), d in zip(anchors[1:], ranges[1:]):
        dx = x - x0
        dy = y - y0
        # 线性化：2·dx·x + 2·dy·y = d0² - d² + x² - x0² + y² - y0²；
        # 系数矩阵行取 (dx, dy)，故右端除以 2。
        b = 0.5 * (d0 * d0 - d * d + x * x - x0 * x0 + y * y - y0 * y0)
        s11 += dx * dx
        s12 += dx * dy
        s22 += dy * dy
        t1 += dx * b
        t2 += dy * b
    det = s11 * s22 - s12 * s12
    if abs(det) < _MIN_DETERMINANT:
        return None
    return ((s22 * t1 - s12 * t2) / det, (s11 * t2 - s12 * t1) / det)


def _residual_rms(
    point: tuple[float, float], anchors: list[tuple[float, float]], ranges: list[float]
) -> float:
    px, py = point
    residuals = [math.hypot(px - ax, py - ay) - d for (ax, ay), d in zip(anchors, ranges)]
    return math.sqrt(sum(r * r for r in residuals) / len(residuals))


def estimate_location(
    pkg: MapPackage,
    observations: list[BeaconInput],
    floor_hint: str | None,
    params: LocationParams = DEFAULT_LOCATION_PARAMS,
) -> LocationEstimate:
    catalog = {b.beaconId: b for b in pkg.beacons}
    deduped = _dedupe_strongest(observations)
    known = [(obs, catalog[obs.beacon_id]) for obs in deduped if obs.beacon_id in catalog]
    target_floor = _select_floor(pkg, floor_hint, known)

    ignored: list[str] = sorted(
        obs.beacon_id for obs in deduped if obs.beacon_id not in catalog
    )
    anchors: list[tuple[float, float]] = []
    ranges: list[float] = []
    for obs, beacon in known:
        if beacon.floorId != target_floor:
            ignored.append(obs.beacon_id)
            continue
        anchors.append((beacon.x, beacon.y))
        # txPower 是设备广播档位，不参与测距；参考值统一取配置的 RSSI@1m（docs/28 §1）
        ranges.append(rssi_to_distance(obs.rssi, params))
    ignored.sort()

    if (
        target_floor is None
        or len(anchors) < MIN_BEACONS_FOR_TRILATERATION
    ):
        return LocationEstimate(
            x=None,
            y=None,
            floor_id=target_floor,
            quality=LocationQuality.LOST,
            confidence=0.0,
            ignored_beacon_ids=tuple(ignored),
        )

    point = _solve_least_squares(anchors, ranges)
    if point is None:
        return LocationEstimate(
            x=None,
            y=None,
            floor_id=target_floor,
            quality=LocationQuality.LOST,
            confidence=0.0,
            ignored_beacon_ids=tuple(ignored),
        )

    rms = _residual_rms(point, anchors, ranges)
    confidence = round(max(0.0, min(1.0, 1.0 - rms / 10.0)), 3)
    if rms <= params.residual_reliable_m:
        quality = LocationQuality.RELIABLE
    elif rms <= params.residual_degraded_m:
        quality = LocationQuality.DEGRADED
    else:
        return LocationEstimate(
            x=None,  # unreliable 不给坐标：不得用于导航判断
            y=None,
            floor_id=target_floor,
            quality=LocationQuality.UNRELIABLE,
            confidence=confidence,
            ignored_beacon_ids=tuple(ignored),
        )
    return LocationEstimate(
        x=round(point[0], 3),
        y=round(point[1], 3),
        floor_id=target_floor,
        quality=quality,
        confidence=confidence,
        ignored_beacon_ids=tuple(ignored),
    )
