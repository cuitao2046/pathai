/**
 * 静态标定采样器：与 positioning-calibrator utils/sampler.js 同口径——
 * 停留期间累积**全部原始样本**（截断 [-100,-20]），按信标产出
 * {n, rssiMedian, rssiMin, rssiMax, rssiStd, truthDistanceM}；
 * n 为会话内原始样本数（PC 侧拟合以此为权重）。
 * truthDistanceM 为真值点与信标的三维斜距（spec 0.3.3，installHeightM 缺失退化为平面）。
 * 纯逻辑、不依赖 wx，可 jest 直测。
 */
import { PHONE_HEIGHT_M } from '../../config/env';
import { RSSI_MAX_DBM, RSSI_MIN_DBM, type BeaconSample } from '../ble/scan-window';
import type { StaticObservation } from './types';

/** 采样页注入：三元组 → 信标几何（来自 BeaconRegistry + 地图包信标台账）。 */
export interface StaticBeaconRef {
  beaconId: string;
  x: number;
  y: number;
  installHeightM: number | null;
}

export type StaticBeaconResolver = (
  uuid: string,
  major: number,
  minor: number,
) => StaticBeaconRef | null;

export function clampRssi(rssi: number): number | null {
  if (!Number.isFinite(rssi)) {
    return null;
  }
  return Math.min(RSSI_MAX_DBM, Math.max(RSSI_MIN_DBM, rssi));
}

/** 与生产 scan-window.ts 同口径：偶数个取中间两值平均后取整（API-05 rssi 为 strict int）。 */
function medianInt(values: number[]): number {
  const sorted = [...values].sort((a, b) => a - b);
  const mid = sorted.length >> 1;
  return sorted.length % 2 === 1
    ? sorted[mid]
    : Math.round((sorted[mid - 1] + sorted[mid]) / 2);
}

function stdDev1(values: number[]): number {
  if (values.length === 0) {
    return 0;
  }
  const mean = values.reduce((s, v) => s + v, 0) / values.length;
  const variance = values.reduce((s, v) => s + (v - mean) * (v - mean), 0) / values.length;
  return Math.round(Math.sqrt(variance) * 10) / 10;
}

/** 真值点与信标的三维斜距（米）：dz = installHeightM − 手持高度 1.2 m。 */
export function truthDistance3D(
  truth: [number, number],
  beacon: { x: number; y: number; installHeightM: number | null },
): number {
  const dz = beacon.installHeightM != null ? beacon.installHeightM - PHONE_HEIGHT_M : 0;
  return Math.hypot(truth[0] - beacon.x, truth[1] - beacon.y, dz);
}

function tripletKey(uuid: string, major: number, minor: number): string {
  return `${uuid.trim().toLowerCase()}|${major}|${minor}`;
}

export interface StaticSamplerOptions {
  /** 标定点真值坐标。 */
  truth: [number, number];
  resolve: StaticBeaconResolver;
  startedAtMs?: number;
  /**
   * 固定真值斜距（对数等距页）：跳过地图坐标，直接使用档位计算的 d。
   * 未提供时按静态标定口径计算三维斜距。
   */
  fixedTruthDistanceM?: number;
  /** 只统计该信标（对数等距页锁定信标）；其他信标样本丢弃。 */
  onlyBeaconId?: string;
}

export class StaticSampler {
  private readonly byBeacon = new Map<string, { ref: StaticBeaconRef; values: number[] }>();
  private readonly resolve: StaticBeaconResolver;
  private readonly fixedTruthDistanceM: number | null;
  private readonly onlyBeaconId: string | null;
  readonly truth: [number, number];
  readonly startedAtMs: number;

  constructor(opts: StaticSamplerOptions) {
    this.truth = opts.truth;
    this.resolve = opts.resolve;
    this.startedAtMs = opts.startedAtMs ?? Date.now();
    this.fixedTruthDistanceM = opts.fixedTruthDistanceM ?? null;
    this.onlyBeaconId = opts.onlyBeaconId ?? null;
  }

  /** 推入一条原始样本；无法解析的信标丢弃（不虚构 beaconId）。返回是否被采纳。 */
  push(sample: BeaconSample): boolean {
    const rssi = clampRssi(sample.rssi);
    if (rssi === null) {
      return false;
    }
    const key = tripletKey(sample.uuid, sample.major, sample.minor);
    let entry = this.byBeacon.get(key);
    if (entry === undefined) {
      // uuid 先归一（trim + 小写）再交给 resolver：与 tripletKey 同口径
      const sep = key.indexOf('|');
      const ref = this.resolve(key.slice(0, sep), sample.major, sample.minor);
      if (ref === null) {
        return false;
      }
      if (this.onlyBeaconId !== null && ref.beaconId !== this.onlyBeaconId) {
        return false;
      }
      entry = { ref, values: [] };
      this.byBeacon.set(key, entry);
    }
    entry.values.push(rssi);
    return true;
  }

  /** 当前逐信标统计（按 rssiMedian 从强到弱，供实时表展示）。 */
  observations(): StaticObservation[] {
    const rows = [...this.byBeacon.values()].map(({ ref, values }) => ({
      beaconId: ref.beaconId,
      n: values.length,
      rssiMedian: medianInt(values),
      rssiMin: Math.min(...values),
      rssiMax: Math.max(...values),
      rssiStd: stdDev1(values),
      truthDistanceM:
        Math.round(
          (this.fixedTruthDistanceM ?? truthDistance3D(this.truth, ref)) * 100,
        ) / 100,
    }));
    return rows.sort((a, b) => b.rssiMedian - a.rssiMedian);
  }

  get sampleCount(): number {
    let total = 0;
    for (const { values } of this.byBeacon.values()) {
      total += values.length;
    }
    return total;
  }

  get resolvedBeaconCount(): number {
    return this.byBeacon.size;
  }

  /** 已解析到的信标 ID（供对数等距页发现候选）。 */
  get beaconIds(): string[] {
    return [...new Set([...this.byBeacon.values()].map(({ ref }) => ref.beaconId))];
  }

  /** 结束本次停留：产出会话主体（startedAt 为 ISO 时刻）。 */
  finish(durationS: number): { startedAt: string; durationS: number; observations: StaticObservation[] } {
    return {
      startedAt: new Date(this.startedAtMs).toISOString(),
      durationS,
      observations: this.observations(),
    };
  }
}
