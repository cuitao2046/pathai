/**
 * 对数等距页信标选择：列表来自地图包、按楼层分组、探测只作标记。
 */
import {
  SIGNAL_ACTIVE_MS,
  activeSeenBeacons,
  buildBeaconGroups,
  detectHintText,
  floorTitle,
  hasBeaconTriplet,
  reconcileBeaconSelection,
  rssiLevel,
  uniqueSeenBeaconId,
  type SeenBeacon,
} from '../core/collection/beacon-picker';
import type { MapBeaconEntry } from '../typings/api';

const UUID = 'B9407F30-F5F8-466E-AFF9-25556B57FE6D';

function beacon(
  beaconId: string,
  floorId: string,
  minor: number,
  overrides: Partial<MapBeaconEntry> = {},
): MapBeaconEntry {
  return {
    beaconId,
    floorId,
    x: minor / 100,
    y: -minor / 100,
    installHeightM: 2.5,
    uuid: UUID,
    major: 1,
    minor,
    locationDesc: `${floorId}F ${beaconId} 位置`,
    ...overrides,
  };
}

const BEACONS: MapBeaconEntry[] = [
  beacon('BK-02-001', '2', 2001, { x: 1.5, y: -2.25 }),
  beacon('BK-01-002', '1', 1002),
  beacon('BK-01-001', '1', 1001, { installHeightM: 3 }),
  beacon('BK-01-003', '1', 1003, {
    uuid: null,
    major: null,
    minor: null,
    locationDesc: null,
  }),
];

describe('hasBeaconTriplet / rssiLevel / floorTitle', () => {
  it('缺 uuid/major/minor 视为注册表不可解析', () => {
    expect(hasBeaconTriplet(BEACONS[0])).toBe(true);
    expect(hasBeaconTriplet(BEACONS[3])).toBe(false);
  });

  it('RSSI 强弱分档与楼层标题稳定', () => {
    expect(rssiLevel(-50)).toBe('strong');
    expect(rssiLevel(-65)).toBe('medium');
    expect(rssiLevel(-80)).toBe('weak');
    expect(floorTitle('1')).toBe('1F');
    expect(floorTitle('B1')).toBe('B1');
  });
});

describe('buildBeaconGroups', () => {
  it('按楼层数值升序、同层 beaconId 升序，并带出地图包描述/坐标/高度', () => {
    const groups = buildBeaconGroups(BEACONS, {
      seen: new Map(),
      nowMs: 1000,
      selectedId: null,
      uniqueId: null,
    });
    expect(groups.map((g) => g.floorId)).toEqual(['1', '2']);
    expect(groups[0].title).toBe('1F');
    expect(groups[0].items.map((i) => i.beaconId)).toEqual([
      'BK-01-001',
      'BK-01-002',
      'BK-01-003',
    ]);
    const first = groups[0].items[0];
    expect(first.locationDesc).toBe('1F BK-01-001 位置');
    expect(first.coordText).toBe('10.01, -10.01');
    expect(first.installHeightText).toBe('3.0 m');
    expect(groups[0].items[2].locationDesc).toBe('地图包未提供位置描述');
    expect(groups[0].items[2].installHeightText).toBe('2.5 m');
  });

  it('收到信号显示 RSSI；未收到灰显但仍是可选条目', () => {
    const seen = new Map<string, SeenBeacon>([
      ['BK-01-001', { rssi: -58, at: 900 }],
    ]);
    const groups = buildBeaconGroups(BEACONS, {
      seen,
      nowMs: 1000,
      selectedId: 'BK-01-002',
      uniqueId: 'BK-01-001',
    });
    const received = groups[0].items[0];
    const missing = groups[0].items[1];
    expect(received.received).toBe(true);
    expect(received.rssiText).toBe('-58 dBm');
    expect(received.rssiLevel).toBe('strong');
    expect(received.unique).toBe(true);
    expect(missing.received).toBe(false);
    expect(missing.rssiText).toBe('');
    // 未收到信号不改变可选性（与注册表同源，可解析即可选）
    expect(missing.resolvable).toBe(true);
    expect(missing.selected).toBe(true);
    expect(groups[0].items[2].resolvable).toBe(false);
  });

  it('超过信号窗口后不再标记为已收到', () => {
    const seen = new Map<string, SeenBeacon>([
      ['BK-01-001', { rssi: -58, at: 0 }],
    ]);
    const groups = buildBeaconGroups(BEACONS, {
      seen,
      nowMs: SIGNAL_ACTIVE_MS + 1,
      selectedId: null,
      uniqueId: null,
    });
    expect(groups[0].items[0].received).toBe(false);
  });

  it('地图包为空（未加载/加载失败）时返回空列表', () => {
    expect(
      buildBeaconGroups([], {
        seen: new Map(),
        nowMs: 0,
        selectedId: null,
        uniqueId: null,
      }),
    ).toEqual([]);
  });
});

describe('探测提示（只提示不锁定）', () => {
  it('唯一信标给出高亮提示，但不产生选中项', () => {
    const seen = new Map<string, SeenBeacon>([
      ['BK-01-001', { rssi: -62, at: 900 }],
    ]);
    const active = activeSeenBeacons(seen, 1000);
    const unique = uniqueSeenBeaconId(active);
    expect(unique).toBe('BK-01-001');
    expect(detectHintText(active, unique)).toContain('探测到唯一信标 BK-01-001');
  });

  it('0 枚 / 多枚时分别给出选择提示', () => {
    expect(detectHintText([], null)).toContain('未探测到信标');
    const active = [
      { beaconId: 'BK-01-001', rssi: -60 },
      { beaconId: 'BK-01-002', rssi: -70 },
    ];
    expect(uniqueSeenBeaconId(active)).toBeNull();
    expect(detectHintText(active, null)).toContain('探测到 2 枚信标');
  });
});

describe('reconcileBeaconSelection', () => {
  it('选中项仍在新地图包时保留', () => {
    expect(reconcileBeaconSelection('BK-01-001', BEACONS)).toEqual({
      selectedId: 'BK-01-001',
      dropped: false,
    });
  });

  it('地图包版本变化导致选中信标不存在时重置', () => {
    expect(reconcileBeaconSelection('BK-09-999', BEACONS)).toEqual({
      selectedId: null,
      dropped: true,
    });
  });

  it('未选择时不产生重置提示', () => {
    expect(reconcileBeaconSelection(null, BEACONS)).toEqual({
      selectedId: null,
      dropped: false,
    });
  });
});
