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
import { vibrateLong, vibrateShort } from '../../../utils/haptic';
import { createBeaconSource } from '../../../core/ble/beacon-source-factory';
import type { BeaconSourceLike, BleScanPhase, BleScanStatus } from '../../../core/ble/beacon-source';
import type { BeaconSample } from '../../../core/ble/scan-window';
import { LocationPipeline } from '../../../core/position/location-pipeline';
import { SequenceCounter } from '../../../core/position/sequence-counter';
import { CollectMapView } from '../../../core/collection/collect-map-view';
import { pickPointOnScreen } from '../../../core/collection/hit-test';
import { createTeeSource, estimateSnapshot } from '../../../core/collection/collect-scan';
import { StaticSampler } from '../../../core/collection/static-sampler';
import { StaticPointsStore } from '../../../core/collection/points-store';
import { buildStaticExport, type CollectionExportMeta } from '../../../core/collection/export-builder';
import type { CalibrationPointRec } from '../../../core/collection/types';
import { PRESET_CALIBRATION_POINTS } from '../../../data/calibration_points';
import { SIMULATED_BEACONS } from '../../../data/fingerprint_grid';
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

/**
 * live 扫描状态 → 页面文案。只上屏与项目相关的口径：
 * 扫描健康状态 + 按 uuid+major+minor 命中台账的去重信标数（及其最强 RSSI）。
 * 设备数、原始帧数、未命中三元组只进日志。
 */
