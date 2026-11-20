export const FIELD_ORIENTATIONS = ['N', 'E', 'S', 'W'] as const;
export type FieldOrientation = typeof FIELD_ORIENTATIONS[number];
export type FieldTask = 'CAL' | 'FP' | 'ST' | 'DY' | 'NV';
export type FieldMethod = 'A' | 'B' | 'C';
export const FINGERPRINT_MIN_SECONDS = 25;
export const FINGERPRINT_MAX_SECONDS = 30;

export interface SurveyPoint {
  pointId: string;
  floorId: string;
  x: number;
  y: number;
  measuredOn: string;
  anchorA: string;
  anchorB: string;
  distanceA: number;
  distanceB: number;
  checkA: number;
  checkB: number;
  note: string;
  surveyVersion?: string;
  candidateX?: number;
  candidateY?: number;
}

export interface FieldPlan {
  planId: string;
  mapVersion: string;
  day: string;
  task: FieldTask;
  batch: string;
  points: SurveyPoint[];
}

export interface FrozenFieldConfig {
  configId: string;
  mapVersion: string;
  frozenAt: number;
  A: { paramsVersion: string };
  B: { paramsVersion: string };
  C: { libraryVersion: string; fallback: 'B' };
}

export interface NavExperimentSelection {
  configId: string;
  runId: string;
  planId: string;
  method: FieldMethod;
}

export function rotatedFieldMethod(dayIndex: number, completed: number): FieldMethod | undefined {
  if (!Number.isInteger(completed) || completed < 0 || completed >= 9) return undefined;
  const methods = ['A', 'B', 'C'] as const;
  return methods[(dayIndex + Math.floor(completed / 3) + completed % 3) % 3];
}

export interface FieldFrame { t: number; beacon: string; rssi: number }
export interface FieldCheckpoint { t: number; pointId: string; undo: boolean }
export interface FieldSegment {
  segmentId: string;
  runId: string;
  task: FieldTask;
  batch: string;
  planId?: string;
  pointId?: string;
  orientation?: FieldOrientation;
  part: number;
  startedAtEpoch: number;
  endedAtEpoch: number;
  mapVersion: string;
  paramsVersion: string;
  deviceId: string;
  status: 'complete' | 'suspect' | 'aborted';
  reason?: string;
  retryOf?: string;
  rssi: FieldFrame[];
  checkpoints: FieldCheckpoint[];
  method?: 'A' | 'B' | 'C' | 'RAW';
  configId?: string;
  navigation?: Record<string, unknown>[];
}

type RecordKind = 'segments' | 'notes' | 'inspections';
interface OutboxItem { kind: RecordKind; id: string; body: object; owner: string }
export interface SegmentSummary {
  planId?: string;
  task: FieldTask;
  batch: string;
  pointId?: string;
  orientation?: FieldOrientation;
  part: number;
  status: FieldSegment['status'];
  segmentId: string;
  runId: string;
  startedAtEpoch: number;
  mapVersion: string;
}

export function fieldId(): string {
  return Date.now().toString(36) + '-' + Math.random().toString(36).slice(2, 12);
}

export function fieldDay(timestamp = Date.now()): string {
  return new Date(timestamp + 8 * 3600_000).toISOString().slice(0, 10);
}

export function fieldSteps(task: FieldTask, trial = false): { part: number; orientation?: FieldOrientation; duration: number }[] {
  if (task === 'CAL') return [{ part: 1, duration: trial ? 10 : 120 }];
  if (task === 'FP' || task === 'ST') {
    const duration = trial ? 10 : task === 'FP' ? FINGERPRINT_MIN_SECONDS : 30;
    return FIELD_ORIENTATIONS.map((orientation, index) => ({ part: index + 1, orientation, duration }));
  }
  return [{ part: 1, duration: 0 }];
}

export function fingerprintWindow(segment: Pick<FieldSegment, 'startedAtEpoch' | 'rssi'>, deadline: number, now: number) {
  const countFailure = (end: number): string => {
    const frames = segment.rssi.filter((frame) => frame.t <= end);
    if (frames.length < 60) return 'RSSI 样本不足 60 条';
    const beacons = new Set(frames.filter((frame) => frame.rssi >= -100 && frame.rssi <= -20)
      .map((frame) => frame.beacon));
    return beacons.size < 3 ? '有效信标不足 3 枚' : '';
  };
  const reason = countFailure(deadline);
  const maximum = segment.startedAtEpoch + FINGERPRINT_MAX_SECONDS * 1000;
  if (!reason || deadline >= maximum) return { deadline, reason };
  return { deadline: maximum, reason: now >= maximum ? countFailure(maximum) : reason };
}

export function checkpointCount(events: readonly FieldCheckpoint[]): number {
  return events.reduce((count, event) => count + (event.undo ? -1 : 1), 0);
}

const LOCAL_STATE = 'field:experiment:v1';
interface LocalState {
  deviceId: string;
  // Legacy versions stored all upload summaries under one KV key.
  summaries: SegmentSummary[];
}

export class FieldOutbox {
  private readonly fs = wx.getFileSystemManager();
  private readonly directory: string;
  private uploading: Promise<void> | null = null;
  private readonly receipts = new Map<string, SegmentSummary>();

  constructor(private readonly owner: string) {
    this.directory = `${wx.env.USER_DATA_PATH}/experiment-${owner}`;
    try { this.fs.accessSync(this.directory); }
    catch { this.fs.mkdirSync(this.directory, true); }
  }

  private get key(): string { return `${LOCAL_STATE}:${this.owner}`; }

