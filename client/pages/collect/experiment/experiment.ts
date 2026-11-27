import { appContext } from '../../../services/app-context';
import { applyHeadingOffset, HeadingSource } from '../../../core/a11y/heading-source';
import { createBeaconSource } from '../../../core/ble/beacon-source-factory';
import type { BeaconSourceLike } from '../../../core/ble/beacon-source';
import type { BeaconSample } from '../../../core/ble/scan-window';
import { cachedRuntimeParams, clearRuntimeParams } from '../../../core/config/runtime-config';
import { clearOverlayCache } from '../../../core/collection/grid-overlay';
import { CollectMapView } from '../../../core/collection/collect-map-view';
import { FP_POINT_COLORS } from '../../../core/collection/fingerprint-visibility';
import { pickPointOnScreen } from '../../../core/collection/hit-test';
import { drawReferenceWalls } from '../../../core/collection/reference-walls';
import {
  FieldOutbox, checkpointCount, fieldDay, fieldId, fieldSteps, FIELD_ORIENTATIONS, fingerprintWindow,
  rotatedFieldMethod, type FieldPlan, type FieldSegment, type FieldTask, type SurveyPoint,
  type FieldMethod,
  type FrozenFieldConfig, type SegmentSummary,
} from '../../../core/collection/field-experiment';
import { armFieldNavigation, takeFieldNavigation } from '../../../services/field-navigation';
import { expandFieldSurveyPoints, FIELD_SURVEY_POINTS } from '../../../data/field-survey-points';
import { FIELD_FINGERPRINT_GRID } from '../../../data/field-fingerprint-grid';
import { missingSurveyBaselines, nextSurveyVersion, surveyCoordinates, surveyOffset, surveyReferences, surveyRouteWallOffset, type SurveyReference } from
  '../../../core/collection/survey-coordinates';

const TABS = [
  { id: 2, label: '真值登记' },
  { id: 0, label: '任务' },
  { id: 1, label: '信标巡检' },
  { id: 3, label: '场记及导出' },
];
const TASKS = ['CAL', 'FP', 'ST', 'DY', 'NV', 'UT'] as const;
const TASK_LABELS = ['T3 参数标定', 'T4 指纹建库', 'T6 静态对比', 'T7 路线试走（可选）', 'T8 导航与动态对比', 'T9 反向掉头时效'];
const PLAN_POINT_LIMITS = { CAL: [12, 12], FP: [1, 256], ST: [18, 18], DY: [8, 8], NV: [8, 8], UT: [1, 1] } as const;
const ACTIONS = ['installed', 'checked', 'issue', 'removed'] as const;
const ACTION_LABELS = ['贴装', '复核', '异常', '拆除'];
const RATINGS = ['on_time', 'early', 'late', 'missed'] as const;
const RATING_LABELS = ['及时', '提前', '滞后', '漏报'];
const METHODS = ['A', 'B', 'C'] as const;
/** T9 只对比 B/C（不做 A 默认参数）；每轮按完成次数交替，保证两组 ≥30 轮。 */
const UT_METHODS = ['B', 'C'] as const;
const METHOD_LABELS: Record<FieldMethod, string> = {
  A: 'A · 默认参数三点定位', B: 'B · 标定参数三点定位', C: 'C · 指纹增强，未命中回退 B',
};
const NAV_DAYS = ['2026-12-26', '2026-12-27', '2027-01-09'];

interface Candidate {
  pointId: string; floorId: string; x: number; y: number;
  references?: SurveyReference[];
  sharedPointId?: string;
  /** 仅 T4 指纹网格点携带：网格数据预估口径（当前部署下 RSSI≥-85dBm 且 ≥3 信标可见）。 */
  collectable?: boolean;
  measurementWalls?: MeasurementWall[];
}
interface MeasurementWall {
  id: string; label: string; distanceM: number;
  hit?: { hitX: number; hitY: number };
}

function measurementWalls(point: Candidate): MeasurementWall[] {
  return point.measurementWalls ?? (point.references ?? []).map((wall) => ({
    id: wall.label.split(' ')[0], label: wall.label, distanceM: wall.baseDistance,
    hit: wall.routeOriginY === undefined
      ? { hitX: wall.axis === 'X' ? wall.coordinate : point.x, hitY: wall.axis === 'Y' ? wall.coordinate : point.y }
      : undefined,
  }));
}

function publishedMeasurementWalls(point: SurveyPoint): MeasurementWall[] {
  return [[point.anchorA, point.distanceA], [point.anchorB, point.distanceB]].map(([label, distance]) => {
    const name = String(label);
    const distanceM = Number(distance);
    const id = name.split(/\s+/)[0];
    const direction = /(?:^|\s)([+-])([XY])(?:\s|$)/.exec(name);
    const offset = direction?.[1] === '-' ? -distanceM : distanceM;
    const hit = direction ? {
      hitX: point.x + (direction[2] === 'X' ? offset : 0),
      hitY: point.y + (direction[2] === 'Y' ? offset : 0),
    } : undefined;
    return { id, label: name, distanceM, hit };
  });
}
function candidates(task: FieldTask): Candidate[] {
  if (task === 'FP') {
    return [...FIELD_FINGERPRINT_GRID].sort((a, b) => Number(b.isBase2m) - Number(a.isBase2m))
      .map((p) => ({ pointId: p.fpId, floorId: p.floorId, x: p.x, y: p.y, collectable: p.collectable,
        references: surveyReferences(p, p.references) }));
  }
  const purpose = task === 'CAL' ? 'calibration' : task === 'ST' ? 'static_test' : 'checkpoint';
  const shared = appContext.map.fieldSurveyPoints;
  const points = shared.length ? expandFieldSurveyPoints(shared) : FIELD_SURVEY_POINTS;
  return points.filter((p) => p.purpose === purpose && (task !== 'DY' || !p.checkpointOnly) &&
      (task === 'UT' ? !!p.uturnCheckpoint : !p.uturnCheckpoint))
    .map((p) => ({ pointId: p.pointId, floorId: p.floorId, x: p.truth[0], y: p.truth[1],
      sharedPointId: p.sharedPointId,
      references: surveyReferences({ x: p.truth[0], y: p.truth[1] }, p.walls, p.routeReference) }));
}

function planPointLimits(task: FieldTask): readonly [number, number] {
  if (task === 'NV' && appContext.map.fieldSurveyPoints.some((p) => p.checkpointOnly)) {
    const count = appContext.map.fieldSurveyPoints.filter((p) => p.checkpointId && !p.uturnCheckpoint).length;
    return [count, count];
  }
  return PLAN_POINT_LIMITS[task];
}

type Form = Record<string, string>;
interface FingerprintBuildPlan {
  planId: string;
  batch: string;
  day: string;
  pointCount: number;
  active: boolean;
  selected: boolean;
}
interface CalibrationParams {
  rssiAt1mDbm: number;
  pathLossN: number;
  residualReliableM?: number;
  residualDegradedM?: number;
}
interface CalibrationRun {
  runId: string;
  status: 'queued' | 'computing' | 'computed' | 'compute_failed' | 'applied' | 'superseded';
  errorMessage?: string;
  result: { deployable: boolean; applyBlockedReason?: string; params: CalibrationParams;
    thresholds: Partial<CalibrationParams> | null; warnings: string[] } | null;
}
const CAL_FIELDS = [
  { key: 'rssiAt1mDbm', label: '1 米 RSSI（dBm）' }, { key: 'pathLossN', label: '衰减指数 n' },
  { key: 'residualReliableM', label: '可靠残差（m）' }, { key: 'residualDegradedM', label: '降级残差（m）' },
] as const;
const computingCalibration = (run: CalibrationRun | null): boolean => run?.status === 'queued' || run?.status === 'computing';
const message = (error: unknown) => error instanceof Error ? error.message : String(error);
const surveyDistances = (p: SurveyPoint): number[] => [p.distanceA, p.distanceB, p.checkA, p.checkB];
const surveyRank = (p: SurveyPoint): number => p.surveyVersion === 'base' ? 0 : Number(p.surveyVersion?.slice(1) ?? -1);
const DIRECTION_NAMES = { N: '北', E: '东', S: '南', W: '西' };
const HEADING_TOLERANCE = 15;
const HEADING_READ_TIMEOUT_MS = 5000;

