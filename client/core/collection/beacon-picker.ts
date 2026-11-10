/**
 * 对数等距页信标选择（spec：calibration-console §5）。
 * 候选列表 = 当前地图包已登记信标（与 BeaconRegistry 同源），按楼层分组；
 * 实时探测只作标记：收到显示 RSSI，未收到灰显但仍可选，绝不自动锁定。
 * 纯逻辑、不依赖 wx，可 jest 直测。
 */
import type { MapBeaconEntry } from '../../typings/api';

/** 实时探测标记的有效窗口：超过该时长未再收到视为“当前未收到”。 */
export const SIGNAL_ACTIVE_MS = 5_000;

export interface SeenBeacon {
  rssi: number;
  at: number;
}

export interface BeaconPickerItem {
  beaconId: string;
  floorId: string;
  /** 地图包区域/位置描述；缺失时给出明确占位。 */
  locationDesc: string;
  /** 地图包坐标（米制局部坐标），保留 2 位。 */
  coordText: string;
  /** 地图包安装高度；缺失时为“未知”。 */
  installHeightText: string;
  /** 地图包是否带完整 uuid/major/minor（缺三元组无法被注册表解析）。 */
  resolvable: boolean;
  /** 当前是否收到该信标（仅标记，不参与过滤）。 */
  received: boolean;
  rssiText: string;
  rssiLevel: '' | 'strong' | 'medium' | 'weak';
  selected: boolean;
  /** 是否为“唯一探测到”的信标高亮项（仅提示，不自动选择）。 */
  unique: boolean;
}

export interface BeaconPickerGroup {
  floorId: string;
  title: string;
  items: BeaconPickerItem[];
}

export interface BuildBeaconGroupsOptions {
  seen: ReadonlyMap<string, SeenBeacon>;
  nowMs: number;
  selectedId: string | null;
  uniqueId: string | null;
  activeMs?: number;
}

export interface ActiveSeenBeacon {
  beaconId: string;
  rssi: number;
}

/** 地图包三元组完整性（与 BeaconRegistry 可解析条件一致）。 */
export function hasBeaconTriplet(beacon: MapBeaconEntry): boolean {
  return (
    typeof beacon.uuid === 'string' &&
    beacon.uuid.length > 0 &&
    typeof beacon.major === 'number' &&
    typeof beacon.minor === 'number'
  );
}

export function rssiLevel(rssi: number): 'strong' | 'medium' | 'weak' {
  if (rssi >= -60) {
    return 'strong';
  }
  if (rssi >= -75) {
    return 'medium';
  }
  return 'weak';
}

export function floorTitle(floorId: string): string {
  return /^\d+$/.test(floorId) ? `${floorId}F` : floorId;
}

function floorSortKey(floorId: string): [number, string] {
  const numeric = Number(floorId);
  return Number.isFinite(numeric) ? [numeric, ''] : [Number.POSITIVE_INFINITY, floorId];
}

function compareFloor(a: string, b: string): number {
  const [an, as] = floorSortKey(a);
  const [bn, bs] = floorSortKey(b);
  if (an !== bn) {
    return an - bn;
  }
  return as.localeCompare(bs);
}

/** 当前仍在信号窗口内的信标，按 RSSI 从强到弱排序。 */
export function activeSeenBeacons(
  seen: ReadonlyMap<string, SeenBeacon>,
  nowMs: number,
  activeMs: number = SIGNAL_ACTIVE_MS,
): ActiveSeenBeacon[] {
  const out: ActiveSeenBeacon[] = [];
  for (const [beaconId, value] of seen) {
    if (nowMs - value.at <= activeMs) {
      out.push({ beaconId, rssi: value.rssi });
    }
  }
  return out.sort((a, b) => b.rssi - a.rssi);
}

/** 恰好探测到 1 枚时返回其 ID；0 枚或多枚返回 null（不自动锁定）。 */
export function uniqueSeenBeaconId(active: ActiveSeenBeacon[]): string | null {
  return active.length === 1 ? active[0].beaconId : null;
}

export function detectHintText(
  active: ActiveSeenBeacon[],
  uniqueId: string | null,
): string {
  if (active.length === 0) {
    return '未探测到信标；仍可从地图包列表手动选择';
  }
  if (uniqueId !== null) {
    return `探测到唯一信标 ${uniqueId}（${Math.round(active[0].rssi)} dBm），请确认选择`;
  }
  return `探测到 ${active.length} 枚信标，请按现场部署选择`;
}

/** 按楼层分组（楼层数值升序、同层 beaconId 升序），并附加探测标记。 */
export function buildBeaconGroups(
  beacons: MapBeaconEntry[],
  opts: BuildBeaconGroupsOptions,
): BeaconPickerGroup[] {
  const activeMs = opts.activeMs ?? SIGNAL_ACTIVE_MS;
  const byFloor = new Map<string, MapBeaconEntry[]>();
  for (const beacon of beacons) {
    const floorId = String(beacon.floorId);
    const list = byFloor.get(floorId);
    if (list === undefined) {
      byFloor.set(floorId, [beacon]);
    } else {
      list.push(beacon);
    }
  }
  return [...byFloor.keys()].sort(compareFloor).map((floorId) => {
    const items = byFloor
      .get(floorId)!
      .slice()
      .sort((a, b) => a.beaconId.localeCompare(b.beaconId))
      .map((beacon): BeaconPickerItem => {
        const seen = opts.seen.get(beacon.beaconId);
        const received = seen !== undefined && opts.nowMs - seen.at <= activeMs;
        return {
          beaconId: beacon.beaconId,
          floorId,
          locationDesc: beacon.locationDesc?.trim() || '地图包未提供位置描述',
          coordText: `${beacon.x.toFixed(2)}, ${beacon.y.toFixed(2)}`,
          installHeightText:
            typeof beacon.installHeightM === 'number'
              ? `${beacon.installHeightM.toFixed(1)} m`
              : '未知',
          resolvable: hasBeaconTriplet(beacon),
          received,
          rssiText: received ? `${Math.round(seen.rssi)} dBm` : '',
          rssiLevel: received ? rssiLevel(seen.rssi) : '',
          selected: beacon.beaconId === opts.selectedId,
          unique: beacon.beaconId === opts.uniqueId,
        };
      });
    return { floorId, title: floorTitle(floorId), items };
  });
}

/**
 * 地图包版本切换后核对选中项：不在新包中则重置（由页面提示并禁用采集）。
 */
export function reconcileBeaconSelection(
  selectedId: string | null,
  beacons: MapBeaconEntry[],
): { selectedId: string | null; dropped: boolean } {
  if (selectedId === null) {
    return { selectedId: null, dropped: false };
  }
  const exists = beacons.some((b) => b.beaconId === selectedId);
  return exists ? { selectedId, dropped: false } : { selectedId: null, dropped: true };
}
