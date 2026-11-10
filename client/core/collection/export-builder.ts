/**
 * 导出构造：schema 与独立工具严格一致（PC 侧工具链零改动）。
 * - 静态标定 = positioning_calibration_static schemaVersion 1.0（cstore.js 契约）
 * - 网格指纹 = ble_fingerprint_collection schemaVersion 1.2.0（fpstore.js 契约；含 deletions/mode）
 * 新增可选字段（estimateAtStart/estimateAtEnd 等）仅为超集，PC 侧忽略未知字段。
 * 纯逻辑、不依赖 wx。
 */
import type {
  CalibrationPointRec,
  EstimateSnapshot,
  FpDeletion,
  FpRecord,
  StaticSessionRec,
} from './types';
import type { FpRecordsStore, StaticPointsStore } from './points-store';
import type { LogdistBeacon, LogdistSession, LogdistStore } from './logdist-store';

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

export interface LogdistExportMeta {
  operator: string;
  device: string | null;
  appVersion: string;
  /** 地图包版本未加载时为 null（导出仍可用）。 */
  mapVersion: string | null;
}

interface LogdistExportStop {
  stopId: string;
  horizontalDistanceM: number;
  slantDistanceM: number;
  sessions: LogdistSession[];
}

export interface LogdistExportData {
  schemaVersion: '1.0';
  type: 'positioning_calibration_logdist';
  /** 阶段一专用：永远不可部署。 */
  deployable: false;
  exportedAt: string;
  appVersion: string;
  operator: string;
  device: string | null;
  mapVersion: string | null;
  beacon: LogdistBeacon | null;
  installHeightM: number;
  phoneHeightM: number;
  model: { form: 'log_distance'; expression: string };
  stops: LogdistExportStop[];
  summary: { stopCount: number; sessionCount: number; observationCount: number };
}

/**
 * 对数等距导出：与静态标定完全独立 schema，deployable 恒为 false。
 * 斜距直接取会话观测里的 truthDistanceM（采集时按档位与安装高度快照计算）。
 * 只导出当前选中信标的会话：切换信标后各自导出，避免一个文件混入多枚信标
 * （fit_location_params.py 对单文件只允许一枚信标）。
 */
export function buildLogdistExport(
  store: LogdistStore,
  meta: LogdistExportMeta,
): { data: LogdistExportData; warnings: string[] } {
  const warnings: string[] = [];
  const total = store.stats();
  if (total.stopCount === 0) {
    warnings.push('还没有任何档位会话');
  }

  const selectedId = store.beacon?.beaconId ?? null;
  let omittedSessions = 0;
  const views = store.views();
  const stops: LogdistExportStop[] = store
    .allStops()
    .map((rec) => {
      const sessions =
        selectedId === null
          ? rec.sessions
          : rec.sessions.filter((s) => s.beaconId === selectedId);
      omittedSessions += rec.sessions.length - sessions.length;
      if (sessions.length === 0) {
        return null;
      }
      const view = views.find((v) => v.horizontalDistanceM === rec.horizontalDistanceM);
      return {
        stopId: rec.stopId,
        horizontalDistanceM: rec.horizontalDistanceM,
        slantDistanceM: view?.slantDistanceM ?? 0,
        sessions,
      };
    })
    .filter((rec): rec is LogdistExportStop => rec !== null);

  const summary = stops.reduce(
    (acc, stop) => ({
      stopCount: acc.stopCount + 1,
      sessionCount: acc.sessionCount + stop.sessions.length,
      observationCount:
        acc.observationCount +
        stop.sessions.reduce((n, session) => n + session.observations.length, 0),
    }),
    { stopCount: 0, sessionCount: 0, observationCount: 0 },
  );

  if (selectedId !== null && total.stopCount > 0 && summary.sessionCount === 0) {
    warnings.push(`当前信标 ${selectedId} 还没有档位会话`);
  }
  if (summary.stopCount > 0 && summary.stopCount < 3) {
    warnings.push('已采档位少于 3 个，A/n 拟合不稳定');
  }
  if (summary.stopCount > 0 && summary.sessionCount < summary.stopCount * 3) {
    warnings.push('部分档位会话少于 3 次，建议每档 3 次以上');
  }
  if (omittedSessions > 0) {
    warnings.push(
      `另有 ${omittedSessions} 次会话属于其他信标，未包含在本次导出；请切换信标后单独导出`,
    );
  }

  return {
    data: {
      schemaVersion: '1.0',
      type: 'positioning_calibration_logdist',
      deployable: false,
      exportedAt: new Date().toISOString(),
      appVersion: meta.appVersion,
      operator: meta.operator,
      device: meta.device,
      mapVersion: meta.mapVersion,
      beacon: store.beacon,
      installHeightM: store.installHeightM,
      phoneHeightM: store.phoneHeightM,
      model: {
        form: 'log_distance',
        expression: 'RSSI = A - 10 * n * log10(d)',
      },
      stops,
      summary,
    },
    warnings,
  };
}

export interface FpExportData {
  schemaVersion: '1.2.0';
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
  /** 显式删除墓碑：仅本地真实删除的记录，服务端据此生成新版本。 */
  deletions: FpDeletion[];
  /** 入库模式：merge（默认，只增改）/ replaceFloor（显式整层重采）。 */
  mode: 'merge' | 'replaceFloor';
  /** replaceFloor 的作用楼层（mode=merge 时缺省）。 */
  replaceFloors?: string[];
}

export function buildFpExport(
  store: FpRecordsStore,
  meta: CollectionExportMeta,
  opts: {
    dataset?: string;
    beaconUuid: string;
    majors?: number[];
    /** 待提交的删除墓碑（默认无）。 */
    deletions?: FpDeletion[];
    /** 缺省 merge：普通入库永远不推断删除。 */
    mode?: 'merge' | 'replaceFloor';
    replaceFloors?: string[];
  },
): { data: FpExportData; warnings: string[] } {
  const records = store.allRecords();
  const warnings: string[] = [];
  if (records.length === 0) {
    warnings.push('还没有指纹记录');
  }
  const fpSet = new Set(records.map((r) => r.fpId));
  const mode = opts.mode ?? 'merge';
  // 服务端契约：replaceFloor 是整层作用域，已含删除语义，不接受同时提交 deletions
  // （fingerprint_library._parse_content → INVALID_MODE_COMBINATION）。
  // 墓碑仍留在本地，下次 merge 入库时再提交，不做静默丢弃。
  const deletions = mode === 'replaceFloor' ? [] : (opts.deletions ?? []);
  return {
    data: {
      schemaVersion: '1.2.0',
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
      deletions,
      mode,
      ...(mode === 'replaceFloor' ? { replaceFloors: opts.replaceFloors ?? [] } : {}),
    },
    warnings,
  };
}
