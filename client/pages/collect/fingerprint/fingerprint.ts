/**
 * 网格指纹采集页（spec：field-collection-integration §4，v0.2.0 两层模型）：
 * - 设计层全量网格（按层过滤），按当前实际信标部署区分“可采集/当前不可采集”，
 *   已采点绿 + 次数角标，仅现场实际采集的点写入记录（D4）；
 * - LOD：低缩放只画 2m 底网点，放大后再显示 1m 细分点，避免整楼视图误点；
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
import {
  FP_POINT_COLORS,
  filterVisiblePoints,
  floorCounts,
  isRoutePoint,
  isSimOnly,
  pointStyle,
  routeText,
  type FilterMode,
  type FloorCounts,
} from '../../../core/collection/fingerprint-visibility';
import { createTeeSource } from '../../../core/collection/collect-scan';
import { buildFpRecord, type FpBeaconLookup, type FpSnapshotPoint } from '../../../core/collection/fp-snapshot';
import { FpRecordsStore } from '../../../core/collection/points-store';
import {
  buildFpExport,
  type CollectionExportMeta,
  type FpExportData,
} from '../../../core/collection/export-builder';
import {
  countUnexported,
  countUnexportedDeletions,
  listUnexported,
  markDeletionsExported,
  markRecordsExported,
} from '../../../core/collection/export-ledger';
import {
  addTombstones,
  buildTombstone,
  listTombstones,
  removeTombstones,
} from '../../../core/collection/tombstones';
import { diffManifest } from '../../../core/collection/manifest-diff';
import { describeError } from '../../../core/http/error-messages';
import { ApiClientError } from '../../../core/http/errors';
import type { ApiResponse } from '../../../core/http/api-client';
import type { FpDeletion, FpRecord } from '../../../core/collection/types';
import { FINGERPRINT_GRID, FINGERPRINT_GRID_VERSION, type FpGridPoint } from '../../../data/fingerprint_grid';
import {
  resolveRuntimeGrid,
  type RuntimeGrid,
} from '../../../core/collection/grid-overlay';
import {
  cachedRuntimeParams,
  refreshRuntimeParams,
} from '../../../core/config/runtime-config';
import type {
  FingerprintActiveResponse,
  FingerprintIngestResponse,
  FingerprintManifestResponse,
  FingerprintVersionsResponse,
  PositionEstimateResponse,
} from '../../../typings/api';

const log = createLogger('collect-fp');

const STORE_KEY = 'col:fp:v1';

/** 快照倒计时（秒）：走-停-拍语义。 */
const SNAPSHOT_COUNTDOWN_S = 3;

/** 网格点命中半径（画布 CSS 像素）。 */
const HIT_RADIUS_PX = 16;

/** 网格点配色（集中定义；路线点橙、可采集蓝、不可采集灰、已采集绿）。 */
const COLORS = FP_POINT_COLORS;

/**
 * 1m 细分点的 LOD 判据见 fingerprint-visibility（8 CSS px，等价 scale≥8px/m；
 * 按各层 fitScale 换算：F1 约 2.4×、F2 约 1.8×）。路线点不受该限制。
 */

const REGION_TEXT: Record<string, string> = {
  normal: '普通',
  intersection: '交叉路口',
  doorway: '门口',
  high_risk: '楼梯/电梯口',
};

function qualityUsable(q: string): boolean {
  return q === 'reliable' || q === 'degraded';
}

interface CardData {
  fpId: string;
  shortId: string;
  regionText: string;
  floorText: string;
  coordText: string;
  gridText: string;
  collectText: string;
  refText: string;
  routeText: string;
  recordCount: number;
  distanceText: string;
}