function describeBleStatus(status: BleScanStatus): string {
  switch (status.phase) {
    case 'idle':
      return '蓝牙未启动';
    case 'opening':
      return '正在启动蓝牙扫描…';
    case 'scanning':
      if (status.matchedCount === 0) {
        return '扫描中，附近暂未发现本项目信标';
      }
      if (status.strongestBeaconId !== null && status.strongestRssi !== null) {
        return `扫描中 · 本项目信标 ${status.matchedCount} 枚 · 最强 ${status.strongestBeaconId} ${Math.round(status.strongestRssi)} dBm`;
      }
      return `扫描中 · 本项目信标 ${status.matchedCount} 枚`;
    case 'error':
      return `蓝牙不可用：${status.message}`;
  }
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
    /** 是否在标定地图上叠加 5 枚未安装虚拟信标（设置项，默认关闭＝现状口径）。 */
    simPreview: false,
    // 采集进行中
    collecting: false,
    countdownText: '',
    sampleCount: 0,
    beaconCount: 0,
    noSampleHint: false,
    /** 真实 BLE 状态（live 源驱动；mock 显示模拟提示），不再让页面“看起来正常”。 */
    blePhase: 'idle' as BleScanPhase,
    bleStatusText: '',
    bleErrorText: '',
    noSampleText: '',
    obsRows: [] as Array<{ beaconId: string; n: number; rssiMedian: number }>,
    // 确认卡
    card: null as CardData | null,
  },

  mapView: null as CollectMapView | null,
  store: null as StaticPointsStore | null,
  pipeline: null as LocationPipeline | null,
  /** 底层信标源（mock 无 subscribeStatus；live 用它上屏真实扫描状态）。 */
  bleSource: null as BeaconSourceLike | null,
  bleUnsubscribe: null as (() => void) | null,
  bleErrorMessage: '',
  /** 本次采集的原始广播计数（仅用于日志诊断，不上界面）。 */
  rawSamplesInCollection: 0,
  rawTripletsInCollection: null as Set<string> | null,
  sequence: null as SequenceCounter | null,
  lastEstimate: null as PositionEstimateResponse | null,
  /**
   * floorId → 该层点位列表（refreshPoints 时重建）。
   * Page 定义期只允许简单值，Map 会被框架深拷贝并丢失方法（console 告警
   * `Free data with key ... seems not a simple value`），故定义期 null 占位，
   * onLoad 实例化，使用点 `!` 断言（均发生在 onLoad 之后）。
   */
  pointsByFloor: null as Map<string, CalibrationPointRec[]> | null,
  /** pointId → 已采会话数。 */
  sessionCounts: null as Map<string, number> | null,
  // 采集进行中状态（不进 data，避免高频 setData 冲突）
  sampler: null as StaticSampler | null,
  collectPoint: null as CalibrationPointRec | null,
  estimateAtStart: null as ReturnType<typeof estimateSnapshot>,
  tickTimer: null as ReturnType<typeof setInterval> | null,
  remainingS: 0,
  pausedRemainingS: 0,

  onLoad(): void {
    this.pointsByFloor = new Map();
    this.sessionCounts = new Map();
    this.rawTripletsInCollection = new Set();
    this.store = new StaticPointsStore(
      PRESET_CALIBRATION_POINTS,
      () => getJson(STORE_KEY),
      (d) => setJson(STORE_KEY, d),
    );
    this.sequence = new SequenceCounter();
    this.setData({
      simPreview: appContext.getSimBeaconPreviewSetting(),
      bleStatusText:
        appContext.dataSource === 'mock'
          ? '模拟数据源：不扫描真实蓝牙（开启「真实信标」后重启）'
          : '蓝牙未启动',
    });
    void this.bootstrap();
  },

  onUnload(): void {
    this.stopTick();
    this.bleUnsubscribe?.();
    this.bleUnsubscribe = null;
    this.bleSource = null;
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
    // 释放蓝牙：后台挂起时监听器可能失效，回前台由 onShow 重新建立
    this.pipeline?.stop();
  },

  onShow(): void {
    this.startScan();
    // 设置页开关可能刚被改动：回前台时同步并重绘（打开后地图出现 5 枚虚拟信标）
    const simPreview = appContext.getSimBeaconPreviewSetting();
    if (simPreview !== this.data.simPreview) {
      this.setData({ simPreview });
      this.mapView?.requestDraw();
    }
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
    // 首次进入时 onShow 早于异步 bootstrap 完成（pipeline 尚不存在），
    // 必须在这里补一次启动；否则真机上永远不调用 openBluetoothAdapter。
    this.startScan();
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
      (s) => {
        if (this.data.collecting) {
          this.rawSamplesInCollection += 1;
          this.rawTripletsInCollection?.add(
            `${s.uuid.trim().toLowerCase()}|${s.major}|${s.minor}`,
          );
        }
        this.sampler?.push(s);
      },
    ];
    const base = createBeaconSource(appContext.dataSource, appContext.map);
    this.bleSource = base;
    this.pipeline = new LocationPipeline({
      client: appContext.http.client,
      map: appContext.map,
      source: createTeeSource(base, taps),
      sequence: this.sequence ?? new SequenceCounter(),
      onEstimate: (estimate) => {
        this.lastEstimate = estimate;
        this.mapView?.requestDraw();
      },
      onUnusable: () => {
        // 定位不可用不影响采集（D3 非门禁）；保留上次估计用于快照展示
      },
    });
    this.bindBleStatus();
  },

  /** 订阅底层扫描状态：适配器未就绪/扫描失败直接上屏，不再静默。 */
  bindBleStatus(): void {
    this.bleUnsubscribe?.();
    this.bleUnsubscribe = null;
    const source = this.bleSource;
    if (source?.subscribeStatus === undefined) {
      this.setData({
        blePhase: 'idle',
        bleStatusText: '模拟数据源：不扫描真实蓝牙（开启「真实信标」后重启）',
        bleErrorText: '',
      });
      return;
    }
    this.bleUnsubscribe = source.subscribeStatus((status) => {
      this.bleErrorMessage = status.phase === 'error' ? status.message : '';
      const text = describeBleStatus(status);
      if (text !== this.data.bleStatusText || status.phase !== this.data.blePhase) {
        this.setData({
          blePhase: status.phase,
          bleStatusText: text,
          bleErrorText: this.bleErrorMessage,
        });
      }
    });
  },

  /** 启动扫描（幂等）：onShow / bootstrap / 开始采集都可以安全调用。 */
  startScan(): void {
    const pipeline = this.pipeline;
    if (pipeline === null || pipeline.isRunning) {
      return;
    }
    log.info('启动 BLE 扫描');
    void pipeline.start('navigating').catch((err: unknown) => {
      const message = err instanceof Error ? err.message : String(err);
      log.warn('BLE 扫描启动失败', message);
      this.bleErrorMessage = message;
      this.setData({
        blePhase: 'error',
        bleStatusText: `蓝牙扫描启动失败：${message}`,
        bleErrorText: message,
      });
    });
  },

  /** 现场重试：先停掉可能半死的监听，再重新走 open → startBeaconDiscovery。 */
  onRetryBle(): void {
    this.pipeline?.stop();
    this.bleErrorMessage = '';
    this.setData({
      blePhase: 'opening',
      bleStatusText: '正在重启蓝牙扫描…',
      bleErrorText: '',
    });
    this.startScan();
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
        onTap: (_world, screen) => this.handleMapTap(screen),
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
      const list = this.pointsByFloor!.get(p.floorId);
      if (list) {
        list.push(p);
      } else {
        this.pointsByFloor!.set(p.floorId, [p]);
      }
    }
    this.sessionCounts = new Map();
    for (const s of this.store.allSessions()) {
      this.sessionCounts!.set(s.pointId, (this.sessionCounts!.get(s.pointId) ?? 0) + 1);
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
  // 手势转发 / 地图交互
  // ------------------------------------------------------------------

  // touch 事件绑定在 canvas 自身（WXML），页面只做转发：
  // 手势绑定在容器上会吞掉覆盖控件的 bindtap（已知坑）
  onTouchStart(e: WechatMiniprogram.TouchEvent): void {
    this.mapView?.onTouchStart(e);
  },

  onTouchMove(e: WechatMiniprogram.TouchEvent): void {
    this.mapView?.onTouchMove(e);
  },

  onTouchEnd(e: WechatMiniprogram.TouchEvent): void {
    this.mapView?.onTouchEnd(e);
  },

  onTouchCancel(e: WechatMiniprogram.TouchEvent): void {
    this.mapView?.onTouchCancel(e);
  },

  onLongPressEvent(e: WechatMiniprogram.TouchEvent): void {
    this.mapView?.onLongPressEvent(e);
  },

  handleMapTap(screen: { x: number; y: number }): void {
    if (this.data.collecting || this.data.card !== null) {
      return;
    }
    const vp = this.mapView?.vp;
    if (vp === null || vp === undefined) {
      return;
    }
    const pts = this.pointsByFloor!.get(vp.floorId) ?? [];
    // 屏幕空间命中：点位按当前视口投影后再比距离，缩放/平移后不偏移
    const hit = pickPointOnScreen(
      pts,
      (p) => ({ x: p.truth[0], y: p.truth[1] }),
      vp,
      screen,
      HIT_RADIUS_PX,
    );
    if (hit !== null) {
      this.openCard(hit.point);
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
        sessionCount: this.sessionCounts!.get(p.pointId) ?? 0,
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

  /** 确认到点：倒计时开始（振动提示由 startTick 统一给出，避免与确认反馈叠成三连振）。 */
  onCardConfirm(): void {
    const p = this.pendingCardPoint;
    if (p === null || this.data.collecting) {
      return;
    }
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
    this.rawSamplesInCollection = 0;
    this.rawTripletsInCollection?.clear();
    // 采集开始前兜底启动一次（用户可能刚从后台回来/蓝牙刚打开）
    this.startScan();
    this.setData({
      collecting: true,
      countdownText: `${this.remainingS}s`,
      sampleCount: 0,
      beaconCount: 0,
      noSampleHint: false,
      noSampleText: '',
      obsRows: [],
    });
    this.startTick();
    this.mapView?.requestDraw();
  },

  /** 倒计时启动（首次开始或后台恢复）：一次短振提示「计时已开始」。 */
  startTick(): void {
    this.stopTick();
    vibrateShort();
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
    const noSample = sampler.sampleCount === 0;
    this.setData({
      countdownText: `${this.remainingS}s`,
      sampleCount: sampler.sampleCount,
      beaconCount: sampler.resolvedBeaconCount,
      noSampleHint: noSample,
      noSampleText: noSample ? this.describeNoSample() : '',
      obsRows: rows,
    });
  },

  /**
   * 采集无样本提示：上屏只给面向现场的结论，不暴露设备数/原始帧数；
   * “收到帧但 0 命中台账”的细节留在 scanner 的 8s 诊断日志里。
   */
  describeNoSample(): string {
    if (this.data.blePhase === 'error') {
      return `蓝牙不可用：${this.bleErrorMessage}。请检查手机蓝牙与定位权限后点「重试蓝牙」`;
    }
    return '扫描中，附近暂未发现本项目信标；请确认信标已通电、UUID/major/minor 与台账一致';
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
    // 原始广播明细只进日志：包含第三方/未命中台账的帧，供现场诊断，不上界面
    log.info(
      '会话原始广播统计',
      `samples=${this.rawSamplesInCollection}`,
      `triplets=${this.rawTripletsInCollection?.size ?? 0}`,
      `matched=${sampler.sampleCount}`,
    );
    this.sampler = null;
    this.collectPoint = null;
    this.estimateAtStart = null;
    this.rawSamplesInCollection = 0;
    this.rawTripletsInCollection?.clear();
    this.setData({
      collecting: false,
      countdownText: '',
      noSampleText: '',
      obsRows: [],
      noSampleHint: false,
    });
    this.refreshStatsAndDraw();
    // 倒计时结束：一次长振，与开始时的短振可区分
    vibrateLong();
    wx.showToast({ title: `已记录 ${p.pointId}`, icon: 'success' });
  },

  /** 会话/点位管理（确认卡内入口）。 */
  onManageSessions(): void {
    const p = this.pendingCardPoint;
    if (p === null || this.store === null) {
      return;
    }
    const n = this.sessionCounts!.get(p.pointId) ?? 0;
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

  /** 缩放控件：单指精细调整（双指缩放之外的补充） */
  onZoomIn(): void {
    this.mapView?.zoomIn();
  },

  onZoomOut(): void {
    this.mapView?.zoomOut();
  },

  onResetView(): void {
    this.mapView?.fit();
  },

  onDurationChange(e: WechatMiniprogram.PickerChange): void {
    const index = Number(e.detail.value);
    const durationS = DURATION_OPTIONS[index] ?? DURATION_OPTIONS[1];
    this.setData({ durationIndex: index, durationS });
  },

  // ------------------------------------------------------------------
  // 清理记录
  // ------------------------------------------------------------------

  /**
   * 清理本机采集记录：确认后清空 col:static:v1（采样会话 + 现场补标点 +
   * 预置点隐藏状态），回到首次打开页面的干净状态。
   * 已导出到 USER_DATA_PATH 的 JSON 文件与剪贴板内容不在此列，保留不动。
   */
  onClearRecords(): void {
    const store = this.store;
    if (store === null) {
      return;
    }
    if (this.data.collecting) {
      wx.showToast({ title: '采集中不能清理', icon: 'none' });
      return;
    }
    const stats = store.stats();
    const points = store.listPoints();
    const customCount = points.filter((p) => p.custom === true).length;
    const visiblePreset = points.length - customCount;
    const hiddenCount = Math.max(0, PRESET_CALIBRATION_POINTS.length - visiblePreset);
    if (stats.sessionCount === 0 && customCount === 0 && hiddenCount === 0) {
      wx.showToast({ title: '还没有采集记录', icon: 'none' });
      return;
    }
    wx.showModal({
      title: '清理采集记录',
      content:
        `将清除本机保存的全部静态标定记录：${stats.sessionCount} 条采样会话、` +
        `${customCount} 个现场补标点，并恢复 ${hiddenCount} 个被隐藏的预置点。\n` +
        '清理后不可恢复；已导出的 JSON 文件不受影响。',
      confirmText: '清理',
      confirmColor: '#b91c1c',
      success: (res) => {
        if (!res.confirm) {
          return;
        }
        store.clearAll();
        this.setData({ card: null });
        this.refreshStatsAndDraw();
        wx.showToast({ title: '已清理', icon: 'success' });
      },
    });
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
    const pts = this.pointsByFloor!.get(vp.floorId) ?? [];
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    for (const p of pts) {
      const { sx, sy } = vp.toScreen(p.truth[0], p.truth[1]);
      const n = this.sessionCounts!.get(p.pointId) ?? 0;
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

    // 补装 5 枚虚拟信标（未安装·仅仿真）：仅当设置开关打开时叠加，紫色虚线环 + 菱形
    // 与实装信标（地图底图的实心点）和标定点（白心蓝圈）都不同，避免误认为已部署。
    if (this.data.simPreview) {
      for (const b of SIMULATED_BEACONS) {
        if (b.floorId !== vp.floorId) {
          continue;
        }
        const { sx, sy } = vp.toScreen(b.x, b.y);
        ctx.setLineDash([4, 4]);
        ctx.beginPath();
        ctx.arc(sx, sy, 12, 0, Math.PI * 2);
        ctx.strokeStyle = '#8E24AA';
        ctx.lineWidth = 2;
        ctx.stroke();
        ctx.setLineDash([]);
        ctx.beginPath();
        ctx.moveTo(sx, sy - 6);
        ctx.lineTo(sx + 6, sy);
        ctx.lineTo(sx, sy + 6);
        ctx.lineTo(sx - 6, sy);
        ctx.closePath();
        ctx.fillStyle = 'rgba(142, 36, 170, 0.85)';
        ctx.fill();
        ctx.font = '10px -apple-system, "PingFang SC", sans-serif';
        ctx.lineWidth = 3;
        ctx.strokeStyle = 'rgba(255, 255, 255, 0.92)';
        ctx.strokeText('未安装·仅仿真', sx, sy - 19);
        ctx.fillStyle = '#6b21a8';
        ctx.fillText('未安装·仅仿真', sx, sy - 19);
      }
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
