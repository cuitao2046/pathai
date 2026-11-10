/**
 * 对数等距测量数据层（spec：calibration-console §5）。
 * - 10 个固定水平距离档位，安装高度可调，真值距离由档位与安装高度直接计算；
 * - 存储键 col:logdist:v1，只持久化“有会话”的档位；
 * - 会话追加制（同档多次采集互不覆盖），captureIndex 仅展示；
 * - 纯逻辑、不依赖 wx，可 jest 直测。
 */
import { PHONE_HEIGHT_M } from '../../config/env';
import type { StaticObservation } from './types';

/** 固定水平距离档位（米）。 */
export const LOGDIST_STOPS_M = [0, 1, 1.5, 2, 3, 4.5, 6.5, 9, 13, 18] as const;

export const LOGDIST_INSTALL_MIN_M = 2.0;
export const LOGDIST_INSTALL_MAX_M = 4.0;
export const LOGDIST_INSTALL_STEP_M = 0.1;
export const LOGDIST_DEFAULT_INSTALL_M = 2.5;

/** 导出/存储 schema 版本。 */
export const LOGDIST_SCHEMA_VERSION = 1;

export interface LogdistBeacon {
  beaconId: string;
  uuid: string;
  major: number;
  minor: number;
}

export interface LogdistSession {
  startedAt: string;
  durationS: number;
  captureIndex: number;
  /** 采集时选中的信标；切换信标后按会话归属，导出不串。 */
  beaconId: string;
  /** 采集时的安装高度快照；页面后续修改不回溯。 */
  installHeightM: number;
  observations: StaticObservation[];
}

export interface LogdistStopRecord {
  horizontalDistanceM: number;
  sessions: LogdistSession[];
}

export interface LogdistPersist {
  schemaVersion: number;
  installHeightM: number;
  phoneHeightM: number;
  beacon: LogdistBeacon | null;
  stops: Record<string, LogdistStopRecord>;
}

export interface LogdistStopView {
  stopId: string;
  horizontalDistanceM: number;
  slantDistanceM: number;
  sessionCount: number;
  sampled: boolean;
}

function round2(value: number): number {
  return Math.round(value * 100) / 100;
}

function clampInstallHeight(value: number): number {
  const stepped = Math.round(value / LOGDIST_INSTALL_STEP_M) * LOGDIST_INSTALL_STEP_M;
  return round2(
    Math.min(LOGDIST_INSTALL_MAX_M, Math.max(LOGDIST_INSTALL_MIN_M, stepped)),
  );
}

/** 档位编号按固定档位顺序生成，与水平距离绑定（LD-00 … LD-09）。 */
export function stopIdFor(horizontalDistanceM: number): string {
  const index = LOGDIST_STOPS_M.findIndex((h) => h === horizontalDistanceM);
  const i = index >= 0 ? index : 0;
  return `LD-${String(i).padStart(2, '0')}`;
}

/** 斜距 d = √(h² + (installHeight − 1.2)²)，手机高固定 1.2 m。 */
export function slantDistanceM(
  horizontalDistanceM: number,
  installHeightM: number,
  phoneHeightM: number = PHONE_HEIGHT_M,
): number {
  const dz = installHeightM - phoneHeightM;
  return Math.hypot(horizontalDistanceM, dz);
}

/** 页面档位视图：水平距离 + 实时斜距 + 已采会话数。 */
export function stopViews(
  persist: Pick<LogdistPersist, 'installHeightM' | 'stops'>,
): LogdistStopView[] {
  return LOGDIST_STOPS_M.map((h) => {
    const stopId = stopIdFor(h);
    const rec = persist.stops[stopId];
    return {
      stopId,
      horizontalDistanceM: h,
      slantDistanceM: round2(slantDistanceM(h, persist.installHeightM)),
      sessionCount: rec?.sessions.length ?? 0,
      sampled: (rec?.sessions.length ?? 0) > 0,
    };
  });
}

function emptyPersist(): LogdistPersist {
  return {
    schemaVersion: LOGDIST_SCHEMA_VERSION,
    installHeightM: LOGDIST_DEFAULT_INSTALL_M,
    phoneHeightM: PHONE_HEIGHT_M,
    beacon: null,
    stops: {},
  };
}

/**
 * 旧记录（schema v1 早期）会话没有 beaconId：用文件级 beacon 回填，
 * 避免升级后导出归属丢失；新会话一律写采集时的选中信标。
 */