Page({
  data: {
    tabs: TABS, tab: 0, taskLabels: TASK_LABELS, taskIndex: 0,
    loading: true, allowed: false, demo: appContext.dataSource === 'mock', issue: '',
    day: fieldDay(), plans: [] as FieldPlan[], planLabels: [] as string[],
    planIndex: -1, trial: false, points: [] as Candidate[], pointLabels: [] as string[],
    pointIndex: 0, progressText: '', pending: 0, busy: false,
    phase: 'idle' as 'idle' | 'prepare' | 'record', remaining: '', frames: 0, checkpointText: '',
    directionProgress: '', directionCompleted: 0,
    selectedPointId: '', selectedWalls: [] as MeasurementWall[], selectedPointComplete: false,
    mapFloor: '', mapFloors: [] as { floorId: string; displayName: string }[],
    surveyMapOpen: false, surveyMapFloor: '', surveyMapFloors: [] as { floorId: string; displayName: string }[],
    surveyListOpen: false, surveyListScrollInto: '', surveyUncollectable: false,
    headingTarget: '', headingCurrent: '', headingStatus: '', headingManual: false,
    undo: false, runsToday: 0, lastRun: '', summary: '', needsRetry: false,
    beacons: [] as { id: string; name: string; rssi: number | null; seen: boolean; last: string }[],
    scanText: '', selectedBeacon: '', actionIndex: 1, actionLabels: ACTION_LABELS,
    ratingLabels: RATING_LABELS, ratingIndex: -1, cleared: false, form: {} as Form,
    drafts: 0, surveyPlanRequirement: '', surveyPlanReady: false, surveyPublished: '',
    calPlanId: '', calSampleId: '', calRun: null as CalibrationRun | null, calComputing: false,
    calDefaults: null as CalibrationParams | null, calRows: [] as { label: string; baseline: string; fitted: string }[],
    calMessage: '',
    surveyDisplay: { base: '', baseDistances: '', offset: '', preview: '', version: '', history: [] as string[], canSave: false },
    configs: [] as FrozenFieldConfig[], configLabels: [] as string[], configIndex: 0,
    methodLabels: METHODS.map((method) => METHOD_LABELS[method]),
    methodIndex: 0, nextMethod: '',
    replayText: '',
    makeup: [] as string[],
    fingerprintBuildPlans: [] as FingerprintBuildPlan[], fingerprintBuildLoading: false,
    fingerprintBuildError: '', fingerprintBuildCount: 0,
  },

  source: null as BeaconSourceLike | null,
  stopStatus: null as (() => void) | null,
  outbox: null as FieldOutbox | null,
  owner: '',
  mapVersion: '',
  disposed: false,
  timer: null as ReturnType<typeof setInterval> | null,
  deadline: 0,
  headingSource: null as HeadingSource | null,
  headingWaitSince: 0,
  headingBuzzed: false,
  headingManuallyConfirmed: false,
  prepareSeconds: 0,
  lastSave: 0,
  warned: false,
  active: null as FieldSegment | null,
  pendingFinalized: false,
  lastTapeAt: 0,
  segmentPoint: null as Candidate | null,
  segmentPlan: null as FieldPlan | null,
  retryOf: '',
  steps: [] as ReturnType<typeof fieldSteps>,
  stepIndex: 0,
  savedRunId: '',
  surveyPoints: [] as Candidate[],
  remoteProgress: [] as SegmentSummary[],
  readings: null as Map<string, { rssi: number; t: number }> | null,
  pointMap: null as CollectMapView | null,
  surveyMap: null as CollectMapView | null,
  completedMapPoints: null as Set<string> | null,
  pageScrollTop: 0,
  calTimer: null as ReturnType<typeof setTimeout> | null,

  onLoad(options: Record<string, string | undefined>): void {
    this.readings = new Map();
    const tab = Number(options.tab);
    const taskIndex = TASKS.indexOf(options.task as FieldTask);
    this.setData({ tab: TABS.some((item) => item.id === tab) ? tab : 0, taskIndex: Math.max(0, taskIndex), day: fieldDay(),
      lastRun: /^[A-Za-z0-9_-]{1,128}$/.test(options.runId ?? '') ? options.runId ?? '' : '' });
    void this.load();
  },

  onHide(): void {
    try { if (this.active) this.finish('aborted', '采集页进入后台'); }
    catch { /* Preserve the draft for recovery after a storage error. */ }
    this.resetRun();
    this.stopScan();
  },
  onUnload(): void {
    this.disposed = true;
    this.pointMap?.destroy();
    this.pointMap = null;
    this.surveyMap?.destroy();
    this.surveyMap = null;
    if (this.calTimer !== null) clearTimeout(this.calTimer);
    this.onHide();
  },

  onShow(): void {
    if (!this.data.loading && this.data.allowed && this.data.tab === 1) this.onScan();
  },

  async load(): Promise<void> {
    this.setData({ loading: true, issue: '' });
    try {
      const identity = appContext.getIdentity() ?? await appContext.refreshIdentity();
      if (!identity?.userId) throw new Error('未能读取登录身份，请联网后重试');
      if (this.disposed) return;
      this.owner = identity.userId;
      this.outbox = new FieldOutbox(this.data.demo ? `${identity.userId}-demo` : identity.userId);
      await appContext.map.ensureLoaded();
      if (this.disposed) return;
      this.mapVersion = appContext.map.mapVersion ?? '';
      if (!this.mapVersion) throw new Error('地图尚未加载');
      const draft = this.outbox.recoverDraft();
      if (draft) {
        if (!this.outbox.progress().some((row) => row.segmentId === draft.segmentId)) {
          this.outbox.enqueue('segments', draft.segmentId, { ...draft, status: 'aborted', reason: '上次采集意外退出' });
        }
        this.outbox.clearDraft();
      }
      this.setData({ allowed: true, loading: false, pending: this.outbox.pendingCount() });
      await this.loadPlans();
      if (this.data.tab === 1) this.onScan();
      if (this.data.tab === 2) this.openSurvey();
      if (this.data.tab === 3) void this.loadFingerprintBuildPlans();
    } catch (error) {
      if (!this.disposed) this.setData({ loading: false, allowed: false, issue: message(error) });
    }
  },

  async loadPlans(): Promise<void> {
    const day = this.data.day;
    const cacheKey = `field:active-plans:${this.owner}:${day}`;
    const cached = (wx.getStorageSync(cacheKey) || {}) as { plans?: FieldPlan[]; progress?: SegmentSummary[] };
    let plans = cached.plans ?? [];
    let progress = cached.progress ?? [];
    try {
      const response = await appContext.http.client.request<{ plans: FieldPlan[]; progress: SegmentSummary[] }>({
        method: 'GET', path: `/api/v1/field/plans?day=${day}`,
      });
      if (!response.data) throw new Error('未返回生效的任务清单');
      plans = response.data.plans;
      progress = response.data.progress;
      wx.setStorageSync(cacheKey, { plans, progress });
    } catch {
      if (this.data.day === day) this.setData({ issue: '未能刷新清单，使用本机已缓存的版本' });
    }
    if (this.disposed || this.data.day !== day) return;
    this.remoteProgress = progress;
    this.setData({ plans }, () => {
      if (this.data.tab === 0) this.refreshPlan();
    });
    void this.loadConfigurations();
    void this.loadMakeup();
  },

  async loadMakeup(): Promise<void> {
    const day = this.data.day;
    try {
      const response = await appContext.http.client.request<{ points: string[] }>({
        method: 'GET', path: `/api/v1/field/fingerprint-makeup?day=${day}&mapVersion=${encodeURIComponent(this.mapVersion)}`,
      });
      if (this.data.day === day) this.setData({ makeup: response.data?.points ?? [] });
    } catch {
      if (this.data.day === day) this.setData({ makeup: [] });
    }
  },

  task(): FieldTask { return TASKS[this.data.taskIndex]; },
  /** 当前任务可选方法：T9 只用 B/C，其余任务为 A/B/C。 */
  methods(): readonly FieldMethod[] { return this.task() === 'UT' ? UT_METHODS : METHODS; },
  matchingPlans(): FieldPlan[] {
    return this.data.plans.filter((p) => p.task === this.task() && p.mapVersion === this.mapVersion);
  },
  currentPlan(): FieldPlan | null {
    return this.data.trial ? null : this.matchingPlans()[this.data.planIndex] ?? null;
  },

  syncPointMap(): void {
    const show = !this.disposed && this.data.allowed && this.data.tab === 0
      && this.data.phase === 'idle' && this.data.taskIndex < 3;
    if (!show) {
      this.pointMap?.destroy();
      this.pointMap = null;
      return;
    }
    if (this.pointMap) {
      this.pointMap.refreshCanvasPosition();
      this.pointMap.requestDraw();
      return;
    }
    const map = new CollectMapView(this, {
      canvasId: 'experimentPointMap',
      deps: { floors: appContext.map.floors, places: appContext.map.places, beacons: appContext.map.beacons },
      callbacks: {
        onReady: () => {
          this.setData({ mapFloors: map.floorList, mapFloor: map.floorId });
          const initial = this.data.points[0]?.floorId;
          if (initial) map.setFloor(initial);
        },
        onTap: (_world, screen) => this.onPointMapTap(screen),
        onLongPress: () => {},
        onFloorChanged: (floorId) => this.setData({ mapFloor: floorId }),
        drawOverlay: (ctx, vp) => this.drawPointMap(ctx, vp),
      },
    });
    this.pointMap = map;
    map.updatePageScroll(this.pageScrollTop);
    map.init();
  },
  onPageScroll({ scrollTop }: { scrollTop: number }): void {
    this.pageScrollTop = scrollTop;
    this.pointMap?.updatePageScroll(scrollTop);
  },
  onMapTouchStart(event: WechatMiniprogram.TouchEvent): void { this.pointMap?.onTouchStart(event); },
  onMapTouchMove(event: WechatMiniprogram.TouchEvent): void { this.pointMap?.onTouchMove(event); },
  onMapTouchEnd(event: WechatMiniprogram.TouchEvent): void { this.pointMap?.onTouchEnd(event); },
  onMapTouchCancel(event: WechatMiniprogram.TouchEvent): void { this.pointMap?.onTouchCancel(event); },
  onMapZoomIn(): void { this.pointMap?.zoomIn(); },
  onMapZoomOut(): void { this.pointMap?.zoomOut(); },
  onMapFit(): void { this.pointMap?.fit(); },
  onMapFloor(event: WechatMiniprogram.TouchEvent): void {
    this.closePointMenu();
    this.pointMap?.setFloor(String(event.currentTarget.dataset.floor));
  },
  onOpenSurveyMap(): void {
    if (this.data.busy || !this.data.pointLabels.length) return;
    this.setData({ surveyMapOpen: true }, () => this.syncSurveyMap());
  },
  onCloseSurveyMap(): void {
    this.surveyMap?.destroy();
    this.surveyMap = null;
    this.setData({ surveyMapOpen: false, surveyMapFloor: '', surveyMapFloors: [] });
  },
  syncSurveyMap(): void {
    if (this.disposed || !this.data.surveyMapOpen || !this.data.allowed) return;
    const map = new CollectMapView(this, {
      canvasId: 'surveyPointMap',
      deps: { floors: appContext.map.floors, places: appContext.map.places, beacons: appContext.map.beacons },
      callbacks: {
        onReady: () => {
          this.setData({ surveyMapFloors: map.floorList, surveyMapFloor: map.floorId });
          const initial = this.data.points[this.data.pointIndex]?.floorId;
          if (initial) map.setFloor(initial);
        },
        onTap: (_world, screen) => this.onSurveyMapTap(screen),
        onLongPress: () => {},
        onFloorChanged: (floorId) => this.setData({ surveyMapFloor: floorId }),
        drawOverlay: (ctx, vp) => this.drawSurveyPointMap(ctx, vp),
      },
    });
    this.surveyMap = map;
    map.init();
  },
  onSurveyMapTap(screen: { x: number; y: number }): void {
    const vp = this.surveyMap?.vp;
    if (!vp) return;
    const hit = pickPointOnScreen(this.data.points.filter((point) => point.floorId === vp.floorId),
      (point) => point, vp, screen, 20);
    if (!hit) return;
    const pointIndex = this.data.points.indexOf(hit.point);
    if (pointIndex < 0) return;
    this.setData({ pointIndex }, () => this.fillSurvey());
    this.onCloseSurveyMap();
  },
  onSurveyMapTouchStart(event: WechatMiniprogram.TouchEvent): void { this.surveyMap?.onTouchStart(event); },
  onSurveyMapTouchMove(event: WechatMiniprogram.TouchEvent): void { this.surveyMap?.onTouchMove(event); },
  onSurveyMapTouchEnd(event: WechatMiniprogram.TouchEvent): void { this.surveyMap?.onTouchEnd(event); },
  onSurveyMapTouchCancel(event: WechatMiniprogram.TouchEvent): void { this.surveyMap?.onTouchCancel(event); },
  onSurveyMapZoomIn(): void { this.surveyMap?.zoomIn(); },
  onSurveyMapZoomOut(): void { this.surveyMap?.zoomOut(); },
  onSurveyMapFit(): void { this.surveyMap?.fit(); },
  onSurveyMapFloor(event: WechatMiniprogram.TouchEvent): void {
    this.surveyMap?.setFloor(String(event.currentTarget.dataset.floor));
  },
  onOpenSurveyList(): void {
    if (this.data.busy || !this.data.pointLabels.length) return;
    // 先渲染列表再定位到当前选中项（scroll-into-view 需节点已存在）
    this.setData({ surveyListOpen: true, surveyListScrollInto: '' }, () => {
      this.setData({ surveyListScrollInto: `survey-point-${this.data.pointIndex}` });
    });
  },
  onCloseSurveyList(): void {
    this.setData({ surveyListOpen: false, surveyListScrollInto: '' });
  },
  onSurveyListPick(event: WechatMiniprogram.TouchEvent): void {
    const pointIndex = Number(event.currentTarget.dataset.index);
    if (!this.data.points[pointIndex]) return;
    this.setData({ pointIndex }, () => this.fillSurvey());
    this.onCloseSurveyList();
  },
  onPointMapTap(screen: { x: number; y: number }): void {
    const vp = this.pointMap?.vp;
    if (!vp || this.data.busy || this.data.phase !== 'idle') return;
    const hit = pickPointOnScreen(this.data.points.filter((point) => point.floorId === vp.floorId),
      (point) => point, vp, screen, 20);
    if (!hit) {
      this.closePointMenu();
      return;
    }
    const { point } = hit;
    this.setData({ pointIndex: this.data.points.indexOf(point), selectedPointId: point.pointId,
      selectedWalls: measurementWalls(point) }, () => this.refreshProgress());
  },
  closePointMenu(): void {
    this.setData({ selectedPointId: '', selectedWalls: [] });
    this.pointMap?.requestDraw();
  },
  drawPointMap(ctx: WechatMiniprogram.CanvasRenderingContext.CanvasRenderingContext2D,
    vp: NonNullable<CollectMapView['vp']>): void {
    const points = this.data.points.filter((point) => point.floorId === vp.floorId);
    const selected = points.find((point) => point.pointId === this.data.selectedPointId);
    if (selected) {
      drawReferenceWalls(ctx, vp, selected, appContext.map.floors[vp.floorId]?.geometry.walls ?? [],
        this.data.selectedWalls.flatMap((wall, index) => wall.hit
          ? [{ id: wall.id, ...wall.hit, marker: index === 0 ? 'A' : 'B' }] : []));
    }
    ctx.save();
    ctx.font = '11px sans-serif';
    ctx.textAlign = 'center';
    for (const point of points) {
      const { sx, sy } = vp.toScreen(point.x, point.y);
      const active = point === selected;
      ctx.beginPath();
      ctx.arc(sx, sy, active ? 8 : 5, 0, 2 * Math.PI);
      ctx.fillStyle = active ? '#2563eb' : this.completedMapPoints?.has(point.pointId) ? '#15803d' : '#ffffff';
      ctx.fill();
      ctx.strokeStyle = active ? '#ffffff' : '#2563eb';
      ctx.lineWidth = 2;
      ctx.stroke();
      if (active || this.task() !== 'FP') {
        ctx.strokeStyle = '#ffffff';
        ctx.strokeText(point.pointId, sx, sy - 11);
        ctx.fillStyle = '#172554';
        ctx.fillText(point.pointId, sx, sy - 11);
      }
    }
    ctx.restore();
  },
  drawSurveyPointMap(ctx: WechatMiniprogram.CanvasRenderingContext.CanvasRenderingContext2D,
    vp: NonNullable<CollectMapView['vp']>): void {
    const points = this.data.points.filter((point) => point.floorId === vp.floorId);
    const selected = this.data.points[this.data.pointIndex];
    ctx.save();
    ctx.font = '11px sans-serif';
    ctx.textAlign = 'center';
    for (const point of points) {
      const { sx, sy } = vp.toScreen(point.x, point.y);
      const active = point === selected;
      ctx.beginPath();
      ctx.arc(sx, sy, active ? 8 : 5, 0, 2 * Math.PI);
      ctx.fillStyle = active ? '#2563eb' :
        point.collectable === false ? FP_POINT_COLORS.unavailable : '#ffffff';
      ctx.fill();
      ctx.strokeStyle = active ? '#ffffff' : '#2563eb';
      ctx.lineWidth = 2;
      ctx.stroke();
      if (active || this.task() !== 'FP') {
        ctx.strokeStyle = '#ffffff';
        ctx.strokeText(point.pointId, sx, sy - 11);
        ctx.fillStyle = '#172554';
        ctx.fillText(point.pointId, sx, sy - 11);
      }
    }
    ctx.restore();
  },
  refreshPlan(index = 0): void {
    const plans = this.matchingPlans();
    const planIndex = plans[index] ? index : plans.length > 0 ? 0 : -1;
    const plan = this.data.trial ? null : plans[planIndex];
    const points = this.data.trial ? candidates(this.task()) :
      (plan?.points ?? []).map((p) => ({ ...p, measurementWalls: publishedMeasurementWalls(p) }));
    this.setData({ planIndex, planLabels: plans.map((p) => `${p.batch} · ${p.planId}`),
      points, pointLabels: points.map((p) => p.pointId), pointIndex: 0, selectedPointId: '', selectedWalls: [] }, () => {
      this.refreshProgress(true);
      this.syncPointMap();
      void this.loadCalibrationPlan(plan?.task === 'CAL' ? plan.planId : '');
    });
  },

  refreshProgress(advance = false): void {
    const progress = this.records();
    const task = this.task();
    const isRoute = task === 'DY' || task === 'NV' || task === 'UT';
    const attempts = progress.filter((p) => p.status === 'complete');
    const done = new Set(attempts.map((p) => p.runId)).size;
    const expected = fieldSteps(task).length;
    const byPoint = new Map<string, Set<number>>();
    for (const row of attempts) {
      if (!row.pointId) continue;
      const parts = byPoint.get(row.pointId) ?? new Set<number>();
      parts.add(row.part);
      byPoint.set(row.pointId, parts);
    }
    const isComplete = (p: Candidate) => (byPoint.get(p.pointId)?.size ?? 0) >= expected;
    const skipped = this.skipped();
    const eligible = (p: Candidate) => !isComplete(p) && !skipped.includes(p.pointId);
    const preferred = task === 'FP' ? this.data.points.findIndex((p) => eligible(p) && this.data.makeup.includes(p.pointId)) : -1;
    const next = preferred >= 0 ? preferred : this.data.points.findIndex(eligible);
    const complete = this.data.points.filter(isComplete).length;
    this.completedMapPoints = new Set(this.data.points.filter(isComplete).map((point) => point.pointId));
    const issueCount = progress.filter((p) => p.status !== 'complete' &&
      !attempts.some((attempt) => p.pointId === attempt.pointId && (task === 'CAL' || p.part === attempt.part))).length;
    const pointIndex = advance && next >= 0 && !isRoute && !this.data.selectedPointId ? next : this.data.pointIndex;
    const selectedPoint = this.data.points[pointIndex];
    const directions = selectedPoint && (task === 'FP' || task === 'ST') ? FIELD_ORIENTATIONS : [];
    const selectedParts = selectedPoint ? byPoint.get(selectedPoint.pointId) : undefined;
    const directionCompleted = directions.filter((_, i) => selectedParts?.has(i + 1)).length;
    this.setData({
      progressText: isRoute ? `已完成 ${done} 次路线` : `已完成 ${complete}/${this.data.points.length} 点${issueCount ? ` · ${issueCount} 段待补采` : ''}${skipped.length ? ` · 已跳过 ${skipped.length} 点` : ''}`,
      runsToday: done,
      pending: this.outbox?.pendingCount() ?? 0,
      pointIndex, directionCompleted, selectedPointComplete: !!selectedPoint && isComplete(selectedPoint),
      directionProgress: directions.map((direction, i) =>
        `${DIRECTION_NAMES[direction]} ${selectedParts?.has(i + 1) ? '已完成' : '待采'}`).join(' · '),
    });
    this.pointMap?.requestDraw();
    if (task === 'NV') {
      const nextMethod = rotatedFieldMethod(Math.max(0, NAV_DAYS.indexOf(this.data.day)), done);
      this.setData({ nextMethod: nextMethod ?? '', methodIndex: nextMethod ? METHODS.indexOf(nextMethod) : 0,
        methodLabels: METHODS.map((method) => METHOD_LABELS[method]) });
    } else if (task === 'UT') {
      // T9 独立实验：不按日期轮换，只按已完成轮次交替 B/C。
      const nextMethod = UT_METHODS[done % UT_METHODS.length];
      this.setData({ nextMethod, methodIndex: UT_METHODS.indexOf(nextMethod),
        methodLabels: UT_METHODS.map((method) => METHOD_LABELS[method]) });
    }
  },

  records() {
    const plan = this.currentPlan();
    const task = this.task();
    const day = this.data.day;
    const index = new Map((this.outbox?.progress() ?? []).map((row) => [row.segmentId, row]));
    for (const row of this.remoteProgress) index.set(row.segmentId, row);
    return [...index.values()].filter((p) => {
      if (p.task !== task || p.mapVersion !== this.mapVersion) return false;
      if (!plan) return this.data.trial && !p.planId && p.batch === `trial-${day}`;
      if (p.planId !== plan.planId) return false;
      const started = fieldDay(p.startedAtEpoch);
      return started === day || (started < day && (task === 'CAL' || task === 'FP'));
    });
  },

  remainingSteps(point: Candidate) {
    const task = this.task();
    const done = new Set(this.records().filter((p) => p.pointId === point.pointId && p.status === 'complete')
      .map((p) => task === 'CAL' ? 1 : p.part));
    return fieldSteps(task, this.data.trial).filter((step) => !done.has(step.part));
  },

  skipKey(): string {
    return `field:skips:${this.owner}:${this.data.day}:${this.currentPlan()?.planId ?? this.task()}`;
  },
  skipped(): string[] { return (wx.getStorageSync(this.skipKey()) || []) as string[]; },
  onSkip(): void {
    const point = this.data.points[this.data.pointIndex];
    if (!point || !this.outbox || !this.remainingSteps(point).length) return;
    const reasons = ['参照待复核', '信号待复核', '现场暂不可达', '设备异常'];
    wx.showActionSheet({ itemList: reasons, success: ({ tapIndex }) => {
      try {
        const noteId = fieldId();
        this.outbox!.enqueue('notes', noteId, { noteId, t: Date.now(), kind: 'skip',
          day: this.data.day, planId: this.currentPlan()?.planId, pointId: point.pointId, note: reasons[tapIndex] });
        wx.setStorageSync(this.skipKey(), [...new Set([...this.skipped(), point.pointId])]);
        this.closePointMenu();
        this.refreshProgress(true);
        void this.flush();
      } catch (error) { this.setData({ issue: message(error) }); }
    } });
  },

  onTab(e: WechatMiniprogram.TouchEvent): void {
    if (this.data.phase !== 'idle' || this.data.busy) return;
    this.stopScan();
    const tab = Number(e.currentTarget.dataset.index);
    this.setData({ tab, issue: '', form: {} });
    if (tab !== 0) {
      this.closePointMenu();
      this.syncPointMap();
    }
    if (tab === 0) this.refreshPlan();
    if (tab === 1) this.onScan();
    if (tab === 3) void this.loadFingerprintBuildPlans();
    if (tab === 2) {
      const today = fieldDay();
      if (this.data.day !== today) {
        this.setData({ day: today, plans: [], planIndex: -1 });
        this.openSurvey();
        void this.loadPlans().then(() => { if (!this.disposed && this.data.tab === 2) this.openSurvey(); });
      } else {
        this.openSurvey();
      }
    }
  },
  onTask(e: WechatMiniprogram.PickerChange): void {
    if (this.data.phase !== 'idle') return;
    const taskIndex = Number(e.detail.value);
    if (!TASKS[taskIndex]) return;
    this.setData({ taskIndex, form: {} }, () => {
      if (this.data.tab === 2) this.openSurvey();
      else this.refreshPlan();
    });
  },
  onDate(e: WechatMiniprogram.PickerChange): void {
    this.setData({ day: String(e.detail.value), plans: [], planIndex: -1 });
    if (this.data.tab === 0) this.refreshPlan();
    void this.loadPlans();
  },
  onPlan(e: WechatMiniprogram.PickerChange): void { this.refreshPlan(Number(e.detail.value)); },
  onTrial(e: WechatMiniprogram.SwitchChange): void {
    this.setData({ trial: e.detail.value }, () => this.refreshPlan());
  },
  startScan(): Promise<void> {
    if (this.source) return Promise.resolve();
    const source = createBeaconSource(appContext.dataSource, appContext.map);
    this.source = source;
    this.stopStatus = source.subscribeStatus?.((status) => {
      if (this.source === source) this.setData({ scanText: status.message || status.phase });
    }) ?? null;
    if (this.data.tab === 1) {
      this.showBeacons();
      this.timer = setInterval(() => this.showBeacons(), 1000);
    }
    return source.start((sample) => { if (this.source === source) this.acceptSample(sample); })
      .catch((error) => {
        if (this.source === source) this.stopScan();
        throw error;
      });
  },

  stopScan(): void {
    this.stopPreparationHeading();
    this.stopStatus?.();
    this.stopStatus = null;
    this.source?.stop();
    this.source = null;
    if (this.timer) clearInterval(this.timer);
    this.timer = null;
  },

  acceptSample(sample: BeaconSample): void {
    const id = appContext.map.beaconRegistry.resolve(sample.uuid, sample.major, sample.minor);
    if (!id || !Number.isFinite(sample.rssi) || sample.rssi < -127 || sample.rssi >= 0) return;
    if (this.data.tab === 1) this.readings?.set(id, { rssi: sample.rssi, t: sample.observedAtMs });
    const segment = this.active;
    if (!segment || this.data.phase !== 'record' || segment.rssi.length >= 30000) return;
    const t = sample.observedAtMs;
    if (t >= segment.startedAtEpoch && t <= Date.now()) {
      segment.rssi.push({ t, beacon: id, rssi: Math.round(sample.rssi) });
    }
  },

  showBeacons(): void {
    const now = Date.now();
    this.setData({ beacons: appContext.map.beacons.map((beacon) => {
      const last = this.readings?.get(beacon.beaconId);
      return { id: beacon.beaconId, name: beacon.locationDesc ?? beacon.beaconId,
        rssi: last?.rssi ?? null, seen: !!last && now - last.t <= 10000,
        last: last ? new Date(last.t).toLocaleTimeString() : '未收到' };
    }) });
  },
  onScan(): void { void this.startScan().catch((error) => this.setData({ issue: message(error) })); },
  onBeacon(e: WechatMiniprogram.TouchEvent): void {
    this.setData({ selectedBeacon: String(e.currentTarget.dataset.id), form: {} });
  },
  onAction(e: WechatMiniprogram.PickerChange): void { this.setData({ actionIndex: Number(e.detail.value) }); },
  onInspection(): void {
    const id = this.data.selectedBeacon;
    const beacon = this.data.beacons.find((b) => b.id === id);
    const last = this.readings?.get(id);
    if (!beacon || this.data.demo || !this.outbox) return;
    try {
      const inspectionId = fieldId();
      this.outbox.enqueue('inspections', inspectionId, { inspectionId, beaconId: id,
        mapVersion: this.mapVersion, t: Date.now(), action: ACTIONS[this.data.actionIndex],
        location: this.data.form.location || beacon.name, mount: this.data.form.mount ?? '',
        rssi: last?.rssi, lastSeen: last?.t, note: this.data.form.note ?? '' });
      this.setData({ selectedBeacon: '', form: {}, pending: this.outbox.pendingCount() });
      void this.flush();
    } catch (error) { this.setData({ issue: message(error) }); }
  },

  async onStart(): Promise<void> {
    if (!this.outbox || this.active || this.data.busy || this.data.needsRetry || this.data.phase !== 'idle') return;
    const task = this.task();
    const plan = this.currentPlan();
    const point = this.data.points[this.data.pointIndex] ?? null;
    if ((task === 'CAL' || task === 'FP' || task === 'ST') && point?.pointId !== this.data.selectedPointId) return;
    if ((!plan && !this.data.trial) || (!this.data.trial && this.data.demo) || !point) {
      this.setData({ issue: '请选择有效任务清单和点位。模拟数据仅可试采。' });
      return;
    }
    if (plan && this.data.day !== fieldDay()) {
      this.setData({ issue: '正式采集请选择今天的采集日期' }); return;
    }
    if (task === 'NV' || task === 'UT') {
      const config = this.data.configs[this.data.configIndex];
      const method = this.methods()[this.data.methodIndex];
      const identity = appContext.getIdentity();
      if (!plan || !config || identity?.userId !== this.owner || !this.data.nextMethod || method !== this.data.nextMethod) {
        this.setData({ issue: '请选择正式清单、冻结配置和本轮指定方法' });
        return;
      }
      armFieldNavigation({ plan, config, owner: identity.userId,
        selection: { planId: plan.planId, configId: config.configId, runId: fieldId(), method } });
      wx.reLaunch({ url: '/pages/map/map', fail: () => {
        takeFieldNavigation();
        this.setData({ issue: '未能打开导航实验' });
      } });
      return;
    }
    if (task === 'DY' && plan && this.data.runsToday >= 6) {
      this.setData({ issue: '本日 6 次动态任务已完成' }); return;
    }
    const steps = task === 'DY' ? fieldSteps(task) : this.remainingSteps(point);
    if (!steps.length) { this.setData({ issue: '该点已完成' }); return; }
    this.setData({ busy: true, issue: '', summary: '' });
    try {
      await this.startScan();
      if (this.disposed || !this.source) return;
      this.segmentPoint = task === 'DY' ? null : point;
      this.segmentPlan = plan;
      this.savedRunId = fieldId();
      this.steps = steps;
      this.stepIndex = 0;
      this.prepare(3);
      this.timer = setInterval(() => this.tick(), 250);
    } catch (error) { this.setData({ issue: message(error) }); }
    finally { if (!this.disposed) this.setData({ busy: false }); }
  },

  prepare(seconds: number): void {
    const { orientation } = this.steps[this.stepIndex];
    this.prepareSeconds = orientation ? 3 : seconds;
    this.headingWaitSince = orientation ? Date.now() : 0;
    this.headingBuzzed = false;
    this.headingManuallyConfirmed = false;
    this.deadline = orientation ? 0 : Date.now() + seconds * 1000;
    this.setData({ phase: 'prepare', frames: 0, undo: false, selectedPointId: '', selectedWalls: [],
      remaining: orientation ? `请转向${DIRECTION_NAMES[orientation]}` : `准备 · ${seconds}s`,
      headingTarget: orientation ? DIRECTION_NAMES[orientation] : '',
      headingCurrent: '', headingStatus: '正在读取罗盘…', headingManual: false }, () => this.syncPointMap());
    if (!orientation) {
      wx.vibrateShort({ type: 'light' });
      return;
    }
    // Keep the native listener running between directions to avoid racing stop/start.
    if (this.headingSource?.isRunning) return;
    this.headingSource = new HeadingSource({
      api: {
        startCompass: (options) => wx.startCompass(options),
        stopCompass: () => wx.stopCompass(),
        onCompassChange: (callback) => wx.onCompassChange(callback),
        // WeChat types listener removal as a general callback, but it takes the registered listener.
        offCompassChange: (callback) => wx.offCompassChange?.(callback as unknown as WechatMiniprogram.OffCompassChangeCallback),
        startDeviceMotionListening: (options) => wx.startDeviceMotionListening(options),
        stopDeviceMotionListening: () => wx.stopDeviceMotionListening(),
        onDeviceMotionChange: (callback) => wx.onDeviceMotionChange(callback),
      },
      compassOnly: true,
      timeoutMs: HEADING_READ_TIMEOUT_MS,
      onHeading: (raw) => this.updatePreparationHeading(raw),
      onUnavailable: () => this.preparationHeadingUnavailable(),
    });
    this.headingSource.start();
  },

  stopPreparationHeading(): void {
    this.headingSource?.stop();
    this.headingSource = null;
  },

  updatePreparationHeading(raw: number): void {
    if (this.data.phase !== 'prepare' || this.headingManuallyConfirmed) return;
    const orientation = this.steps[this.stepIndex]?.orientation;
    if (!orientation) return;
    const now = Date.now();
    // A stationary phone may stop emitting compass changes during the countdown.
    this.headingWaitSince = 0;
    const heading = applyHeadingOffset(raw, appContext.getHeadingOffsetSetting());
    const direction = DIRECTION_NAMES[FIELD_ORIENTATIONS[Math.round(heading / 90) % 4]];
    const target = FIELD_ORIENTATIONS.indexOf(orientation) * 90;
    const offset = (target - heading + 540) % 360 - 180;
    const aligned = Math.abs(offset) <= HEADING_TOLERANCE;
    if (!aligned) {
      this.deadline = 0;
    } else if (!this.deadline) {
      this.deadline = now + this.prepareSeconds * 1000;
      if (!this.headingBuzzed) {
        this.headingBuzzed = true;
        wx.vibrateShort({ type: 'medium' });
      }
    }
    this.setData({
      headingCurrent: `${direction} · ${Math.round(heading) % 360}°`, headingManual: false,
      headingStatus: aligned ? '已对准，请保持朝向' : offset > 0 ? '向右转' : '向左转',
    });
  },

  preparationHeadingUnavailable(): void {
    this.headingWaitSince = 0;
    this.deadline = 0;
    this.setData({ headingCurrent: '', headingManual: true, headingStatus: '罗盘不可用，请按现场参照确认朝向' });
  },

  onConfirmPreparationHeading(): void {
    if (this.data.phase !== 'prepare' || !this.data.headingManual || this.headingManuallyConfirmed) return;
    this.headingManuallyConfirmed = true;
    this.deadline = Date.now() + this.prepareSeconds * 1000;
    this.setData({ headingManual: false, headingStatus: '已手动确认，请保持朝向' });
  },

  begin(now: number): void {
    const task = this.task();
    const step = this.steps[this.stepIndex];
    const plan = this.segmentPlan;
    const pointId = this.segmentPoint?.pointId;
    const batch = plan?.batch ?? `trial-${this.data.day}`;
    const retrySource = task === 'FP' && plan
      ? (this.outbox?.progress() ?? []).filter((p) => p.task === 'FP' && p.planId && p.mapVersion === this.mapVersion)
      : pointId ? this.records() : [];
    const retry = retrySource.filter((p) => p.pointId === pointId && p.part === step.part &&
      p.status !== 'complete').sort((a, b) => a.startedAtEpoch - b.startedAtEpoch);
    const id = [batch, task, pointId, step.orientation, fieldId()].filter(Boolean).join('-');
    this.active = {
      segmentId: retry.length ? `${id}-R${retry.length}` : id,
      runId: this.savedRunId, task, batch, planId: plan?.planId, pointId,
      orientation: step.orientation, part: step.part, startedAtEpoch: now,
      endedAtEpoch: now, mapVersion: this.mapVersion,
      paramsVersion: cachedRuntimeParams()?.paramsVersion ?? 'unknown',
      deviceId: this.outbox!.deviceId, status: 'aborted', method: 'RAW',
      rssi: [], checkpoints: [], retryOf: retry[0]?.segmentId,
    };
    this.deadline = now + step.duration * 1000;
    this.lastSave = now;
    this.lastTapeAt = 0;
    this.warned = false;
    this.outbox!.saveDraft(this.active);
    this.setData({ phase: 'record', checkpointText: task === 'DY' ? this.nextCheckpoint() : '' });
  },

  tick(): void {
    const now = Date.now();
    try {
      if (this.data.phase === 'prepare') {
        if (this.headingWaitSince && now - this.headingWaitSince >= HEADING_READ_TIMEOUT_MS) {
          this.preparationHeadingUnavailable();
        }
        if (this.deadline && now >= this.deadline) this.begin(now);
        else this.setData({ remaining: this.deadline
          ? `准备 · ${Math.ceil((this.deadline - now) / 1000)}s`
          : `请转向${this.data.headingTarget}` });
        return;
      }
      const segment = this.active;
      if (!segment) return;
      const elapsed = Math.floor((now - segment.startedAtEpoch) / 1000);
      const duration = this.steps[this.stepIndex].duration;
      const formalFingerprint = segment.task === 'FP' && !!segment.planId;
      // 达标即停：正式 FP 段每个 tick 判定一次，≥12 s 达标立即结束，无需等到 25 s。
      const fingerprint = formalFingerprint ? fingerprintWindow(segment, this.deadline, now) : null;
      if (fingerprint) this.deadline = fingerprint.deadline;
      if (duration > 0 && now >= this.deadline) {
        const minimum = segment.planId && segment.task === 'ST' ? 60 : 1;
        let reason = fingerprint?.reason ?? (segment.rssi.length < minimum ? `RSSI 样本不足 ${minimum} 条` : '');
        if (segment.planId && segment.task === 'CAL') {
          const beacons = new Set(segment.rssi.filter((frame) => frame.t <= this.deadline &&
            frame.rssi >= -100 && frame.rssi <= -20).map((frame) => frame.beacon));
          if (beacons.size < 3) reason = '120 秒内有效信标不足 3 枚';
        }
        const ok = !reason;
        if (fingerprint) segment.rssi = segment.rssi.filter((frame) => frame.t <= fingerprint.deadline);
        this.finish(ok ? 'complete' : 'suspect', reason, fingerprint?.deadline ?? now);
        if (ok && ++this.stepIndex < this.steps.length) this.prepare(5);
        else this.resetRun(!ok);
        return;
      }
      if (duration === 0 && elapsed >= 29 * 60) {
        this.finish('aborted', '超过连续录制上限');
        this.resetRun();
        return;
      }
      const seconds = duration > 0 ? Math.ceil((this.deadline - now) / 1000) : Math.max(0, elapsed);
      if (duration > 0 && !segment.orientation && seconds <= 3 && !this.warned) {
        this.warned = true;
        wx.vibrateShort({ type: 'medium' });
      }
      const extended = formalFingerprint && this.deadline > segment.startedAtEpoch + duration * 1000;
      const remaining = duration ? `${segment.orientation ?? '标定采集'} · ${extended ? '延长采集 ' : ''}${seconds}s` :
        `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, '0')}${seconds >= 900 ? ' · 已超过 15 分钟' : ''}`;
      this.setData({ remaining, frames: segment.rssi.length, undo: now - this.lastTapeAt <= 2000 });
      if (now - this.lastSave >= 5000) {
        segment.endedAtEpoch = now;
        this.outbox!.saveDraft(segment);
        this.lastSave = now;
      }
    } catch (error) {
      this.setData({ issue: `本地保存失败。请留在此页，释放空间后点「重试本地保存」。${message(error)}`,
        needsRetry: this.active !== null });
      this.resetRun(false);
    }
  },

  nextCheckpoint(): string {
    const n = checkpointCount(this.active?.checkpoints ?? []);
    const next = this.data.points[n];
    return next ? `下一点 ${next.pointId} · 已打 ${n}/${this.data.points.length}` :
      `已打 ${n}/${this.data.points.length}`;
  },
  onMark(): void {
    const active = this.active;
    if (active?.task !== 'DY') return;
    const point = this.data.points[checkpointCount(active.checkpoints)];
    if (!point) return;
    const now = Date.now();
    active.checkpoints.push({ pointId: point.pointId, t: now, undo: false });
    this.lastTapeAt = now;
    this.setData({ checkpointText: this.nextCheckpoint(), undo: true });
    wx.vibrateShort({ type: 'light' });
  },
  onUndo(): void {
    const active = this.active;
    const last = active?.checkpoints[active.checkpoints.length - 1];
    if (!active || !last || last.undo || Date.now() - this.lastTapeAt > 2000) return;
    active.checkpoints.push({ pointId: last.pointId, t: Date.now(), undo: true });
    this.lastTapeAt = 0;
    this.setData({ checkpointText: this.nextCheckpoint(), undo: false });
  },
  onFinishRoute(): void {
    if (this.active?.task !== 'DY') return;
    const segment = this.active;
    const n = checkpointCount(segment.checkpoints);
    const markTimes = segment.checkpoints.reduce<typeof segment.checkpoints>((items, event) => {
      if (event.undo) items.pop(); else items.push(event);
      return items;
    }, []).map((event) => `${event.pointId} ${new Date(event.t).toLocaleTimeString()}`);
    this.setData({ summary: [`已打 ${n}/${this.data.points.length} · ${segment.rssi.length} 条 RSSI`, ...markTimes].join('\n') });
    this.finish(segment.rssi.length ? 'complete' : 'suspect', segment.rssi.length ? '' : '无 RSSI');
    this.resetRun();
  },
  onAbort(): void {
    wx.showActionSheet({ itemList: ['人流', '身体不适', '设备', '其他'], success: ({ tapIndex }) => {
      this.finish('aborted', ['人流', '身体不适', '设备', '其他'][tapIndex]);
      this.resetRun();
    } });
  },

  finish(status: FieldSegment['status'], reason: string, endedAt = Date.now()): void {
    const segment = this.active;
    if (!segment || !this.outbox) return;
    if (!this.pendingFinalized) {
      segment.endedAtEpoch = Math.max(segment.startedAtEpoch, endedAt);
      segment.status = status;
      segment.reason = reason;
    }
    this.pendingFinalized = true;
    try {
      this.outbox.enqueue('segments', segment.segmentId, segment);
      this.outbox.clearDraft();
      this.pendingFinalized = false;
      this.active = null;
      this.setData({ lastRun: segment.runId, needsRetry: false });
      this.refreshProgress();
      if (segment.task !== 'DY') void this.flush();
    } catch (error) {
      this.setData({ issue: `本地写入失败：${message(error)}`, needsRetry: true, phase: 'idle' });
      this.stopScan();
      throw error;
    }
  },
  retrySave(): void {
    try {
      this.finish('aborted', '重试本地保存');
      this.resetRun();
    } catch { /* The original payload remains in memory. */ }
  },
  resetRun(advance = true): void {
    this.stopScan();
    this.steps = [];
    this.setData({ phase: 'idle', remaining: '', undo: false }, () => this.syncPointMap());
    if (this.outbox) this.refreshProgress(advance);
  },

  onInput(e: WechatMiniprogram.Input): void {
    const key = e.currentTarget.dataset.field as string;
    const value = e.detail.value;
    if (this.data.tab === 2 && /^(distance|check)[AB]$/.test(key)) {
      this.setData({ [`form.${key}`]: value }, () => this.refreshSurveyPreview());
    } else {
      this.setData({ [`form.${key}`]: value });
    }
  },
  onRating(e: WechatMiniprogram.PickerChange): void { this.setData({ ratingIndex: Number(e.detail.value) }); },
  onCleared(e: WechatMiniprogram.SwitchChange): void { this.setData({ cleared: e.detail.value }); },
  onNote(): void {
    if (!this.outbox || this.data.demo) return;
    const { note, runId, pointId, observed } = this.data.form;
    const text = note?.trim();
    const lastRun = this.data.lastRun;
    if ((!text && !observed && this.data.ratingIndex < 0) ||
        (observed && (!pointId?.trim() || !/^(?:[01]\d|2[0-3]):[0-5]\d:[0-5]\d$/.test(observed)))) {
      this.setData({ issue: '请输入场记，时刻请用 HH:mm:ss' }); return;
    }
    try {
      const noteId = fieldId();
      const t = observed ? Date.parse(`${this.data.day}T${observed}+08:00`) : Date.now();
      this.outbox.enqueue('notes', noteId, { noteId, runId: runId?.trim() || lastRun || null,
        pointId: pointId?.trim() || null, t, note: text || '检查点观察记录', cleared: this.data.cleared,
        isArrival: Boolean(observed && pointId?.trim()),
        turnRating: RATINGS[this.data.ratingIndex] ?? null });
      this.setData({ form: {}, pending: this.outbox.pendingCount(), cleared: false, ratingIndex: -1 });
      void this.flush();
    } catch (error) { this.setData({ issue: message(error) }); }
  },

  surveyKey(): string {
    const group = this.surveyPoints.some((p) => p.sharedPointId) ? 'shared' : this.task();
    return `field:survey:${this.owner}:${this.mapVersion}:${group}`;
  },
  surveyRecords(): SurveyPoint[] {
    const records = (wx.getStorageSync(this.surveyKey()) || []) as SurveyPoint[];
    const shared = appContext.map.fieldSurveyPoints;
    if (this.surveyPoints.some((p) => p.sharedPointId)) {
      const aliases = new Map(shared.flatMap((p) => [p.pointId, p.calibrationId, p.checkpointId]
        .filter((id): id is string => !!id).map((id) => [id, p.pointId] as const)));
      const taskIds = new Map(this.surveyPoints.map((p) => [p.sharedPointId, p.pointId]));
      const latest = new Map(records.map((p) => [p.pointId, p]));
      const published = new Map<string, SurveyPoint>();
      for (const plan of this.data.plans.filter((p) => p.mapVersion === this.mapVersion && p.task !== 'FP')
        .sort((a, b) => a.day.localeCompare(b.day) || a.batch.localeCompare(b.batch, 'en', { numeric: true }))) {
        for (const point of plan.points) {
          const id = aliases.get(point.pointId);
          const previous = id ? published.get(id) : undefined;
          if (id && (!previous || surveyRank(point) >= surveyRank(previous))) {
            published.set(id, { ...point, pointId: id });
          }
        }
      }
      return [...records, ...[...published.values()].filter((point) => {
        const draft = latest.get(point.pointId);
        return !draft || surveyRank(point) > surveyRank(draft);
      })].flatMap((point) => {
        const taskId = taskIds.get(point.pointId);
        return taskId ? [{ ...point, pointId: taskId }] : [];
      });
    }
    const latest = new Map(records.map((p) => [p.pointId, p]));
    const published = this.matchingPlans()[0]?.points ?? [];
    return [...records, ...published.filter((p) => {
      const local = latest.get(p.pointId);
      return !local || surveyRank(p) > surveyRank(local);
    })];
  },
  saveSurveyRecords(records: SurveyPoint[]): void {
    const key = this.surveyKey();
    const aliases = new Map(this.surveyPoints.flatMap((p) => p.sharedPointId ? [[p.pointId, p.sharedPointId] as const] : []));
    if (!aliases.size) {
      wx.setStorageSync(key, records);
      return;
    }
    const ids = new Set(aliases.values());
    const existing = (wx.getStorageSync(key) || []) as SurveyPoint[];
    const others = existing.filter((p) => !ids.has(p.pointId));
    wx.setStorageSync(key, [...others, ...records.map((p) => ({ ...p, pointId: aliases.get(p.pointId) ?? p.pointId }))]);
  },
  surveyDrafts(): SurveyPoint[] {
    return [...new Map(this.surveyRecords().map((p) => [p.pointId, p])).values()];
  },
  surveyPlanPoints(records: readonly SurveyPoint[]): SurveyPoint[] {
    const drafts = new Map(records.map((p) => [p.pointId, p]));
    return this.surveyPoints.flatMap((p) => {
      const draft = drafts.get(p.pointId);
      return draft ? [draft] : [];
    });
  },
  refreshSurveyPublication(): void {
    const drafts = this.surveyPlanPoints(this.surveyRecords()).length;
    const [min, max] = planPointLimits(this.task());
    const published = this.matchingPlans()[0];
    this.setData({ drafts, surveyPlanReady: drafts >= min && drafts <= max,
      surveyPublished: published ? `当前生效批次 ${published.batch} · ${published.day} 起沿用` : '当前任务尚未发布正式清单',
      surveyPlanRequirement: min === max ? `须登记 ${min} 个点，已登记 ${drafts} 个` :
        `每份清单可发布 ${min} 至 ${max} 个点，已登记 ${drafts} 个` });
  },
  openSurvey(): void {
    this.surveyPoints = candidates(this.task());
    this.setData({ points: this.surveyPoints, pointLabels: this.surveyPoints.map((p) => p.pointId),
      pointIndex: 0, surveyUncollectable: this.surveyPoints.some((p) => p.collectable === false) },
      () => this.fillSurvey());
    this.refreshSurveyPublication();
  },
  fillSurvey(): void {
    const point = this.surveyPoints[this.data.pointIndex];
    if (!point) return;
    const saved = this.surveyDrafts().find((p) => p.pointId === point.pointId);
    const a = point.references?.find((wall) => wall.axis === 'X');
    const b = point.references?.find((wall) => wall.axis === 'Y');
    this.setData({ form: {
      ...this.data.form,
      anchorA: a?.label ?? '', anchorB: b?.label ?? '',
      distanceA: String(saved?.distanceA ?? a?.baseDistance ?? ''),
      distanceB: String(saved?.distanceB ?? b?.baseDistance ?? ''),
      checkA: String(saved?.checkA ?? a?.baseDistance ?? ''),
      checkB: String(saved?.checkB ?? b?.baseDistance ?? ''),
      note: saved?.note ?? '',
    } }, () => this.refreshSurveyPreview());
  },
  refreshSurveyPreview(): void {
    const point = this.surveyPoints[this.data.pointIndex];
    if (!point) return;
    const refs = point.references ?? [];
    const [a, b] = refs;
    const base = surveyCoordinates(refs, a?.baseDistance, b?.baseDistance);
    const { distanceA, distanceB, checkA, checkB } = this.data.form;
    const measured = surveyCoordinates(refs, Number(distanceA), Number(distanceB));
    const check = surveyCoordinates(refs, Number(checkA), Number(checkB));
    const history = this.surveyRecords().filter((p) => p.pointId === point.pointId);
    const latest = history[history.length - 1];
    const baseOffset = base ? surveyOffset(point, base) : null;
    const wallOffset = measured ? surveyRouteWallOffset(refs, measured.y, Number(distanceB)) : null;
    this.setData({ surveyDisplay: {
      base: base ? `base 换算 ${base.x}, ${base.y} · ${baseOffset === 0 ? '与候选坐标一致' : `相差 ${baseOffset} m`}` :
        '缺少测距参照或基准坐标不一致，暂不能换算坐标',
      baseDistances: base ? `base 距离 A ${a.baseDistance} m / B ${b.baseDistance} m` : '',
      preview: measured ? `${measured.x}, ${measured.y}` : '—',
      offset: measured ? `距候选点 ${surveyOffset(point, measured)} m` +
        (wallOffset === null ? '' : ` · 墙距复核偏差 ${wallOffset} m`) : '',
      version: latest ? latest.surveyVersion ?? '旧记录' : 'base（待现场确认）',
      history: [...history].reverse().map((p) =>
        `${p.surveyVersion ?? '旧记录'} · ${p.measuredOn} · A ${p.distanceA} m / B ${p.distanceB} m · (${p.x}, ${p.y})`),
      canSave: !!measured && !!check && baseOffset === 0,
    } });
  },
  onSaveSurvey(): void {
    const point = this.surveyPoints[this.data.pointIndex];
    if (!point || this.data.demo || this.data.busy) return;
    const form = this.data.form;
    const refs = point.references ?? [];
    const [a, b] = refs;
    const values = [form.distanceA, form.distanceB, form.checkA, form.checkB].map(Number);
    const [distanceA, distanceB, checkA, checkB] = values;
    const measured = surveyCoordinates(refs, distanceA, distanceB);
    const base = surveyCoordinates(refs, a?.baseDistance, b?.baseDistance);
    if (!a || !b || !base || surveyOffset(point, base) !== 0 || !measured || !surveyCoordinates(refs, checkA, checkB)) {
      this.setData({ issue: '请先确认候选点的测距参照和基准坐标，再填写有效的实测与复测距离' }); return;
    }
    try {
      const records = this.surveyRecords();
      const history = records.filter((p) => p.pointId === point.pointId);
      const surveyVersion = nextSurveyVersion([a.baseDistance, b.baseDistance, a.baseDistance, b.baseDistance],
        values, history.map((p) => ({ surveyVersion: p.surveyVersion, readings: surveyDistances(p) })));
      const latest = history[history.length - 1];
      const next: SurveyPoint = { pointId: point.pointId, floorId: point.floorId, ...measured,
        distanceA, distanceB, checkA, checkB, measuredOn: fieldDay(), anchorA: a.label, anchorB: b.label,
        surveyVersion, candidateX: point.x, candidateY: point.y, note: form.note?.trim() ?? '' };
      if (latest?.surveyVersion === surveyVersion) records[records.lastIndexOf(latest)] = next;
      else records.push(next);
      this.saveSurveyRecords(records);
      this.setData({
        issue: surveyVersion === latest?.surveyVersion ? `距离未变，沿用 ${surveyVersion}` :
          surveyVersion === 'base' ? '已确认 base，未产生坐标变更' : `复测距离已更新，已保存 ${surveyVersion}` });
      this.refreshSurveyPreview();
      this.refreshSurveyPublication();
    } catch (error) { this.setData({ issue: `未能保存复测版本：${message(error)}` }); }
  },
  onCopyAndPublish(): void { void this.publishSurvey(true); },
  onPublish(): void { void this.publishSurvey(false); },
  async publishSurvey(copyCandidates: boolean): Promise<void> {
    if (this.data.demo || this.data.busy || this.data.tab !== 2 || !this.surveyPoints.length) return;
    this.setData({ busy: true });
    try {
      const records = this.surveyRecords();
      const { baselines, skipped } = copyCandidates
        ? missingSurveyBaselines(this.surveyPoints, records, fieldDay()) : { baselines: [], skipped: [] };
      const updatedRecords = [...records, ...baselines];
      const points = this.surveyPlanPoints(updatedRecords);
      const plan = { task: this.task(), mapVersion: this.mapVersion, day: fieldDay(), points };
      const [min, max] = planPointLimits(plan.task);
      const skippedText = skipped.length ? `。${skipped.length} 个点未通过测距参照和坐标校验：${skipped.join('、')}` : '';
      if (points.length < min || points.length > max) {
        const required = min === max ? `${min}` : `${min} 至 ${max}`;
        this.setData({ issue: `清单须有 ${required} 个点，当前可发布 ${points.length} 个${skippedText}` });
        return;
      }
      if (points.some((point) => !point.surveyVersion)) {
        this.setData({ issue: '清单含旧版手填坐标。请先逐点确认换算坐标并重新保存复测，再发布。' });
        return;
      }
      const copyText = baselines.length ? `其中 ${baselines.length} 个点使用候选 base 真值。` : '';
      const skippedCount = skipped.length ? `跳过 ${skipped.length} 个未通过校验的点。` : '';
      const confirmed = await new Promise<boolean>((resolve) => wx.showModal({
        title: '发布正式清单',
        content: `发布 ${points.length} 个点，自 ${plan.day} 起沿用。${copyText}${skippedCount}批次号由系统生成。`,
        success: (r) => resolve(r.confirm), fail: () => resolve(false),
      }));
      if (!confirmed) return;
      if (baselines.length) {
        this.saveSurveyRecords(updatedRecords);
        this.refreshSurveyPreview();
        this.refreshSurveyPublication();
      }
      // Reuse the same ID if the network fails after the backend committed.
      const retryKey = `field:publish:${this.owner}:${plan.mapVersion}:${plan.task}:${plan.day}`;
      const signature = JSON.stringify(plan);
      const pending = wx.getStorageSync(retryKey) as { planId: string; signature: string } | '';
      const planId = pending && pending.signature === signature ? pending.planId : fieldId();
      wx.setStorageSync(retryKey, { planId, signature });
      const response = await appContext.http.client.request<{ batch: string; unchanged?: boolean }>({
        method: 'POST', path: '/api/v1/field/plans', body: { ...plan, planId },
      });
      if (!response.data?.batch) throw new Error('未收到系统分配的批次号');
      wx.removeStorageSync(retryKey);
      const outcome = response.data.unchanged ? '真值无调整，继续沿用正式清单' : '正式清单已发布';
      this.setData({ issue: `${outcome} · 批次 ${response.data.batch}${skippedText}` });
      void this.loadPlans();
    } catch (error) { this.setData({ issue: message(error) }); }
    finally { this.setData({ busy: false }); }
  },

  async flush(): Promise<void> {
    if (!this.outbox || this.data.demo) return;
    try {
      const identity = await appContext.refreshIdentity();
      if (identity?.userId !== this.owner) throw new Error('当前账号无法补传此记录');
      await this.outbox.flush(async (kind, body) => {
        return appContext.http.client.request({ method: 'POST', path: `/api/v1/field/${kind}`, body });
      });
      if (!this.disposed) this.setData({ pending: this.outbox.pendingCount(), issue: this.data.needsRetry ? this.data.issue : '' });
    } catch (error) {
      if (!this.disposed) this.setData({ pending: this.outbox.pendingCount(),
        issue: this.data.needsRetry ? this.data.issue : `待补传：${message(error)}` });
    }
  },
  onFlush(): void { void this.flush(); },
  onPrepareCalibration(): void {
    const plan = this.currentPlan();
    if (plan?.task === 'CAL') void this.publishSamples('calibration-sample', [plan.planId]);
  },
  calibrationKey(planId: string): string { return `field:calibration:${this.owner}:${planId}`; },
  async loadCalibrationPlan(planId: string): Promise<void> {
    if (this.data.calPlanId === planId) return;
    if (this.calTimer !== null) clearTimeout(this.calTimer);
    const sampleId = planId ? String(wx.getStorageSync(this.calibrationKey(planId)) || '') : '';
    this.setData({ calPlanId: planId, calSampleId: sampleId, calRun: null, calComputing: false, calMessage: '' });
    this.updateCalibrationRows();
    if (!planId) return;
    const defaults = appContext.http.client.request<{ defaults: CalibrationParams }>({
      method: 'GET', path: '/api/v1/config/location-params',
    }).then(({ data }) => {
      if (this.disposed || this.data.calPlanId !== planId) return;
      if (!data?.defaults) throw new Error('后端未返回默认定位参数');
      this.setData({ calDefaults: data.defaults });
      this.updateCalibrationRows();
    }).catch((error) => {
      if (!this.disposed && this.data.calPlanId === planId) this.setData({ calMessage: message(error) });
    });
    await Promise.all([defaults, this.onRefreshCalibration()]);
  },
  updateCalibrationRows(): void {
    const result = this.data.calRun?.result;
    const fitted = { ...result?.params, ...result?.thresholds };
    this.setData({ calRows: CAL_FIELDS.map(({ key, label }) => ({
      label, baseline: String(this.data.calDefaults?.[key] ?? '—'), fitted: String(fitted[key] ?? '—'),
    })) });
  },
  async onRefreshCalibration(runId?: string | WechatMiniprogram.TouchEvent): Promise<void> {
    const { calPlanId, calSampleId } = this.data;
    if (!calSampleId) return;
    if (this.calTimer !== null) clearTimeout(this.calTimer);
    this.calTimer = null;
    try {
      let run: CalibrationRun | null;
      if (typeof runId === 'string') {
        const response = await appContext.http.client.request<CalibrationRun>({
          method: 'GET', path: `/api/v1/calibration/runs/${encodeURIComponent(runId)}`,
        });
        run = response.data;
      } else {
        const response = await appContext.http.client.request<{ run: CalibrationRun | null }>({
          method: 'GET', path: `/api/v1/calibration/runs/latest?sampleId=${encodeURIComponent(calSampleId)}`,
        });
        run = response.data?.run ?? null;
      }
      if (this.disposed || this.data.calPlanId !== calPlanId || this.data.calSampleId !== calSampleId) return;
      this.showCalibrationRun(run);
    } catch (error) {
      if (!this.disposed && this.data.calPlanId === calPlanId && this.data.calSampleId === calSampleId) {
        this.setData({ calMessage: `刷新失败，点「刷新结果」重试。 ${message(error)}` });
      }
    }
  },
  showCalibrationRun(run: CalibrationRun | null): void {
    const computing = computingCalibration(run);
    this.setData({ calRun: run, calComputing: computing, calMessage: computing ? '正在拟合参数并计算网格…' :
      run?.errorMessage || run?.result?.applyBlockedReason || (run?.status === 'applied' ? '标定参数已应用' : '') });
    this.updateCalibrationRows();
    if (run && computing) {
      this.calTimer = setTimeout(() => void this.onRefreshCalibration(run.runId), 1500);
    }
  },
  async onComputeCalibration(): Promise<void> {
    const { calSampleId, busy, calComputing } = this.data;
    if (!calSampleId || busy || calComputing) return;
    this.setData({ busy: true, calMessage: '正在提交计算…' });
    try {
      const { data } = await appContext.http.client.request<CalibrationRun>({
        method: 'POST', path: '/api/v1/calibration/runs', body: { sampleId: calSampleId },
      });
      if (!data) throw new Error('未收到计算任务');
      if (!this.disposed) this.showCalibrationRun(data);
    } catch (error) {
      if (!this.disposed) this.setData({ calMessage: message(error) });
    } finally {
      if (!this.disposed) this.setData({ busy: false });
    }
  },
  async onApplyCalibration(): Promise<void> {
    const { calRun, busy } = this.data;
    if (busy || calRun?.status !== 'computed' || !calRun.result?.deployable) return;
    this.setData({ busy: true });
    try {
      const current = await appContext.http.client.request<{ paramsVersion: string }>({
        method: 'GET', path: '/api/v1/config/location-params',
      });
      if (!current.data?.paramsVersion) throw new Error('无法确认当前生效的参数版本');
      if (this.disposed) return;
      const confirmed = await new Promise<boolean>((resolve) => wx.showModal({
        title: '应用拟合参数', content: '确认将这次拟合的参数与网格应用到当前场地？',
        success: ({ confirm }) => resolve(confirm), fail: () => resolve(false),
      }));
      if (!confirmed || this.disposed) return;
      const { data } = await appContext.http.client.request<{ applied: boolean; paramVersion: string }>({
        method: 'POST', path: `/api/v1/calibration/runs/${calRun.runId}/apply`,
        body: { confirm: true, expectParamVersion: current.data.paramsVersion },
      });
      if (!data?.applied) throw new Error('后端未确认参数生效');
      clearRuntimeParams();
      clearOverlayCache();
      if (!this.disposed) {
        this.showCalibrationRun({ ...calRun, status: 'applied' });
        this.setData({ calMessage: `标定参数已生效 · ${data.paramVersion}` });
      }
    } catch (error) {
      if (!this.disposed) this.setData({ calMessage: message(error) });
    } finally {
      if (!this.disposed) this.setData({ busy: false });
    }
  },
  async loadFingerprintBuildPlans(): Promise<void> {
    if (this.data.demo || this.data.fingerprintBuildLoading) return;
    this.setData({ fingerprintBuildLoading: true, fingerprintBuildError: '',
      fingerprintBuildPlans: [], fingerprintBuildCount: 0 });
    try {
      const response = await appContext.http.client.request<{ plans: Omit<FingerprintBuildPlan, 'selected'>[] }>({
        method: 'GET', path: `/api/v1/field/fingerprint-build?mapVersion=${encodeURIComponent(this.mapVersion)}`,
      });
      if (!response.data) throw new Error('未返回 T4 清单');
      const plans = response.data.plans.map((plan) => ({ ...plan, selected: plan.active }));
      if (!this.disposed) this.setData({ fingerprintBuildPlans: plans,
        fingerprintBuildCount: plans.filter((plan) => plan.selected).length });
    } catch (error) {
      if (!this.disposed) this.setData({ fingerprintBuildError: `读取 T4 清单失败：${message(error)}` });
    } finally {
      if (!this.disposed) this.setData({ fingerprintBuildLoading: false });
    }
  },
  onFingerprintBuildPlans(e: WechatMiniprogram.CheckboxGroupChange): void {
    const selected = new Set(e.detail.value);
    const plans = this.data.fingerprintBuildPlans.map((plan) => ({ ...plan, selected: selected.has(plan.planId) }));
    this.setData({ fingerprintBuildPlans: plans, fingerprintBuildCount: plans.filter((plan) => plan.selected).length });
  },
  async onBuildFingerprint(): Promise<void> {
    if (this.data.demo || this.data.busy || this.data.fingerprintBuildLoading) return;
    const plans = this.data.fingerprintBuildPlans.filter((plan) => plan.selected);
    if (!plans.length) return;
    if (plans.length > 30) { this.setData({ issue: '一次最多选择 30 份 T4 清单' }); return; }
    const batches = plans.slice(0, 4).map((plan) => plan.batch).join('、');
    const sources = plans.length > 4 ? `${batches}等 ${plans.length} 批` : batches;
    const confirmed = await new Promise<boolean>((resolve) => wx.showModal({
      title: '建库并启用', content: `使用 T4 批次 ${sources} 建库。采集楼层的旧指纹将替换，旧版本仍可回滚。`,
      success: (r) => resolve(r.confirm), fail: () => resolve(false),
    }));
    if (confirmed && !this.disposed) await this.publishSamples('fingerprint-build', plans.map((plan) => plan.planId));
  },
  async publishSamples(kind: 'calibration-sample' | 'fingerprint-build', planIds: string[]): Promise<void> {
    if (this.data.demo || this.data.busy) return;
    if (this.data.pending) { this.setData({ issue: '请先完成待补传' }); return; }
    this.setData({ busy: true });
    try {
      const response = await appContext.http.client.request<{ version?: string; sampleId?: string; active?: boolean }>({
        method: 'POST', path: `/api/v1/field/${kind}`, body: { mapVersion: this.mapVersion, planIds },
        timeoutMs: 60_000,
      });
      const data = response.data;
      if (!data) throw new Error('服务器未返回处理结果');
      this.setData({ issue: data.sampleId ? `标定样本 ${data.sampleId} 已同步` :
        `指纹库 ${data.version} · ${data.active ? '已启用' : '版本已存在，当前未启用'}` });
      if (data.sampleId) {
        wx.setStorageSync(this.calibrationKey(planIds[0]), data.sampleId);
        if (!this.disposed && this.data.calPlanId === planIds[0]) {
          this.setData({ calSampleId: data.sampleId, calMessage: '' });
          await this.onRefreshCalibration();
        }
      }
    } catch (error) { this.setData({ issue: message(error) }); }
    finally { this.setData({ busy: false }); }
  },
  onMethod(e: WechatMiniprogram.PickerChange): void { this.setData({ methodIndex: Number(e.detail.value) }); },
  onConfig(e: WechatMiniprogram.PickerChange): void { this.setData({ configIndex: Number(e.detail.value) }); },
  async loadConfigurations(): Promise<void> {
    try {
      const response = await appContext.http.client.request<{ configurations: FrozenFieldConfig[] }>({
        method: 'GET', path: '/api/v1/field/configurations',
      });
      const configs = (response.data?.configurations ?? []).filter((row) => row.mapVersion === this.mapVersion)
        .sort((a, b) => b.frozenAt - a.frozenAt);
      this.setData({ configs, configIndex: 0, configLabels: configs.map((row) =>
        `${row.configId} · 标定 ${row.B.paramsVersion} · 指纹 ${row.C.libraryVersion}`) });
    } catch { /* Configurations are required only for online navigation. */ }
  },
  async onFreeze(): Promise<void> {
    if (this.data.busy || this.data.demo) return;
    const confirm = await new Promise<boolean>((resolve) => wx.showModal({
      title: '冻结实验配置', content: '确认已完成标定、指纹建库和重放预检。当前配置将用于正式对比。',
      success: (r) => resolve(r.confirm), fail: () => resolve(false),
    }));
    if (!confirm) return;
    this.setData({ busy: true });
    try {
      await appContext.http.client.request({ method: 'POST', path: '/api/v1/field/configurations',
        body: { configId: fieldId(), mapVersion: this.mapVersion } });
      await this.loadConfigurations();
      this.setData({ issue: '冻结配置已生成' });
    } catch (error) { this.setData({ issue: message(error) }); }
    finally { this.setData({ busy: false }); }
  },
  async onReplay(): Promise<void> {
    const runId = this.data.form.replayRun?.trim() || this.data.lastRun;
    const config = this.data.configs[this.data.configIndex];
    if (this.data.busy) return;
    if (!runId) { this.setData({ issue: '请输入已上传的路线编号' }); return; }
    this.setData({ busy: true, replayText: '', issue: '' });
    try {
      const response = await appContext.http.client.request<{
        configId: string; missingPointIds: string[];
        checkpoints: { pointId: string; methods: Record<string, { errorM: number | null; source?: string }> }[];
      }>({ method: 'POST', path: '/api/v1/field/replay', body: { runId, configId: config?.configId }, timeoutMs: 60_000 });
      if (!response.data) throw new Error('后端未返回重放结果');
      const { configId, missingPointIds, checkpoints } = response.data;
      const details = checkpoints.map((row) => {
        const result = METHODS.map((method) => {
          const estimate = row.methods[method];
          if (estimate.errorM === null) return `${method} —`;
          const source = method === 'C' ? estimate.source === 'fingerprint' ? '（命中）' : '（回退）' : '';
          return `${method} ${estimate.errorM} m${source}`;
        });
        return `${row.pointId} · ${result.join(' / ')}`;
      });
      const lines = [`配置 ${configId}`, ...(details.length ? details : ['没有可对齐的打点'])];
      if (missingPointIds?.length) lines.push(`缺少打点：${missingPointIds.join('、')}`);
      this.setData({ replayText: lines.join('\n') });
    } catch (error) { this.setData({ issue: message(error) }); }
    finally { this.setData({ busy: false }); }
  },
  onExport(): void { void this.exportFile(false); },
  onExportTrial(): void { void this.exportFile(false, true); },
  onExportInspections(): void { void this.exportFile(true); },
  async exportFile(inspection: boolean, trial = false): Promise<void> {
    try {
      const path = inspection ? '/api/v1/field/inspection-export' :
        `/api/v1/export/experiment?include_trial=${trial}`;
      const response = await appContext.http.client.request<ArrayBuffer>({
        method: 'GET', path, responseType: 'arraybuffer', timeoutMs: 60_000,
      });
      if (!response.data) throw new Error('服务器未返回导出文件');
      const name = inspection ? 'beacon_inspections.csv' : `experiment_export_${fieldDay()}.zip`;
      const filePath = `${wx.env.USER_DATA_PATH}/${name}`;
      wx.getFileSystemManager().writeFileSync(filePath, response.data);
      wx.shareFileMessage({ filePath, fileName: name, fail: () => {
        wx.showModal({ title: '文件已保存', content: name, showCancel: false });
      } });
    } catch (error) { this.setData({ issue: message(error) }); }
  },
});
