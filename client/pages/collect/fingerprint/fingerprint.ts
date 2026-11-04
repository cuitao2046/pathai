/**
 * 网格指纹采集页（spec：field-collection-integration §4）：
 * - 地图模式展示全楼 1434 点网格（按层过滤），已采点绿 + 次数角标，
 *   仅现场实际采集的点写入记录（D4）；
 * - 到点（现场参照物人工判定）→ 点地图网格点 → 确认卡 → 两短振
 *   → 3s 快照倒计时 → 2s 窗口逐信标中位数 → 自动落盘（记录追加制，
 *   captureIndex = 该点记录数 + 1）；
 * - 分区字段恒 null（D5）；导出 schema 与 fingerprint-collector 完全一致（D6）。
 */
import { appContext } from '../../../services/app-context';
import { DEFAULT_SITE_ID, BEACON_RSSI_AT_1M_DBM } from '../../../config/env';
import { createLogger } from '../../../utils/logger';
import { getJson, setJson } from '../../../utils/kv';
import { vibrateShort } from '../../../utils/haptic';
import { createBeaconSource } from '../../../core/ble/beacon-source-factory';
import {
  ScanWindowAggregator,
  type BeaconSample,
} from '../../../core/ble/scan-window';
import { LocationPipeline } from '../../../core/position/location-pipeline';
import { SequenceCounter } from '../../../core/position/sequence-counter';
import { CollectMapView } from '../../../core/collection/collect-map-view';
import { pickPointOnScreen } from '../../../core/collection/hit-test';
import { createTeeSource } from '../../../core/collection/collect-scan';
import { buildFpRecord, type FpBeaconLookup, type FpSnapshotPoint } from '../../../core/collection/fp-snapshot';
import { FpRecordsStore } from '../../../core/collection/points-store';
import { buildFpExport, type CollectionExportMeta } from '../../../core/collection/export-builder';
import type { FpRecord } from '../../../core/collection/types';
import { FINGERPRINT_GRID, FINGERPRINT_GRID_VERSION, type FpGridPoint } from '../../../data/fingerprint_grid';
import type { PositionEstimateResponse } from '../../../typings/api';

const log = createLogger('collect-fp');

const STORE_KEY = 'col:fp:v1';

/** 快照倒计时（秒）：走-停-拍语义。 */
const SNAPSHOT_COUNTDOWN_S = 3;

/** 网格点命中半径（画布 CSS 像素）。 */
const HIT_RADIUS_PX = 16;

const REGION_TEXT: Record<string, string> = {
  normal: '走廊面',
  safe: '楼梯电梯口加密',
};

function qualityUsable(q: string): boolean {
  return q === 'reliable' || q === 'degraded';
}

interface CardData {
  fpId: string;
  regionText: string;
  floorText: string;
  coordText: string;
  recordCount: number;
  distanceText: string;
}

