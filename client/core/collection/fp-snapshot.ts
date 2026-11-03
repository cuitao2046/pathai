/**
 * 网格指纹快照构造：与 fingerprint-collector utils/fpstore.js addSample 同契约——
 * 输入为 2s 窗口逐信标中位数（生产 ScanWindowAggregator.snapshot 口径），
 * 输出一条 FpRecord（分区字段恒 null，spec D5）。
 * accuracy 由路径损耗模型反推：d = 10^((RSSI@1m − rssi) / (10·n))，n=2。
 * 纯逻辑、不依赖 wx。
 */
import type { FpBeaconObs, FpRecord, SnapshotWindow } from './types';

export interface FpSnapshotPoint {
  fpId: string;
  floorId: string;
  x: number;
  y: number;
  regionType: string;
}

/** beaconId → 三元组（来自 BeaconRegistry/地图包信标台账）。 */
export interface FpBeaconLookup {
  (beaconId: string): { uuid: string; major: number; minor: number } | null;
}

export interface BuildFpRecordOptions {
  point: FpSnapshotPoint;
  /** 2s 窗口快照（已解析 beaconId 的中位数）。 */
  window: SnapshotWindow;
  lookup: FpBeaconLookup;
  /** 1m 参考 RSSI（与后端解算同源，BEACON_RSSI_AT_1M_DBM）。 */
  rssiAt1m: number;
  /** 路径损耗指数（默认 2，与生产模型一致）。 */
  pathLossN?: number;
  collectedAtMs?: number;
}

export function buildFpRecord(opts: BuildFpRecordOptions): FpRecord {
  const n = opts.pathLossN ?? 2;
  const beacons: FpBeaconObs[] = [];
  for (const obs of opts.window) {
    const triplet = opts.lookup(obs.beaconId);
    if (triplet === null) {
      continue;
    }
    beacons.push({
      uuid: triplet.uuid,
      major: triplet.major,
      minor: triplet.minor,
      rssi: obs.rssi,
      txPower: opts.rssiAt1m,
      accuracy: Math.round(Math.pow(10, (opts.rssiAt1m - obs.rssi) / (10 * n)) * 100) / 100,
    });
  }
  // 从强到弱（与独立工具 normalize 后的顺序一致，便于人工核对）
  beacons.sort((a, b) => b.rssi - a.rssi);
  return {
    fpId: opts.point.fpId,
    floor: Number(opts.point.floorId),
    coordinates: [opts.point.x, opts.point.y],
    relCoordinates: null,
    zoneId: null,
    zoneName: null,
    anchor: null,
    regionType: opts.point.regionType,
    captureIndex: 0, // 由 FpRecordsStore.addRecord 落库时定
    collectedAt: new Date(opts.collectedAtMs ?? Date.now()).toISOString(),
    beaconCount: beacons.length,
    beacons,
  };
}
