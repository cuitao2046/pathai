/**
 * 导出构造：schema 与独立工具严格一致（PC 侧工具链零改动）。
 * - 静态标定 = positioning_calibration_static schemaVersion 1.0（cstore.js 契约）
 * - 网格指纹 = ble_fingerprint_collection schemaVersion 1.1.0（fpstore.js 契约）
 * 新增可选字段（estimateAtStart/estimateAtEnd 等）仅为超集，PC 侧忽略未知字段。
 * 纯逻辑、不依赖 wx。
 */
import type {
  CalibrationPointRec,
  EstimateSnapshot,
  FpRecord,
  StaticSessionRec,
} from './types';
import type { FpRecordsStore, StaticPointsStore } from './points-store';

export interface CollectionExportMeta {
  venueId: string;
  venueName: string;
  operator: string;
  device: string | null;
  appVersion: string;
  mapVersion: string;
  /** 台账摘要（地图包未携带时为 null）。 */
  ledgerSha256: string | null;
}

interface StaticExportSession {
  sessionIndex: number;
  startedAt: string;
  durationS: number;
  trafficWindow: boolean;
  observations: StaticSessionRec['observations'];
  estimateAtStart?: EstimateSnapshot;
  estimateAtEnd?: EstimateSnapshot;
}

interface StaticExportPoint {
  pointId: string;
  floor: number;
  truth: [number, number];
  scene: string;
  envNote: string;
  markType: string;
  sessions: StaticExportSession[];
  /** 超集字段：标识点位来源（预置/补标），PC 侧忽略。 */
  custom?: boolean;
}

export interface StaticExportData {
  schemaVersion: '1.0';
  type: 'positioning_calibration_static';
  venueId: string;
  operator: string;
  device: string | null;
  appVersion: string;
  beaconLedgerSha256: string | null;
  mapVersion: string;
  windowMs: number;
  exportedAt: string;
  points: StaticExportPoint[];
}

export function buildStaticExport(
  store: StaticPointsStore,
  meta: CollectionExportMeta,
): { data: StaticExportData; warnings: string[] } {
  const warnings: string[] = [];
  const points = store.listPoints();
  if (points.length === 0) {
    warnings.push('还没有标定点');
  }
  const sessionsByPoint = new Map<string, StaticSessionRec[]>();
  for (const s of store.allSessions()) {
    const list = sessionsByPoint.get(s.pointId);
    if (list) {
      list.push(s);
    } else {
      sessionsByPoint.set(s.pointId, [s]);
    }
  }
  const outPoints: StaticExportPoint[] = points.map((p: CalibrationPointRec) => {
    const list = (sessionsByPoint.get(p.pointId) ?? []).sort(
      (a, b) => a.sessionIndex - b.sessionIndex,
    );
    if (list.length === 0) {
      warnings.push(`${p.pointId} 还没有采样会话`);
    } else if (list.length < 3) {
      warnings.push(`${p.pointId} 仅 ${list.length} 次，建议每点 3–5 次`);
    }
    return {
      pointId: p.pointId,
      floor: Number(p.floorId),
      truth: p.truth,
      scene: p.scene,
      envNote: p.label !== p.scene ? p.label : '',
      markType: 'map_tap',
      sessions: list.map((s) => ({
        sessionIndex: s.sessionIndex,
        startedAt: s.startedAt,
        durationS: s.durationS,
        trafficWindow: s.trafficWindow,
        observations: s.observations,
        ...(s.estimateAtStart ? { estimateAtStart: s.estimateAtStart } : {}),
        ...(s.estimateAtEnd ? { estimateAtEnd: s.estimateAtEnd } : {}),
      })),
      ...(p.custom ? { custom: true } : {}),
    };
  });
  return {
    data: {
      schemaVersion: '1.0',
      type: 'positioning_calibration_static',
      venueId: meta.venueId,
      operator: meta.operator,
      device: meta.device,
      appVersion: meta.appVersion,
      beaconLedgerSha256: meta.ledgerSha256,
      mapVersion: meta.mapVersion,
      windowMs: 2000,
      exportedAt: new Date().toISOString(),
      points: outPoints,
    },
    warnings,
  };
}

export interface FpExportData {
  schemaVersion: '1.1.0';
  type: 'ble_fingerprint_collection';
  venueId: string;
  venueName: string;
  dataset: string;
  exportedAt: string;
  appVersion: string;
  operator: string;
  device: string | null;
  beaconConfig: { uuid: string; majors: number[] };
  exportScope: 'all';
  zones: unknown[];
  recordCount: number;
  fpCount: number;
  records: FpRecord[];
}

export function buildFpExport(
  store: FpRecordsStore,
  meta: CollectionExportMeta,
  opts: { dataset?: string; beaconUuid: string; majors?: number[] },
): { data: FpExportData; warnings: string[] } {
  const records = store.allRecords();
  const warnings: string[] = [];
  if (records.length === 0) {
    warnings.push('还没有指纹记录');
  }
  const fpSet = new Set(records.map((r) => r.fpId));
  return {
    data: {
      schemaVersion: '1.1.0',
      type: 'ble_fingerprint_collection',
      venueId: meta.venueId,
      venueName: meta.venueName,
      dataset: opts.dataset ?? 'routes',
      exportedAt: new Date().toISOString(),
      appVersion: meta.appVersion,
      operator: meta.operator,
      device: meta.device,
      beaconConfig: { uuid: opts.beaconUuid, majors: opts.majors ?? [1, 2] },
      exportScope: 'all',
      zones: [],
      recordCount: records.length,
      fpCount: fpSet.size,
      records,
    },
    warnings,
  };
}
