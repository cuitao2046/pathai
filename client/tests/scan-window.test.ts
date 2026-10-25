/**
 * ScanWindowAggregator 测试（C6）：窗口剔除、中位数、RSSI 截断、
 * 未登记三元组丢弃、不足 3 信标返回 null。
 */
import { BeaconRegistry } from '../core/ble/beacon-registry';
import {
  MIN_RESOLVED_BEACONS,
  ScanWindowAggregator,
  type BeaconSample,
} from '../core/ble/scan-window';
import type { MapBeaconEntry } from '../typings/api';

const UUID = 'FDA50693-A4E2-4FB1-AFCF-C6EB07647825';

const CATALOG: MapBeaconEntry[] = [
  { beaconId: 'BK-1', floorId: '1', x: 5, y: 5, uuid: UUID, major: 1, minor: 1 },
  { beaconId: 'BK-2', floorId: '1', x: 10, y: 10, uuid: UUID, major: 1, minor: 2 },
  { beaconId: 'BK-3', floorId: '1', x: 5, y: 10, uuid: UUID, major: 1, minor: 3 },
];

const REGISTRY = new BeaconRegistry(CATALOG);

function sample(overrides: Partial<BeaconSample>): BeaconSample {
  return { uuid: UUID, major: 1, minor: 1, rssi: -60, observedAtMs: 1000, ...overrides };
}

describe('ScanWindowAggregator 窗口聚合', () => {
  it('每信标取窗口内 RSSI 中位数（奇数样本）', () => {
    const agg = new ScanWindowAggregator();
    agg.push(sample({ minor: 1, rssi: -60, observedAtMs: 100 }));
    agg.push(sample({ minor: 1, rssi: -70, observedAtMs: 200 }));
    agg.push(sample({ minor: 1, rssi: -65, observedAtMs: 300 }));
    agg.push(sample({ minor: 2, rssi: -80, observedAtMs: 150 }));
    agg.push(sample({ minor: 3, rssi: -90, observedAtMs: 250 }));
    const result = agg.snapshot(REGISTRY, 1000);
    expect(result).not.toBeNull();
    const byId = new Map(result!.map((o) => [o.beaconId, o]));
    expect(byId.get('BK-1')?.rssi).toBe(-65);
    expect(byId.get('BK-2')?.rssi).toBe(-80);
    expect(byId.get('BK-3')?.rssi).toBe(-90);
  });

  it('偶数样本取中间两值平均', () => {
    const agg = new ScanWindowAggregator();
    agg.push(sample({ rssi: -60, observedAtMs: 100 }));
    agg.push(sample({ rssi: -70, observedAtMs: 200 }));
    agg.push(sample({ minor: 2, observedAtMs: 100 }));
    agg.push(sample({ minor: 3, observedAtMs: 100 }));
    const result = agg.snapshot(REGISTRY, 1000);
    const bk1 = result!.find((o) => o.beaconId === 'BK-1');
    expect(bk1?.rssi).toBe(-65);
  });

  it('RSSI 截断到 [-100,-20]，非数值丢弃', () => {
    const agg = new ScanWindowAggregator();
    agg.push(sample({ rssi: -120, observedAtMs: 100 })); // → -100
    agg.push(sample({ rssi: -5, observedAtMs: 200 })); // → -20
    agg.push(sample({ rssi: Number.NaN, observedAtMs: 300 })); // 丢弃
    agg.push(sample({ minor: 2, observedAtMs: 100 }));
    agg.push(sample({ minor: 3, observedAtMs: 100 }));
    const result = agg.snapshot(REGISTRY, 1000);
    expect(result).toEqual([
      expect.objectContaining({ beaconId: 'BK-1', rssi: -60 }),
      expect.objectContaining({ beaconId: 'BK-2' }),
      expect.objectContaining({ beaconId: 'BK-3' }),
    ]);
  });

  it('窗口外样本被剔除；observedAtMs 取窗口内最新', () => {
    const agg = new ScanWindowAggregator(2000);
    agg.push(sample({ rssi: -50, observedAtMs: 0 })); // 窗口外
    agg.push(sample({ rssi: -70, observedAtMs: 100 }));
    agg.push(sample({ rssi: -60, observedAtMs: 1500 }));
    agg.push(sample({ minor: 2, observedAtMs: 100 }));
    agg.push(sample({ minor: 3, observedAtMs: 100 }));
    const result = agg.snapshot(REGISTRY, 2000);
    expect(result).toEqual([
      expect.objectContaining({ beaconId: 'BK-1', rssi: -65, observedAtMs: 1500 }),
      expect.anything(),
      expect.anything(),
    ]);
    expect(result!.map((o) => o.beaconId)).toEqual(['BK-1', 'BK-2', 'BK-3']);
  });

  it('未登记三元组丢弃，不虚构 beaconId', () => {
    const agg = new ScanWindowAggregator();
    agg.push(sample({ minor: 1, observedAtMs: 100 }));
    agg.push(sample({ minor: 9, observedAtMs: 100 })); // 未登记
    agg.push(sample({ major: 99, observedAtMs: 100 })); // 未登记
    const result = agg.snapshot(REGISTRY, 1000);
    expect(result).toBeNull(); // 只剩 1 个可解析信标
  });

  it(`可解析信标不足 ${MIN_RESOLVED_BEACONS} 个返回 null`, () => {
    const agg = new ScanWindowAggregator();
    agg.push(sample({ minor: 1, observedAtMs: 100 }));
    agg.push(sample({ minor: 2, observedAtMs: 100 }));
    expect(agg.snapshot(REGISTRY, 1000)).toBeNull();
  });

  it('全部样本过期后清理信标条目，返回 null', () => {
    const agg = new ScanWindowAggregator(2000);
    agg.push(sample({ minor: 1, observedAtMs: 100 }));
    agg.push(sample({ minor: 2, observedAtMs: 100 }));
    agg.push(sample({ minor: 3, observedAtMs: 100 }));
    expect(agg.snapshot(REGISTRY, 100)).not.toBeNull();
    expect(agg.snapshot(REGISTRY, 5000)).toBeNull(); // 样本全部过期
  });
});
