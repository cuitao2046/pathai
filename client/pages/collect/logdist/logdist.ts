/**
 * 对数等距测量页（spec：calibration-console §5）。
 * - 阶段一专用：单信标 + 已知水平距离，采集 RSSI 供 A/n 拟合；
 * - 信标候选列表来自**当前地图包**（与 BeaconRegistry 同源），按楼层分组；
 * - 探测只作实时标记：收到显示 RSSI，未收到灰显但仍可选，**不自动锁定**；
 * - 真值斜距 d = √(h² + (installHeight − 1.2)²)，安装高度按会话快照；
 * - 存储 col:logdist:v1，导出 positioning_calibration_logdist（deployable=false）。
 */
import { appContext } from '../../../services/app-context';
import { createLogger } from '../../../utils/logger';
import { getJson, setJson } from '../../../utils/kv';
import { vibrateLong, vibrateShort } from '../../../utils/haptic';
import { createBeaconSource } from '../../../core/ble/beacon-source-factory';
import type { BeaconSourceLike } from '../../../core/ble/beacon-source';
import type { BeaconSample } from '../../../core/ble/scan-window';
import {
  StaticSampler,
  type StaticBeaconRef,
} from '../../../core/collection/static-sampler';
import {
  LogdistStore,
  LOGDIST_INSTALL_STEP_M,
  slantDistanceM,
  type LogdistBeacon,
  type LogdistPersist,
  type LogdistStopView,
} from '../../../core/collection/logdist-store';
import {
  SIGNAL_ACTIVE_MS,
  activeSeenBeacons,
  buildBeaconGroups,
  detectHintText,
  floorTitle,
  hasBeaconTriplet,
  reconcileBeaconSelection,
  uniqueSeenBeaconId,
  type BeaconPickerGroup,
  type SeenBeacon,
} from '../../../core/collection/beacon-picker';
import { buildLogdistExport } from '../../../core/collection/export-builder';
import type { MapBeaconEntry } from '../../../typings/api';

const log = createLogger('collect-logdist');

const STORE_KEY = 'col:logdist:v1';
const DURATION_OPTIONS = [30, 60, 120];
/** 选中信标连续无样本的判定阈值（spec §5.3）。 */
const BEACON_LOST_MS = 10_000;

type Phase = 'select' | 'ready' | 'capturing' | 'paused' | 'beacon_lost';

interface ObsRow {
  beaconId: string;
  n: number;
  rssiMedian: number;
}

function phaseText(phase: Phase, hasSelection: boolean): string {
  switch (phase) {
    case 'select':
      return '请选择要测量的信标';
    case 'capturing':
      return '采集中';
    case 'paused':
      return '已暂停';
    case 'beacon_lost':
      return '信标暂时丢失，倒计时继续';
    default:
      return hasSelection ? '就绪' : '请选择要测量的信标';
  }
}

