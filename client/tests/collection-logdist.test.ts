/**
 * 对数等距测量页数据层：斜距、存储隔离、会话追加、导出 schema。
 * 断言与 spec（calibration-console §5）一致：deployable 恒 false。
 */
import {
  LogdistStore,
  LOGDIST_DEFAULT_INSTALL_M,
  slantDistanceM,
  stopIdFor,
  stopViews,
  type LogdistPersist,
} from '../core/collection/logdist-store';
import { buildLogdistExport } from '../core/collection/export-builder';
import { StaticSampler } from '../core/collection/static-sampler';
import type { StaticObservation } from '../core/collection/types';

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

function obs(rssi: number, distance: number): StaticObservation {
  return {
    beaconId: 'BK-01-007',
    n: 20,
    rssiMedian: rssi,
    rssiMin: rssi - 4,
    rssiMax: rssi + 3,
    rssiStd: 1.8,
    truthDistanceM: distance,
  };
}

describe('logdist 斜距与档位', () => {
  it('安装 2.5m 时 h=0 → 1.30m，h=18 → 18.05m', () => {
    expect(slantDistanceM(0, 2.5)).toBeCloseTo(1.3, 2);
    expect(slantDistanceM(18, 2.5)).toBeCloseTo(18.05, 2);
    expect(slantDistanceM(1.5, 2.5)).toBeCloseTo(1.985, 2);
  });

  it('档位编号按固定顺序稳定', () => {
    expect(stopIdFor(0)).toBe('LD-00');
    expect(stopIdFor(1.5)).toBe('LD-02');
    expect(stopIdFor(18)).toBe('LD-09');
  });

  it('修改安装高度实时反映到档位斜距', () => {
    const before = stopViews({ installHeightM: 2.5, stops: {} });
    const after = stopViews({ installHeightM: 4.0, stops: {} });
    expect(before[0].slantDistanceM).toBeCloseTo(1.3, 2);
    expect(after[0].slantDistanceM).toBeCloseTo(2.8, 2);
  });
});

describe('LogdistStore', () => {
  it('只持久化有会话的档位，captureIndex 递增且不覆盖', () => {
    const k = kv<LogdistPersist>();
    const store = new LogdistStore(k.load, k.save);
    expect(store.addSession('LD-00', 0, {
      startedAt: '2026-11-09T09:00:00Z',
      durationS: 60,
      installHeightM: 2.5,
      observations: [obs(-62, 1.3)],
    })?.captureIndex).toBe(1);
    store.addSession('LD-00', 0, {
      startedAt: '2026-11-09T09:02:00Z',
      durationS: 60,
      installHeightM: 2.5,
      observations: [obs(-63, 1.3)],
    });
    expect(store.sessionsFor('LD-00')).toHaveLength(2);
    expect(store.sessionsFor('LD-00')[1].captureIndex).toBe(2);
    const persisted = k.data()!;
    expect(Object.keys(persisted.stops)).toEqual(['LD-00']);
    expect(persisted.stops['LD-00'].sessions).toHaveLength(2);
  });

  it('空会话不落盘', () => {
    const k = kv<LogdistPersist>();
    const store = new LogdistStore(k.load, k.save);
    expect(store.addSession('LD-01', 1, {
      startedAt: '2026-11-09T09:00:00Z',
      durationS: 60,
      installHeightM: 2.5,
      observations: [],
    })).toBeNull();
    expect(store.stats().sessionCount).toBe(0);
  });

  it('显式选择信标后可切换；已有会话按采集时信标归属', () => {
    const k = kv<LogdistPersist>();
    const store = new LogdistStore(k.load, k.save);
    store.selectBeacon({ beaconId: 'BK-01-007', uuid: 'u', major: 1, minor: 7 });
    store.addSession('LD-00', 0, {
      startedAt: '2026-11-09T09:00:00Z',
      durationS: 60,
      installHeightM: 2.5,
      observations: [obs(-62, 1.3)],
    });
    store.selectBeacon({ beaconId: 'BK-01-008', uuid: 'u', major: 1, minor: 8 });
    expect(store.beacon?.beaconId).toBe('BK-01-008');
    const second = store.addSession('LD-01', 1, {
      startedAt: '2026-11-09T09:05:00Z',
      durationS: 60,
      installHeightM: 2.5,
      observations: [obs(-66, 1.3)],
    });
    expect(second?.beaconId).toBe('BK-01-008');
    expect(store.sessionsFor('LD-00')[0].beaconId).toBe('BK-01-007');
    expect(store.sessionCountForBeacon('BK-01-007')).toBe(1);
    expect(store.sessionCountForBeacon('BK-01-008')).toBe(1);
    store.clearAll();
    expect(store.beacon).toBeNull();
    expect(store.installHeightM).toBe(LOGDIST_DEFAULT_INSTALL_M);
  });
});

