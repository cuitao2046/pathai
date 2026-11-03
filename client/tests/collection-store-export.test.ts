/**
 * 点位存储与导出构造：断言与两个独立工具的导出 envelope 完全一致
 * （positioning_calibration_static 1.0 / ble_fingerprint_collection 1.1.0）。
 */
import { StaticPointsStore, FpRecordsStore, type StaticPointsPersist } from '../core/collection/points-store';
import { buildStaticExport, buildFpExport } from '../core/collection/export-builder';
import { buildFpRecord } from '../core/collection/fp-snapshot';
import type { StaticSessionRec } from '../core/collection/types';
import type { PresetCalibrationPoint } from '../data/calibration_points';
import type { StaticObservation } from '../core/collection/types';

const PRESET: PresetCalibrationPoint[] = [
  { pointId: 'CAL-F1-01', floorId: '1', truth: [-10, 5], scene: 'intersection', batch: 1, nearGridId: 'FP-1-R001' },
  { pointId: 'CAL-F2-01', floorId: '2', truth: [-30, -72], scene: 'doorway', batch: 1, nearGridId: 'FP-2-R001' },
];

function kv<T>(): { load: () => T | null; save: (d: T) => void; data: () => T | null } {
  let state: T | null = null;
  return {
    load: () => state,
    save: (d) => {
      state = d;
    },
    data: () => state,
  };
}

function obs(beaconId: string, rssi: number): StaticObservation {
  return { beaconId, n: 10, rssiMedian: rssi, rssiMin: rssi - 5, rssiMax: rssi + 3, rssiStd: 2.1, truthDistanceM: 5.31 };
}

function session(_pointId: string, index: number): Omit<StaticSessionRec, 'pointId' | 'sessionIndex'> {
  return {
    startedAt: `2026-11-05T09:1${index}:00Z`,
    durationS: 60,
    trafficWindow: false,
    observations: [obs('BK-A', -60)],
    estimateAtStart: { x: -9.8, y: 5.2, floorId: '1', quality: 'reliable', confidence: 0.9 },
  };
}

describe('StaticPointsStore', () => {
  it('预置点位列出、隐藏与恢复', () => {
    const k = kv<StaticPointsPersist>();
    const store = new StaticPointsStore(PRESET, k.load, k.save);
    expect(store.listPoints().map((p) => p.pointId)).toEqual(['CAL-F1-01', 'CAL-F2-01']);
    store.setPresetHidden('CAL-F1-01', true);
    expect(store.listPoints().map((p) => p.pointId)).toEqual(['CAL-F2-01']);
    store.setPresetHidden('CAL-F1-01', false);
    expect(store.listPoints()).toHaveLength(2);
  });

  it('补标点可增删，预置点不可删', () => {
    const k = kv<StaticPointsPersist>();
    const store = new StaticPointsStore(PRESET, k.load, k.save);
    const c = store.addCustomPoint([1.23, 4.56], '1', '我的点');
    expect(c.custom).toBe(true);
    expect(c.scene).toBe('custom');
    expect(c.pointId).toMatch(/^CUS-1-/);
    expect(store.listPoints()).toHaveLength(3);
    expect(store.removeCustomPoint('CAL-F1-01')).toBe(false);
    expect(store.removeCustomPoint(c.pointId)).toBe(true);
    expect(store.listPoints()).toHaveLength(2);
  });

  it('会话追加制：同点多会话 sessionIndex 递增、互不覆盖', () => {
    const k = kv<StaticPointsPersist>();
    const store = new StaticPointsStore(PRESET, k.load, k.save);
    const a1 = store.addSession('CAL-F1-01', session('CAL-F1-01', 0));
    const a2 = store.addSession('CAL-F1-01', session('CAL-F1-01', 1));
    const b1 = store.addSession('CAL-F2-01', session('CAL-F2-01', 0));
    expect([a1.sessionIndex, a2.sessionIndex, b1.sessionIndex]).toEqual([1, 2, 1]);
    expect(store.sessionsFor('CAL-F1-01')).toHaveLength(2);
    expect(store.allSessions()).toHaveLength(3);
    store.removeSession('CAL-F1-01', 1);
    expect(store.sessionsFor('CAL-F1-01').map((s) => s.sessionIndex)).toEqual([2]);
    expect(store.stats()).toEqual({ pointCount: 2, sampledPointCount: 2, sessionCount: 2, beaconPairs: 2 });
  });
});