  private state(): LocalState {
    const saved = wx.getStorageSync<LocalState | ''>(this.key);
    if (saved) return saved;
    const path = `${this.directory}/device-id`;
    try { this.fs.accessSync(path); }
    catch { this.writeAtomic(path, fieldId()); }
    return { deviceId: this.fs.readFileSync(path, 'utf8') as string, summaries: [] };
  }

  get deviceId(): string { return this.state().deviceId; }

  progress(): SegmentSummary[] {
    const records = new Map(this.state().summaries.map((record) => [record.segmentId, record]));
    for (const name of this.fs.readdirSync(this.directory)) {
      if (/^sent-segments-[A-Za-z0-9_-]+\.json$/.test(name)) {
        let receipt = this.receipts.get(name);
        if (!receipt) {
          const saved = JSON.parse(this.fs.readFileSync(`${this.directory}/${name}`, 'utf8') as string) as
            SegmentSummary | OutboxItem;
          // A receipt may still contain the full payload if it has not been compacted.
          receipt = 'body' in saved ? summary(saved.body as FieldSegment) : saved;
          if ('body' in saved) this.compactReceipt(name, receipt);
          this.receipts.set(name, receipt);
        }
        records.set(receipt.segmentId, receipt);
      } else if (/^pending-segments-[A-Za-z0-9_-]+\.json$/.test(name)) {
        const pending = summary(this.read(name).body as FieldSegment);
        if (!records.has(pending.segmentId)) records.set(pending.segmentId, pending);
      }
    }
    return [...records.values()];
  }

  private files(): string[] {
    return this.fs.readdirSync(this.directory)
      .filter((name) => /^pending-[a-z]+-[A-Za-z0-9_-]+\.json$/.test(name))
      .sort();
  }

  private read(name: string): OutboxItem {
    return JSON.parse(this.fs.readFileSync(`${this.directory}/${name}`, 'utf8') as string) as OutboxItem;
  }

  pendingCount(): number { return this.files().length; }

  private path(kind: RecordKind, id: string): string {
    if (!/^[A-Za-z0-9_-]+$/.test(id)) throw new Error('无效记录编号');
    return `${this.directory}/pending-${kind}-${id}.json`;
  }

  enqueue(kind: RecordKind, id: string, body: object): void {
    const path = this.path(kind, id);
    const serialized = JSON.stringify({ kind, id, body, owner: this.owner });
    let existing: string | ArrayBuffer | undefined;
    try { this.fs.accessSync(path); existing = this.fs.readFileSync(path, 'utf8'); }
    catch (error) {
      if (existing !== undefined) throw error;
    }
    if (existing !== undefined) {
      if (existing !== serialized) throw new Error('本机已存在同编号的不同记录');
      return;
    }
    this.writeAtomic(path, serialized);
  }

  private writeAtomic(path: string, data: string): void {
    const tmp = `${path}.tmp`;
    try {
      this.fs.writeFileSync(tmp, data, 'utf8');
      this.fs.renameSync(tmp, path);
    } catch (error) {
      try { this.fs.unlinkSync(tmp); } catch { /* The last committed file is still usable. */ }
      throw error;
    }
  }

  private compactReceipt(name: string, receipt: SegmentSummary): void {
    try { this.writeAtomic(`${this.directory}/${name}`, JSON.stringify(receipt)); }
    catch { /* The full receipt remains readable and is already out of the queue. */ }
  }

  saveDraft(segment: FieldSegment): void {
    this.writeAtomic(`${this.directory}/draft.json`, JSON.stringify(segment));
  }

  recoverDraft(): FieldSegment | null {
    const path = `${this.directory}/draft.json`;
    try { this.fs.accessSync(path); } catch { return null; }
    return JSON.parse(this.fs.readFileSync(path, 'utf8') as string) as FieldSegment;
  }

  clearDraft(): void {
    const path = `${this.directory}/draft.json`;
    try { this.fs.accessSync(path); } catch { return; }
    this.fs.unlinkSync(path);
  }

  flush(post: (kind: RecordKind, body: object) => Promise<unknown>): Promise<void> {
    if (this.uploading) return this.uploading;
    this.uploading = (async () => {
      let error: unknown;
      for (const name of this.files()) {
        try {
          const item = this.read(name);
          if (item.owner !== this.owner) throw new Error('待补传记录的账号不匹配');
          await post(item.kind, item.body);
          if (item.kind === 'segments') {
            // The server has committed the raw data. Rename the pending file
            // before compacting so acknowledgement itself needs no payload write.
            const receiptName = `sent-segments-${item.id}.json`;
            const receiptPath = `${this.directory}/${receiptName}`;
            this.fs.renameSync(this.path(item.kind, item.id), receiptPath);
            const receipt = summary(item.body as FieldSegment);
            this.receipts.set(receiptName, receipt);
            this.compactReceipt(receiptName, receipt);
          } else {
            this.fs.unlinkSync(this.path(item.kind, item.id));
          }
        } catch (failure) {
          error ??= failure;
          // Network/auth failures apply to all subsequent rows. A rejected payload
          // stays queued for inspection while other rows can still be delivered.
          const status = (failure as { statusCode?: number | null } | null)?.statusCode;
          if (status === undefined || status === null || status === 401 || status === 403 || status >= 500) break;
        }
      }
      if (error) throw error;
    })().finally(() => { this.uploading = null; });
    return this.uploading;
  }
}

function summary(segment: FieldSegment): SegmentSummary {
  const { planId, task, batch, pointId, orientation, part, status, segmentId, runId, startedAtEpoch, mapVersion } = segment;
  return { planId, task, batch, pointId, orientation, part, status, segmentId, runId, startedAtEpoch, mapVersion };
}
