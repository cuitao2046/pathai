/**
 * 指纹快照构造：与 fingerprint-collector fpstore.addSample 契约对齐。
 */
import { buildFpRecord, type FpBeaconLookup } from '../core/collection/fp-snapshot';
import { FpRecordsStore } from '../core/collection/points-store';
import type { FpRecord } from '../core/collection/types';
import type { ResolvedObservation } from '../core/ble/scan-window';

const RSSI_AT_1M = -71;

const lookup: FpBeaconLookup = (beaconId) => {
  if (beaconId === 'BK-A') {
    return { uuid: 'uuid-a', major: 1, minor: 7 };
  }
  if (beaconId === 'BK-B') {
    return { uuid: 'uuid-a', major: 1, minor: 8 };
  }
  return null;
};

function window(): ResolvedObservation[] {
  return [
    { beaconId: 'BK-A', rssi: -59, observedAtMs: 2_000 },
    { beaconId: 'BK-B', rssi: -71, observedAtMs: 2_000 },
    { beaconId: 'BK-UNKNOWN', rssi: -60, observedAtMs: 2_000 },
  ];
}

const point = { fpId: 'FP-1-001', floorId: '1', x: -30.5, y: 8.25, regionType: 'normal' };

describe('buildFpRecord', () => {
  it('契约字段与 fpstore.addSample 一致（分区恒 null，spec D5）', () => {
    const rec = buildFpRecord({ point, window: window(), lookup, rssiAt1m: RSSI_AT_1M, collectedAtMs: 5_000 });
    expect(rec.fpId).toBe('FP-1-001');
    expect(rec.floor).toBe(1);
    expect(rec.coordinates).toEqual([-30.5, 8.25]);
    expect(rec.relCoordinates).toBeNull();
    expect(rec.zoneId).toBeNull();
    expect(rec.zoneName).toBeNull();
    expect(rec.anchor).toBeNull();
    expect(rec.regionType).toBe('normal');
    expect(rec.collectedAt).toBe(new Date(5_000).toISOString());
    expect(Object.keys(rec).sort()).toEqual(
      [
        'anchor', 'beaconCount', 'beacons', 'captureIndex', 'collectedAt', 'coordinates',
        'fpId', 'floor', 'regionType', 'relCoordinates', 'zoneId', 'zoneName',
      ].sort(),
    );
  });

  it('逐信标字段、从强到弱排序、accuracy 按路径损耗反推', () => {
    const rec = buildFpRecord({ point, window: window(), lookup, rssiAt1m: RSSI_AT_1M });
    expect(rec.beaconCount).toBe(2); // 未登记三元组丢弃
    expect(rec.beacons.map((b) => b.minor)).toEqual([7, 8]); // -59 强于 -71
    const a = rec.beacons[0];
    expect(a.uuid).toBe('uuid-a');
    expect(a.major).toBe(1);
    expect(a.rssi).toBe(-59);
    expect(a.txPower).toBe(RSSI_AT_1M);
    expect(a.accuracy).toBeCloseTo(Math.pow(10, (-71 - -59) / 20), 2);
  });

  it('captureIndex 由 FpRecordsStore 落库时按点位递增', () => {
    let saved: FpRecord[] | null = null;
    const store = new FpRecordsStore(
      () => saved,
      (d) => {
        saved = d;
      },
    );
    const base = buildFpRecord({ point, window: window(), lookup, rssiAt1m: RSSI_AT_1M });
    const r1 = store.addRecord(base);
    const r2 = store.addRecord({ ...base, collectedAt: new Date(6_000).toISOString() });
    expect(r1.captureIndex).toBe(1);
    expect(r2.captureIndex).toBe(2);
    expect(store.stats()).toEqual({ recordCount: 2, fpCount: 1, beaconSampleSum: 4 });
    store.removeRecord(r1.fpId, r1.collectedAt);
    expect(store.recordsFor(r1.fpId)).toHaveLength(1);
  });
});