Page({
  data: {
    loading: true,
    loadError: '',
    isMock: appContext.dataSource === 'mock',
    durationOptions: DURATION_OPTIONS,
    durationIndex: 1,
    durationS: DURATION_OPTIONS[1],
    installHeightM: 2.5,
    installHeightText: '2.5 m',
    stops: [] as LogdistStopView[],
    selectedStopId: null as string | null,
    selectedStopText: '',
    phase: 'select' as Phase,
    phaseText: phaseText('select', false),
    selectedBeaconId: null as string | null,
    selectedBeaconText: '未选择信标',
    selectedFloorText: '',
    selectedLocation: '',
    selectedHeightText: '',
    selectedSessionCount: 0,
    beaconGroups: [] as BeaconPickerGroup[],
    beaconPickerOpen: false,
    beaconCount: 0,
    detectHint: '未探测到信标；仍可从地图包列表手动选择',
    uniqueBeaconId: null as string | null,
    collecting: false,
    countdownText: '',
    sampleCount: 0,
    obsRow: null as ObsRow | null,
    stats: { stopCount: 0, sessionCount: 0, observationCount: 0 },
  },

  store: null as LogdistStore | null,
  source: null as BeaconSourceLike | null,
  sampler: null as StaticSampler | null,
  beaconById: null as Map<string, MapBeaconEntry> | null,
  mapBeacons: [] as MapBeaconEntry[],
  /** 已解析到的信标 → 最近样本（仅用于实时标记，不参与选择）。 */
  seen: null as Map<string, SeenBeacon> | null,
  selected: null as LogdistBeacon | null,
  selectedLastSampleAt: 0,
  tickTimer: null as ReturnType<typeof setInterval> | null,
  statusTimer: null as ReturnType<typeof setInterval> | null,
  remainingS: 0,
  pausedRemainingS: 0,
  captureStopId: null as string | null,
  captureHorizontalM: 0,
  sourceRunning: false,
  loadedMapVersion: null as string | null,
  mapUnsubscribe: null as (() => void) | null,
  pendingMapSync: false,

  onLoad(): void {
    this.seen = new Map();
    this.beaconById = new Map();
    this.mapBeacons = [];
    this.selected = null;
    this.store = new LogdistStore(
      () => getJson<LogdistPersist>(STORE_KEY),
      (d) => setJson(STORE_KEY, d),
    );
    this.setData({
      installHeightM: this.store.installHeightM,
      installHeightText: `${this.store.installHeightM.toFixed(1)} m`,
      stops: this.store.views(),
      stats: this.store.stats(),
    });
    // 地图包版本切换：重新对账列表与选中项；采集中则等本次会话结束后再同步。
    this.mapUnsubscribe = appContext.map.subscribe((event) => {
      if (event.type !== 'ready' || this.data.loading) {
        return;
      }
      if (this.data.collecting) {
        this.pendingMapSync = true;
        return;
      }
      void this.bootstrap();
    });
    this.startStatusTimer();
    void this.bootstrap();
  },

  onUnload(): void {
    this.stopTick();
    this.stopStatusTimer();
    this.stopSource();
    this.mapUnsubscribe?.();
    this.mapUnsubscribe = null;
    this.sampler = null;
  },

  onHide(): void {
    this.stopStatusTimer();
    if (this.data.collecting) {
      this.pausedRemainingS = this.remainingS;
      this.stopTick();
      this.stopSource();
      this.setPhase('paused');
    }
  },

  onShow(): void {
    this.startStatusTimer();
    if (this.data.loading || this.data.loadError !== '') {
      return;
    }
    if (
      appContext.map.isLoaded &&
      !this.data.collecting &&
      appContext.map.requireVersion() !== this.loadedMapVersion
    ) {
      void this.bootstrap();
      return;
    }
    if (this.data.collecting && this.pausedRemainingS > 0) {
      this.remainingS = this.pausedRemainingS;
      this.pausedRemainingS = 0;
      this.setPhase('capturing');
      this.startSource();
      this.startTick();
    } else if (!this.data.collecting) {
      this.startSource();
    }
    this.refreshDetection(true);
  },

  async bootstrap(): Promise<void> {
    this.setData({ loading: true, loadError: '' });
    try {
      await appContext.map.ensureLoaded();
      const beacons = appContext.map.beacons;
      const version = appContext.map.requireVersion();
      const previousVersion = this.loadedMapVersion;
      const selectedId = this.selected?.beaconId ?? null;
      const reconciled = reconcileBeaconSelection(selectedId, beacons);

      this.mapBeacons = [...beacons];
      this.beaconById = new Map(beacons.map((b) => [b.beaconId, b]));
      this.loadedMapVersion = version;

      if (reconciled.dropped && selectedId !== null) {
        this.selected = null;
        this.store?.selectBeacon(null);
        this.setData({
          selectedBeaconId: null,
          selectedBeaconText: '未选择信标',
          selectedFloorText: '',
          selectedLocation: '',
          selectedHeightText: '',
          selectedSessionCount: 0,
          phase: 'select' as Phase,
          phaseText: phaseText('select', false),
        });
        const versionChange =
          previousVersion !== null && previousVersion !== version
            ? `（${previousVersion} → ${version}）`
            : '';
        wx.showModal({
          title: '地图包已更新',
          content: `原选中信标 ${selectedId} 不在当前地图包${versionChange}中，已重置，请重新选择。`,
          showCancel: false,
        });
      } else if (this.selected !== null) {
        const entry = this.beaconById.get(this.selected.beaconId);
        if (entry !== undefined) {
          this.applySelectedBeaconView(entry);
        }
      }

      this.setData({ loading: false, beaconCount: beacons.length });
      this.refreshDetection(true);
      this.startSource();
    } catch (err) {
      log.warn('地图包信标加载失败', err);
      this.mapBeacons = [];
      this.beaconById = new Map();
      this.selected = null;
      this.store?.selectBeacon(null);
      this.setData({
        loading: false,
        loadError: '地图包加载失败，请检查后端服务与网络后重试',
        beaconCount: 0,
        beaconGroups: [],
        selectedBeaconId: null,
        selectedBeaconText: '未选择信标',
        selectedFloorText: '',
        selectedLocation: '',
        selectedHeightText: '',
        selectedSessionCount: 0,
        phase: 'select' as Phase,
        phaseText: phaseText('select', false),
      });
    }
  },

  onRetry(): void {
    void this.bootstrap();
  },

  // ------------------------------------------------------------------
  // 信标选择与实时探测（探测只作提示，不自动锁定）
  // ------------------------------------------------------------------

  resolveBeacon(uuid: string, major: number, minor: number): StaticBeaconRef | null {
    const beaconId = appContext.map.beaconRegistry.resolve(uuid, major, minor);
    if (beaconId === null) {
      return null;
    }
    const entry = this.beaconById?.get(beaconId);
    if (entry === undefined) {
      return null;
    }
    return {
      beaconId,
      x: entry.x,
      y: entry.y,
      installHeightM: entry.installHeightM ?? null,
    };
  },

  startSource(): void {
    if (this.sourceRunning || this.source !== null) {
      return;
    }
    const source = createBeaconSource(appContext.dataSource, appContext.map);
    this.source = source;
    this.sourceRunning = true;
    void source
      .start((sample) => this.onSample(sample))
      .catch((err) => {
        log.warn('启动 BLE 扫描失败', err);
        this.sourceRunning = false;
      });
  },

  stopSource(): void {
    this.source?.stop();
    this.source = null;
    this.sourceRunning = false;
  },

  onSample(sample: BeaconSample): void {
    const ref = this.resolveBeacon(sample.uuid, sample.major, sample.minor);
    if (ref === null || this.seen === null) {
      return;
    }
    const now = Date.now();
    this.seen.set(ref.beaconId, { rssi: sample.rssi, at: now });

    if (this.selected !== null && this.selected.beaconId === ref.beaconId) {
      this.selectedLastSampleAt = now;
      if (this.data.phase === 'beacon_lost') {
        this.setPhase('capturing');
      }
    }

    if (this.data.collecting && this.sampler !== null) {
      this.sampler.push(sample);
    }
  },

  startStatusTimer(): void {
    if (this.statusTimer !== null) {
      return;
    }
    this.statusTimer = setInterval(() => this.refreshDetection(false), 1000);
  },

  stopStatusTimer(): void {
    if (this.statusTimer !== null) {
      clearInterval(this.statusTimer);
      this.statusTimer = null;
    }
  },

  /** 刷新“是否收到信号”标记与唯信标提示；列表打开时才重建分组行。 */
  refreshDetection(forceList: boolean): void {
    if (this.seen === null) {
      return;
    }
    const now = Date.now();
    const active = activeSeenBeacons(this.seen, now);
    const uniqueId = uniqueSeenBeaconId(active);
    const patch: Record<string, unknown> = {
      detectHint: detectHintText(active, uniqueId),
      uniqueBeaconId: uniqueId,
    };
    if (forceList || this.data.beaconPickerOpen) {
      patch.beaconGroups = buildBeaconGroups(this.mapBeacons, {
        seen: this.seen,
        nowMs: now,
        selectedId: this.selected?.beaconId ?? null,
        uniqueId,
      });
    }
    this.setData(patch);
  },

  onOpenPicker(): void {
    if (this.data.loadError !== '') {
      wx.showToast({ title: this.data.loadError, icon: 'none' });
      return;
    }
    if (this.data.collecting) {
      wx.showToast({ title: '采集中不能切换信标', icon: 'none' });
      return;
    }
    this.refreshDetection(true);
    this.setData({ beaconPickerOpen: true });
  },

  onClosePicker(): void {
    this.setData({ beaconPickerOpen: false });
  },

  noop(): void {
    // 阻止选择面板内的点击冒泡关闭
  },

  onPickBeacon(e: WechatMiniprogram.TouchEvent): void {
    const beaconId = String(e.currentTarget.dataset.beaconId ?? '');
    const entry = this.beaconById?.get(beaconId);
    if (entry === undefined) {
      wx.showToast({ title: '该信标不在当前地图包中', icon: 'none' });
      return;
    }
    this.selectBeacon(entry);
  },

  selectBeacon(entry: MapBeaconEntry): void {
    if (this.data.collecting) {
      wx.showToast({ title: '采集中不能切换信标', icon: 'none' });
      return;
    }
    if (!hasBeaconTriplet(entry)) {
      wx.showToast({ title: '该信标缺少 uuid/major/minor，无法采集', icon: 'none' });
      return;
    }
    const beacon: LogdistBeacon = {
      beaconId: entry.beaconId,
      uuid: entry.uuid!,
      major: entry.major!,
      minor: entry.minor!,
    };
    if (this.store === null) {
      return;
    }
    this.store.selectBeacon(beacon);
    this.selected = beacon;

    // 安装高度默认取地图包值，之后仍可微调（会话按采集时快照）。
    if (typeof entry.installHeightM === 'number' && Number.isFinite(entry.installHeightM)) {
      const next = this.store.setInstallHeightM(entry.installHeightM);
      this.setData({
        installHeightM: next,
        installHeightText: `${next.toFixed(1)} m`,
        stops: this.store.views(),
      });
      this.syncSelectedText();
    }

    this.setData({ beaconPickerOpen: false });
    this.applySelectedBeaconView(entry);
    this.setPhase('ready');
    this.refreshDetection(true);
    wx.showToast({ title: `已选择 ${entry.beaconId}`, icon: 'none' });
  },

  applySelectedBeaconView(entry: MapBeaconEntry): void {
    const sessionCount = this.store?.sessionCountForBeacon(entry.beaconId) ?? 0;
    this.setData({
      selectedBeaconId: entry.beaconId,
      selectedBeaconText: entry.beaconId,
      selectedFloorText: floorTitle(String(entry.floorId)),
      selectedLocation: entry.locationDesc?.trim() || '地图包未提供位置描述',
      selectedHeightText:
        typeof entry.installHeightM === 'number'
          ? `${entry.installHeightM.toFixed(1)} m`
          : '未知',
      selectedSessionCount: sessionCount,
    });
  },

  // ------------------------------------------------------------------
  // 档位 / 安装高度 / 倒计时
  // ------------------------------------------------------------------

  onSelectStop(e: WechatMiniprogram.TouchEvent): void {
    if (this.data.collecting) {
      wx.showToast({ title: '采集中不能切换档位', icon: 'none' });
      return;
    }
    const stopId = String(e.currentTarget.dataset.stop ?? '');
    const view = this.data.stops.find((s) => s.stopId === stopId);
    this.setData({
      selectedStopId: stopId,
      selectedStopText:
        view !== undefined
          ? `${view.horizontalDistanceM} m（斜距 ${view.slantDistanceM} m）`
          : '',
    });
  },

  onHeightMinus(): void {
    this.adjustHeight(-LOGDIST_INSTALL_STEP_M);
  },

  onHeightPlus(): void {
    this.adjustHeight(LOGDIST_INSTALL_STEP_M);
  },

  adjustHeight(delta: number): void {
    if (this.data.collecting) {
      wx.showToast({ title: '采集中不能修改安装高度', icon: 'none' });
      return;
    }
    if (this.store === null) {
      return;
    }
    const next = this.store.setInstallHeightM(this.data.installHeightM + delta);
    this.setData({
      installHeightM: next,
      installHeightText: `${next.toFixed(1)} m`,
      stops: this.store.views(),
    });
    this.syncSelectedText();
  },

  syncSelectedText(): void {
    if (this.data.selectedStopId === null) {
      return;
    }
    const view = this.data.stops.find((s) => s.stopId === this.data.selectedStopId);
    if (view !== undefined) {
      this.setData({
        selectedStopText: `${view.horizontalDistanceM} m（斜距 ${view.slantDistanceM} m）`,
      });
    }
  },

  onDurationChange(e: WechatMiniprogram.PickerChange): void {
    if (this.data.collecting) {
      wx.showToast({ title: '采集中不能修改倒计时', icon: 'none' });
      return;
    }
    const index = Number(e.detail.value);
    const safe = index >= 0 && index < DURATION_OPTIONS.length ? index : 1;
    this.setData({ durationIndex: safe, durationS: DURATION_OPTIONS[safe] });
  },

  onToggleCapture(): void {
    if (this.data.collecting) {
      this.finishCapture(true);
    } else {
      this.beginCapture();
    }
  },

  beginCapture(): void {
    const store = this.store;
    if (store === null) {
      return;
    }
    if (this.data.loadError !== '') {
      wx.showToast({ title: '地图包未加载，无法采集', icon: 'none' });
      return;
    }
    const selected = this.selected;
    if (selected === null) {
      wx.showToast({ title: '请先从地图包选择要测量的信标', icon: 'none' });
      this.onOpenPicker();
      return;
    }
    const entry = this.beaconById?.get(selected.beaconId);
    if (entry === undefined) {
      this.selected = null;
      store.selectBeacon(null);
      this.setData({
        selectedBeaconId: null,
        selectedBeaconText: '未选择信标',
        selectedFloorText: '',
        selectedLocation: '',
        selectedHeightText: '',
        selectedSessionCount: 0,
      });
      this.setPhase('select');
      wx.showToast({ title: '该信标不在当前地图包中，无法采集', icon: 'none' });
      return;
    }
    if (!hasBeaconTriplet(entry)) {
      wx.showToast({ title: '该信标缺少 uuid/major/minor，无法采集', icon: 'none' });
      return;
    }
    const stopId = this.data.selectedStopId;
    if (stopId === null) {
      wx.showToast({ title: '请先选择距离档位', icon: 'none' });
      return;
    }
    const view = this.data.stops.find((s) => s.stopId === stopId);
    if (view === undefined) {
      return;
    }
    const slant = slantDistanceM(view.horizontalDistanceM, this.data.installHeightM);

    const last = this.seen?.get(selected.beaconId);
    const signalActive = last !== undefined && Date.now() - last.at <= SIGNAL_ACTIVE_MS;
    if (!signalActive) {
      wx.showModal({
        title: '未收到该信标信号',
        content: `${selected.beaconId} 当前未探测到信号，可能未通电或不在扫描范围内。继续采集可能得到 0 样本，仍要开始？`,
        confirmText: '仍要采集',
        confirmColor: '#b45309',
        success: (res) => {
          if (res.confirm) {
            this.startCapture(stopId, view, slant);
          }
        },
      });
      return;
    }
    this.startCapture(stopId, view, slant);
  },

  startCapture(stopId: string, view: LogdistStopView, slant: number): void {
    const selected = this.selected;
    if (selected === null) {
      return;
    }
    this.sampler = new StaticSampler({
      truth: [0, 0],
      resolve: (uuid, major, minor) => this.resolveBeacon(uuid, major, minor),
      fixedTruthDistanceM: slant,
      onlyBeaconId: selected.beaconId,
    });
    this.captureStopId = stopId;
    this.captureHorizontalM = view.horizontalDistanceM;
    this.remainingS = this.data.durationS;
    this.selectedLastSampleAt = Date.now();
    this.setData({
      collecting: true,
      countdownText: `${this.remainingS}s`,
      sampleCount: 0,
      obsRow: null,
    });
    this.setPhase('capturing');
    vibrateShort();
    this.startTick();
  },

  startTick(): void {
    this.stopTick();
    this.tickTimer = setInterval(() => {
      this.remainingS -= 1;
      if (this.data.collecting) {
        this.setData({ countdownText: `${Math.max(0, this.remainingS)}s` });
        this.refreshLiveRow();
        if (
          this.data.phase === 'capturing' &&
          Date.now() - this.selectedLastSampleAt > BEACON_LOST_MS
        ) {
          this.setPhase('beacon_lost');
        }
      }
      if (this.remainingS <= 0) {
        this.finishCapture(false);
      }
    }, 1000);
  },

  stopTick(): void {
    if (this.tickTimer !== null) {
      clearInterval(this.tickTimer);
      this.tickTimer = null;
    }
  },

  refreshLiveRow(): void {
    const sampler = this.sampler;
    if (sampler === null) {
      return;
    }
    const rows = sampler.observations();
    this.setData({
      sampleCount: sampler.sampleCount,
      obsRow:
        rows.length > 0
          ? { beaconId: rows[0].beaconId, n: rows[0].n, rssiMedian: rows[0].rssiMedian }
          : null,
    });
  },

  finishCapture(early: boolean): void {
    if (!this.data.collecting) {
      return;
    }
    if (early) {
      wx.showModal({
        title: '结束采集',
        content: '提前结束将保存当前已收到的样本，确定结束？',
        confirmText: '结束',
        success: (res) => {
          if (res.confirm) {
            this.finalizeCapture();
          }
        },
      });
      return;
    }
    this.finalizeCapture();
  },

  finalizeCapture(): void {
    this.stopTick();
    const sampler = this.sampler;
    const store = this.store;
    const stopId = this.captureStopId;
    const durationS = this.data.durationS;
    this.sampler = null;
    this.setData({ collecting: false, obsRow: null });
    if (sampler === null || store === null || stopId === null) {
      this.setPhase('ready');
      return;
    }
    const body = sampler.finish(durationS);
    if (body.observations.length === 0) {
      wx.showToast({ title: '未收到样本，请检查信标', icon: 'none' });
      this.setPhase('ready');
      if (this.pendingMapSync) {
        this.pendingMapSync = false;
        void this.bootstrap();
      }
      return;
    }
    store.addSession(stopId, this.captureHorizontalM, {
      ...body,
      installHeightM: this.data.installHeightM,
    });
    vibrateLong();
    this.setData({
      stops: store.views(),
      stats: store.stats(),
      sampleCount: 0,
      selectedSessionCount:
        this.selected !== null ? store.sessionCountForBeacon(this.selected.beaconId) : 0,
    });
    this.setPhase('ready');
    wx.showToast({ title: '已保存本次会话', icon: 'success' });
    if (this.pendingMapSync) {
      this.pendingMapSync = false;
      void this.bootstrap();
    }
  },

  setPhase(phase: Phase): void {
    this.setData({ phase, phaseText: phaseText(phase, this.selected !== null) });
  },

  // ------------------------------------------------------------------
  // 清理 / 导出
  // ------------------------------------------------------------------

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
    if (stats.sessionCount === 0 && this.selected === null) {
      wx.showToast({ title: '还没有采集记录', icon: 'none' });
      return;
    }
    wx.showModal({
      title: '清理本页记录',
      content: `将清除本机保存的全部对数等距记录（${stats.sessionCount} 次会话），并清除已选信标。清理后不可恢复。`,
      confirmText: '清理',
      confirmColor: '#b91c1c',
      success: (res) => {
        if (!res.confirm) {
          return;
        }
        store.clearAll();
        this.selected = null;
        this.seen = new Map();
        this.setData({
          stops: store.views(),
          stats: store.stats(),
          installHeightM: store.installHeightM,
          installHeightText: `${store.installHeightM.toFixed(1)} m`,
          selectedBeaconId: null,
          selectedBeaconText: '未选择信标',
          selectedFloorText: '',
          selectedLocation: '',
          selectedHeightText: '',
          selectedSessionCount: 0,
          selectedStopId: null,
          selectedStopText: '',
          beaconPickerOpen: false,
        });
        this.setPhase('select');
        this.refreshDetection(true);
        wx.showToast({ title: '已清理', icon: 'success' });
      },
    });
  },

  onExport(): void {
    const store = this.store;
    if (store === null) {
      return;
    }
    const selected = this.selected;
    if (selected === null) {
      wx.showToast({ title: '请先选择信标再导出', icon: 'none' });
      return;
    }
    if (store.sessionCountForBeacon(selected.beaconId) === 0) {
      wx.showToast({ title: '当前信标还没有采集记录', icon: 'none' });
      return;
    }
    let device: unknown = null;
    try {
      const info = wx.getSystemInfoSync();
      device = { model: info.model, system: info.system, platform: info.platform };
    } catch {
      device = null;
    }
    const { data, warnings } = buildLogdistExport(store, {
      operator: appContext.getCalibrationOperator(),
      device: device as string | null,
      appVersion: '1.0.0',
      mapVersion: appContext.map.isLoaded ? appContext.map.requireVersion() : null,
    });
    const json = JSON.stringify(data, null, 2);
    const fs = wx.getFileSystemManager();
    const safeBeaconId = selected.beaconId.replace(/[^A-Za-z0-9_-]/g, '_');
    const path = `${wx.env.USER_DATA_PATH}/logdist_${safeBeaconId}_${Date.now()}.json`;
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
              content: `信标 ${selected.beaconId} · 档位 ${data.summary.stopCount}/10 · 会话 ${data.summary.sessionCount} · 观测 ${data.summary.observationCount}${
                warnings.length > 0 ? `\n提示：${warnings.slice(0, 3).join('；')}` : ''
              }\n阶段一数据 deployable=false，仅用于评审。`,
              showCancel: false,
            });
          },
        });
      },
      fail: () => wx.showModal({ title: '导出失败', content: '写文件失败', showCancel: false }),
    });
  },
});
