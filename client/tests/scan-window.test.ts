/**
 * ScanWindowAggregator 测试（C6）：窗口剔除、中位数、RSSI 截断、
 * 未登记三元组丢弃、不足 3 信标返回 null。
 * mock 数据使用真实 1 层信标 BK-01-001/004/005（uuid B9407F30-...）。
 */
import { BeaconRegistry } from '../core/ble/beacon-registry';
import {
  MIN_RESOLVED_BEACONS,
  ScanWindowAggregator,
  type BeaconSample,
} from '../core/ble/scan-window';
import { REAL_F1_TRIO, REAL_UUID } from './fixtures/real-map';

const UUID = REAL_UUID;
const CATALOG = REAL_F1_TRIO;
const [BK1, BK2, BK3] = CATALOG;

const REGISTRY = new BeaconRegistry(CATALOG);

function sample(overrides: Partial<BeaconSample>): BeaconSample {
  return { uuid: UUID, major: 1, minor: 10101, rssi: -60, observedAtMs: 1000, ...overrides };
}

describe('ScanWindowAggregator 窗口聚合', () => {
  it('每信标取窗口内 RSSI 中位数（奇数样本）', () => {
    const agg = new ScanWindowAggregator();
    agg.push(sample({ minor: 10101, rssi: -60, observedAtMs: 100 }));
    agg.push(sample({ minor: 10101, rssi: -70, observedAtMs: 200 }));
    agg.push(sample({ minor: 10101, rssi: -65, observedAtMs: 300 }));
    agg.push(sample({ minor: 10104, rssi: -80, observedAtMs: 150 }));
    agg.push(sample({ minor: 10105, rssi: -90, observedAtMs: 250 }));
    const result = agg.snapshot(REGISTRY, 1000);
    expect(result).not.toBeNull();
    const byId = new Map(result!.map((o) => [o.beaconId, o]));
    expect(byId.get(BK1.beaconId)?.rssi).toBe(-65);
    expect(byId.get(BK2.beaconId)?.rssi).toBe(-80);
    expect(byId.get(BK3.beaconId)?.rssi).toBe(-90);
  });

  it('偶数样本取中间两值平均', () => {
    const agg = new ScanWindowAggregator();
    agg.push(sample({ rssi: -60, observedAtMs: 100 }));
    agg.push(sample({ rssi: -70, observedAtMs: 200 }));
    agg.push(sample({ minor: 10104, observedAtMs: 100 }));
    agg.push(sample({ minor: 10105, observedAtMs: 100 }));
    const result = agg.snapshot(REGISTRY, 1000);
    const bk1 = result!.find((o) => o.beaconId === BK1.beaconId);
    expect(bk1?.rssi).toBe(-65);
  });

  it('RSSI 截断到 [-100,-20]，非数值丢弃', () => {
    const agg = new ScanWindowAggregator();
    agg.push(sample({ rssi: -120, observedAtMs: 100 })); // → -100
    agg.push(sample({ rssi: -5, observedAtMs: 200 })); // → -20
    agg.push(sample({ rssi: Number.NaN, observedAtMs: 300 })); // 丢弃
    agg.push(sample({ minor: 10104, observedAtMs: 100 }));
    agg.push(sample({ minor: 10105, observedAtMs: 100 }));
    const result = agg.snapshot(REGISTRY, 1000);
    expect(result).toEqual([
      expect.objectContaining({ beaconId: BK1.beaconId, rssi: -60 }),
      expect.objectContaining({ beaconId: BK2.beaconId }),
      expect.objectContaining({ beaconId: BK3.beaconId }),
    ]);
  });

  it('窗口外样本被剔除；observedAtMs 取窗口内最新', () => {
    const agg = new ScanWindowAggregator(2000);
    agg.push(sample({ rssi: -50, observedAtMs: 0 })); // 窗口外
    agg.push(sample({ rssi: -70, observedAtMs: 100 }));
    agg.push(sample({ rssi: -60, observedAtMs: 1500 }));
    agg.push(sample({ minor: 10104, observedAtMs: 100 }));
    agg.push(sample({ minor: 10105, observedAtMs: 100 }));
    const result = agg.snapshot(REGISTRY, 2000);
    expect(result).toEqual([
      expect.objectContaining({ beaconId: BK1.beaconId, rssi: -65, observedAtMs: 1500 }),
      expect.anything(),
      expect.anything(),
    ]);
    expect(result!.map((o) => o.beaconId)).toEqual(
      CATALOG.map((b) => b.beaconId),
    );
  });

  it('未登记三元组丢弃，不虚构 beaconId', () => {
    const agg = new ScanWindowAggregator();
    agg.push(sample({ minor: 10101, observedAtMs: 100 }));
    agg.push(sample({ minor: 99999, observedAtMs: 100 })); // 未登记
    agg.push(sample({ major: 99, observedAtMs: 100 })); // 未登记
    const result = agg.snapshot(REGISTRY, 1000);
    expect(result).toBeNull(); // 只剩 1 个可解析信标
  });

  it(`可解析信标不足 ${MIN_RESOLVED_BEACONS} 个返回 null`, () => {
    const agg = new ScanWindowAggregator();
    agg.push(sample({ minor: 10101, observedAtMs: 100 }));
    agg.push(sample({ minor: 10104, observedAtMs: 100 }));
    expect(agg.snapshot(REGISTRY, 1000)).toBeNull();
  });

  it('全部样本过期后清理信标条目，返回 null', () => {
    const agg = new ScanWindowAggregator(2000);
    agg.push(sample({ minor: 10101, observedAtMs: 100 }));
    agg.push(sample({ minor: 10104, observedAtMs: 100 }));
    agg.push(sample({ minor: 10105, observedAtMs: 100 }));
    expect(agg.snapshot(REGISTRY, 100)).not.toBeNull();
    expect(agg.snapshot(REGISTRY, 5000)).toBeNull(); // 样本全部过期
  });
});
