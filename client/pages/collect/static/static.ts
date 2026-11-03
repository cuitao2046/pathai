/**
 * 静态标定采集页（spec：field-collection-integration §3）：
 * - 地图模式展示预置 20 点 + 现场长按补标点；已采点绿圆 + 次数角标；
 * - 到点（现场参照物人工判定）→ 点地图点位 → 确认卡（含定位距离辅助）
 *   → 两短振 → 倒计时（30/60/120s，默认 60）→ 自动落盘（会话追加制）；
 * - 采样口径与 positioning-calibrator 同（全量原始样本 → 逐信标统计 +
 *   三维斜距 truthDistanceM）；定位管线仅作距离辅助与 estimate 快照，非门禁。
 */
import { appContext } from '../../../services/app-context';
import { DEFAULT_SITE_ID } from '../../../config/env';
import { createLogger } from '../../../utils/logger';
import { getJson, setJson } from '../../../utils/kv';
import { vibrateShort } from '../../../utils/haptic';
import { createBeaconSource } from '../../../core/ble/beacon-source-factory';
import type { BeaconSample } from '../../../core/ble/scan-window';
import { LocationPipeline } from '../../../core/position/location-pipeline';
import { SequenceCounter } from '../../../core/position/sequence-counter';
import { CollectMapView } from '../../../core/collection/collect-map-view';
import { createTeeSource, estimateSnapshot } from '../../../core/collection/collect-scan';
import { StaticSampler } from '../../../core/collection/static-sampler';
import { StaticPointsStore } from '../../../core/collection/points-store';
import { buildStaticExport, type CollectionExportMeta } from '../../../core/collection/export-builder';
import type { CalibrationPointRec } from '../../../core/collection/types';
import { PRESET_CALIBRATION_POINTS } from '../../../data/calibration_points';
import type { PositionEstimateResponse } from '../../../typings/api';

const log = createLogger('collect-static');

const STORE_KEY = 'col:static:v1';

const DURATION_OPTIONS = [30, 60, 120];

/** 采集进行中实时表最多展示的行数（完整数据在导出里）。 */
const OBS_ROWS_MAX = 6;

/** 点位命中半径（画布 CSS 像素）。 */
const HIT_RADIUS_PX = 24;

/** 距离辅助提示只在定位可用时显示。 */
function qualityUsable(q: string): boolean {
  return q === 'reliable' || q === 'degraded';
}

interface CardData {
  pointId: string;
  sceneText: string;
  floorText: string;
  truthText: string;
  sessionCount: number;
  distanceText: string;
  custom: boolean;
}

