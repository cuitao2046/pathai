# -*- coding: utf-8 -*-
"""第一阶段 BLE 三点定位解算（REQ-LOCATION，spec 0.3.3 三维联合解算）。

- 只使用地图包信标目录内、与目标楼层一致的信标；未知/跨层信标进 ignoredBeaconIds；
  缺少 installHeightM 的信标视为部署数据错误，同样不参与解算（fail-safe，spec 0.3.1）；
- RSSI→距离：对数路径损耗模型 d = 10^((RSSI@1m - rssi) / (10·n))，n=2 为工程初始值；
  RSSI@1m 取部署档位推算的占位值（-71 dBm），**不使用**信标 txPower（docs/28 §1）；
- 距离解释为信标与手机之间的**空间斜距**：预测距离
  sqrt((x-x_i)^2 + (y-y_i)^2 + (z-z_i)^2)。信标 z_i 取自地图包 installHeightM
  （现行统一 2.5 m），手机位置 (x,y,z) **三个分量同时作为未知量**参与高斯-牛顿解算；
- 现场 61 枚信标共面（同一安装高度），仅凭斜距无法唯一确定 z（设计矩阵近秩亏），
  故对 z 施加手持高度先验（PHONE_HEIGHT_M ± Z_PRIOR_SIGMA_M，MAP 形式的软约束）：
  z 是待估未知量而非固定常数，由斜距数据在物理先验附近自行确定；
- 正规方程 3×3 手工高斯消元求解，不引入 numpy；
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
#: 手机参考高度（米，所在楼层地面以上）：成人手持手机的工程参考（spec 0.3.1 决策 13，
#: 2026-11-01 用户确认）。三维解算中作为 z 的先验中心，而非固定值。
PHONE_HEIGHT_M = 1.2
#: z 先验标准差（米）：信标同层共面时斜距无法唯一确定 z，须以手持高度软约束
#: （MAP/高斯-牛顿正规方程追加一行 (0,0,1) 观测，spec 0.3.3 决策 15）。
#: 0.5 m 由蒙特卡洛探针确定（σ_prior 0.05–1.0 m 区间内误差平台，取中段）。
Z_PRIOR_SIGMA_M = 0.5
#: 单次测距标准差（米）：仅用于确定 z 先验与测距观测的相对权重，
#: 不参与 quality 判定（quality 仍按解算后残差 RMS 映射）。
RANGE_SIGMA_M = 2.0
#: 高斯-牛顿迭代上限与收敛判据（步长，米）。
_GN_MAX_ITERATIONS = 20
_GN_STEP_TOLERANCE_M = 1e-4
#: 单次迭代步长上限（米）：防止病态几何下解跳飞。
_GN_MAX_STEP_M = 20.0
#: 正规方程列主元相对下限：信标近似共线时几何退化，判为 lost。
_MIN_DETERMINANT = 1e-6
#: 最少同层可用信标数（三点定位）；不足时不回退 2D，直接判 lost（spec 0.3.1）。
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


def _solve3(matrix: list[list[float]], rhs: list[float]) -> list[float] | None:
    """3×3 线性方程组：列主元高斯消元（纯 Python，不引入 numpy）。

    主元绝对值低于 `_MIN_DETERMINANT` 视为几何退化，返回 None。
    """
    m = [row[:] + [value] for row, value in zip(matrix, rhs)]
    for col in range(3):
        pivot = max(range(col, 3), key=lambda r: abs(m[r][col]))
        if abs(m[pivot][col]) < _MIN_DETERMINANT:
            return None
        if pivot != col:
            m[col], m[pivot] = m[pivot], m[col]
        denom = m[col][col]
        for row in range(col + 1, 3):
            factor = m[row][col] / denom
            if factor == 0.0:
                continue
            for c in range(col, 4):
                m[row][c] -= factor * m[col][c]
    solution = [0.0, 0.0, 0.0]
    for i in (2, 1, 0):
        acc = m[i][3] - sum(m[i][j] * solution[j] for j in range(i + 1, 3))
        solution[i] = acc / m[i][i]
    return solution


def _solve_least_squares(
    anchors: list[tuple[float, float, float]], ranges: list[float]
) -> tuple[float, float, float] | None:
    """三维联合高斯-牛顿解算 (x, y, z)（spec 0.3.3）。

    anchors 元素为信标真实坐标 (x_i, y_i, z_i)，z_i = installHeightM；
    ranges 为 RSSI 反演的空间斜距。未知量为手机位置 (x, y, z)，观测量为
    ‖p − b_i‖ = d_i，另加一行 z 先验观测 z = PHONE_HEIGHT_M（权重 1/Z_PRIOR_SIGMA_M²）
    以消除同层共面信标带来的 z 不可辨识性——z 仍由数据参与估计，只是被软约束在
    手持高度附近，而非固定常数。

    正规方程 (JᵀWJ)·Δ = −JᵀW·r 为 3×3，手工消元求解。返回 (x, y, z) 米。
    """
    n = len(anchors)
    if n < MIN_BEACONS_FOR_TRILATERATION:
        return None

    # 初值：平面取信标质心，高度取手持先验（避免从远端起跳）
    px = sum(a[0] for a in anchors) / n
    py = sum(a[1] for a in anchors) / n
    pz = PHONE_HEIGHT_M

    w_range = 1.0 / (RANGE_SIGMA_M * RANGE_SIGMA_M)
    w_prior = 1.0 / (Z_PRIOR_SIGMA_M * Z_PRIOR_SIGMA_M)

    for _ in range(_GN_MAX_ITERATIONS):
        normal = [[0.0, 0.0, 0.0] for _ in range(3)]
        grad = [0.0, 0.0, 0.0]
        for (bx, by, bz), d in zip(anchors, ranges):
            vx, vy, vz = px - bx, py - by, pz - bz
            dist = math.sqrt(vx * vx + vy * vy + vz * vz)
            if dist < 1e-6:
                continue
            unit = (vx / dist, vy / dist, vz / dist)
            residual = dist - d
            for i in range(3):
                grad[i] += w_range * unit[i] * residual
                for j in range(3):
                    normal[i][j] += w_range * unit[i] * unit[j]

        # z 软先验：追加一行 (0, 0, 1)，残差 z − PHONE_HEIGHT_M
        normal[2][2] += w_prior
        grad[2] += w_prior * (pz - PHONE_HEIGHT_M)

        # 平面几何退化（信标近似共线）：z 先验会让 3×3 可逆，须单独判 x/y 块
        if (
            normal[0][0] * normal[1][1] - normal[0][1] * normal[1][0]
            < _MIN_DETERMINANT
        ):
            return None

        step = _solve3(normal, [-g for g in grad])
        if step is None:
            return None

        step_norm = math.sqrt(sum(s * s for s in step))
        if step_norm > _GN_MAX_STEP_M:
            scale = _GN_MAX_STEP_M / step_norm
            step = [s * scale for s in step]
        px += step[0]
        py += step[1]
        pz += step[2]
        if step_norm < _GN_STEP_TOLERANCE_M:
            break

    return (px, py, pz)


def _residual_rms(
    point: tuple[float, float, float],
    anchors: list[tuple[float, float, float]],
    ranges: list[float],
) -> float:
    """斜距残差 RMS：预测空间斜距与 RSSI 反演斜距之差。"""
    px, py, pz = point
    residuals = [
        math.sqrt((px - ax) ** 2 + (py - ay) ** 2 + (pz - az) ** 2) - d
        for (ax, ay, az), d in zip(anchors, ranges)
    ]
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
    anchors: list[tuple[float, float, float]] = []
    ranges: list[float] = []
    for obs, beacon in known:
        if beacon.floorId != target_floor:
            ignored.append(obs.beacon_id)
            continue
        if beacon.installHeightM is None:
            # 缺安装高度属部署数据错误：不参与解算，也不得按无高度模型静默替代
            # （spec 0.3.1 统一安装高度数据规则，fail-safe）。
            ignored.append(obs.beacon_id)
            continue
        anchors.append((beacon.x, beacon.y, beacon.installHeightM))
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
