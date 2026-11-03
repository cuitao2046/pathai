/**
 * 静态标定采样器：与 positioning-calibrator utils/sampler.js 同口径断言。
 */
import {
  StaticSampler,
  clampRssi,
  truthDistance3D,
  type StaticBeaconRef,
} from '../core/collection/static-sampler';
import { RSSI_MAX_DBM, RSSI_MIN_DBM, type BeaconSample } from '../core/ble/scan-window';
import { PHONE_HEIGHT_M } from '../config/env';

function sample(uuid: string, major: number, minor: number, rssi: number): BeaconSample {
  return { uuid, major, minor, rssi, observedAtMs: 1_000 };
}

describe('clampRssi', () => {
  it('截断到 [-100,-20]（与生产 scan-window 同口径）', () => {
    expect(clampRssi(-120)).toBe(RSSI_MIN_DBM);
    expect(clampRssi(-10)).toBe(RSSI_MAX_DBM);
    expect(clampRssi(-63)).toBe(-63);
    expect(clampRssi(NaN)).toBeNull();
  });
});

describe('truthDistance3D', () => {
  it('无高度差时退化为平面距离', () => {
    const b: StaticBeaconRef = { beaconId: 'B1', x: 3, y: 4, installHeightM: PHONE_HEIGHT_M };
    expect(truthDistance3D([0, 0], b)).toBeCloseTo(5, 9);
  });

  it('三维斜距：3-4-5 平面叠加 dz=1.3', () => {
    const b: StaticBeaconRef = { beaconId: 'B1', x: 3, y: 4, installHeightM: 2.5 };
    expect(truthDistance3D([0, 0], b)).toBeCloseTo(Math.sqrt(25 + 1.3 * 1.3), 9);
  });

  it('缺 installHeightM 时按平面处理（fail-safe，与 sampler.js 一致）', () => {
    const b: StaticBeaconRef = { beaconId: 'B1', x: 3, y: 4, installHeightM: null };
    expect(truthDistance3D([0, 0], b)).toBeCloseTo(5, 9);
  });
});

describe('StaticSampler', () => {
  const beacons: Record<string, StaticBeaconRef> = {
    'a|1|1': { beaconId: 'BK-A', x: 0, y: 3, installHeightM: 2.5 },
    'b|1|2': { beaconId: 'BK-B', x: 0, y: 10, installHeightM: 2.5 },
  };
  const resolve = (uuid: string, major: number, minor: number): StaticBeaconRef | null =>
    beacons[`${uuid}|${major}|${minor}`] ?? null;

  function makeSampler(): StaticSampler {
    return new StaticSampler({ truth: [0, 0], resolve, startedAtMs: 1_000_000 });
  }

  it('未知三元组忽略、uuid 大小写/空白归一', () => {
    const s = makeSampler();
    expect(s.push(sample('UNKNOWN', 9, 9, -60))).toBe(false);
    expect(s.push(sample(' A ', 1, 1, -60))).toBe(true);
    expect(s.push(sample('a', 1, 1, -62))).toBe(true);
    expect(s.resolvedBeaconCount).toBe(1);
  });

  it('逐信标统计与 sampler.js _row 同构（字段与口径）', () => {
    const s = makeSampler();
    for (const rssi of [-60, -62, -64, -66]) {
      expect(s.push(sample('a', 1, 1, rssi))).toBe(true);
    }
    expect(s.push(sample('b', 1, 2, -80))).toBe(true);
    const rows = s.observations();
    expect(rows[0].beaconId).toBe('BK-A'); // 从强到弱
    const row = rows[0];
    expect(Object.keys(row).sort()).toEqual(
      ['beaconId', 'n', 'rssiMax', 'rssiMedian', 'rssiMin', 'rssiStd', 'truthDistanceM'].sort(),
    );
    expect(row.n).toBe(4);
    expect(row.rssiMedian).toBe(-63); // 偶数个取中间两值平均后取整
    expect(row.rssiMin).toBe(-66);
    expect(row.rssiMax).toBe(-60);
    expect(row.rssiStd).toBeGreaterThan(0);
    expect(row.truthDistanceM).toBeCloseTo(Math.sqrt(9 + 1.3 * 1.3), 2);
  });

  it('finish 产出 ISO startedAt 与会话主体', () => {
    const s = makeSampler();
    s.push(sample('a', 1, 1, -60));
    const out = s.finish(60);
    expect(out.durationS).toBe(60);
    expect(out.startedAt).toBe(new Date(1_000_000).toISOString());
    expect(out.observations).toHaveLength(1);
  });
});
