#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""定位参数拟合（docs/28 §5）：static_samples.json + 信标台账 → 参数版本文件 + 报告。

口径：
- 真值距离一律按台账坐标重算并校验（样本里的 truthDistanceM 只用于交叉核对）；
- 模型与生产同形：rssi = A − 10·n·log10(d)，OLS 反演 A/n；
- 按楼层分组仅在「每组 ≥50 对且 RMSE 相对全局下降 >30%」时启用，禁止逐信标分组；
- 静态点回放复用生产解算 `estimate_location`，给出 CEP50/75/90 与阈值建议；
- **只产出文件，不修改任何配置**（写入 `PATHAI_LOCATION_*` 由人工按冻结流程执行）。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
BACKEND_DIR = PROJECT_ROOT / "backend"
for _p in (str(BACKEND_DIR), str(PROJECT_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from pathai_backend.location_solver import (  # noqa: E402
    BeaconInput,
    LocationParams,
    estimate_location,
)
from pathai_backend.map_package.schema import MapPackage  # noqa: E402

DEFAULT_LEDGER = PROJECT_ROOT / "result" / "ble_deployment.json"
DEFAULT_SAMPLES = PROJECT_ROOT / "result" / "calibration" / "static_samples.json"
DEFAULT_MAPS_DIR = PROJECT_ROOT / "backend" / "maps_data"

RSSI_MIN_DBM = -100
RSSI_MAX_DBM = -20
#: 真值点与信标过近时 log10(d) 不稳定，判为录入错误（docs/28 采样距离覆盖 1–15 m）。
MIN_DISTANCE_M = 0.5
#: 样本内 truthDistanceM 与台账重算值的允许偏差（米）。
DISTANCE_TOLERANCE_M = 0.05
MIN_FIT_PAIRS = 3
DEFAULT_MIN_GROUP_PAIRS = 50
DEFAULT_GROUP_IMPROVEMENT = 0.30
DEFAULT_TARGET_CEP90_M = 3.0
DEFAULT_DEGRADED_TARGET_M = 6.0
#: 回放时把门限抬到极大值，保证几何有效即可拿到坐标（仅用于 CEP/RMS 统计）。
_UNBOUNDED = 1e6


class CalibrationError(ValueError):
    """样本/台账不自洽或无法拟合；CLI 以非零退出码拦截。"""


@dataclass(frozen=True)
class Pair:
    point_id: str
    session_index: int
    floor: int
    beacon_id: str
    distance_m: float
    rssi: int


@dataclass(frozen=True)
class FitResult:
    intercept: float
    path_loss_n: float
    r2: float
    rmse: float
    pairs: int


@dataclass
class ReplayResult:
    session_count: int = 0
    estimated: int = 0
    lost: int = 0
    errors: list[float] = field(default_factory=list)
    rms_values: list[float] = field(default_factory=list)
    quality_counts: dict[str, int] = field(default_factory=dict)


def load_json(path: Path) -> dict:
    try:
        with path.open(encoding="utf-8") as fp:
            return json.load(fp)
    except FileNotFoundError as exc:
        raise CalibrationError(f"文件不存在：{path}") from exc
    except json.JSONDecodeError as exc:
        raise CalibrationError(f"JSON 解析失败：{path}（{exc}）") from exc


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def beacon_catalog(ledger: dict) -> dict[str, dict]:
    catalog = {}
    for raw in ledger.get("beacons") or []:
        beacon_id = raw.get("beaconId")
        coords = raw.get("coordinates")
        if not beacon_id or not isinstance(coords, list) or len(coords) != 2:
            raise CalibrationError(f"台账信标缺字段：{beacon_id!r}")
        catalog[beacon_id] = {
            "floor": int(raw.get("floor")),
            "x": float(coords[0]),
            "y": float(coords[1]),
        }
    if not catalog:
        raise CalibrationError("信标台账为空")
    return catalog


def validate_samples(
    samples: dict,
    ledger: dict,
    distance_tolerance_m: float = DISTANCE_TOLERANCE_M,
) -> list[Pair]:
    """校验样本结构并按台账重算真值距离；任何不自洽都报错（不静默跳过）。"""
    if samples.get("schemaVersion") != "1.0":
        raise CalibrationError("static_samples.schemaVersion 必须是 1.0")
    if samples.get("type") != "positioning_calibration_static":
        raise CalibrationError("static_samples.type 必须是 positioning_calibration_static")
    points = samples.get("points")
    if not isinstance(points, list) or not points:
        raise CalibrationError("static_samples.points 为空")

    catalog = beacon_catalog(ledger)
    pairs: list[Pair] = []
    for point in points:
        point_id = point.get("pointId")
        truth = point.get("truth")
        floor = point.get("floor")
        if not point_id or not isinstance(truth, list) or len(truth) != 2:
            raise CalibrationError(f"标定点缺 pointId/truth：{point_id!r}")
        if not isinstance(floor, int):
            raise CalibrationError(f"{point_id} 的 floor 必须是整数")
        sessions = point.get("sessions")
        if not isinstance(sessions, list) or not sessions:
            raise CalibrationError(f"{point_id} 没有采样会话")
        for session in sessions:
            session_index = session.get("sessionIndex")
            for obs in session.get("observations") or []:
                beacon_id = obs.get("beaconId")
                if beacon_id not in catalog:
                    raise CalibrationError(f"{point_id} 引用了台账外信标：{beacon_id}")
                rssi = obs.get("rssiMedian")
                if not isinstance(rssi, int) or not RSSI_MIN_DBM <= rssi <= RSSI_MAX_DBM:
                    raise CalibrationError(
                        f"{point_id}/{beacon_id} 的 rssiMedian 越界：{rssi!r}"
                    )
                beacon = catalog[beacon_id]
                distance = math.hypot(truth[0] - beacon["x"], truth[1] - beacon["y"])
                if distance < MIN_DISTANCE_M:
                    raise CalibrationError(
                        f"{point_id} 距 {beacon_id} 仅 {distance:.2f} m，"
                        f"低于 {MIN_DISTANCE_M} m（疑似真值坐标录错）"
                    )
                declared = obs.get("truthDistanceM")
                if declared is not None and abs(float(declared) - distance) > distance_tolerance_m:
                    raise CalibrationError(
                        f"{point_id}/{beacon_id} 的 truthDistanceM={declared} 与台账重算 "
                        f"{distance:.3f} m 不一致（容差 {distance_tolerance_m} m）"
                    )
                pairs.append(
                    Pair(point_id, int(session_index or 0), floor, beacon_id, distance, rssi)
                )
    return pairs


def fit_pairs(pairs: list[Pair]) -> FitResult:
    """OLS：x=log10(d), y=rssi → 斜率=−10n、截距=A（docs/28 §5.1）。"""
    if len(pairs) < MIN_FIT_PAIRS:
        raise CalibrationError(f"观测对不足：{len(pairs)} < {MIN_FIT_PAIRS}")
    xs = [math.log10(p.distance_m) for p in pairs]
    ys = [float(p.rssi) for p in pairs]
    mean_x = sum(xs) / len(xs)
    mean_y = sum(ys) / len(ys)
    sxx = sum((x - mean_x) ** 2 for x in xs)
    if sxx == 0:
        raise CalibrationError("所有真值距离相同，无法拟合路径损耗指数")
    sxy = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys))
    slope = sxy / sxx
    intercept = mean_y - slope * mean_x
    path_loss_n = -slope / 10.0
    if not path_loss_n > 0:
        raise CalibrationError(f"拟合得到非法路径损耗指数 n={path_loss_n:.3f}")
    residuals = [y - (intercept + slope * x) for x, y in zip(xs, ys)]
    sse = sum(r * r for r in residuals)
    sst = sum((y - mean_y) ** 2 for y in ys)
    r2 = 1.0 - sse / sst if sst > 0 else 0.0
    rmse = math.sqrt(sse / len(pairs))
    return FitResult(intercept, path_loss_n, r2, rmse, len(pairs))


