/**
 * 真实地图包 fixture：读取后端 v9 转换产物 2026-10-25.1.json
 * （2 层 / 708 节点 / 56 地点 / 61 信标，局部米制坐标）。
 *
 * tsconfig 未启用 resolveJsonModule，按 smoke 测试 declare process 先例
 * 用 declare const require；jest moduleFileExtensions 含 json，模块级缓存。
 */
import type { MapBeaconEntry, MapPackageRoot } from '../../typings/api';

declare const require: (id: string) => unknown;

export const REAL_MAP = require('../../../backend/maps_data/school-building-01/2026-10-25.1.json') as MapPackageRoot;

/** 真实 iBeacon uuid（61 个信标共用，major=楼层号）。 */
export const REAL_UUID = 'B9407F30-F5F8-466E-AFF9-25556B57FE6D';

/** 按 beaconId 从真实目录取信标（缺失即抛，防地图再发布静默漂移）。 */
export function realBeacon(beaconId: string): MapBeaconEntry {
  const beacon = REAL_MAP.beacons.find((b) => b.beaconId === beaconId);
  if (!beacon) {
    throw new Error(`真实地图缺少信标 ${beaconId}`);
  }
  return beacon;
}

/** 1 层三点定位/窗口聚合用的三个真实信标。 */
export const REAL_F1_TRIO: MapBeaconEntry[] = ['BK-01-001', 'BK-01-004', 'BK-01-005'].map(realBeacon);