Page({
  data: {
    loading: true,
    loadError: '',
    isMock: appContext.dataSource === 'mock',
    gridVersion: FINGERPRINT_GRID_VERSION,
    /** 网格来源：内置快照 / 在线覆盖层 / 缓存覆盖层（顶栏展示）。 */
    gridSourceText: '内置快照',
    /** 覆盖层降级说明（基线不一致、离线等），空串=正常。 */
    gridMessage: '',
    /** 采集时写入记录的参数版本（未知时不写）。 */
    gridParamsVersion: '',
    floors: [] as Array<{ floorId: string; displayName: string; level: number }>,
    currentFloor: '1',
    currentFloorText: '1F',
    stats: { recordCount: 0, fpCount: 0 },
    /** 相对导出台账尚未导出的记录数（>0 时禁止入库并提示先导出）。 */
    unexportedCount: 0,
    /** 本地删除墓碑数量（本地删了→导出→入库→库里真删）。 */
    tombstoneCount: 0,
    /** 尚未随导出文件落盘的墓碑数（>0 时同样禁止入库）。 */
    unexportedDeletionCount: 0,
    /** 入库进行中（防重复点击）。 */
    ingestBusy: false,
    /** 服务端当前指纹库状态（顶栏展示）：'未入库' / '已入库 fpl-…' / '不可用…'。 */
    libraryStatusText: '指纹库未入库',
    /**
     * 筛选档位：collectable（默认，仅可采集）/ all / route（仅路线点）。
     * 现场口径是「只采高亮点」，默认就该只显示这些点，避免在几百个灰点里找目标。
     */
    filterMode: 'collectable' as FilterMode,
    /** 本层点数统计（总 / 可采集 / 已采），随换层与采集刷新。 */
    floorStats: { total: 0, collectable: 0, collected: 0, simOnly: 0 } as FloorCounts,
    /**
     * 是否叠加「补装 5 枚未安装虚拟信标」的覆盖影响（设置项，默认关闭）。
     * 关闭时展示 v11 真实口径；打开后多出的可采点用紫色虚线标出。
     */
    simPreview: false,
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
  /**
   * beaconId → 三元组（fp 记录回填 uuid/major/minor 用）。
   * Page 定义期只允许简单值，Map 会被框架深拷贝并丢失方法（console 告警
   * `Free data with key ... seems not a simple value`），故定义期 null 占位，
   * onLoad 实例化，使用点 `!` 断言（均发生在 onLoad 之后）。
   */
  tripletById: null as Map<string, { uuid: string; major: number; minor: number }> | null,
  /** fpId → 已采记录数。 */
  recordCounts: null as Map<string, number> | null,
  /** floorId → 该层网格点。 */
  gridByFloor: null as Map<string, FpGridPoint[]> | null,
  /** 当前生效的运行时网格（内置或覆盖层合并结果）。 */
  runtimeGrid: null as RuntimeGrid | null,
  /** 采集时写入记录的参数版本（null=未知）。 */
  runtimeParamsVersion: null as string | null,
  /** 防止 onShow/bootstrap 重复拉取覆盖层。 */
  gridRefreshInFlight: false,
  lastEstimate: null as PositionEstimateResponse | null,
  collectPoint: null as FpSnapshotPoint | null,
  tickTimer: null as ReturnType<typeof setInterval> | null,
  remainingS: 0,
  pausedRemainingS: 0,

  onLoad(): void {
    this.tripletById = new Map();
    this.recordCounts = new Map();
    this.gridByFloor = new Map();
    this.store = new FpRecordsStore(
      () => getJson<FpRecord[]>(STORE_KEY),
      (d) => setJson(STORE_KEY, d),
    );
    this.sequence = new SequenceCounter();
    this.setData({ simPreview: appContext.getSimBeaconPreviewSetting() });
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
    // 倒计时暂停（回前台继续）
    if (this.data.collecting && this.tickTimer !== null) {
      this.pausedRemainingS = this.remainingS;
      this.stopTick();
    }
    // 释放蓝牙：后台挂起时监听器可能失效，回前台由 onShow 重新建立。
    // 共享扫描器按 handler 引用计数，这里只释放本页，不会顶掉其他采集页。
    this.pipeline?.stop();
  },

  onShow(): void {
    this.startScan();
    // 设置页开关可能刚被改动：回前台时同步，切换口径要立即重算计数并重绘
    const simPreview = appContext.getSimBeaconPreviewSetting();
    if (simPreview !== this.data.simPreview) {
      this.setData({ simPreview });
      if (!this.data.loading) {
        this.refreshFloorStats(this.data.currentFloor);
        this.mapView?.requestDraw();
      }
    }
    if (this.data.collecting && this.pausedRemainingS > 0) {
      this.remainingS = this.pausedRemainingS;
      this.pausedRemainingS = 0;
      this.startTick();
    }
    // 再次进入页面时校核一次版本（非阻塞；失败保留当前点集）
    if (!this.data.loading) {
      void this.refreshRuntimeGrid();
      void this.refreshLibraryStatus();
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
        this.tripletById!.set(b.beaconId, { uuid: b.uuid, major: b.major, minor: b.minor });
      }
    }
    // 网格按层分组
    this.gridByFloor = new Map();
    for (const p of FINGERPRINT_GRID) {
      const list = this.gridByFloor!.get(p.floorId);
      if (list) {
        list.push(p);
      } else {
        this.gridByFloor!.set(p.floorId, [p]);
      }
    }
    this.refreshStats();
    this.setData({ loading: false });
    this.initPipeline();
    // 首次进入时 onShow 早于异步 bootstrap 完成（pipeline 尚不存在），
    // 必须在这里补一次启动；否则 mock/live 都不会开始采样。
    this.startScan();
    this.initMap();
    // 覆盖层异步下发：不阻塞采集页首帧（spec P1），失败自动回退内置/缓存
    void this.refreshRuntimeGrid();
    // 指纹库状态（是否已入库/版本号）：异步，不影响采集
    void this.refreshLibraryStatus();
  },

  /**
   * 拉取运行时网格覆盖层（启动 + 进页各一次，异步非阻塞）。
   * 基线不一致/离线时 resolveRuntimeGrid 内部降级，不会抛错。
   */
  async refreshRuntimeGrid(): Promise<void> {
    if (this.gridRefreshInFlight || this.data.loading) {
      return;
    }
    this.gridRefreshInFlight = true;
    try {
      const grid = await resolveRuntimeGrid(appContext.http.client, FINGERPRINT_GRID);
      const params =
        cachedRuntimeParams() ??
        (await refreshRuntimeParams(appContext.http.client, { maxAgeMs: 60_000 }));
      this.applyRuntimeGrid(grid, params?.paramsVersion ?? grid.paramsVersion);
    } catch (err) {
      log.warn('运行时网格解析失败，保留当前点集', err);
    } finally {
      this.gridRefreshInFlight = false;
    }
  },

  /** 应用合并后的点集；不影响当前楼层、筛选档位与已采记录。 */
  applyRuntimeGrid(grid: RuntimeGrid, paramsVersion: string | null): void {
    this.runtimeGrid = grid;
    this.runtimeParamsVersion = paramsVersion;
    const byFloor = new Map<string, FpGridPoint[]>();
    for (const p of grid.points) {
      const list = byFloor.get(p.floorId);
      if (list) {
        list.push(p);
      } else {
        byFloor.set(p.floorId, [p]);
      }
    }
    this.gridByFloor = byFloor;
    this.setData({
      gridVersion: grid.gridVersion,
      gridSourceText:
        grid.source === 'online'
          ? '在线覆盖层'
          : grid.source === 'cache'
            ? '缓存覆盖层'
            : '内置快照',
      gridMessage: grid.message,
      gridParamsVersion: paramsVersion ?? '',
    });
    this.refreshStats();
    this.mapView?.requestDraw();
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

  /** 启动信标源（幂等）：onShow / bootstrap 都可以安全调用。 */
  startScan(): void {
    const pipeline = this.pipeline;
    if (pipeline === null || pipeline.isRunning) {
      return;
    }
    log.info('启动信标源');
    void pipeline.start('navigating').catch((err: unknown) => {
      // LocationPipeline.start 失败时已把 running 复位，下一次 onShow/重试可自愈
      log.warn('信标源启动失败', err instanceof Error ? err.message : String(err));
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
          const floorId = this.mapView?.floorId ?? '1';
          this.setData({
            floors: this.mapView?.floorList ?? [],
            currentFloor: floorId,
          });
          this.refreshFloorStats(floorId);
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
          this.refreshFloorStats(floorId);
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
    const records = this.store.allRecords();
    const tombstones = listTombstones();
    this.recordCounts = new Map();
    for (const r of records) {
      this.recordCounts!.set(r.fpId, (this.recordCounts!.get(r.fpId) ?? 0) + 1);
    }
    this.setData({
      stats: this.store.stats(),
      unexportedCount: countUnexported(records),
      tombstoneCount: tombstones.length,
      unexportedDeletionCount: countUnexportedDeletions(tombstones),
    });
    this.refreshFloorStats(this.data.currentFloor);
    this.mapView?.requestDraw();
  },

  /** 本层「可采集 N / 总 M / 已采 K」计数（顶栏展示）。 */
  refreshFloorStats(floorId: string): void {
    const pts = this.gridByFloor!.get(floorId) ?? [];
    this.setData({
      floorStats: floorCounts(pts, this.recordCounts!, this.data.simPreview),
    });
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
    // 只对当前实际绘制出来的点做命中，避免低缩放下误点未显示的 1m 点
    const pts = this.visiblePoints(this.gridByFloor!.get(vp.floorId) ?? [], vp);
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

  /** 当前视口下实际绘制的点：LOD（路线点豁免）+ 筛选档位。 */
  visiblePoints(
    pts: FpGridPoint[],
    vp: NonNullable<CollectMapView['vp']>,
  ): FpGridPoint[] {
    return filterVisiblePoints(pts, vp.scale, this.data.filterMode, undefined, this.data.simPreview);
  },

  /** 筛选档位切换：全部 / 仅可采集 / 仅路线点。 */
  onFilterMode(e: WechatMiniprogram.TouchEvent): void {
    const mode = String(e.currentTarget.dataset.mode ?? 'all') as FilterMode;
    if (mode !== 'all' && mode !== 'collectable' && mode !== 'route') {
      return;
    }
    this.setData({ filterMode: mode });
    this.mapView?.requestDraw();
  },

  openCard(p: FpGridPoint): void {
    const floorItem = (this.mapView?.floorList ?? []).find((f) => f.floorId === p.floorId);
    const est = this.lastEstimate;
    const recordCount = this.recordCounts!.get(p.fpId) ?? 0;
    const collectableNow = p.collectable || (this.data.simPreview && p.collectableWithSim === true);
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
    const collectText = p.collectable
      ? `可采集（3D 10m 内可见 ≥3，最强 ${p.strongestRssi ?? '—'}dBm）`
      : this.data.simPreview && p.collectableWithSim === true
        ? `仅补装仿真可采（${p.simAddedBy.length} 枚未安装虚拟信标补足 ≥3）`
        : `当前不可采集（3D 10m 内可见 ${p.visible10m} 个）`;
    this.setData({
      card: {
        fpId: p.fpId,
        shortId: p.shortId,
        regionText: REGION_TEXT[p.regionType] ?? p.regionType,
        floorText: floorItem !== undefined ? `${floorItem.level}F` : p.floorId,
        coordText: `${p.x.toFixed(1)}, ${p.y.toFixed(1)}`,
        gridText: `列 ${p.col} / 行 ${p.row}`,
        collectText:
          recordCount > 0 && !collectableNow
            ? `${collectText}；已采记录保留，导出不丢弃`
            : collectText,
        refText: `15m 可见 ${p.visible15m} 个${p.visible15m >= 3 ? '（已满足备用口径）' : ''}`,
        routeText: routeText(p.routeTags),
        recordCount,
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
    const lookup: FpBeaconLookup = (beaconId) => this.tripletById!.get(beaconId) ?? null;
    const rec = buildFpRecord({
      point: p,
      window,
      lookup,
      rssiAt1m: BEACON_RSSI_AT_1M_DBM,
      // 版本标记：仅当覆盖层成功解析（在线/缓存）时写入；
      // 内置快照（含拉取失败降级）不写网格版本，下游按 unknown 处理，避免误标（spec P2）。
      gridVersion:
        this.runtimeGrid !== null && this.runtimeGrid.source !== 'builtin'
          ? this.runtimeGrid.gridVersion
          : undefined,
      paramsVersion: this.runtimeParamsVersion ?? undefined,
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
          // 删除即留墓碑：导出后服务端据此生成含 deletedCount 的新版本
          addTombstones([buildTombstone(last)]);
          this.store?.removeRecord(p.fpId, last.collectedAt);
          this.refreshStats();
        }
      });
      items.push(`清空该点全部记录（${n} 条）`);
      actions.push(() => {
        const list = this.store?.recordsFor(p.fpId) ?? [];
        addTombstones(list.map((r) => buildTombstone(r)));
        for (const r of list) {
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
  // 清理记录
  // ------------------------------------------------------------------

  /**
   * 清理本机指纹记录：确认后清空 col:fp:v1（全部快照记录），
   * 计数 / 点位着色 / 筛选结果同步刷新。已导出的 JSON 文件保留不动。
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
    if (stats.recordCount === 0) {
      wx.showToast({ title: '还没有指纹记录', icon: 'none' });
      return;
    }
    wx.showModal({
      title: '清理指纹记录',
      content:
        `将清除本机保存的全部指纹记录：${stats.recordCount} 条记录（覆盖 ${stats.fpCount} 个网格点）。\n` +
        '清理后不可恢复；已导出的 JSON 文件不受影响。',
      confirmText: '清理',
      confirmColor: '#b91c1c',
      success: (res) => {
        if (!res.confirm) {
          return;
        }
        store.clearAll();
        this.setData({ card: null });
        this.refreshStats();
        wx.showToast({ title: '已清理', icon: 'success' });
      },
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

  /** 构建导出/入库共用的载荷（入库上传的即「当前已全部导出」的同一份数据）。 */
  buildExportBundle(opts?: {
    mode?: 'merge' | 'replaceFloor';
    replaceFloors?: string[];
  }): { data: FpExportData; warnings: string[] } | null {
    const store = this.store;
    if (store === null) {
      return null;
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
    return buildFpExport(store, meta, {
      beaconUuid: [...uuidSet][0] ?? '',
      majors,
      deletions: listTombstones(),
      mode: opts?.mode,
      replaceFloors: opts?.replaceFloors,
    });
  },

  onExport(): void {
    const store = this.store;
    if (store === null) {
      return;
    }
    const stats = store.stats();
    const tombstones = listTombstones();
    if (stats.recordCount === 0 && tombstones.length === 0) {
      wx.showToast({ title: '还没有指纹记录', icon: 'none' });
      return;
    }
    const bundle = this.buildExportBundle();
    if (bundle === null) {
      return;
    }
    const { data, warnings } = bundle;

    const json = JSON.stringify(data, null, 2);
    const fs = wx.getFileSystemManager();
    const path = `${wx.env.USER_DATA_PATH}/ble_fingerprint_${Date.now()}.json`;
    fs.writeFile({
      filePath: path,
      data: json,
      encoding: 'utf8',
      success: () => {
        // 写文件成功即视为「已导出」：记录与墓碑一起记账，入库才允许上传
        const ledgerStored = markRecordsExported(data.records);
        const deletionLedgerStored = markDeletionsExported(data.deletions);
        this.refreshStats();
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
              content: `记录 ${stats.recordCount} 条 · 点位 ${stats.fpCount} 个 · 删除墓碑 ${data.deletions.length} 条${
                warnings.length > 0 ? `\n提示：${warnings.slice(0, 3).join('；')}` : ''
              }${
                ledgerStored && deletionLedgerStored
                  ? ''
                  : '\n警告：导出台账写入本地存储失败，入库前请重新导出。'
              }\nJSON 已复制剪贴板并写入文件。`,
              showCancel: false,
            });
          },
        });
      },
      fail: () => wx.showModal({ title: '导出失败', content: '写文件失败', showCancel: false }),
    });
  },

  /**
   * 入库：只上传已导出的数据。
   *
   * 采用「明确拒绝 + 去导出」而非自动导出：导出是可在本地留痕的显式动作，
   * 自动导出会把写文件/剪贴板等副作用藏在入库流程里；出现未导出记录时
   * 直接阻断，让现场先完成可审计的导出，再重试入库。
   */
  onIngest(): void {
    const store = this.store;
    if (store === null || this.data.ingestBusy) {
      return;
    }
    const records = store.allRecords();
    const tombstones = listTombstones();
    if (records.length === 0 && tombstones.length === 0) {
      wx.showToast({ title: '还没有指纹记录', icon: 'none' });
      return;
    }
    const pendingRecords = listUnexported(records);
    const pendingDeletions = countUnexportedDeletions(tombstones);
    if (pendingRecords.length > 0 || pendingDeletions > 0) {
      wx.showModal({
        title: '存在未导出变更',
        content: `当前有 ${pendingRecords.length} 条记录、${pendingDeletions} 条删除尚未导出（记录共 ${records.length} 条）。\n入库只接受已成功导出的数据，请先导出后再入库。`,
        confirmText: '去导出',
        cancelText: '取消',
        success: (res) => {
          if (res.confirm) {
            this.onExport();
          }
        },
      });
      return;
    }
    void this.startIngestFlow();
  },

  /** 拉取服务端对账清单（失败不阻断入库，但会明确提示跳过比对）。 */
  async fetchManifest(): Promise<FingerprintManifestResponse | null> {
    try {
      const resp = await appContext.http.client.request<FingerprintManifestResponse>({
        method: 'GET',
        path: '/api/v1/fingerprint-db/manifest',
        auth: false,
        timeoutMs: 10000,
      });
      return resp.data;
    } catch (err) {
      log.warn('manifest 获取失败，跳过对账', err);
      return null;
    }
  },

  /** 入库前对账：库中有、本地无的记录必须显式确认，不得静默忽略。 */
  async startIngestFlow(): Promise<void> {
    const store = this.store;
    if (store === null) {
      return;
    }
    const records = store.allRecords();
    wx.showLoading({ title: '核对库内记录…', mask: true });
    const manifest = await this.fetchManifest();
    wx.hideLoading();
    if (manifest !== null && manifest.version !== null) {
      const diff = diffManifest(records, manifest.items);
      if (diff.dbOnly > 0) {
        wx.showModal({
          title: '对账发现差异',
          content: `${diff.dbOnly} 条库中记录本地已不存在（库内 ${manifest.recordCount} 条 / 本地 ${records.length} 条）。\n是否一并删除？确认后会随本次入库提交，并生成可回滚的新版本。`,
          confirmText: '一并删除',
          cancelText: '跳过',
          success: (res) => {
            if (res.confirm) {
              const items: FpDeletion[] = diff.dbOnlyItems.map((item) => ({
                fpId: item.fpId,
                collectedAt: item.collectedAt,
                contentSha256: item.contentSha256,
                floor: item.floorId,
              }));
              addTombstones(items);
              // 墓碑直接来自服务端清单，随本次入库一并提交，无需再导出一次
              markDeletionsExported(items);
              this.refreshStats();
            }
            this.showIngestModeDialog();
          },
        });
        return;
      }
    } else if (manifest === null) {
      wx.showToast({ title: '对账清单不可用，跳过比对', icon: 'none' });
    }
    this.showIngestModeDialog();
  },

  /** 入库模式必须显式选择；普通入库默认合并且永不推断删除。 */
  showIngestModeDialog(): void {
    const floorLabel = this.data.currentFloorText;
    wx.showActionSheet({
      alertText: '选择入库方式',
      itemList: ['合并入库（只增改，不删除）', `整层替换 ${floorLabel}（删除该层库中多余记录）`],
      success: (res) => {
        if (res.tapIndex === 1) {
          wx.showModal({
            title: `整层替换 ${floorLabel}`,
            content: `将以本次导出的 ${floorLabel} 记录替换库中该层数据：该层中未出现在本次导出的记录会被删除，并生成可回滚的新版本。其他楼层只做合并。`,
            confirmText: '确认替换',
            confirmColor: '#b91c1c',
            success: (r) => {
              if (r.confirm) {
                void this.doIngest('replaceFloor', [this.data.currentFloor]);
              }
            },
          });
          return;
        }
        void this.doIngest('merge');
      },
      fail: () => undefined,
    });
  },

  /** 上传当前（已全部导出）的数据与墓碑，并刷新服务端指纹库状态。 */
  async doIngest(mode: 'merge' | 'replaceFloor', replaceFloors?: string[]): Promise<void> {
    const bundle = this.buildExportBundle({ mode, replaceFloors });
    if (bundle === null) {
      return;
    }
    const sentDeletions = bundle.data.deletions;
    this.setData({ ingestBusy: true });
    wx.showLoading({ title: '入库中…', mask: true });
    try {
      const operator = appContext.getCalibrationOperator();
      const resp = await appContext.http.client.request<FingerprintIngestResponse>({
        method: 'POST',
        path: '/api/v1/fingerprint-db/ingest',
        // 入库不做鉴权（现场私网直连后端），但仍受服务端私网限制
        auth: false,
        timeoutMs: 30000,
        body: {
          fileName: `ble_fingerprint_${Date.now()}.json`,
          content: JSON.stringify(bundle.data),
          ...(operator === '' ? {} : { operator }),
        },
      });
      const summary = resp.data;
      if (summary === null) {
        throw new Error('入库响应为空');
      }
      // 已应用的墓碑从本地移除，避免重复上传（导出文件已留档）
      removeTombstones(sentDeletions.map((item) => `${item.fpId}|${item.collectedAt}`));
      this.refreshStats();
      this.setData({
        libraryStatusText: `指纹库 ${summary.version} · ${summary.recordCount} 条/${summary.fpCount} 点 · 本版删 ${summary.deletedCount}`,
      });
      wx.hideLoading();
      const prunedNote =
        summary.prunedVersions !== undefined && summary.prunedVersions.length > 0
          ? `\n保留策略：最老 ${summary.prunedVersions.length} 个版本明细已裁剪（摘要保留）。`
          : '';
      wx.showModal({
        title: summary.duplicated ? '内容已入库' : '入库成功',
        content: `版本 ${summary.version}${
          summary.mode === 'replaceFloor'
            ? `（整层替换 ${summary.replaceFloors.join('、')}F）`
            : ''
        }\n记录 ${summary.recordCount} 条 · 点位 ${summary.fpCount} 个\n本次新增 ${summary.insertedCount} · 更新 ${summary.updatedCount} · 删除 ${summary.deletedCount} · 保留 ${summary.retainedCount}${
          summary.duplicated
            ? summary.noChanges
              ? '\n墓碑已全部应用过，无新增变更。'
              : '\n该内容此前已入库，未重复写入。'
            : ''
        }${prunedNote}`,
        showCancel: false,
      });
    } catch (err) {
      wx.hideLoading();
      const message =
        err instanceof ApiClientError ? describeError(err.code) : '网络异常，请检查后端连接';
      wx.showModal({ title: '入库失败', content: message, showCancel: false });
      log.warn('ingest failed', err);
    } finally {
      this.setData({ ingestBusy: false });
    }
  },

  /** 读取服务端当前指纹库版本（页面顶栏状态；失败不影响采集）。 */
  async refreshLibraryStatus(): Promise<void> {
    try {
      const resp = await appContext.http.client.request<FingerprintActiveResponse>({
        method: 'GET',
        path: '/api/v1/fingerprint-db/active',
        auth: false,
        timeoutMs: 5000,
      });
      const active = resp.data?.active ?? null;
      this.setData({
        libraryStatusText:
          active === null
            ? '指纹库未入库'
            : `指纹库 ${active.version} · ${active.recordCount} 条/${active.fpCount} 点 · 本版删 ${active.deletedCount}`,
      });
    } catch (err) {
      const message =
        err instanceof ApiClientError ? describeError(err.code) : '网络不可用';
      this.setData({ libraryStatusText: `指纹库状态不可用：${message}` });
    }
  },

  /** 版本历史/回滚：只在保留窗口内选择，已裁剪明细的版本不再列出。 */
  onVersionHistory(): void {
    void this.showVersionHistory();
  },

  async showVersionHistory(): Promise<void> {
    const path = '/api/v1/fingerprint-db/versions?limit=10';
    const requestOnce = (timeoutMs: number) =>
      appContext.http.client.request<FingerprintVersionsResponse>({
        method: 'GET',
        path,
        auth: false,
        timeoutMs,
      });
    try {
      let resp: ApiResponse<FingerprintVersionsResponse>;
      try {
        resp = await requestOnce(8000);
      } catch (err) {
        // 现场出现过「wx 超时但后端访问日志无该请求」的瞬时链路失败：只对无副作用的 GET
        // 重试一次（更短超时），避免第一次新连接丢包就让现场误判成服务端故障。
        if (
          err instanceof ApiClientError &&
          (err.code === 'REQUEST_TIMEOUT' || err.code === 'NETWORK_ERROR')
        ) {
          log.warn('版本列表请求失败，重试一次', err.code, path);
          resp = await requestOnce(5000);
        } else {
          throw err;
        }
      }
      const items = (resp.data?.items ?? []).filter((item) => !item.detailsPruned);
      if (items.length === 0) {
        wx.showToast({ title: '暂无可用版本', icon: 'none' });
        return;
      }
      wx.showActionSheet({
        alertText: '最近 10 个版本（点击可回滚）',
        itemList: items.map(
          (item) =>
            `${item.version.slice(4, 18)} · ${item.recordCount} 条${item.active ? ' · 当前' : ''}`,
        ),
        success: (res) => {
          const target = items[res.tapIndex];
          if (target.active) {
            wx.showToast({ title: '已是当前版本', icon: 'none' });
            return;
          }
          wx.showModal({
            title: '回滚指纹库',
            content: `回滚到 ${target.version}（${target.recordCount} 条 / ${target.fpCount} 点）？\n当前 active 指针会切回该版本，历史版本不被改写。`,
            confirmText: '回滚',
            success: (r) => {
              if (r.confirm) {
                void this.doRollback(target.version);
              }
            },
          });
        },
        fail: () => undefined,
      });
    } catch (err) {
      log.warn('版本列表拉取失败', err);
      // 区分「超时/网络」与「404/500」：HTTP 状态码直接透出，避免把 404 说成超时。
      const message =
        err instanceof ApiClientError
          ? `${describeError(err.code)}${err.statusCode !== null ? `（HTTP ${err.statusCode}）` : ''}`
          : '网络异常，请稍后重试';
      wx.showModal({ title: '版本列表不可用', content: message, showCancel: false });
    }
  },

  async doRollback(version: string): Promise<void> {
    wx.showLoading({ title: '回滚中…', mask: true });
    try {
      const resp = await appContext.http.client.request<FingerprintIngestResponse>({
        method: 'POST',
        path: '/api/v1/fingerprint-db/rollback',
        auth: false,
        timeoutMs: 15000,
        body: { version },
      });
      const summary = resp.data;
      wx.hideLoading();
      this.setData({
        libraryStatusText:
          summary === null
            ? this.data.libraryStatusText
            : `指纹库 ${summary.version} · ${summary.recordCount} 条/${summary.fpCount} 点`,
      });
      wx.showModal({
        title: '已回滚',
        content:
          summary === null
            ? `已切换到 ${version}`
            : `当前版本 ${summary.version}\n记录 ${summary.recordCount} 条 · 点位 ${summary.fpCount} 个 · 本版删 ${summary.deletedCount}`,
        showCancel: false,
      });
    } catch (err) {
      wx.hideLoading();
      const message =
        err instanceof ApiClientError ? describeError(err.code) : '网络异常，请稍后重试';
      wx.showModal({ title: '回滚失败', content: message, showCancel: false });
    }
  },

  // ------------------------------------------------------------------
  // 叠加图层：网格点 + 定位点
  // ------------------------------------------------------------------

  drawOverlay(
    ctx: WechatMiniprogram.CanvasRenderingContext.CanvasRenderingContext2D,
    vp: NonNullable<CollectMapView['vp']>,
  ): void {
    // LOD（路线点豁免）+ 筛选档位；路线点最后绘制，保证在最上层
    const pts = this.visiblePoints(this.gridByFloor!.get(vp.floorId) ?? [], vp);
    const normalPts = pts.filter((p) => !isRoutePoint(p));
    const routePts = pts.filter((p) => isRoutePoint(p));
    for (const p of [...normalPts, ...routePts]) {
      const { sx, sy } = vp.toScreen(p.x, p.y);
      const n = this.recordCounts!.get(p.fpId) ?? 0;
      // 仅因补装虚拟信标才可采的点：紫色虚线空心，与 v11 真实可采（蓝实心）区分
      const simOnly = isSimOnly(p, this.data.simPreview);
      const style = pointStyle(n > 0, isRoutePoint(p), p.collectable, simOnly);
      const half = style.sizePx / 2;
      if (style.fill !== null) {
        ctx.fillStyle = style.fill;
        ctx.fillRect(sx - half, sy - half, style.sizePx, style.sizePx);
      }
      if (style.stroke !== null) {
        // 路线点（含已采集）保留橙色/白色描边，避免丢失路线标记
        ctx.strokeStyle = style.stroke;
        ctx.lineWidth = style.strokeWidth;
        if (style.dashed === true) {
          ctx.setLineDash([3, 3]);
        }
        ctx.strokeRect(sx - half, sy - half, style.sizePx, style.sizePx);
        if (style.dashed === true) {
          ctx.setLineDash([]);
        }
      }
      if (n > 0) {
        ctx.font = 'bold 10px -apple-system, "PingFang SC", sans-serif';
        ctx.textAlign = 'center';
        ctx.textBaseline = 'middle';
        ctx.lineWidth = 2;
        ctx.strokeStyle = COLORS.labelStroke;
        ctx.strokeText(String(n), sx, sy - 10);
        ctx.fillStyle = COLORS.collectedText;
        ctx.fillText(String(n), sx, sy - 10);
        // C8：已采但当前不可采（参数/网格更新后）——灰点标记，记录保留
        const collectableNow = p.collectable || (this.data.simPreview && p.collectableWithSim === true);
        if (!collectableNow) {
          ctx.beginPath();
          ctx.arc(sx + half + 3, sy - half - 3, 3, 0, Math.PI * 2);
          ctx.fillStyle = '#64748b';
          ctx.fill();
        }
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
