import { buildFpRecord } from '../core/collection/fp-snapshot';
import { FpRecordsStore } from '../core/collection/points-store';
import { buildFpExport } from '../core/collection/export-builder';
import type { FpRecord } from '../core/collection/types';

const window = [
  { beaconId: 'BK-01-001', rssi: -60, observedAtMs: 1 },
  { beaconId: 'BK-01-002', rssi: -66, observedAtMs: 1 },
  { beaconId: 'BK-01-003', rssi: -72, observedAtMs: 1 },
];

const lookup = (beaconId: string) => ({
  uuid: '0000feaa-0000-1000-8000-00805f9b34fb',
  major: 1,
  minor: Number(beaconId.slice(-3)),
});

const point = { fpId: 'F1-C001-R001', floorId: '1', x: 1, y: 2, regionType: 'normal' };

describe('指纹记录版本标记（C8 / P2）', () => {
  it('新记录写入采集时的 gridVersion / paramsVersion', () => {
    const rec = buildFpRecord({
      point,
      window,
      lookup,
      rssiAt1m: -59,
      gridVersion: 'g-20261109-abcdef',
      paramsVersion: 'loc-20261109-abcd',
    });
    expect(rec.gridVersion).toBe('g-20261109-abcdef');
    expect(rec.paramsVersion).toBe('loc-20261109-abcd');
  });

  it('未提供版本时不写入字段（旧记录语义，不回填）', () => {
    const rec = buildFpRecord({ point, window, lookup, rssiAt1m: -59 });
    expect('gridVersion' in rec).toBe(false);
    expect('paramsVersion' in rec).toBe(false);
  });

  it('paramsVersion 未知时只写网格版本', () => {
    const rec = buildFpRecord({
      point,
      window,
      lookup,
      rssiAt1m: -59,
      gridVersion: '11.1.0',
    });
    expect(rec.gridVersion).toBe('11.1.0');
    expect('paramsVersion' in rec).toBe(false);
  });

  it('导出原样保留版本字段；旧记录仍可导出且不补字段', () => {
    const saved: FpRecord[] = [];
    const store = new FpRecordsStore(
      () => null,
      (data) => saved.splice(0, saved.length, ...data),
    );
    store.addRecord(
      buildFpRecord({
        point,
        window,
        lookup,
        rssiAt1m: -59,
        gridVersion: 'g-1',
        paramsVersion: 'loc-1',
      }),
    );
    store.addRecord(
      buildFpRecord({
        point: { ...point, fpId: 'F1-C002-R001' },
        window,
        lookup,
        rssiAt1m: -59,
      }),
    );
    const { data } = buildFpExport(
      store,
      {
        venueId: 'demo-site',
        venueName: '演示场地',
        operator: 'tester',
        device: null,
        appVersion: '1.0.0',
        mapVersion: 'm-1',
        ledgerSha256: null,
      },
      { beaconUuid: '0000feaa-0000-1000-8000-00805f9b34fb', majors: [1] },
    );
    expect(data.records).toHaveLength(2);
    expect(data.records[0].gridVersion).toBe('g-1');
    expect(data.records[0].paramsVersion).toBe('loc-1');
    expect('gridVersion' in data.records[1]).toBe(false);
    expect('paramsVersion' in data.records[1]).toBe(false);
  });
});