Page({
  data: {
    loading: true,
    loadError: '',
    isMock: appContext.dataSource === 'mock',
    gridVersion: FINGERPRINT_GRID_VERSION,
    floors: [] as Array<{ floorId: string; displayName: string; level: number }>,
    currentFloor: '1',
    currentFloorText: '1F',
    stats: { recordCount: 0, fpCount: 0 },
    // 快照进行中
    collecting: false,
    countdownText: '',
    // 确认卡
    card: null as CardData | null,
  },

  mapView: null as CollectMapView | null,
  store: null as FpRecordsStore | null,
  pipeline: null as LocationPipeline | null,
  sequence: null as SequenceCounter | null,
  aggregator: null as ScanWindowAggregator | null,
  /** beaconId → 三元组（fp 记录回填 uuid/major/minor 用）。 */
  tripletById: new Map<string, { uuid: string; major: number; minor: number }>(),
  /** fpId → 已采记录数。 */
  recordCounts: new Map<string, number>(),
  /** floorId → 该层网格点。 */
  gridByFloor: new Map<string, FpGridPoint[]>(),
  lastEstimate: null as PositionEstimateResponse | null,
  collectPoint: null as FpSnapshotPoint | null,
  tickTimer: null as ReturnType<typeof setInterval> | null,
  remainingS: 0,
  pausedRemainingS: 0,

  onLoad(): void {
    this.store = new FpRecordsStore(
      () => getJson<FpRecord[]>(STORE_KEY),
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
    // 三元组反查表（fp 记录需要 uuid/major/minor；缺三元组的台账项跳过）
    this.tripletById = new Map();
    for (const b of appContext.map.beacons) {
      if (typeof b.uuid === 'string' && typeof b.major === 'number' && typeof b.minor === 'number') {
        this.tripletById.set(b.beaconId, { uuid: b.uuid, major: b.major, minor: b.minor });
      }
    }
    // 网格按层分组
    this.gridByFloor = new Map();
    for (const p of FINGERPRINT_GRID) {
      const list = this.gridByFloor.get(p.floorId);
      if (list) {
        list.push(p);
      } else {
        this.gridByFloor.set(p.floorId, [p]);
      }
    }
    this.refreshStats();
    this.setData({ loading: false });
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
    this.aggregator = new ScanWindowAggregator();
    const taps: Array<(s: BeaconSample) => void> = [
      (s) => this.aggregator?.push(s),
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
      onUnusable: () => undefined,
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
        onTap: (_world, screen) => this.handleMapTap(screen),
        onLongPress: () => undefined, // 网格固定，不支持补标
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

  refreshStats(): void {
    if (this.store === null) {
      return;
    }
    this.recordCounts = new Map();
    for (const r of this.store.allRecords()) {
      this.recordCounts.set(r.fpId, (this.recordCounts.get(r.fpId) ?? 0) + 1);
    }
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

  handleMapTap(screen: { x: number; y: number }): void {
    if (this.data.collecting || this.data.card !== null) {
      return;
    }
    const vp = this.mapView?.vp;
    if (this.mapView === null || vp === null || vp === undefined) {
      return;
    }
    const pts = this.gridByFloor.get(vp.floorId) ?? [];
    // 屏幕空间命中：网格点按当前视口投影后再比距离，缩放/平移后不偏移
    const hit = pickPointOnScreen(
      pts,
      (p) => ({ x: p.x, y: p.y }),
      vp,
      screen,
      HIT_RADIUS_PX,
    );
    if (hit !== null) {
      this.openCard(hit.point);
    }
  },

  openCard(p: FpGridPoint): void {
    const floorItem = (this.mapView?.floorList ?? []).find((f) => f.floorId === p.floorId);
    const est = this.lastEstimate;
    let distanceText = '—';
    if (
      est !== null &&
      est.position != null &&
      qualityUsable(est.quality) &&
      est.position.floorId === p.floorId
    ) {
      const d = Math.hypot(est.position.x - p.x, est.position.y - p.y);
      distanceText = `≈${d.toFixed(1)} m`;
    }
    this.setData({
      card: {
        fpId: p.fpId,
        regionText: REGION_TEXT[p.regionType] ?? p.regionType,
        floorText: floorItem !== undefined ? `${floorItem.level}F` : p.floorId,
        coordText: `${p.x.toFixed(1)}, ${p.y.toFixed(1)}`,
        recordCount: this.recordCounts.get(p.fpId) ?? 0,
        distanceText,
      },
    });
    this.pendingCardPoint = {
      fpId: p.fpId,
      floorId: p.floorId,
      x: p.x,
      y: p.y,
      regionType: p.regionType,
    };
  },

  pendingCardPoint: null as FpSnapshotPoint | null,

  onCardCancel(): void {
    this.setData({ card: null });
    this.pendingCardPoint = null;
  },

  noop(): void {
    // 阻止确认卡冒泡关闭
  },

  /** 确认到点：两短振 → 3s 快照倒计时（spec §4.2）。 */
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
    // 每次快照用干净窗口：重建聚合器（tee tap 闭包引用 this.aggregator）
    this.aggregator = new ScanWindowAggregator();
    this.remainingS = SNAPSHOT_COUNTDOWN_S;
    this.pausedRemainingS = 0;
    this.setData({
      collecting: true,
      countdownText: `${this.remainingS}s`,
    });
    this.startTick();
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
    this.remainingS -= 1;
    if (this.remainingS <= 0) {
      this.finishSnapshot();
      return;
    }
    this.setData({ countdownText: `${this.remainingS}s` });
  },

  /** 快照落盘：2s 窗口中位数 → FpRecord（记录追加制）。 */
  finishSnapshot(): void {
    this.stopTick();
    const p = this.collectPoint;
    if (p === null || this.store === null || this.aggregator === null) {
      return;
    }
    const window = this.aggregator.snapshot(appContext.map.beaconRegistry, Date.now());
    if (window === null) {
      this.setData({ collecting: false, countdownText: '' });
      this.collectPoint = null;
      wx.showModal({
        title: '未记录',
        content: '窗口内可解析信标不足 3 个，请检查蓝牙或稍后在信号较好时重试。',
        showCancel: false,
      });
      return;
    }
    const lookup: FpBeaconLookup = (beaconId) => this.tripletById.get(beaconId) ?? null;
    const rec = buildFpRecord({
      point: p,
      window,
      lookup,
      rssiAt1m: BEACON_RSSI_AT_1M_DBM,
    });
    const full = this.store.addRecord(rec);
    log.info('指纹已记录', full.fpId, `#${full.captureIndex}`, `${full.beaconCount} 信标`);
    this.collectPoint = null;
    this.setData({ collecting: false, countdownText: '' });
    this.refreshStats();
    wx.showToast({ title: `已记录 ${full.fpId}`, icon: 'success' });
  },

  /** 记录管理（确认卡内入口）。 */
  onManageRecords(): void {
    const p = this.pendingCardPoint;
    if (p === null || this.store === null) {
      return;
    }
    const records = this.store.recordsFor(p.fpId);
    const n = records.length;
    const items: string[] = [];
    const actions: Array<() => void> = [];
    if (n > 0) {
      items.push(`删除最近一条记录（第 ${n} 次）`);
      actions.push(() => {
        const list = this.store?.recordsFor(p.fpId) ?? [];
        const last = list[list.length - 1];
        if (last !== undefined) {
          this.store?.removeRecord(p.fpId, last.collectedAt);
          this.refreshStats();
        }
      });
      items.push(`清空该点全部记录（${n} 条）`);
      actions.push(() => {
        for (const r of this.store?.recordsFor(p.fpId) ?? []) {
          this.store?.removeRecord(p.fpId, r.collectedAt);
        }
        this.refreshStats();
      });
    }
    wx.showActionSheet({
      itemList: items.length > 0 ? items : ['暂无记录'],
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
  // 顶栏控件 / 导出
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

  onExport(): void {
    const store = this.store;
    if (store === null) {
      return;
    }
    const stats = store.stats();
    if (stats.recordCount === 0) {
      wx.showToast({ title: '还没有指纹记录', icon: 'none' });
      return;
    }
    let device: unknown = null;
    try {
      const info = wx.getSystemInfoSync();
      device = { model: info.model, system: info.system, platform: info.platform };
    } catch {
      device = null;
    }
    // beaconConfig：地图包信标台账的 uuid（唯一）/ major 集合
    const uuidSet = new Set<string>();
    const majorSet = new Set<number>();
    for (const b of appContext.map.beacons) {
      if (typeof b.uuid === 'string') {
        uuidSet.add(b.uuid);
      }
      if (typeof b.major === 'number') {
        majorSet.add(b.major);
      }
    }
    const majors = [...majorSet].sort((a, b) => a - b);
    const meta: CollectionExportMeta = {
      venueId: DEFAULT_SITE_ID,
      venueName: DEFAULT_SITE_ID,
      operator: appContext.getCalibrationOperator(),
      device: device as CollectionExportMeta['device'],
      appVersion: '1.0.0',
      mapVersion: appContext.map.isLoaded ? appContext.map.requireVersion() : '',
      ledgerSha256: null,
    };
    const { data, warnings } = buildFpExport(store, meta, {
      beaconUuid: [...uuidSet][0] ?? '',
      majors,
    });

    const json = JSON.stringify(data, null, 2);
    const fs = wx.getFileSystemManager();
    const path = `${wx.env.USER_DATA_PATH}/ble_fingerprint_${Date.now()}.json`;
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
              content: `记录 ${stats.recordCount} 条 · 点位 ${stats.fpCount} 个${
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
  // 叠加图层：网格点 + 定位点
  // ------------------------------------------------------------------

  drawOverlay(
    ctx: WechatMiniprogram.CanvasRenderingContext.CanvasRenderingContext2D,
    vp: NonNullable<CollectMapView['vp']>,
  ): void {
    const pts = this.gridByFloor.get(vp.floorId) ?? [];
    // 网格点：小方块（1434 点逐帧 arc 开销大，fillRect 快一个量级）
    for (const p of pts) {
      const { sx, sy } = vp.toScreen(p.x, p.y);
      const n = this.recordCounts.get(p.fpId) ?? 0;
      if (n > 0) {
        ctx.fillStyle = '#16a34a';
        ctx.fillRect(sx - 4, sy - 4, 8, 8);
        ctx.font = 'bold 10px -apple-system, "PingFang SC", sans-serif';
        ctx.textAlign = 'center';
        ctx.textBaseline = 'middle';
        ctx.lineWidth = 2;
        ctx.strokeStyle = 'rgba(255, 255, 255, 0.9)';
        ctx.strokeText(String(n), sx, sy - 10);
        ctx.fillStyle = '#15803d';
        ctx.fillText(String(n), sx, sy - 10);
      } else {
        ctx.fillStyle = 'rgba(37, 99, 235, 0.55)';
        ctx.fillRect(sx - 2, sy - 2, 4, 4);
      }
    }

    // 定位估计（可信蓝点）
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
