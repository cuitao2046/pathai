/**
 * 信标注册表：iBeacon 扫描三元组 (uuid, major, minor) → beaconId 解析。
 * 只允许解析出地图包已登记的信标；未命中返回 null（调用方丢弃，不得虚构）。
 * uuid 比较大小写不敏感（不同手机系统广播大小写不一致）。
 */
import type { MapBeaconEntry } from '../../typings/api';

function tripletKey(uuid: string, major: number, minor: number): string {
  return `${uuid.trim().toLowerCase()}|${major}|${minor}`;
}

export class BeaconRegistry {
  private readonly byTriplet = new Map<string, string>();
  private readonly uuids = new Set<string>();

  constructor(beacons: MapBeaconEntry[]) {
    for (const beacon of beacons) {
      if (
        typeof beacon.uuid === 'string' &&
        beacon.uuid.length > 0 &&
        typeof beacon.major === 'number' &&
        typeof beacon.minor === 'number'
      ) {
        this.byTriplet.set(tripletKey(beacon.uuid, beacon.major, beacon.minor), beacon.beaconId);
        this.uuids.add(beacon.uuid.trim().toLowerCase());
      }
    }
  }

  /** 解析失败（未登记/缺三元组）返回 null，由管线丢弃该观测。 */
  resolve(uuid: string, major: number, minor: number): string | null {
    return this.byTriplet.get(tripletKey(uuid, major, minor)) ?? null;
  }

  /** wx.startBeaconDiscovery 的 uuids 参数（去重、小写）。 */
  get scanUuids(): string[] {
    return [...this.uuids];
  }

  /** 已登记三元组的信标数。 */
  get resolvableCount(): number {
    return this.byTriplet.size;
  }
}