def fit_by_floor(pairs: list[Pair]) -> dict[int, FitResult]:
    grouped: dict[int, list[Pair]] = {}
    for pair in pairs:
        grouped.setdefault(pair.floor, []).append(pair)
    fits: dict[int, FitResult] = {}
    for floor, items in sorted(grouped.items()):
        try:
            fits[floor] = fit_pairs(items)
        except CalibrationError:
            continue
    return fits


def should_use_floor_groups(
    global_fit: FitResult,
    floor_fits: dict[int, FitResult],
    min_pairs: int = DEFAULT_MIN_GROUP_PAIRS,
    min_improvement: float = DEFAULT_GROUP_IMPROVEMENT,
) -> tuple[bool, str]:
    """分组判据：每组 ≥min_pairs 且加权 RMSE 相对全局下降 > min_improvement。"""
    if len(floor_fits) < 2:
        return False, f"有效楼层分组不足（{len(floor_fits)} 组）"
    short = {f: fit.pairs for f, fit in floor_fits.items() if fit.pairs < min_pairs}
    if short:
        detail = "、".join(f"F{f} {n} 对" for f, n in sorted(short.items()))
        return False, f"分组样本不足（每组需 ≥{min_pairs} 对）：{detail}"
    total = sum(fit.pairs for fit in floor_fits.values())
    grouped_rmse = math.sqrt(
        sum(fit.rmse * fit.rmse * fit.pairs for fit in floor_fits.values()) / total
    )
    improvement = (global_fit.rmse - grouped_rmse) / global_fit.rmse if global_fit.rmse > 0 else 0.0
    if improvement > min_improvement:
        return True, (
            f"分组 RMSE {grouped_rmse:.2f} dB，较全局 {global_fit.rmse:.2f} dB "
            f"下降 {improvement * 100:.1f}%（> {min_improvement * 100:.0f}%）"
        )
    return False, (
        f"分组 RMSE {grouped_rmse:.2f} dB，较全局 {global_fit.rmse:.2f} dB "
        f"仅下降 {improvement * 100:.1f}%（≤ {min_improvement * 100:.0f}%）"
    )


