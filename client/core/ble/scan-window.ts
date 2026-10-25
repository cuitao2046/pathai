/**
 * 2s 扫描窗口聚合：每信标滑动窗口取 RSSI 中位数（抗抖动），
 * RSSI 截断到 [-100,-20]（契约接受域），可解析信标不足 3 个 → null（三点定位下限）。
 */
import type { BeaconRegistry } from './beacon-registry';

export const RSSI_MIN_DBM = -100;
export const RSSI_MAX_DBM = -20;
export const WINDOW_MS = 2000;
export const MIN_RESOLVED_BEACONS = 3;

export interface BeaconSample {
  uuid: string;
  major: number;
  minor: number;
  rssi: number;
  observedAtMs: number;
}

export interface ResolvedObservation {
  beaconId: string;
  /** 截断后的窗口中位数。 */
  rssi: number;
  /** 该信标窗口内最新样本时间（毫秒）。 */
  observedAtMs: number;
}

function clampRssi(rssi: number): number | null {
  if (!Number.isFinite(rssi)) {
    return null;
  }
  return Math.min(RSSI_MAX_DBM, Math.max(RSSI_MIN_DBM, rssi));
}

function median(values: number[]): number {
  const sorted = [...values].sort((a, b) => a - b);
  const mid = sorted.length >> 1;
  return sorted.length % 2 === 1
    ? sorted[mid]
    : (sorted[mid - 1] + sorted[mid]) / 2;
}

interface WindowEntry {
  rssi: number;
  atMs: number;
}

export class ScanWindowAggregator {
  private readonly byTriplet = new Map<string, WindowEntry[]>();
  private readonly windowMs: number;

  constructor(windowMs: number = WINDOW_MS) {
    this.windowMs = windowMs;
  }

  push(sample: BeaconSample): void {
    const rssi = clampRssi(sample.rssi);
    if (rssi === null) {
      return;
    }
    const key = `${sample.uuid.trim().toLowerCase()}|${sample.major}|${sample.minor}`;
    const list = this.byTriplet.get(key);
    if (list === undefined) {
      this.byTriplet.set(key, [{ rssi, atMs: sample.observedAtMs }]);
    } else {
      list.push({ rssi, atMs: sample.observedAtMs });
    }
  }

  /**
   * 快照：清理窗口外样本，逐信标取中位数并经注册表解析 beaconId。
   * 未登记三元组丢弃（不虚构）；可解析信标不足 3 个返回 null。
   */
  snapshot(registry: BeaconRegistry, nowMsValue: number): ResolvedObservation[] | null {
    const observations: ResolvedObservation[] = [];
    for (const [key, list] of this.byTriplet) {
      const kept = list.filter((e) => e.atMs > nowMsValue - this.windowMs);
      if (kept.length === 0) {
        this.byTriplet.delete(key);
        continue;
      }
      this.byTriplet.set(key, kept);
      const separator = key.indexOf('|');
      const uuid = key.slice(0, separator);
      const rest = key.slice(separator + 1);
      const majorSeparator = rest.indexOf('|');
      const major = Number(rest.slice(0, majorSeparator));
      const minor = Number(rest.slice(majorSeparator + 1));
      const beaconId = registry.resolve(uuid, major, minor);
      if (beaconId === null) {
        continue;
      }
      observations.push({
        beaconId,
        rssi: median(kept.map((e) => e.rssi)),
        observedAtMs: Math.max(...kept.map((e) => e.atMs)),
      });
    }
    return observations.length >= MIN_RESOLVED_BEACONS ? observations : null;
  }
}