Page({
  data: {
    loading: true,
    loadError: '',
    isMock: appContext.dataSource === 'mock',
    operator: appContext.getCalibrationOperator(),
    floors: [] as Array<{ floorId: string; displayName: string; level: number }>,
    currentFloor: '1',
    currentFloorText: '1F',
    durationOptions: DURATION_OPTIONS,
    durationIndex: 1,
    durationS: DURATION_OPTIONS[1],
    stats: { pointCount: 0, sampledPointCount: 0, sessionCount: 0 },
    // 采集进行中
    collecting: false,
    countdownText: '',
    sampleCount: 0,
    beaconCount: 0,
    noSampleHint: false,
    obsRows: [] as Array<{ beaconId: string; n: number; rssiMedian: number }>,
    // 确认卡
    card: null as CardData | null,
  },

  mapView: null as CollectMapView | null,
  store: null as StaticPointsStore | null,
  pipeline: null as LocationPipeline | null,
  sequence: null as SequenceCounter | null,
  lastEstimate: null as PositionEstimateResponse | null,
  /** floorId → 该层点位列表（refreshPoints 时重建）。 */
  pointsByFloor: new Map<string, CalibrationPointRec[]>(),
  /** pointId → 已采会话数。 */
  sessionCounts: new Map<string, number>(),
  // 采集进行中状态（不进 data，避免高频 setData 冲突）
  sampler: null as StaticSampler | null,
  collectPoint: null as CalibrationPointRec | null,
  estimateAtStart: null as ReturnType<typeof estimateSnapshot>,
  tickTimer: null as ReturnType<typeof setInterval> | null,
  remainingS: 0,
  pausedRemainingS: 0,

  onLoad(): void {
    this.store = new StaticPointsStore(
      PRESET_CALIBRATION_POINTS,
      () => getJson(STORE_KEY),
      (d) => setJson(STORE_KEY, d),
    );
    this.sequence = new SequenceCounter();
    void this.bootstrap();
  },

  onUnload(): void {
    this.stopTick();
    this.pipeline?.stop();
    this.pipeline = null;
    this.mapView?.destroy();
    this.mapView = null;
  },

  onHide(): void {
    // 倒计时暂停（spec §6：不静默作废，回前台继续）
    if (this.data.collecting && this.tickTimer !== null) {
      this.pausedRemainingS = this.remainingS;
      this.stopTick();
    }
  },

  onShow(): void {
    void this.pipeline?.start('navigating');
    if (this.data.collecting && this.pausedRemainingS > 0) {
      this.remainingS = this.pausedRemainingS;
      this.pausedRemainingS = 0;
      this.startTick();
    }
  },

  async bootstrap(): Promise<void> {
    this.setData({ loading: true, loadError: '' });
    try {
      await appContext.map.ensureLoaded();
    } catch (err) {
      log.warn('地图加载失败', err);
      this.setData({ loading: false, loadError: '地图加载失败，请确认后端服务后重试' });
      return;
    }
    this.refreshPoints();
    this.setData({
      loading: false,
      floors: [],
      stats: this.store?.stats() ?? { pointCount: 0, sampledPointCount: 0, sessionCount: 0 },
    });
    this.initPipeline();
    this.initMap();
  },

  onRetry(): void {
    void this.bootstrap();
  },

  initPipeline(): void {
    if (this.pipeline !== null) {
      return;
    }
    const taps: Array<(s: BeaconSample) => void> = [
      (s) => this.sampler?.push(s),
    ];
    this.pipeline = new LocationPipeline({
      client: appContext.http.client,
      map: appContext.map,
      source: createTeeSource(createBeaconSource(appContext.dataSource, appContext.map), taps),
      sequence: this.sequence ?? new SequenceCounter(),
      onEstimate: (estimate) => {
        this.lastEstimate = estimate;
        this.mapView?.requestDraw();
      },
      onUnusable: () => {
        // 定位不可用不影响采集（D3 非门禁）；保留上次估计用于快照展示
      },
    });
  },

  initMap(): void {
    this.mapView = new CollectMapView(this, {
      canvasId: 'collectCanvas',
      deps: {
        floors: appContext.map.floors,
        places: appContext.map.places,
        beacons: appContext.map.beacons,
      },
      callbacks: {
        onReady: () => {
          this.setData({
            floors: this.mapView?.floorList ?? [],
            currentFloor: this.mapView?.floorId ?? '1',
          });
        },
        onTap: (world) => this.handleMapTap(world),
        onLongPress: (world) => this.handleLongPress(world),
        onFloorChanged: (floorId) => {
          const item = (this.data.floors.length > 0 ? this.data.floors : this.mapView?.floorList ?? [])
            .find((f) => f.floorId === floorId);
          this.setData({
            currentFloor: floorId,
            currentFloorText: item !== undefined ? `${item.level}F` : floorId,
          });
        },
        drawOverlay: (ctx, vp) => this.drawOverlay(ctx, vp),
      },
    });
    this.mapView.init();
  },

  // ------------------------------------------------------------------
  // 点位数据
  // ------------------------------------------------------------------

  refreshPoints(): void {
    if (this.store === null) {
      return;
    }
    this.pointsByFloor = new Map();
    for (const p of this.store.listPoints()) {
      const list = this.pointsByFloor.get(p.floorId);
      if (list) {
        list.push(p);
      } else {
        this.pointsByFloor.set(p.floorId, [p]);
      }
    }
    this.sessionCounts = new Map();
    for (const s of this.store.allSessions()) {
      this.sessionCounts.set(s.pointId, (this.sessionCounts.get(s.pointId) ?? 0) + 1);
    }
  },

  refreshStatsAndDraw(): void {
    if (this.store === null) {
      return;
    }
    this.refreshPoints();
    this.setData({ stats: this.store.stats() });
    this.mapView?.requestDraw();
  },

  // ------------------------------------------------------------------
  // 地图交互
  // ------------------------------------------------------------------

  handleMapTap(world: { x: number; y: number }): void {
    if (this.data.collecting || this.data.card !== null) {
      return;
    }
    const vp = this.mapView?.vp;
    if (vp === null || vp === undefined || this.mapView === null) {
      return;
    }
    const pts = this.pointsByFloor.get(vp.floorId) ?? [];
    let best: CalibrationPointRec | null = null;
    let bestDist = HIT_RADIUS_PX;
    for (const p of pts) {
      // 世界距离 × scale = 屏幕像素距离
      const wdx = p.truth[0] - world.x;
      const wdy = p.truth[1] - world.y;
      const screenDist = Math.hypot(wdx, wdy) * vp.scale;
      if (screenDist <= bestDist) {
        best = p;
        bestDist = screenDist;
      }
    }
    if (best !== null) {
      this.openCard(best);
    }
  },

  handleLongPress(world: { x: number; y: number }): void {
    const vp = this.mapView?.vp;
    if (this.mapView === null || vp === null || vp === undefined || this.data.collecting) {
      return;
    }
    const floorId = vp.floorId;
    wx.showModal({
      title: '补标标定点',
      editable: true,
      placeholderText: '点位名称（如：东走廊柱旁）',
      success: (res) => {
        if (!res.confirm) {
          return;
        }
        if (this.store === null) {
          return;
        }
        const rec = this.store.addCustomPoint(
          [Math.round(world.x * 100) / 100, Math.round(world.y * 100) / 100],
          floorId,
          (res.content ?? '').trim() || '补标点',
        );
        log.info('补标点', rec.pointId);
        this.refreshStatsAndDraw();
        wx.showToast({ title: `已补标 ${rec.pointId}`, icon: 'none' });
      },
    });
  },

  openCard(p: CalibrationPointRec): void {
    const floorItem = (this.mapView?.floorList ?? []).find((f) => f.floorId === p.floorId);
    const est = this.lastEstimate;
    let distanceText = '—';
    if (
      est !== null &&
      est.position != null &&
      qualityUsable(est.quality) &&
      est.position.floorId === p.floorId
    ) {
      const d = Math.hypot(est.position.x - p.truth[0], est.position.y - p.truth[1]);
      distanceText = `≈${d.toFixed(1)} m`;
    }
    this.setData({
      card: {
        pointId: p.pointId,
        sceneText: p.label !== p.scene ? `${p.scene} · ${p.label}` : p.scene,
        floorText: floorItem !== undefined ? `${floorItem.level}F` : p.floorId,
        truthText: `${p.truth[0].toFixed(1)}, ${p.truth[1].toFixed(1)}`,
        sessionCount: this.sessionCounts.get(p.pointId) ?? 0,
        distanceText,
        custom: p.custom,
      },
    });
    this.pendingCardPoint = p;
  },

  pendingCardPoint: null as CalibrationPointRec | null,

  onCardCancel(): void {
    this.setData({ card: null });
    this.pendingCardPoint = null;
  },

  noop(): void {
    // 阻止确认卡冒泡关闭
  },

  /** 确认到点：两短振 → 倒计时开始（spec §3.3）。 */
  onCardConfirm(): void {
    const p = this.pendingCardPoint;
    if (p === null || this.data.collecting) {
      return;
    }
    vibrateShort();
    setTimeout(() => vibrateShort(), 180);
    this.setData({ card: null });
    this.pendingCardPoint = null;

    this.collectPoint = p;
    this.sampler = new StaticSampler({
      truth: p.truth,
      resolve: (uuid, major, minor) => {
        const beaconId = appContext.map.beaconRegistry.resolve(uuid, major, minor);
        if (beaconId === null) {
          return null;
        }
        const beacon = appContext.map.beacons.find((b) => b.beaconId === beaconId);
        return beacon === undefined
          ? null
          : { beaconId, x: beacon.x, y: beacon.y, installHeightM: beacon.installHeightM ?? null };
      },
    });
    this.estimateAtStart = estimateSnapshot(this.lastEstimate);
    this.remainingS = this.data.durationS;
    this.pausedRemainingS = 0;
    this.setData({
      collecting: true,
      countdownText: `${this.remainingS}s`,
      sampleCount: 0,
      beaconCount: 0,
      noSampleHint: false,
      obsRows: [],
    });
    this.startTick();
    this.mapView?.requestDraw();
  },

  startTick(): void {
    this.stopTick();
    this.tickTimer = setInterval(() => this.onTick(), 1000);
  },

  stopTick(): void {
    if (this.tickTimer !== null) {
      clearInterval(this.tickTimer);
      this.tickTimer = null;
    }
  },

  onTick(): void {
    const sampler = this.sampler;
    if (sampler === null) {
      this.stopTick();
      return;
    }
    this.remainingS -= 1;
    if (this.remainingS <= 0) {
      this.finishCollection();
      return;
    }
    const rows = sampler
      .observations()
      .slice(0, OBS_ROWS_MAX)
      .map((o) => ({ beaconId: o.beaconId, n: o.n, rssiMedian: o.rssiMedian }));
    this.setData({
      countdownText: `${this.remainingS}s`,
      sampleCount: sampler.sampleCount,
      beaconCount: sampler.resolvedBeaconCount,
      noSampleHint: sampler.sampleCount === 0,
      obsRows: rows,
    });
  },

  /** 倒计时结束：落盘一条会话（会话追加制，sessionIndex 自动递增）。 */
  finishCollection(): void {
    this.stopTick();
    const sampler = this.sampler;
    const p = this.collectPoint;
    if (sampler === null || p === null || this.store === null) {
      return;
    }
    const finished = sampler.finish(this.data.durationS);
    this.store.addSession(p.pointId, {
      startedAt: finished.startedAt,
      durationS: finished.durationS,
      trafficWindow: false,
      observations: finished.observations,
      ...(this.estimateAtStart ? { estimateAtStart: this.estimateAtStart } : {}),
      ...(estimateSnapshot(this.lastEstimate)
        ? { estimateAtEnd: estimateSnapshot(this.lastEstimate) as NonNullable<ReturnType<typeof estimateSnapshot>> }
        : {}),
    });
    log.info('会话已记录', p.pointId, `${finished.observations.length} 信标`);
    this.sampler = null;
    this.collectPoint = null;
    this.estimateAtStart = null;
    this.setData({
      collecting: false,
      countdownText: '',
      obsRows: [],
      noSampleHint: false,
    });
    this.refreshStatsAndDraw();
    wx.showToast({ title: `已记录 ${p.pointId}`, icon: 'success' });
  },

  /** 会话/点位管理（确认卡内入口）。 */
  onManageSessions(): void {
    const p = this.pendingCardPoint;
    if (p === null || this.store === null) {
      return;
    }
    const n = this.sessionCounts.get(p.pointId) ?? 0;
    const items: string[] = [];
    const actions: Array<() => void> = [];
    if (n > 0) {
      items.push(`删除最近一次会话（第 ${n} 次）`);
      actions.push(() => {
        const sessions = this.store?.sessionsFor(p.pointId) ?? [];
        const last = sessions[sessions.length - 1];
        if (last !== undefined) {
          this.store?.removeSession(p.pointId, last.sessionIndex);
          this.refreshStatsAndDraw();
        }
      });
      items.push(`清空该点全部会话（${n} 次）`);
      actions.push(() => {
        for (const s of this.store?.sessionsFor(p.pointId) ?? []) {
          this.store?.removeSession(p.pointId, s.sessionIndex);
        }
        this.refreshStatsAndDraw();
      });
    }
    if (p.custom) {
      items.push('删除该补标点');
      actions.push(() => {
        this.store?.removeCustomPoint(p.pointId);
        this.onCardCancel();
        this.refreshStatsAndDraw();
      });
    } else {
      items.push('隐藏该预置点');
      actions.push(() => {
        this.store?.setPresetHidden(p.pointId, true);
        this.onCardCancel();
        this.refreshStatsAndDraw();
      });
    }
    wx.showActionSheet({
      itemList: items,
      success: (res) => {
        const fn = actions[res.tapIndex];
        if (fn !== undefined) {
          fn();
        }
      },
      fail: () => undefined,
    });
  },

  // ------------------------------------------------------------------
  // 顶栏控件
  // ------------------------------------------------------------------

  onSwitchFloor(e: WechatMiniprogram.TouchEvent): void {
    const floorId = String(e.currentTarget.dataset.floor ?? '');
    this.mapView?.setFloor(floorId);
  },

  onDurationChange(e: WechatMiniprogram.PickerChange): void {
    const index = Number(e.detail.value);
    const durationS = DURATION_OPTIONS[index] ?? DURATION_OPTIONS[1];
    this.setData({ durationIndex: index, durationS });
  },

  // ------------------------------------------------------------------
  // 导出
  // ------------------------------------------------------------------

  onExport(): void {
    const store = this.store;
    if (store === null) {
      return;
    }
    const stats = store.stats();
    if (stats.sessionCount === 0) {
      wx.showToast({ title: '还没有采集记录', icon: 'none' });
      return;
    }
    let device: unknown = null;
    try {
      const info = wx.getSystemInfoSync();
      device = { model: info.model, system: info.system, platform: info.platform };
    } catch {
      device = null;
    }
    const meta: CollectionExportMeta = {
      venueId: DEFAULT_SITE_ID,
      venueName: DEFAULT_SITE_ID,
      operator: appContext.getCalibrationOperator(),
      device: device as CollectionExportMeta['device'],
      appVersion: '1.0.0',
      mapVersion: appContext.map.isLoaded ? appContext.map.requireVersion() : '',
      ledgerSha256: null,
    };
    const { data, warnings } = buildStaticExport(store, meta);
    const json = JSON.stringify(data, null, 2);
    const fs = wx.getFileSystemManager();
    const path = `${wx.env.USER_DATA_PATH}/static_samples_${Date.now()}.json`;
    fs.writeFile({
      filePath: path,
      data: json,
      encoding: 'utf8',
      success: () => {
        wx.setClipboardData({
          data: json,
          success: () => {
            try {
              if (wx.shareFileMessage) wx.shareFileMessage({ filePath: path, fail: () => undefined });
            } catch {
              // 分享不可用时剪贴板兜底
            }
            wx.showModal({
              title: '导出成功',
              content: `点位 ${stats.sampledPointCount}/${stats.pointCount} · 会话 ${stats.sessionCount} · 观测 ${stats.beaconPairs}${
                warnings.length > 0 ? `\n提示：${warnings.slice(0, 3).join('；')}` : ''
              }\nJSON 已复制剪贴板并写入文件。`,
              showCancel: false,
            });
          },
        });
      },
      fail: () => wx.showModal({ title: '导出失败', content: '写文件失败', showCancel: false }),
    });
  },

  // ------------------------------------------------------------------
  // 叠加图层：点位 + 定位点
  // ------------------------------------------------------------------

  drawOverlay(
    ctx: WechatMiniprogram.CanvasRenderingContext.CanvasRenderingContext2D,
    vp: NonNullable<CollectMapView['vp']>,
  ): void {
    const pts = this.pointsByFloor.get(vp.floorId) ?? [];
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    for (const p of pts) {
      const { sx, sy } = vp.toScreen(p.truth[0], p.truth[1]);
      const n = this.sessionCounts.get(p.pointId) ?? 0;
      const collectingHere = this.data.collecting && this.collectPoint?.pointId === p.pointId;
      if (collectingHere) {
        ctx.beginPath();
        ctx.arc(sx, sy, 14, 0, Math.PI * 2);
        ctx.strokeStyle = '#f59e0b';
        ctx.lineWidth = 3;
        ctx.stroke();
      }
      ctx.beginPath();
      ctx.arc(sx, sy, 8, 0, Math.PI * 2);
      if (n > 0) {
        ctx.fillStyle = '#16a34a';
        ctx.fill();
        ctx.lineWidth = 2;
        ctx.strokeStyle = '#ffffff';
        ctx.stroke();
        ctx.font = 'bold 10px -apple-system, "PingFang SC", sans-serif';
        ctx.fillStyle = '#ffffff';
        ctx.fillText(String(n), sx, sy);
      } else {
        ctx.fillStyle = 'rgba(255, 255, 255, 0.92)';
        ctx.fill();
        ctx.lineWidth = 2;
        ctx.strokeStyle = '#2563eb';
        ctx.stroke();
      }
      // 点位名（预置点为 pointId，补标点为名称）
      ctx.font = '10px -apple-system, "PingFang SC", sans-serif';
      ctx.lineWidth = 3;
      ctx.strokeStyle = 'rgba(255, 255, 255, 0.92)';
      ctx.strokeText(p.pointId, sx, sy - 15);
      ctx.fillStyle = '#374151';
      ctx.fillText(p.pointId, sx, sy - 15);
    }

    // 定位估计（可信蓝点，与导航地图同样式）
    const est = this.lastEstimate;
    if (
      est !== null &&
      est.position != null &&
      qualityUsable(est.quality) &&
      est.position.floorId === vp.floorId
    ) {
      const { sx, sy } = vp.toScreen(est.position.x, est.position.y);
      ctx.beginPath();
      ctx.arc(sx, sy, 14, 0, Math.PI * 2);
      ctx.fillStyle = 'rgba(37, 99, 235, 0.18)';
      ctx.fill();
      ctx.beginPath();
      ctx.arc(sx, sy, 7, 0, Math.PI * 2);
      ctx.fillStyle = '#2563eb';
      ctx.fill();
      ctx.lineWidth = 2;
      ctx.strokeStyle = '#ffffff';
      ctx.stroke();
    }
  },
});