describe('buildLogdistExport', () => {
  it('独立 schema、deployable=false、按档位升序', () => {
    const k = kv<LogdistPersist>();
    const store = new LogdistStore(k.load, k.save);
    store.setInstallHeightM(2.5);
    store.selectBeacon({ beaconId: 'BK-01-007', uuid: 'u', major: 1, minor: 7 });
    store.addSession('LD-03', 2, {
      startedAt: '2026-11-09T09:00:00Z',
      durationS: 60,
      installHeightM: 2.5,
      observations: [obs(-70, 2.39)],
    });
    store.addSession('LD-00', 0, {
      startedAt: '2026-11-09T09:05:00Z',
      durationS: 30,
      installHeightM: 2.5,
      observations: [obs(-62, 1.3)],
    });
    const { data, warnings } = buildLogdistExport(store, {
      operator: '张三',
      device: { model: 'iPhone 15', system: 'iOS', platform: 'ios' } as never,
      appVersion: '1.0.0',
      mapVersion: '2026-11-08.2',
    });
    expect(data.type).toBe('positioning_calibration_logdist');
    expect(data.deployable).toBe(false);
    expect(data.stops.map((s) => s.stopId)).toEqual(['LD-00', 'LD-03']);
    expect(data.summary).toEqual({ stopCount: 2, sessionCount: 2, observationCount: 2 });
    expect(data.installHeightM).toBe(2.5);
    expect(data.phoneHeightM).toBe(1.2);
    expect(data.stops[0].slantDistanceM).toBeCloseTo(1.3, 2);
    expect(warnings.some((w) => w.includes('少于 3 个'))).toBe(true);
  });

  it('切换信标后只导出当前信标会话，并提示其他信标记录', () => {
    const k = kv<LogdistPersist>();
    const store = new LogdistStore(k.load, k.save);
    store.selectBeacon({ beaconId: 'BK-01-007', uuid: 'u', major: 1, minor: 7 });
    store.addSession('LD-00', 0, {
      startedAt: '2026-11-09T09:00:00Z',
      durationS: 60,
      installHeightM: 2.5,
      observations: [obs(-62, 1.3)],
    });
    store.selectBeacon({ beaconId: 'BK-01-008', uuid: 'u', major: 1, minor: 8 });
    store.addSession('LD-01', 1, {
      startedAt: '2026-11-09T09:05:00Z',
      durationS: 60,
      installHeightM: 2.5,
      observations: [obs(-66, 1.3)],
    });
    const { data, warnings } = buildLogdistExport(store, {
      operator: '张三',
      device: null,
      appVersion: '1.0.0',
      mapVersion: '2026-11-08.2',
    });
    expect(data.beacon?.beaconId).toBe('BK-01-008');
    expect(data.stops.map((s) => s.stopId)).toEqual(['LD-01']);
    expect(data.stops[0].sessions[0].beaconId).toBe('BK-01-008');
    expect(data.summary).toEqual({ stopCount: 1, sessionCount: 1, observationCount: 1 });
    expect(warnings.some((w) => w.includes('其他信标'))).toBe(true);
  });

  it('无记录时给出提示', () => {
    const k = kv<LogdistPersist>();
    const store = new LogdistStore(k.load, k.save);
    const { warnings } = buildLogdistExport(store, {
      operator: '',
      device: null,
      appVersion: '1.0.0',
      mapVersion: null,
    });
    expect(warnings[0]).toContain('还没有');
  });
});

describe('StaticSampler 固定斜距 / 单信标', () => {
  it('只统计锁定信标，并使用固定斜距', () => {
    const sampler = new StaticSampler({
      truth: [0, 0],
      resolve: (_uuid, _major, minor) =>
        minor === 7
          ? { beaconId: 'BK-01-007', x: 100, y: 100, installHeightM: 2.5 }
          : { beaconId: 'BK-01-008', x: 0, y: 0, installHeightM: 2.5 },
      fixedTruthDistanceM: 1.3,
      onlyBeaconId: 'BK-01-007',
    });
    sampler.push({ uuid: 'U', major: 1, minor: 7, rssi: -60, observedAtMs: 1 });
    sampler.push({ uuid: 'U', major: 1, minor: 7, rssi: -62, observedAtMs: 2 });
    sampler.push({ uuid: 'U', major: 1, minor: 8, rssi: -55, observedAtMs: 3 });
    const rows = sampler.observations();
    expect(rows).toHaveLength(1);
    expect(rows[0].beaconId).toBe('BK-01-007');
    expect(rows[0].n).toBe(2);
    expect(rows[0].rssiMedian).toBe(-61);
    expect(rows[0].truthDistanceM).toBe(1.3);
  });
});
