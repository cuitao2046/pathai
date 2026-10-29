/**
 * 标定模式轨迹记录器（REQ-CALIB）：只记录可信位置估计，不改定位/导航算法。
 * 纯逻辑 + 注入存储，便于 jest 覆盖；存储由 appContext 提供 wx 同步 KV 适配。
 */
import type { PositionEstimateResponse } from '../../typings/api';

/** 单次记录上限：防止长时间开启占满小程序存储。 */
export const CALIBRATION_MAX_RECORDS = 5000;

export interface CalibrationRecord {
  /** 记录时间（ISO 8601 UTC）。 */
  ts: string;
  mapVersion: string;
  paramsVersion: string;
  floorId: string | null;
  x: number;
  y: number;
  quality: string;
  confidence: number;
  /** 本次 API-05 请求提交的可见信标数。 */
  visibleBeaconCount: number;
  /** 当前生效指令序号；未创建会话时为 null。 */
  instructionSequence: number | null;
  /** 客户端导航态：idle|active|recovering|arrived|ended。 */
  sessionStatus: string | null;
}

export interface CalibrationStorage {
  load(): unknown;
  save(records: CalibrationRecord[]): void;
}

export interface CalibrationExportMeta {
  venueId: string;
  operator: string;
  device: unknown;
  appVersion?: string;
  exportedAt?: string;
}

export interface CalibrationSampleInput {
  estimate: PositionEstimateResponse;
  visibleBeaconCount: number;
  instructionSequence: number | null;
  sessionStatus: string | null;
}

export class TrajectoryRecorder {
  private records: CalibrationRecord[];

  constructor(
    private readonly storage: CalibrationStorage,
    private readonly maxRecords: number = CALIBRATION_MAX_RECORDS,
  ) {
    this.records = this.sanitize(storage.load());
  }

  get count(): number {
    return this.records.length;
  }

  /** 记录一条可信估计；不可信/无坐标的估计不记录（与 REQ-CALIB「可信估计」一致）。 */
  record(input: CalibrationSampleInput): boolean {
    const position = input.estimate.position;
    if (position === undefined || position === null) {
      return false;
    }
    const record: CalibrationRecord = {
      ts: new Date().toISOString(),
      mapVersion: input.estimate.mapVersion,
      paramsVersion: input.estimate.paramsVersion,
      floorId: position.floorId ?? null,
      x: position.x,
      y: position.y,
      quality: input.estimate.quality,
      confidence: input.estimate.confidence,
      visibleBeaconCount: input.visibleBeaconCount,
      instructionSequence: input.instructionSequence,
      sessionStatus: input.sessionStatus,
    };
    this.records.push(record);
    if (this.records.length > this.maxRecords) {
      this.records.splice(0, this.records.length - this.maxRecords);
    }
    this.storage.save(this.records);
    return true;
  }

  clear(): void {
    this.records = [];
    this.storage.save(this.records);
  }

  buildExport(meta: CalibrationExportMeta): Record<string, unknown> {
    return {
      schemaVersion: '1.0',
      type: 'positioning_calibration_dynamic',
      venueId: meta.venueId,
      operator: meta.operator,
      device: meta.device,
      appVersion: meta.appVersion ?? '1.0.0',
      exportedAt: meta.exportedAt ?? new Date().toISOString(),
      recordCount: this.records.length,
      records: this.records.slice(),
    };
  }

  private sanitize(raw: unknown): CalibrationRecord[] {
    if (!Array.isArray(raw)) {
      return [];
    }
    return raw.filter(
      (r): r is CalibrationRecord =>
        r !== null &&
        typeof r === 'object' &&
        typeof (r as CalibrationRecord).ts === 'string' &&
        typeof (r as CalibrationRecord).x === 'number' &&
        typeof (r as CalibrationRecord).y === 'number',
    );
  }
}
