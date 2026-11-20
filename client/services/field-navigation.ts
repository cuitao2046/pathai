import {
  FieldOutbox, checkpointCount, fieldId, type FieldPlan, type FieldSegment,
  type FrozenFieldConfig, type NavExperimentSelection,
} from '../core/collection/field-experiment';
import { appContext } from './app-context';
import type { BeaconSample } from '../core/ble/scan-window';
import { resolveTurnNode } from '../core/nav/turn-haptics';

// Fixed approaches of the four instrumented turns on the T8 route, ordered backwards.
const TURN_APPROACHES = {
  'CP-03': ['F1-TI-0111', 'F1-TI-0013', 'F1-TI-0011'],
  'CP-04': ['F1-TI-0027', 'F1-TI-0111'],
  'CP-05': ['F1-TI-0072', 'F1-TI-0028'],
  'CP-06': ['F1-TI-0054', 'F1-TI-0060'],
} as const;

interface TurnReference {
  pointId: string;
  announcementId: string;
  nodeId: string;
  position: { x: number; y: number; floorId: string };
}

function announcementPoint(nodes: readonly string[], floorId: string) {
  const positions = nodes.map((id) => resolveTurnNode(appContext.map.floors, id));
  let remaining = 3;
  for (let i = 1; i < positions.length; i += 1) {
    const turnward = positions[i - 1];
    const upstream = positions[i];
    if (!turnward || !upstream || turnward.floorId !== floorId || upstream.floorId !== floorId) return null;
    const dx = upstream.x - turnward.x;
    const dy = upstream.y - turnward.y;
    const length = Math.hypot(dx, dy);
    if (length >= remaining) {
      return { floorId, x: turnward.x + dx * remaining / length, y: turnward.y + dy * remaining / length };
    }
    remaining -= length;
  }
  return null;
}

export interface ArmedNavigation {
  selection: NavExperimentSelection;
  plan: FieldPlan;
  config: FrozenFieldConfig;
  owner: string;
}

let armed: ArmedNavigation | null = null;

export function armFieldNavigation(run: ArmedNavigation): void { armed = run; }
export function takeFieldNavigation(): ArmedNavigation | null {
  const run = armed;
  armed = null;
  return run;
}

export class FieldNavigationRecorder {
  private readonly store: FieldOutbox;
  private segment: FieldSegment | null = null;
  private finalized = false;
  private draftWriteFailed = false;
  private lastCheckpoint = 0;
  private actualArrivalAt: number | null = null;
  private timer: ReturnType<typeof setInterval> | null = null;
  turnReferences: readonly TurnReference[];

  constructor(readonly run: ArmedNavigation, private readonly onError: (text: string) => void) {
    this.store = new FieldOutbox(run.owner);
    this.turnReferences = this.loadTurnReferences();
  }

  private loadTurnReferences(): TurnReference[] {
    return this.run.plan.points.flatMap((point) => {
      const nodes = TURN_APPROACHES[point.pointId as keyof typeof TURN_APPROACHES];
      if (!nodes) return [];
      const announcementId = `P-${point.pointId.slice(3)}`;
      const published = this.run.plan.points.find((p) => p.pointId === announcementId);
      const position = published ? { x: published.x, y: published.y, floorId: published.floorId }
        : announcementPoint(nodes, point.floorId);
      return position ? [{ pointId: point.pointId, announcementId,
        nodeId: nodes[0], position }] : [];
    });
  }