describe('buildStaticExport（positioning_calibration_static 1.0 契约）', () => {
  it('envelope 字段与 cstore.js buildExport 一致，floor 为数字，含超集可选字段', () => {
    const k = kv<StaticPointsPersist>();
    const store = new StaticPointsStore(PRESET, k.load, k.save);
    store.addSession('CAL-F1-01', session('CAL-F1-01', 0));
    const { data, warnings } = buildStaticExport(store, {
      venueId: 'school-building-01',
      venueName: '初中学部 1# 教学楼',
      operator: '张三',
      device: 'dev-01',
      appVersion: '0.4.0',
      mapVersion: '2026-11-03.1',
      ledgerSha256: null,
    });
    expect(data.schemaVersion).toBe('1.0');
    expect(data.type).toBe('positioning_calibration_static');
    expect(data.venueId).toBe('school-building-01');
    expect(data.operator).toBe('张三');
    expect(data.windowMs).toBe(2000);
    expect(data.mapVersion).toBe('2026-11-03.1');
    expect(data.exportedAt).toMatch(/^\d{4}-\d{2}-\d{2}T/);
    const p = data.points[0];
    expect(p.floor).toBe(1); // 数字（非 floorId 字符串）
    expect(p.scene).toBe('intersection');
    expect(p.sessions[0].sessionIndex).toBe(1);
    expect(p.sessions[0].estimateAtStart).toEqual({
      x: -9.8, y: 5.2, floorId: '1', quality: 'reliable', confidence: 0.9,
    });
    expect(p.custom).toBeUndefined();
    // 未采样的点给 warning（与独立工具口径一致）
    expect(warnings.some((w) => w.includes('CAL-F2-01'))).toBe(true);
  });
});

describe('buildFpExport（ble_fingerprint_collection 1.1.0 契约）', () => {
  it('envelope 字段与 fpstore.js buildExport 一致', () => {
    let saved: ReturnType<FpRecordsStore['allRecords']> | null = null;
    const store = new FpRecordsStore(
      () => saved,
      (d) => {
        saved = d;
      },
    );
    const base = buildFpRecord({
      point: { fpId: 'FP-1-001', floorId: '1', x: 1, y: 2, regionType: 'normal' },
      window: [],
      lookup: () => null,
      rssiAt1m: -71,
    });
    store.addRecord(base);
    const { data, warnings } = buildFpExport(
      store,
      {
        venueId: 'school-building-01',
        venueName: '初中学部 1# 教学楼',
        operator: '张三',
        device: null,
        appVersion: '0.4.0',
        mapVersion: '2026-11-03.1',
        ledgerSha256: null,
      },
      { dataset: 'routes', beaconUuid: 'B9407F30-F5F8-466E-AFF9-25556B57FE6D' },
    );
    expect(data.schemaVersion).toBe('1.1.0');
    expect(data.type).toBe('ble_fingerprint_collection');
    expect(data.dataset).toBe('routes');
    expect(data.exportScope).toBe('all');
    expect(data.zones).toEqual([]);
    expect(data.beaconConfig).toEqual({
      uuid: 'B9407F30-F5F8-466E-AFF9-25556B57FE6D',
      majors: [1, 2],
    });
    expect(data.recordCount).toBe(1);
    expect(data.fpCount).toBe(1);
    expect(data.records[0].captureIndex).toBe(1);
    expect(warnings).toEqual([]);
  });
});
