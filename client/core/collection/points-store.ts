/**
 * 现场采集本地存储：点位/会话/指纹记录（kv 注入式，wx 依赖留在页面层）。
 * 记录规则（spec §3.5/§4.3）：
 * - 静态标定：会话追加制——同点多会话互不覆盖，sessionIndex = 该点当前会话数 + 1；
 * - 网格指纹：记录追加制——扁平数组，captureIndex = 该点当前记录数 + 1。
 * 纯逻辑、不依赖 wx，可 jest 直测。
 */
import type { CalibrationPointRec, FpRecord, StaticSessionRec } from './types';
import type { PresetCalibrationPoint } from '../../data/calibration_points';

export type KvLoad<T> = () => T | null;
/** 返回 false 表示只写进了会话内存、未真正落盘（调用方需回滚/告警）。 */
export type KvSave<T> = (data: T) => boolean | void;

/** 指纹记录落盘失败（storage 满/不可用）；调用方应提示现场先导出并清理。 */
export class FpRecordsPersistError extends Error {
  constructor() {
    super('指纹记录写入本地存储失败');
    this.name = 'FpRecordsPersistError';
  }
}

interface StaticPersist {
  /** 现场补标点（预置点来自数据文件，不落盘）。 */
  customPoints: CalibrationPointRec[];
  /** 被隐藏的预置点位。 */
  hiddenPreset: string[];
  sessions: StaticSessionRec[];
}

export type StaticPointsPersist = StaticPersist;

export class StaticPointsStore {
  private readonly preset: PresetCalibrationPoint[];
  private data: StaticPersist;

  constructor(
    preset: PresetCalibrationPoint[],
    load: KvLoad<StaticPersist>,
    private save: KvSave<StaticPersist>,
  ) {
    this.preset = preset;
    this.data = load() ?? { customPoints: [], hiddenPreset: [], sessions: [] };
  }

  private commit(): void {
    this.save(this.data);
  }

  /** 全部可用点位（预置 + 补标，隐藏的预置点默认不返回）。 */
  listPoints(): CalibrationPointRec[] {
    const preset: CalibrationPointRec[] = this.preset.map((p) => ({
      pointId: p.pointId,
      floorId: p.floorId,
      truth: p.truth,
      scene: p.scene,
      label: p.scene,
      custom: false,
      hidden: this.data.hiddenPreset.includes(p.pointId) || undefined,
    }));
    return [...preset, ...this.data.customPoints].filter((p) => !p.hidden);
  }

  /** 现场补标：真值坐标取长按处地图坐标。 */
  addCustomPoint(truth: [number, number], floorId: string, label: string): CalibrationPointRec {
    const pointId = `CUS-${floorId}-${Date.now().toString(36)}`;
    const rec: CalibrationPointRec = {
      pointId,
      floorId,
      truth,
      scene: 'custom',
      label: label || '补标点',
      custom: true,
    };
    this.data.customPoints.push(rec);
    this.commit();
    return rec;
  }

  /** 仅自定义点可删。 */
  removeCustomPoint(pointId: string): boolean {
    const before = this.data.customPoints.length;
    this.data.customPoints = this.data.customPoints.filter((p) => p.pointId !== pointId);
    if (this.data.customPoints.length !== before) {
      this.commit();
      return true;
    }
    return false;
  }

  /** 预置点隐藏/恢复（不删除）。 */
  setPresetHidden(pointId: string, hidden: boolean): void {
    const set = new Set(this.data.hiddenPreset);
    if (hidden) {
      set.add(pointId);
    } else {
      set.delete(pointId);
    }
    this.data.hiddenPreset = [...set];
    this.commit();
  }

  /** 追加一次采集会话；sessionIndex = 该点已有会话数 + 1。 */
  addSession(
    pointId: string,
    session: Omit<StaticSessionRec, 'pointId' | 'sessionIndex'>,
  ): StaticSessionRec {
    const rec: StaticSessionRec = {
      ...session,
      pointId,
      sessionIndex: this.data.sessions.filter((s) => s.pointId === pointId).length + 1,
    };
    this.data.sessions.push(rec);
    this.commit();
    return rec;
  }

  sessionsFor(pointId: string): StaticSessionRec[] {
    return this.data.sessions
      .filter((s) => s.pointId === pointId)
      .sort((a, b) => a.sessionIndex - b.sessionIndex);
  }

  allSessions(): StaticSessionRec[] {
    return [...this.data.sessions];
  }

  removeSession(pointId: string, sessionIndex: number): void {
    this.data.sessions = this.data.sessions.filter(
      (s) => !(s.pointId === pointId && s.sessionIndex === sessionIndex),
    );
    this.commit();
  }

  clearSessions(): void {
    this.data.sessions = [];
    this.commit();
  }

  /**
   * 全量清理：采样会话、现场补标点、预置点隐藏状态一并归零，
   * 页面据此回到「首次打开」的干净状态（不残留脏数据）。
   */
  clearAll(): void {
    this.data = { customPoints: [], hiddenPreset: [], sessions: [] };
    this.commit();
  }

  /** 导出前统计（与独立工具 stats 口径一致）。 */
  stats(): {
    pointCount: number;
    sampledPointCount: number;
    sessionCount: number;
    beaconPairs: number;
  } {
    const sampled = new Set(this.data.sessions.map((s) => s.pointId));
    let beaconPairs = 0;
    for (const s of this.data.sessions) {
      beaconPairs += (s.observations || []).length;
    }
    return {
      pointCount: this.listPoints().length,
      sampledPointCount: sampled.size,
      sessionCount: this.data.sessions.length,
      beaconPairs,
    };
  }
}

export type FpRecordsPersist = FpRecord[];

export class FpRecordsStore {
  private records: FpRecord[];

  constructor(
    load: KvLoad<FpRecord[]>,
    private save: KvSave<FpRecord[]>,
  ) {
    this.records = load() ?? [];
  }

  private commit(): boolean {
    return this.save(this.records) !== false;
  }

  /**
   * 追加快照并落定 captureIndex（该点已有记录数 + 1）。
   * 落盘失败时回滚本条并抛错——不能让界面显示出「只在内存里」的记录，
   * 否则重启后已采集数会突然变少。
   */
  addRecord(rec: Omit<FpRecord, 'captureIndex'>): FpRecord {
    const full: FpRecord = {
      ...rec,
      captureIndex: this.records.filter((r) => r.fpId === rec.fpId).length + 1,
    };
    this.records.push(full);
    if (!this.commit()) {
      this.records.pop();
      throw new FpRecordsPersistError();
    }
    return full;
  }

  recordsFor(fpId: string): FpRecord[] {
    return this.records.filter((r) => r.fpId === fpId);
  }

  allRecords(): FpRecord[] {
    return [...this.records];
  }

  removeRecord(fpId: string, collectedAt: string): void {
    this.records = this.records.filter((r) => !(r.fpId === fpId && r.collectedAt === collectedAt));
    this.commit();
  }

  clearAll(): void {
    this.records = [];
    this.commit();
  }

  /** 汇总统计（与独立工具 stats 口径一致）。 */
  stats(): { recordCount: number; fpCount: number; beaconSampleSum: number } {
    const fpSet = new Set<string>();
    let sampleSum = 0;
    for (const r of this.records) {
      fpSet.add(r.fpId);
      sampleSum += r.beaconCount;
    }
    return { recordCount: this.records.length, fpCount: fpSet.size, beaconSampleSum: sampleSum };
  }
}
