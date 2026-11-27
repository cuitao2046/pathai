import {
  RADAR_CYCLE_MS,
  RADAR_HOLD_MS,
  pruneScannedBeacons,
  radarBlips,
  radarStrength,
  type ScannedBeaconState,
} from '../core/map/radar-blips';

function mapOf(entries: Array<[string, ScannedBeaconState]>): Map<string, ScannedBeaconState> {
  return new Map(entries);
}

describe('radarStrength', () => {
  it('把 RSSI 线性映射到 0~1 并截断', () => {
    expect(radarStrength(-50)).toBe(1);
    expect(radarStrength(-95)).toBe(0);
    expect(radarStrength(-72.5)).toBeCloseTo(0.5, 5);
    expect(radarStrength(-20)).toBe(1);
    expect(radarStrength(-120)).toBe(0);
  });
});

describe('radarBlips', () => {
  it('刚扫到的信标从相位 0、满衰减开始', () => {
    const blips = radarBlips(mapOf([['BK-01-005', { rssi: -60, atMs: 1000 }]]), 1000);
    expect(blips).toHaveLength(1);
    expect(blips[0].beaconId).toBe('BK-01-005');
    expect(blips[0].phase).toBe(0);
    expect(blips[0].fade).toBe(1);
    expect(blips[0].strength).toBeGreaterThan(0.7);
  });

  it('衰减与相位随时间推进', () => {
    const scanned = mapOf([['BK-01-005', { rssi: -60, atMs: 0 }]]);
    const half = radarBlips(scanned, RADAR_HOLD_MS / 2)[0];
    expect(half.fade).toBeCloseTo(0.5, 5);
    const quarterCycle = radarBlips(scanned, RADAR_CYCLE_MS / 4)[0];
    expect(quarterCycle.phase).toBeCloseTo(0.25, 5);
    const halfCycle = radarBlips(scanned, RADAR_CYCLE_MS / 2)[0];
    expect(halfCycle.phase).toBeCloseTo(0.5, 5);
  });

  it('超过保持时长或时间倒序的样本不输出', () => {
    const scanned = mapOf([
      ['expired', { rssi: -60, atMs: 0 }],
      ['future', { rssi: -60, atMs: 5000 }],
      ['alive', { rssi: -60, atMs: 3000 }],
    ]);
    const blips = radarBlips(scanned, 4500);
    expect(blips.map((b) => b.beaconId)).toEqual(['alive']);
  });

  it('空集合返回空数组', () => {
    expect(radarBlips(new Map(), 1000)).toEqual([]);
  });
});

describe('pruneScannedBeacons', () => {
  it('只删除超时样本，并返回是否变化', () => {
    const scanned = mapOf([
      ['old', { rssi: -60, atMs: 0 }],
      ['fresh', { rssi: -60, atMs: 3900 }],
    ]);
    expect(pruneScannedBeacons(scanned, 4001)).toBe(true);
    expect([...scanned.keys()]).toEqual(['fresh']);
    expect(pruneScannedBeacons(scanned, 4001)).toBe(false);
  });
});