  start(): void {
    if (this.segment) return;
    if (this.store.recoverDraft()) throw new Error('有未恢复的实验记录，请先打开现场实验页');
    const { plan, config, selection } = this.run;
    const now = Date.now();
    const segment: FieldSegment = {
      segmentId: `${plan.batch}-NV-${selection.method}-${fieldId()}`, runId: selection.runId,
      task: 'NV', batch: plan.batch, planId: plan.planId, part: 1, startedAtEpoch: now,
      endedAtEpoch: now, mapVersion: plan.mapVersion,
      paramsVersion: config[selection.method === 'A' ? 'A' : 'B'].paramsVersion,
      deviceId: this.store.deviceId, status: 'aborted', method: selection.method,
      configId: config.configId, rssi: [], checkpoints: [], navigation: [],
    };
    this.store.saveDraft(segment);
    this.segment = segment;
    if (!this.turnReferences.length) this.turnReferences = this.loadTurnReferences();
    for (const reference of this.turnReferences) this.event('turn_reference', { ...reference });
    this.timer = setInterval(() => this.persistDraft(), 5000);
  }

  persistDraft(): void {
    if (!this.segment || this.finalized) return;
    try {
      this.segment.endedAtEpoch = Date.now();
      this.store.saveDraft(this.segment);
      if (this.draftWriteFailed) {
        this.draftWriteFailed = false;
        this.onError('');
      }
    } catch (error) {
      this.draftWriteFailed = true;
      this.onError(`导航草稿未能保存。请保留此页面并检查本机空间。${String(error)}`);
    }
  }

  addSample(sample: BeaconSample): void {
    if (!this.segment || this.finalized || this.segment.rssi.length >= 30000) return;
    const t = sample.observedAtMs;
    const beacon = appContext.map.beaconRegistry.resolve(sample.uuid, sample.major, sample.minor);
    if (beacon && t >= this.segment.startedAtEpoch && t <= Date.now() &&
        sample.rssi >= -127 && sample.rssi < 0) {
      this.segment.rssi.push({ t, beacon, rssi: Math.round(sample.rssi) });
    }
  }

  event(kind: string, payload: Record<string, unknown>): void {
    const events = this.finalized ? undefined : this.segment?.navigation;
    // Reserve space for the system outcome and the independent reference timestamp.
    if (events && events.length < (kind === 'end' || kind === 'arrival_manual' ? 4000 : 3998)) {
      events.push({ kind, t: Date.now(), ...payload });
      if (kind === 'end' || kind === 'arrival_manual') this.persistDraft();
    }
  }

  confirmArrival(): boolean {
    if (!this.segment || this.finalized || this.actualArrivalAt !== null) return false;
    this.actualArrivalAt = Date.now();
    this.event('arrival_manual', { t: this.actualArrivalAt });
    return true;
  }

  get count(): number { return checkpointCount(this.segment?.checkpoints ?? []); }
  get needsSaveRetry(): boolean { return this.finalized && this.segment !== null; }
  get canUndo(): boolean { return !this.finalized && this.lastCheckpoint > 0 && Date.now() - this.lastCheckpoint <= 2000; }

  mark(): void {
    const point = this.run.plan.points[this.count];
    if (!point || !this.segment || this.finalized) return;
    this.lastCheckpoint = Date.now();
    this.segment.checkpoints.push({ t: this.lastCheckpoint, pointId: point.pointId, undo: false });
    this.persistDraft();
  }

  undo(): void {
    const events = this.segment?.checkpoints;
    if (!events?.length || !this.canUndo) return;
    events.push({ t: Date.now(), pointId: events[events.length - 1].pointId, undo: true });
    this.lastCheckpoint = 0;
    this.persistDraft();
  }

  finish(status: FieldSegment['status'], reason: string): boolean {
    if (this.timer) clearInterval(this.timer);
    this.timer = null;
    if (!this.segment) return true;
    if (!this.finalized) {
      this.segment.endedAtEpoch = Date.now();
      this.segment.status = status;
      this.segment.reason = reason;
      this.finalized = true;
    }
    try {
      this.store.enqueue('segments', this.segment.segmentId, this.segment);
      this.store.clearDraft();
      this.segment = null;
      // The field page owns upload and account verification.
      return true;
    } catch (error) {
      this.onError(`导航记录保存失败。请留在此页，释放空间后重试保存。${String(error)}`);
      return false;
    }
  }
}