function normalizeStops(
  stops: Record<string, LogdistStopRecord>,
  fallbackBeaconId: string,
): Record<string, LogdistStopRecord> {
  const out: Record<string, LogdistStopRecord> = {};
  for (const [stopId, rec] of Object.entries(stops)) {
    out[stopId] = {
      horizontalDistanceM: rec.horizontalDistanceM,
      sessions: rec.sessions.map((s) => ({
        ...s,
        beaconId: typeof s.beaconId === 'string' ? s.beaconId : fallbackBeaconId,
      })),
    };
  }
  return out;
}

export type KvLoad<T> = () => T | null;
export type KvSave<T> = (data: T) => void;

export class LogdistStore {
  private data: LogdistPersist;

  constructor(
    load: KvLoad<LogdistPersist>,
    private save: KvSave<LogdistPersist>,
  ) {
    const loaded = load();
    this.data =
      loaded !== null && loaded.schemaVersion === LOGDIST_SCHEMA_VERSION && loaded.stops
        ? {
            schemaVersion: LOGDIST_SCHEMA_VERSION,
            installHeightM: loaded.installHeightM,
            phoneHeightM: loaded.phoneHeightM ?? PHONE_HEIGHT_M,
            beacon: loaded.beacon ?? null,
            stops: normalizeStops(loaded.stops, loaded.beacon?.beaconId ?? ''),
          }
        : emptyPersist();
  }

  private commit(): void {
    this.save(this.data);
  }

  get installHeightM(): number {
    return this.data.installHeightM;
  }

  /** 修改安装高度：只影响后续会话，已采会话保留各自快照。 */
  setInstallHeightM(value: number): number {
    this.data.installHeightM = clampInstallHeight(value);
    this.commit();
    return this.data.installHeightM;
  }

  get beacon(): LogdistBeacon | null {
    return this.data.beacon;
  }

  /**
   * 显式选择/切换信标（页面不自动锁定）。
   * 记录按会话归属，切换信标不会改写已有会话的 beaconId。
   */
  selectBeacon(beacon: LogdistBeacon | null): void {
    this.data.beacon = beacon === null ? null : { ...beacon };
    this.commit();
  }

  /** 追加一次会话；captureIndex = 该档已有会话数 + 1。 */
  addSession(
    stopId: string,
    horizontalDistanceM: number,
    session: Omit<LogdistSession, 'captureIndex' | 'beaconId'>,
  ): LogdistSession | null {
    if (session.observations.length === 0) {
      return null;
    }
    const stop = this.data.stops[stopId] ?? {
      horizontalDistanceM,
      sessions: [],
    };
    const rec: LogdistSession = {
      ...session,
      beaconId: this.data.beacon?.beaconId ?? '',
      captureIndex: stop.sessions.length + 1,
    };
    stop.sessions.push(rec);
    this.data.stops[stopId] = stop;
    this.commit();
    return rec;
  }

  /** 指定信标在本页的记录次数（切换信标后状态行使用）。 */
  sessionCountForBeacon(beaconId: string): number {
    let count = 0;
    for (const rec of Object.values(this.data.stops)) {
      for (const session of rec.sessions) {
        if (session.beaconId === beaconId) {
          count += 1;
        }
      }
    }
    return count;
  }

  sessionsFor(stopId: string): LogdistSession[] {
    return [...(this.data.stops[stopId]?.sessions ?? [])];
  }

  get phoneHeightM(): number {
    return this.data.phoneHeightM;
  }

  allStops(): Array<LogdistStopRecord & { stopId: string }> {
    return Object.entries(this.data.stops)
      .filter(([, rec]) => rec.sessions.length > 0)
      .sort(([a], [b]) => a.localeCompare(b))
      .map(([stopId, rec]) => ({ ...rec, stopId }));
  }

  views(): LogdistStopView[] {
    return stopViews(this.data);
  }

  stats(): { stopCount: number; sessionCount: number; observationCount: number } {
    let stopCount = 0;
    let sessionCount = 0;
    let observationCount = 0;
    for (const rec of Object.values(this.data.stops)) {
      if (rec.sessions.length === 0) {
        continue;
      }
      stopCount += 1;
      for (const s of rec.sessions) {
        sessionCount += 1;
        observationCount += s.observations.length;
      }
    }
    return { stopCount, sessionCount, observationCount };
  }

  clearAll(): void {
    this.data = emptyPersist();
    this.commit();
  }
}