def percentile(values: list[float], p: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    pos = (len(ordered) - 1) * p
    low = math.floor(pos)
    high = math.ceil(pos)
    if low == high:
        return ordered[low]
    return ordered[low] + (ordered[high] - ordered[low]) * (pos - low)


def replay_sessions(
    pkg: MapPackage,
    samples: dict,
    intercept: float,
    path_loss_n: float,
    reliable_m: float = 1.5,
    degraded_m: float = 4.0,
) -> ReplayResult:
    """用生产解算回放每个静态采样会话（同点同会话的所有观测作为一批）。"""
    result = ReplayResult()
    params = LocationParams(
        rssi_at_1m=intercept,
        path_loss_n=path_loss_n,
        residual_reliable_m=reliable_m,
        residual_degraded_m=degraded_m,
        params_version="replay",
    )
    for point in samples.get("points") or []:
        truth = point["truth"]
        for session in point.get("sessions") or []:
            result.session_count += 1
            observations = [
                BeaconInput(beacon_id=obs["beaconId"], rssi=obs["rssiMedian"])
                for obs in session.get("observations") or []
            ]
            if not observations:
                result.lost += 1
                continue
            estimate = estimate_location(
                pkg, observations, str(point["floor"]), params=params
            )
            result.quality_counts[estimate.quality.value] = (
                result.quality_counts.get(estimate.quality.value, 0) + 1
            )
            if estimate.x is None or estimate.y is None:
                result.lost += 1
                continue
            result.estimated += 1
            result.errors.append(math.hypot(estimate.x - truth[0], estimate.y - truth[1]))
            result.rms_values.append(max(0.0, round(10.0 * (1.0 - estimate.confidence), 3)))
    return result


def recommend_thresholds(
    errors: list[float],
    rms_values: list[float],
    target_cep90_m: float = DEFAULT_TARGET_CEP90_M,
    degraded_target_m: float = DEFAULT_DEGRADED_TARGET_M,
) -> tuple[float, float, float]:
    """按「该门限下静态 CEP90 仍可接受」的最小残差给建议（安全侧偏置）。"""
    pairs = list(zip(errors, rms_values))
    reliable_pool = [rms for err, rms in pairs if err <= target_cep90_m]
    degraded_pool = [rms for err, rms in pairs if err <= degraded_target_m]
    reliable = percentile(reliable_pool, 0.75) if reliable_pool else 1.5
    degraded = percentile(degraded_pool, 0.90) if degraded_pool else 4.0
    reliable = min(4.0, max(0.5, reliable))
    degraded = min(10.0, max(reliable + 0.5, degraded))
    origin_min_confidence = max(0.0, min(1.0, 1.0 - reliable / 10.0))
    return round(reliable, 2), round(degraded, 2), round(origin_min_confidence, 3)


def cep(errors: list[float]) -> dict[str, float | None]:
    return {
        "cep50": percentile(errors, 0.50),
        "cep75": percentile(errors, 0.75),
        "cep90": percentile(errors, 0.90),
    }


def default_map_package_path(samples: dict) -> Path:
    venue = samples.get("venueId")
    version = samples.get("mapVersion")
    if not venue or not version:
        raise CalibrationError("样本缺 venueId/mapVersion，无法定位地图包")
    return DEFAULT_MAPS_DIR / str(venue) / f"{version}.json"


def load_map_package(path: Path) -> MapPackage:
    return MapPackage.model_validate(load_json(path))


def build_params_document(
    *,
    samples_path: Path,
    samples: dict,
    ledger_path: Path,
    global_fit: FitResult,
    floor_fits: dict[int, FitResult],
    use_groups: bool,
    group_reason: str,
    replay: ReplayResult,
    replay_unbounded: ReplayResult,
    reliable_m: float,
    degraded_m: float,
    origin_min_confidence: float,
    params_version: str,
) -> dict:
    static_errors = replay_unbounded.errors
    static_metrics = {
        "sessions": replay_unbounded.session_count,
        "estimated": replay_unbounded.estimated,
        "lost": replay_unbounded.lost,
        **cep(static_errors),
        "qualityCounts": dict(sorted(replay.quality_counts.items())),
    }
    doc = {
        "schemaVersion": "1.0",
        "paramsVersion": params_version,
        "frozenAt": None,
        "generatedAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "sourceStaticSamples": str(samples_path),
        "sourceStaticSamplesSha256": sha256_file(samples_path),
        "mapVersion": samples.get("mapVersion"),
        "beaconLedgerSha256": sha256_file(ledger_path),
        "rssiAt1m": round(global_fit.intercept, 2),
        "pathLossExponent": round(global_fit.path_loss_n, 3),
        "groups": [
            {
                "scope": f"floor:{floor}",
                "rssiAt1m": round(fit.intercept, 2),
                "n": round(fit.path_loss_n, 3),
            }
            for floor, fit in sorted(floor_fits.items())
        ]
        if use_groups
        else None,
        "thresholds": {
            "residualReliableM": reliable_m,
            "residualDegradedM": degraded_m,
            "originMinConfidence": origin_min_confidence,
        },
        "fit": {
            "samples": global_fit.pairs,
            "r2": round(global_fit.r2, 4),
            "rmseDb": round(global_fit.rmse, 3),
        },
        "groupDecision": group_reason,
        "metrics": {
            "global": {
                "pairs": global_fit.pairs,
                "r2": round(global_fit.r2, 4),
                "rmse": round(global_fit.rmse, 3),
            },
            "floors": {
                str(floor): {
                    "pairs": fit.pairs,
                    "r2": round(fit.r2, 4),
                    "rmse": round(fit.rmse, 3),
                }
                for floor, fit in sorted(floor_fits.items())
            },
            "staticReplay": static_metrics,
        },
        "dynamicRegression": None,
    }
    return doc


def render_report(
    doc: dict, samples: dict, pairs: list[Pair], out_path: Path
) -> str:
    metrics = doc["metrics"]
    static = metrics["staticReplay"]

    def fmt(value, digits=2):
        return "—" if value is None else f"{value:.{digits}f}"

    sessions = samples.get("points") or []
    lines = [
        "定位参数拟合报告",
        f"样本：{doc['sourceStaticSamples']}（sha256 {doc['sourceStaticSamplesSha256'][:12]}…）",
        f"地图版本：{doc['mapVersion']}；标定点 {len(sessions)}；观测对 {len(pairs)}",
        (
            f"全局：A={doc['rssiAt1m']:.2f} dBm，n={doc['pathLossExponent']:.3f}，"
            f"R²={metrics['global']['r2']:.4f}，RMSE={metrics['global']['rmse']:.2f} dB"
        ),
        f"分组：{'floor' if doc['groups'] else 'none'}（{doc['groupDecision']}）",
        (
            f"静态回放：{static['sessions']} 会话，有效 {static['estimated']}，"
            f"lost {static['lost']}；CEP50/75/90 = "
            f"{fmt(static['cep50'])} / {fmt(static['cep75'])} / {fmt(static['cep90'])} m；"
            f"质量分布 {static['qualityCounts']}"
        ),
        (
            f"建议阈值：RESIDUAL_RELIABLE_M={doc['thresholds']['residualReliableM']}，"
            f"RESIDUAL_DEGRADED_M={doc['thresholds']['residualDegradedM']}，"
            f"ORIGIN_MIN_CONFIDENCE={doc['thresholds']['originMinConfidence']}"
        ),
        f"输出：{out_path}",
        "注意：本文件仅作标定建议，写入 PATHAI_LOCATION_* 与冻结 paramsVersion 须人工执行。",
    ]
    return "\n".join(lines)


def run(
    samples_path: Path,
    ledger_path: Path,
    out_path: Path,
    *,
    map_package_path: Path | None = None,
    params_version: str | None = None,
    min_group_pairs: int = DEFAULT_MIN_GROUP_PAIRS,
    group_improvement: float = DEFAULT_GROUP_IMPROVEMENT,
    target_cep90_m: float = DEFAULT_TARGET_CEP90_M,
) -> dict:
    samples = load_json(samples_path)
    ledger = load_json(ledger_path)
    pairs = validate_samples(samples, ledger)
    global_fit = fit_pairs(pairs)
    floor_fits = fit_by_floor(pairs)
    use_groups, reason = should_use_floor_groups(
        global_fit, floor_fits, min_pairs=min_group_pairs, min_improvement=group_improvement
    )
    pkg = load_map_package(map_package_path or default_map_package_path(samples))

    # 每次回放都从同一份样本构造，避免一次异常影响后续统计
    replay = replay_sessions(pkg, samples, global_fit.intercept, global_fit.path_loss_n)
    replay_unbounded = replay_sessions(
        pkg, samples, global_fit.intercept, global_fit.path_loss_n,
        reliable_m=_UNBOUNDED, degraded_m=_UNBOUNDED,
    )
    reliable_m, degraded_m, origin_min = recommend_thresholds(
        replay_unbounded.errors, replay_unbounded.rms_values, target_cep90_m
    )
    doc = build_params_document(
        samples_path=samples_path,
        samples=samples,
        ledger_path=ledger_path,
        global_fit=global_fit,
        floor_fits=floor_fits,
        use_groups=use_groups,
        group_reason=reason,
        replay=replay,
        replay_unbounded=replay_unbounded,
        reliable_m=reliable_m,
        degraded_m=degraded_m,
        origin_min_confidence=origin_min,
        params_version=params_version or f"loc-{date.today():%Y%m%d}.1",
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(render_report(doc, samples, pairs, out_path))
    return doc


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="定位参数拟合（docs/28 §5）")
    parser.add_argument("--samples", type=Path, default=DEFAULT_SAMPLES)
    parser.add_argument("--ledger", type=Path, default=DEFAULT_LEDGER)
    parser.add_argument("--map-package", type=Path, default=None)
    parser.add_argument("--params-version", default=None)
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument("--min-group-pairs", type=int, default=DEFAULT_MIN_GROUP_PAIRS)
    parser.add_argument("--group-improvement", type=float, default=DEFAULT_GROUP_IMPROVEMENT)
    parser.add_argument("--target-cep90-m", type=float, default=DEFAULT_TARGET_CEP90_M)
    args = parser.parse_args(argv)

    version = args.params_version or f"loc-{date.today():%Y%m%d}.1"
    out = args.out or (PROJECT_ROOT / "result" / "calibration" / f"location_params_{version}.json")
    try:
        run(
            args.samples,
            args.ledger,
            out,
            map_package_path=args.map_package,
            params_version=version,
            min_group_pairs=args.min_group_pairs,
            group_improvement=args.group_improvement,
            target_cep90_m=args.target_cep90_m,
        )
    except CalibrationError as exc:
        print(f"[fit_location_params] {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
