/**
 * 楼层地图启动首屏：墙体平面图 + 1F/2F 切换 + 单指拖动/双指缩放 + mock 位置点。
 *
 * 视口数学在 core/map/floor-map-viewport.ts（纯逻辑，单测覆盖），本文件只做
 * 页面编排、canvas 绘制与手势采集；地图数据经 MapService 单例（本地文件缓存
 * 秒开，未缓存时前台下载，失败走 loadError + 重试）。
 */
import { appContext } from '../../services/app-context';
import { ensureServiceEndpoint } from '../../services/service-endpoint';
import { createLogger } from '../../utils/logger';
import { ApiClientError } from '../../core/http/errors';
import { describeError } from '../../core/http/error-messages';
import {
  flattenWalls,
  flatBounds,
  floorsSortedByLevel,
  floorPlaceLabels,
  flattenRoomFills,
  MAP_BACKGROUND_COLOR,
  type PlaceLabel,
  type RoomFillGroup,
} from '../../core/map/floor-geometry';
import { FloorMapViewport } from '../../core/map/floor-map-viewport';
import { PushToTalk, type PttState } from '../../core/asr/push-to-talk';
import { createBeaconSource } from '../../core/ble/beacon-source-factory';
import type { BeaconSourceLike } from '../../core/ble/beacon-source';
import { MockBeaconSource } from '../../core/ble/mock-beacon-source';
import { MOCK_POSITION, MOCK_WALK_SPEED_MPS } from '../../core/map/mock-position';
import { SpeechQueue } from '../../core/speech/speech-queue';
import type { MapServiceEvent } from '../../core/map/map-service';
import { IntentService } from '../../services/intent.service';
import { LocationQueryService, UNRESOLVED_TEXT } from '../../services/location-query.service';
import {
  NavSessionController,
  type NavPipelineControl,
  type NavState,
} from '../../core/nav/nav-session-controller';
import { LocationPipeline } from '../../core/position/location-pipeline';
import { SequenceCounter } from '../../core/position/sequence-counter';
import { buildRouteOverview } from '../../core/nav/route-overview';
import { vibrateLong, vibrateShort } from '../../utils/haptic';
import {
  HoldToTalkGesture,
  HOLD_TO_TALK_DEFAULTS,
} from '../../core/a11y/hold-to-talk-gesture';
import { isPointExcluded, toCanvasRect, type Rect } from '../../core/a11y/tap-exclusion';
import {
  HeadingSmoother,
  arrowAngle,
  deviationOfArrow,
  deviationStep,
  routeBearing,
  shortestDelta,
} from '../../core/a11y/nav-arrow-angle';
import { HeadingSource, type HeadingApi, type HeadingMode } from '../../core/a11y/heading-source';
import type { A11yMode } from '../../services/app-context';
import type {
  IntentParseResponse,
  PlaceCandidate,
  PositionEstimateResponse,
  RouteInfo,
} from '../../typings/api';

const log = createLogger('map');

/** PTT 状态文案（自原语音首页平移，REQ-PAGE 单页化）。 */
const PTT_TEXT: Record<PttState, string> = {
  idle: '按住说话',
  recording: '松开结束',
  recognizing: '识别中…',
  error: '识别失败，请重试',
};

/** 导航态文案（自原导航页平移）。 */
const NAV_TEXT: Record<NavState, string> = {
  idle: '正在定位…',
  active: '导航中',
  recovering: '附近定位信标不足或信号不稳定，导航已暂停；请原地稍候或移至信号覆盖较好处',
  arrived: '已到达目的地',
  ended: '导航结束',
};

const DOUBLE_TAP_WINDOW_MS = 500;

/**
 * 进入导航时的视野收窄倍数（相对全图适配 scale；工程默认值，按真机观感调）。
 * 全路线视野 → 局部跟随视野（类高德步行导航），后续跟随只平移不再改缩放，
 * 用户手动缩放始终优先。
 */
const NAV_VIEW_FACTOR = 2.5;

/** 工具条按钮排版参数（与 styles/map-tools.wxss 的 .zoom-face 保持一致：字号 26rpx、边框 1rpx）。 */
const TOOL_BTN_FONT_RPX = 26;
const TOOL_BTN_PAD_X_RPX = 10;
const TOOL_BTN_BORDER_RPX = 1;
/** 估算余量：避免不同机型字体度量差异导致文字贴边/截断。 */
const TOOL_BTN_SAFETY_RPX = 6;

/** 方向箭头：偏离警示需持续该时长才亮红并播报（spec §5.6）。 */
const ARROW_ALERT_HOLD_MS = 2000;
/** 偏离播报节流（≥5 s）。 */
const ARROW_SPEAK_THROTTLE_MS = 5000;
/** 角度变化小于该值不重绘（spec §5.5）。 */
const ARROW_REDRAW_DEG = 2;
/** 两次重绘的最小间隔（约 20 fps）。 */
const ARROW_REDRAW_MIN_MS = 50;
const ALIGNED_TEXT = '方向已对准，请直行';
const HEADING_UNAVAILABLE_TEXT = '当前设备无法提供方向，请改用语音指令导航';

/**
 * 采样排除区时使用的选择器（按钮热区）。`selectAll` 一次取一类，
 * 避免复合选择器在部分基础库上解析不一致。
 */
const EXCLUSION_SELECTORS = ['.zoom-hit', '.ptt-zone', '.nav-hud', '.center-card'];

/**
 * 工具条统一按钮宽度（rpx）：按「设置 / 全图 / 各楼层文案」中最宽者估算。
 * 全角（CJK）按 1em、半角按 0.6em 计，再叠加左右留白、边框与安全余量。
 * 三枚按钮共用同一宽度 → 点击展开楼层候选时其他按钮位置不变。
 */
function toolBtnWidthRpx(labels: string[]): number {
  let maxText = 0;
  for (const label of labels) {
    let w = 0;
    for (const ch of label) {
      w += ch.charCodeAt(0) < 0x2e80 ? TOOL_BTN_FONT_RPX * 0.6 : TOOL_BTN_FONT_RPX;
    }
    if (w > maxText) {
      maxText = w;
    }
  }
  return Math.ceil(
    maxText + TOOL_BTN_PAD_X_RPX * 2 + TOOL_BTN_BORDER_RPX * 2 + TOOL_BTN_SAFETY_RPX,
  );
}

type Canvas2d = WechatMiniprogram.CanvasRenderingContext.CanvasRenderingContext2D;

interface FloorItem {
  floorId: string;
  displayName: string;
  level: number;
}

interface GestureState {
  mode: 'pan' | 'zoom' | 'pending' | null;
  lastX: number;
  lastY: number;
  baseDist: number;
  lastMidX: number;
  lastMidY: number;
}

interface CanvasQueryFields {
  node?: unknown;
  width?: number;
  height?: number;
  left?: number;
  top?: number;
}

Page({
  data: {
    loading: true,
    loadError: '',
    mapReady: false,
    currentFloor: '1',
    /** 楼层选择按钮常显文案（如 "1F"，随楼层切换/定位换层更新）。 */
    currentFloorText: '1F',
    /** 楼层选择菜单是否横向展开。 */
    floorMenuOpen: false,
    /** 工具条三枚按钮的统一宽度（rpx，按文案最宽者估算，见 toolBtnWidthRpx）。 */
    toolBtnWidth: toolBtnWidthRpx(['设置', '全图', '1F']),
    floors: [] as FloorItem[],
    // 意图/语音（REQ-PAGE：下端居中按住说话按钮 + 居中浮层）
    pttState: 'idle' as PttState,
    pttStateText: PTT_TEXT.idle,
    partial: '',
    recognized: '',
    reply: '',
    fallbackText: '',
    candidates: [] as PlaceCandidate[],
    destination: null as PlaceCandidate | null,
    isMockSource: appContext.dataSource === 'mock',
    // 现场采集工具（设置页开关控制入口可见性，即时生效；导航中隐藏）
    collectEntry: false,
    collectMenuOpen: false,
    /** 采集候选（静态标定 / 网格指纹，4 字文案）统一宽度（rpx），与 toolBtnWidth 同一套估算 */
    collectOptWidth: toolBtnWidthRpx(['静态标定']),
    // 导航态（REQ-PAGE：导航融合进地图页，原独立导航页退役）
    navActive: false,
    state: 'idle' as NavState,
    stateText: NAV_TEXT.idle,
    instruction: '',
    destinationName: '',
    ended: false,
    // 无障碍（specs/nav-accessibility-ux）：模式 + 大箭头
    /** 无障碍模式：视障模式（默认）/ 普通模式；普通模式回归既有地图手势。 */
    a11yMode: 'a11y' as A11yMode,
    /** 全屏按住说话开关（仅视障模式生效，默认开）。 */
    fullScreenPtt: true,
    /** 导航方向大箭头开关（仅视障模式生效，默认开）。 */
    navArrow: true,
    /** 箭头覆盖层是否显示。 */
    arrowVisible: false,
    /** 箭头旋转角（相对手机朝向；0=朝上=对准）。 */
    arrowDeg: 0,
    /** 偏离警示态（红 + 白描边）。 */
    arrowAlert: false,
    /** 箭头配色主题（跟随系统深浅色）。 */
    arrowDark: false,
  },

  canvas: null as WechatMiniprogram.Canvas | null,
  speech: null as SpeechQueue | null,
  /** 地图下载/就绪事件订阅（启动播报序列用）。 */
  mapUnsubscribe: null as (() => void) | null,
  ptt: null as PushToTalk | null,
  intents: null as IntentService | null,
  locationQuery: null as LocationQueryService | null,
  controller: null as NavSessionController | null,
  pipeline: null as LocationPipeline | null,
  /** 当前导航的观测源（mock 时为 MockBeaconSource，路线就绪后切换行走轨迹）。 */
  navSource: null as BeaconSourceLike | null,
  navDestination: null as PlaceCandidate | null,
  routeForDisplay: null as RouteInfo | null,
  /** 导航态最近一次可信位置（unreliable/lost 不更新 → 恢复态不推进显示）。 */
  navPosition: null as { floorId: string; x: number; y: number } | null,
  overviewAnnounced: false,
  lastTapAt: 0,
  sessionEnded: false,
  parseInFlight: false,
  locationInFlight: false,
  /** 定位不可用日志去噪：只在连续不可用的第一拍打日志（恢复后重置）。 */
  locationWarned: false,
  ctx: null as Canvas2d | null,
  viewport: null as FloorMapViewport | null,
  wallSegs: {} as Record<string, Float32Array>,
  /** 房间填色组（按类型聚合，draw 时每色一次 fill）。 */
  roomFills: {} as Record<string, RoomFillGroup[]>,
  placeLabels: {} as Record<string, PlaceLabel[]>,
  floorList: [] as FloorItem[],
  canvasRect: { left: 0, top: 0 },
  canvasReady: false,
  canvasSize: { w: 0, h: 0 },
  dpr: 1,
  dirty: false,
  rafPending: false,
  gesture: {
    mode: null,
    lastX: 0,
    lastY: 0,
    baseDist: 0,
    lastMidX: 0,
    lastMidY: 0,
  } as GestureState,
  /** 全屏按住说话手势状态机（仅视障模式使用；普通模式不走该机）。 */
  holdGesture: new HoldToTalkGesture(HOLD_TO_TALK_DEFAULTS),
  holdTimer: null as ReturnType<typeof setTimeout> | null,
  holdEndTimer: null as ReturnType<typeof setTimeout> | null,
  /** 排除矩形（canvas 坐标系；采样失败为空集合）。 */
  tapExclusions: [] as Rect[],
  /** 朝向传感器（仅导航态启动，结束/后台/卸载停止）。 */
  headingSource: null as HeadingSource | null,
  headingSmoother: new HeadingSmoother(),
  headingDeg: null as number | null,
  headingMode: 'compass' as HeadingMode,
  /** wx 传感器监听只注册一次，回调转交给 HeadingSource 当前实例。 */
  headingApi: null as HeadingApi | null,
  compassHandler: null as ((res: { direction: number; accuracy?: number | string }) => void) | null,
  motionHandler: null as ((res: { alpha: number }) => void) | null,
  headingUnavailableAnnounced: false,
  alignedAnnounced: false,
  arrowLastDeg: 0,
  arrowUpdatedAt: 0,
  deviationSince: null as number | null,
  deviationSpokenAt: 0,

  onLoad(): void {
    this.initSpeech();
    this.initPtt();
    this.applyA11ySettings(); // 模式与全屏语音开关（设置页改动通过 onShow 回读）
    this.intents = new IntentService(appContext.http.client, appContext.map);
    this.locationQuery = new LocationQueryService({
      client: appContext.http.client,
      map: appContext.map,
      makeSource: () => createBeaconSource(appContext.dataSource, appContext.map),
      floorName: (floorId) => (floorId ? appContext.map.floors[floorId]?.displayName : undefined),
    });
    // 启动播报序列由地图事件驱动（下载开始/就绪/过旧），必须在 bootstrap 之前订阅
    this.mapUnsubscribe = appContext.map.subscribe((event) => this.onMapEvent(event));
    void this.bootstrap();
  },

  onHide(): void {
    this.ptt?.handleAppHide(); // 切后台：取消识别，不提交
    this.clearHoldTimers();
    this.holdGesture.reset();
    this.stopHeading(); // 传感器不进后台（省电 + 避免系统限流）
    this.controller?.handleBackground(); // 导航中：停采集，恢复交给 onShow
  },

  onShow(): void {
    // 从设置页返回后刷新采集入口可见性与无障碍模式（开关即时生效）
    this.setData({ collectEntry: appContext.getFieldCollectionToolSetting() });
    this.applyA11ySettings();
    const controller = this.controller;
    if (controller !== null && controller.sessionId !== null && !this.sessionEnded) {
      void controller.handleForeground();
    }
  },

  onUnload(): void {
    this.mapUnsubscribe?.();
    this.mapUnsubscribe = null;
    this.clearHoldTimers();
    this.stopHeading();
    const controller = this.controller;
    if (controller !== null && !this.sessionEnded && controller.sessionId !== null) {
      controller.endSession('user_cancelled').catch(() => undefined);
    }
    this.pipeline?.stop();
  },

  /**
   * 启动播报序列（REQ-MAP）：
   * - 无缓存首启：正在加载地图 → 加载成功 → 程序已启动（失败则 加载失败，不播已启动）；
   * - 缓存不过旧：程序已启动；
   * - 缓存过旧：程序已启动 → 地图过旧（新版后台静默下载，不播下载过程）。
   * 「地图已读取到内存」只是状态节点，不单独播报。
   */
  onMapEvent(event: MapServiceEvent): void {
    if (event.type === 'download-start') {
      if (event.foreground) {
        void appContext.voicePrompts.play('map-loading');
      }
      return;
    }
    if (event.type !== 'ready') {
      return;
    }
    if (event.source === 'download') {
      void appContext.voicePrompts.play('map-loaded');
      vibrateShort(); // 加载成功短震动
    }
    void appContext.voicePrompts.play('app-started');
    if (event.stale) {
      void appContext.voicePrompts.play('map-stale');
    }
  },

  onReady(): void {
    this.initCanvas();
  },

  // ------------------------------------------------------------------
  // 数据加载（照 index 页 bootstrap 范式）
  // ------------------------------------------------------------------

  async bootstrap(): Promise<void> {
    this.setData({ loading: true, loadError: '' });
    try {
      // 先解析业务地址：缓存优先，失败则 UDP 广播发现。
      const endpoint = await ensureServiceEndpoint();
      if (endpoint === null) {
        log.warn('未发现局域网服务，停止加载地图');
        this.setData({
          loading: false,
          mapReady: false,
          loadError: '未发现局域网服务：请确认后端已启动，且手机与电脑在同一网络后重试',
        });
        void appContext.voicePrompts.play('map-load-failed');
        vibrateLong();
        return;
      }
      log.info('业务地址已就绪', endpoint);
      await appContext.map.ensureLoaded();
      const floorsData = appContext.map.floors;
      const floors = floorsSortedByLevel(floorsData);
      const segs: Record<string, Float32Array> = {};
      const fills: Record<string, RoomFillGroup[]> = {};
      const labels: Record<string, PlaceLabel[]> = {};
      for (const item of floors) {
        segs[item.floorId] = flattenWalls(floorsData, item.floorId);
        // 房间面按类型着色（教室/办公室/卫生间/实验室；走廊大厅留白）
        fills[item.floorId] = flattenRoomFills(floorsData, item.floorId);
        // 空间语义标注（教室/办公室等名称）：一次预计算，draw 按层引用
        labels[item.floorId] = floorPlaceLabels(floorsData, appContext.map.places, item.floorId);
      }
      this.wallSegs = segs;
      this.roomFills = fills;
      this.placeLabels = labels;
      this.floorList = floors;
      const cur = floors.find((f) => f.floorId === this.data.currentFloor);
      this.setData({
        floors,
        loading: false,
        loadError: '',
        mapReady: true,
        currentFloorText: cur !== undefined ? `${cur.level}F` : this.data.currentFloorText,
        // 楼层数量与文案确定后重算三枚按钮的统一宽度（取三者最宽者）
        toolBtnWidth: toolBtnWidthRpx(['设置', '全图', ...floors.map((f) => `${f.level}F`)]),
      });
      log.info('地图首屏就绪', appContext.map.requireVersion());
      this.tryFirstDraw();
      this.refreshTapExclusions();
      // 启动集中申请权限：地图就绪后再弹，避免与加载叠加
      this.requestStartupPermissions();
    } catch (err) {
      const message =
        err instanceof ApiClientError ? describeError(err.code) : '地图加载失败，请检查网络';
      log.warn('地图加载失败', err);
      this.setData({ loading: false, loadError: message, mapReady: false });
      // 无可用地图 → 本地提示音 + 长震动（不播「程序已启动」）
      void appContext.voicePrompts.play('map-load-failed');
      vibrateLong();
    }
  },

  /**
   * 启动授权：麦克风（按住说话/同声传译插件）、蓝牙（iBeacon 实扫）。
   * wx.authorize 仅首次弹窗；用户此前拒绝过则直接 fail（记录日志，
   * 需在设置页手动开启），一律不阻塞启动。
   */
  requestStartupPermissions(): void {
    for (const scope of ['scope.record', 'scope.bluetooth']) {
      wx.authorize({
        scope,
        success: () => log.info('授权通过', scope),
        fail: (err) => log.info('授权未通过（拒绝过或不支持）', scope, err.errMsg ?? ''),
      });
    }
  },

  onRetry(): void {
    void this.bootstrap();
  },

  // ------------------------------------------------------------------
  // canvas 初始化与首绘
  // ------------------------------------------------------------------

  initCanvas(): void {
    const query = this.createSelectorQuery();
    query
      .select('#mapCanvas')
      .fields({ node: true, size: true, rect: true })
      .exec((res: Array<CanvasQueryFields | undefined>) => {
        const fields = res[0];
        // fields 回调为弱类型对象（IAnyObject），node 需一处受控断言
        const node = fields?.node as WechatMiniprogram.Canvas | undefined;
        if (node === undefined || typeof fields?.width !== 'number' || typeof fields?.height !== 'number') {
          log.warn('canvas 节点获取失败');
          return;
        }
        const info = wx.getWindowInfo();
        this.dpr = info.pixelRatio > 0 ? info.pixelRatio : 2;
        this.canvasSize = { w: fields.width, h: fields.height };
        node.width = Math.round(fields.width * this.dpr);
        node.height = Math.round(fields.height * this.dpr);
        this.canvasRect = { left: fields.left ?? 0, top: fields.top ?? 0 };
        this.canvas = node;
        this.ctx = node.getContext('2d');
        this.canvasReady = true;
        log.info('canvas 就绪', `${this.canvasSize.w}x${this.canvasSize.h}`, `dpr=${this.dpr}`);
        this.refreshTapExclusions(); // 排除区依赖画布原点，画布就绪后再采样
        this.tryFirstDraw();
      });
  },

  /** 数据与画布两边就绪后建视口并首绘。 */
  tryFirstDraw(): void {
    if (!this.canvasReady || this.data.loading || this.viewport !== null) {
      return;
    }
    const firstFloor = this.floorList[0];
    const segs = firstFloor !== undefined ? this.wallSegs[firstFloor.floorId] : undefined;
    if (firstFloor === undefined || segs === undefined) {
      return;
    }
    this.viewport = new FloorMapViewport(
      flatBounds(segs),
      this.canvasSize.w,
      this.canvasSize.h,
      firstFloor.floorId,
    );
    this.draw();
  },

  // ------------------------------------------------------------------
  // 绘制
  // ------------------------------------------------------------------

  draw(): void {
    const ctx = this.ctx;
    const vp = this.viewport;
    if (ctx === null || vp === null) {
      return;
    }
    const { w, h } = this.canvasSize;
    ctx.setTransform(this.dpr, 0, 0, this.dpr, 0, 0);

    // 背景：建筑外部底色（房间面与公共空间填色后，仅剩余外部区域可见）
    ctx.fillStyle = MAP_BACKGROUND_COLOR;
    ctx.fillRect(0, 0, w, h);

    // 房间面按类型着色（教室/办公室/卫生间/实验室各有色系；走廊/大厅/门厅白色）
    const fills = this.roomFills[vp.floorId];
    if (fills !== undefined) {
      const { scale, offsetX, offsetY } = vp;
      for (const group of fills) {
        ctx.beginPath();
        for (const ring of group.rings) {
          for (let i = 0; i + 1 < ring.length; i += 2) {
            const sx = ring[i] * scale + offsetX;
            const sy = -ring[i + 1] * scale + offsetY;
            if (i === 0) {
              ctx.moveTo(sx, sy);
            } else {
              ctx.lineTo(sx, sy);
            }
          }
          ctx.closePath();
        }
        ctx.fillStyle = group.color;
        ctx.fill();
      }
    }

    // 墙体：扁平段单 path 一次 stroke（F1 2436 段）；浅灰细线，压住房间色块边界
    const segs = this.wallSegs[vp.floorId];
    if (segs !== undefined) {
      const { scale, offsetX, offsetY } = vp;
      ctx.beginPath();
      for (let i = 0; i + 3 < segs.length; i += 4) {
        ctx.moveTo(segs[i] * scale + offsetX, -segs[i + 1] * scale + offsetY);
        ctx.lineTo(segs[i + 2] * scale + offsetX, -segs[i + 3] * scale + offsetY);
      }
      ctx.strokeStyle = '#aeb7c4';
      ctx.lineWidth = Math.max(1, 0.12 * scale);
      ctx.stroke();
    }

    // 信标（当前层）
    ctx.fillStyle = '#059669';
    for (const beacon of appContext.map.beacons) {
      if (beacon.floorId !== vp.floorId) {
        continue;
      }
      const { sx, sy } = vp.toScreen(beacon.x, beacon.y);
      if (sx < -6 || sy < -6 || sx > w + 6 || sy > h + 6) {
        continue;
      }
      ctx.beginPath();
      ctx.arc(sx, sy, 3, 0, Math.PI * 2);
      ctx.fill();
    }

    // 空间语义标注（教室/办公室等）：固定屏幕字号、白描边压在墙线上、
    // 贪心防重叠（超出视口的跳过；缩得过小时自然只显示互不相交的子集）
    const labels = this.placeLabels[vp.floorId];
    if (labels !== undefined) {
      const FONT = 12;
      ctx.font = `${FONT}px -apple-system, "PingFang SC", sans-serif`;
      ctx.textAlign = 'center';
      ctx.textBaseline = 'middle';
      const boxes: Array<[number, number, number, number]> = [];
      for (const label of labels) {
        const { sx, sy } = vp.toScreen(label.x, label.y);
        const halfW = (label.text.length * FONT) / 2;
        if (sx < -halfW - 4 || sy < -10 || sx > w + halfW + 4 || sy > h + 10) {
          continue;
        }
        const box: [number, number, number, number] = [
          sx - halfW - 3,
          sy - FONT / 2 - 3,
          sx + halfW + 3,
          sy + FONT / 2 + 3,
        ];
        const overlaps = boxes.some(
          (r) => !(box[2] < r[0] || box[0] > r[2] || box[3] < r[1] || box[1] > r[3]),
        );
        if (overlaps) {
          continue;
        }
        boxes.push(box);
        ctx.lineWidth = 3;
        ctx.strokeStyle = 'rgba(255, 255, 255, 0.92)';
        ctx.strokeText(label.text, sx, sy);
        ctx.fillStyle = '#374151';
        ctx.fillText(label.text, sx, sy);
      }
    }

    // 导航态：当前楼层路线全貌 + 标记（REQ-NAV 展示层；route 由 onRoute 存入）
    const route = this.routeForDisplay;
    if (route !== null && this.data.navActive && route.polyline.length >= 2) {
      // polyline 按楼层切段（floorId null 沿用前一点楼层，桥接跨层内的空洞）
      const routeSegs: Array<{ floorId: string; pts: Array<{ x: number; y: number }> }> = [];
      let lastFloor: string | null = null;
      for (const pt of route.polyline) {
        const fid: string | null = pt.floorId ?? lastFloor;
        if (fid === null) {
          continue;
        }
        lastFloor = fid;
        const tail = routeSegs[routeSegs.length - 1];
        if (tail !== undefined && tail.floorId === fid) {
          tail.pts.push({ x: pt.x, y: pt.y });
        } else {
          routeSegs.push({ floorId: fid, pts: [{ x: pt.x, y: pt.y }] });
        }
      }

      // 当前层整条路线高亮（宽底衬 + 主线，非只显示前方一段）
      ctx.lineCap = 'round';
      ctx.lineJoin = 'round';
      for (const seg of routeSegs) {
        if (seg.floorId !== vp.floorId || seg.pts.length < 2) {
          continue;
        }
        ctx.beginPath();
        const head = vp.toScreen(seg.pts[0].x, seg.pts[0].y);
        ctx.moveTo(head.sx, head.sy);
        for (let i = 1; i < seg.pts.length; i += 1) {
          const pt = vp.toScreen(seg.pts[i].x, seg.pts[i].y);
          ctx.lineTo(pt.sx, pt.sy);
        }
        ctx.strokeStyle = 'rgba(37, 99, 235, 0.22)';
        ctx.lineWidth = Math.max(10, 1.0 * vp.scale);
        ctx.stroke();
        ctx.strokeStyle = '#2563eb';
        ctx.lineWidth = Math.max(5, 0.5 * vp.scale);
        ctx.stroke();
      }

      // 楼层切换点（相邻点楼层变化处、属本层）：橙色空心圈
      for (let i = 1; i < route.polyline.length; i += 1) {
        const prevFloor = route.polyline[i - 1].floorId;
        const cur = route.polyline[i];
        if (
          prevFloor === null || prevFloor === undefined ||
          cur.floorId === null || cur.floorId === undefined ||
          prevFloor === cur.floorId ||
          cur.floorId !== vp.floorId
        ) {
          continue;
        }
        const { sx, sy } = vp.toScreen(cur.x, cur.y);
        ctx.beginPath();
        ctx.arc(sx, sy, 9, 0, Math.PI * 2);
        ctx.strokeStyle = '#f59e0b';
        ctx.lineWidth = 4;
        ctx.stroke();
      }

      // 转向点：decision_point 指令按 nodeId 查本层拓扑节点坐标 → 白边橙菱形
      const nodes = appContext.map.floors[vp.floorId]?.topology.nodes ?? [];
      for (const seg of route.segments) {
        const nodeId = seg.trigger.nodeId;
        if (seg.trigger.type !== 'decision_point' || nodeId === null || nodeId === undefined) {
          continue;
        }
        const node = nodes.find((n) => n.id === nodeId);
        if (node === undefined) {
          continue;
        }
        const { sx, sy } = vp.toScreen(node.coordinates[0], node.coordinates[1]);
        ctx.save();
        ctx.translate(sx, sy);
        ctx.rotate(Math.PI / 4);
        ctx.fillStyle = '#f59e0b';
        ctx.fillRect(-6, -6, 12, 12);
        ctx.strokeStyle = '#ffffff';
        ctx.lineWidth = 2;
        ctx.strokeRect(-6, -6, 12, 12);
        ctx.restore();
      }

      // 终点标记（本层）：红底白圈 + 「终」
      const lastPt = route.polyline[route.polyline.length - 1];
      if (lastPt !== undefined && (lastPt.floorId ?? lastFloor) === vp.floorId) {
        const { sx, sy } = vp.toScreen(lastPt.x, lastPt.y);
        ctx.beginPath();
        ctx.arc(sx, sy, 11, 0, Math.PI * 2);
        ctx.fillStyle = '#dc2626';
        ctx.fill();
        ctx.lineWidth = 3;
        ctx.strokeStyle = '#ffffff';
        ctx.stroke();
        ctx.font = '12px -apple-system, "PingFang SC", sans-serif';
        ctx.textAlign = 'center';
        ctx.textBaseline = 'middle';
        ctx.fillStyle = '#ffffff';
        ctx.fillText('终', sx, sy);
      }
    }

    // 位置点：导航态画最近可信位置（无估计则不画，不伪造）；待命态保留 mock 锚点
    const displayPos = this.data.navActive ? this.navPosition : MOCK_POSITION;
    if (displayPos !== null && displayPos.floorId === vp.floorId) {
      const { sx, sy } = vp.toScreen(displayPos.x, displayPos.y);
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

  /** 手势期间每帧最多重绘一次；静止零重绘。 */
  requestDraw(): void {
    this.dirty = true;
    if (this.rafPending || this.canvas === null) {
      return;
    }
    this.rafPending = true;
    this.canvas.requestAnimationFrame(() => {
      this.rafPending = false;
      if (this.dirty) {
        this.dirty = false;
        this.draw();
      }
    });
  },

  // ------------------------------------------------------------------
  // 手势：单指平移、双指围绕中点缩放
  // ------------------------------------------------------------------

  toCanvasPoint(touch: { clientX: number; clientY: number }): { x: number; y: number } {
    return { x: touch.clientX - this.canvasRect.left, y: touch.clientY - this.canvasRect.top };
  },

  onTouchStart(e: WechatMiniprogram.TouchEvent): void {
    const ts = e.touches;
    // 普通模式：回归既有地图手势（按下即可拖拽，无长按判定、无死区）
    if (this.data.a11yMode !== 'a11y') {
      this.startLegacyTouch(ts);
      return;
    }
    if (ts.length >= 2) {
      this.clearHoldTimers();
      this.holdGesture.reset();
      this.startZoomGesture(ts);
      return;
    }
    if (ts.length !== 1) {
      return;
    }
    const p = this.toCanvasPoint(ts[0]);
    const action = this.holdGesture.onStart({
      x: p.x,
      y: p.y,
      pointCount: 1,
      excluded: this.isTapExcluded(p.x, p.y),
      now: Date.now(),
    });
    if (action === 'pending' && this.isFullScreenPttEnabled()) {
      // 先挂起：500 ms 静止转语音，移动超过 10px 转拖拽
      this.gesture = { mode: 'pending', lastX: p.x, lastY: p.y, baseDist: 0, lastMidX: 0, lastMidY: 0 };
      this.scheduleHold();
      return;
    }
    // 排除区 / 地图未就绪 / 开关关闭：保持原有「按下即拖拽」
    this.holdGesture.reset();
    this.gesture = { mode: 'pan', lastX: p.x, lastY: p.y, baseDist: 0, lastMidX: 0, lastMidY: 0 };
  },

  onTouchMove(e: WechatMiniprogram.TouchEvent): void {
    const vp = this.viewport;
    if (vp === null) {
      return;
    }
    const g = this.gesture;
    const ts = e.touches;
    if (this.data.a11yMode === 'a11y' && g.mode === 'pending') {
      if (ts.length >= 2) {
        // 1→2 指：缩放优先，取消语音候选
        this.clearHoldTimers();
        this.holdGesture.reset();
        this.startZoomGesture(ts);
      } else if (ts.length === 1) {
        const p = this.toCanvasPoint(ts[0]);
        const action = this.holdGesture.onMove(p.x, p.y, 1);
        if (action !== 'pan-start') {
          return; // 仍在死区内：不拖拽、不重绘
        }
        // 超出 10px 死区：从按下点开始拖拽，避免首帧跳变
        this.clearHoldTimers();
        const origin = this.holdGesture.origin;
        g.mode = 'pan';
        g.lastX = origin.x;
        g.lastY = origin.y;
      }
    }
    if (this.data.a11yMode === 'a11y' && this.holdGesture.currentPhase === 'voice') {
      return; // 语音态内移动不取消（沿用按住说话容错）
    }
    const cur = this.gesture; // 1→2 指可能已重建 gesture 对象
    if (cur.mode === 'pan' && ts.length === 1) {
      const p = this.toCanvasPoint(ts[0]);
      vp.panBy(p.x - cur.lastX, p.y - cur.lastY);
      cur.lastX = p.x;
      cur.lastY = p.y;
      this.requestDraw();
    } else if (cur.mode === 'zoom' && ts.length >= 2) {
      const a = this.toCanvasPoint(ts[0]);
      const b = this.toCanvasPoint(ts[1]);
      const dist = Math.hypot(a.x - b.x, a.y - b.y);
      const midX = (a.x + b.x) / 2;
      const midY = (a.y + b.y) / 2;
      if (cur.baseDist > 0) {
        vp.zoomAt(midX, midY, dist / cur.baseDist);
      }
      vp.panBy(midX - cur.lastMidX, midY - cur.lastMidY);
      cur.baseDist = dist;
      cur.lastMidX = midX;
      cur.lastMidY = midY;
      this.requestDraw();
    }
  },

  onTouchEnd(e: WechatMiniprogram.TouchEvent): void {
    const ts = e.touches;
    if (this.data.a11yMode === 'a11y') {
      const action = this.holdGesture.onEnd(ts.length);
      if (action === 'voice-end') {
        this.clearHoldTimers();
        this.gesture.mode = null;
        this.onPttEnd();
        return;
      }
      this.clearHoldTimers();
    }
    if (ts.length === 1) {
      // 双指抬一根 → 剩余单指继续平移
      const p = this.toCanvasPoint(ts[0]);
      this.gesture = { mode: 'pan', lastX: p.x, lastY: p.y, baseDist: 0, lastMidX: 0, lastMidY: 0 };
    } else if (ts.length === 0) {
      this.gesture.mode = null;
    }
  },

  onTouchCancel(e: WechatMiniprogram.TouchEvent): void {
    if (this.data.a11yMode === 'a11y') {
      const action = this.holdGesture.onCancel();
      this.clearHoldTimers();
      this.gesture.mode = null;
      if (action === 'voice-cancel') {
        this.onPttCancel();
      }
      return;
    }
    this.onTouchEnd(e);
  },

  /** 原有单指/双指手势基线（普通模式与视障模式的排除区分支共用）。 */
  startLegacyTouch(ts: WechatMiniprogram.TouchEvent['touches']): void {
    if (ts.length === 1) {
      const p = this.toCanvasPoint(ts[0]);
      this.gesture = { mode: 'pan', lastX: p.x, lastY: p.y, baseDist: 0, lastMidX: 0, lastMidY: 0 };
    } else if (ts.length >= 2) {
      this.startZoomGesture(ts);
    }
  },

  startZoomGesture(ts: WechatMiniprogram.TouchEvent['touches']): void {
    // 1→2 指切换：重置基线防止缩放跳变
    const a = this.toCanvasPoint(ts[0]);
    const b = this.toCanvasPoint(ts[1]);
    this.gesture = {
      mode: 'zoom',
      lastX: 0,
      lastY: 0,
      baseDist: Math.hypot(a.x - b.x, a.y - b.y),
      lastMidX: (a.x + b.x) / 2,
      lastMidY: (a.y + b.y) / 2,
    };
  },

  // ------------------------------------------------------------------
  // 全屏按住说话（仅视障模式）
  // ------------------------------------------------------------------

  /** 生效条件：视障模式 + 全屏开关打开 + 地图已就绪。 */
  isFullScreenPttEnabled(): boolean {
    return (
      this.data.a11yMode === 'a11y' &&
      this.data.fullScreenPtt &&
      !this.data.loading &&
      this.data.loadError === '' &&
      this.data.mapReady
    );
  },

  isTapExcluded(x: number, y: number): boolean {
    return isPointExcluded(x, y, this.tapExclusions);
  },

  /** 采样按钮热区作为排除区（canvas 坐标系，外扩 2px 在判定时生效）。 */
  refreshTapExclusions(): void {
    const query = this.createSelectorQuery();
    const rects: Rect[] = [];
    let pending = EXCLUSION_SELECTORS.length;
    for (const selector of EXCLUSION_SELECTORS) {
      query.selectAll(selector).boundingClientRect((res: unknown) => {
        const list = res as Array<{ left: number; top: number; width: number; height: number }> | null;
        if (Array.isArray(list)) {
          for (const item of list) {
            rects.push(toCanvasRect(item, this.canvasRect));
          }
        }
        pending -= 1;
        if (pending === 0) {
          this.tapExclusions = rects;
        }
      });
    }
    query.exec();
    if (this.tapExclusions.length === 0) {
      // 首次采样前为空集合：不排除任何区域（控件是 canvas 兄弟节点，不会误触语音）
      log.info('排除区待采样');
    }
  },

  /** 长按 500 ms 计时：到点转语音（进入语音态后改挂 15 s 上限计时）。 */
  scheduleHold(): void {
    this.clearHoldTimers();
    this.holdTimer = setTimeout(() => {
      this.holdTimer = null;
      if (this.holdGesture.currentPhase !== 'pending' || !this.isFullScreenPttEnabled()) {
        this.holdGesture.reset();
        return;
      }
      if (this.holdGesture.onTick(Date.now()) === 'voice-start') {
        this.gesture.mode = null; // 语音态不再拖拽
        this.onPttStart();
        this.scheduleHoldEnd();
      }
    }, HOLD_TO_TALK_DEFAULTS.holdMs);
  },

  /** 15 s 上限：到时自动结束并提交（防止误触后长时间占用麦克风）。 */
  scheduleHoldEnd(): void {
    this.clearHoldTimers();
    this.holdEndTimer = setTimeout(() => {
      this.holdEndTimer = null;
      if (this.holdGesture.currentPhase !== 'voice') {
        return;
      }
      if (this.holdGesture.onTick(Date.now()) === 'voice-timeout') {
        this.gesture.mode = null;
        this.onPttEnd();
      }
    }, HOLD_TO_TALK_DEFAULTS.maxHoldMs);
  },

  clearHoldTimers(): void {
    if (this.holdTimer !== null) {
      clearTimeout(this.holdTimer);
      this.holdTimer = null;
    }
    if (this.holdEndTimer !== null) {
      clearTimeout(this.holdEndTimer);
      this.holdEndTimer = null;
    }
  },

  // ------------------------------------------------------------------
  // 导航方向大箭头（仅视障模式；相对手机朝向）
  // ------------------------------------------------------------------

  /** 读取设置页的无障碍配置并即时生效（切换模式后正在进行的导航也会重绘）。 */
  applyA11ySettings(): void {
    const mode = appContext.getA11yMode();
    const fullScreenPtt = appContext.getFullScreenPttSetting();
    const navArrow = appContext.getNavArrowSetting();
    let arrowDark = false;
    try {
      const info = wx.getSystemInfoSync() as { theme?: string };
      arrowDark = info.theme === 'dark';
    } catch {
      arrowDark = false;
    }
    this.setData({ a11yMode: mode, fullScreenPtt, navArrow, arrowDark });
    this.syncArrowLifecycle();
  },

  /** 箭头显示条件：视障模式 + 箭头开关 + 导航中且未结束。 */
  isArrowEnabled(): boolean {
    return this.data.a11yMode === 'a11y' && this.data.navArrow && this.data.navActive && !this.data.ended;
  },

  /** 按当前状态启动/停止传感器与箭头（模式切换、导航开始/结束、前后台共用）。 */
  syncArrowLifecycle(): void {
    if (!this.isArrowEnabled()) {
      this.stopHeading();
      this.deviationSince = null;
      if (this.data.arrowVisible || this.data.arrowAlert) {
        this.setData({ arrowVisible: false, arrowAlert: false });
      }
      return;
    }
    this.startHeading();
    this.updateArrow();
  },

  startHeading(): void {
    if (this.headingSource !== null) {
      return;
    }
    if (typeof wx.startCompass !== 'function') {
      this.onHeadingUnavailable();
      return;
    }
    this.headingSource = new HeadingSource({
      api: this.getHeadingApi(),
      onHeading: (deg, mode) => {
        this.headingMode = mode;
        this.headingDeg = this.headingSmoother.update(deg, Date.now());
        this.updateArrow();
      },
      onModeChange: (mode) => {
        this.headingMode = mode;
        if (mode === 'unavailable') {
          this.onHeadingUnavailable();
        }
      },
      onUnavailable: () => this.onHeadingUnavailable(),
    });
    this.headingSource.start();
  },

  stopHeading(): void {
    this.headingSource?.stop();
    this.headingSource = null;
    this.headingDeg = null;
    this.headingSmoother.reset();
  },

  /** 构建 wx → HeadingApi 适配层；监听只注册一次，回调委托给当前 handler。 */
  getHeadingApi(): HeadingApi {
    if (this.headingApi !== null) {
      return this.headingApi;
    }
    let compassBound = false;
    let motionBound = false;
    const api: HeadingApi = {
      startCompass: (opts) => wx.startCompass(opts),
      stopCompass: () => wx.stopCompass(),
      onCompassChange: (cb) => {
        this.compassHandler = cb;
        if (!compassBound) {
          compassBound = true;
          wx.onCompassChange((res) => {
            this.compassHandler?.({ direction: res.direction, accuracy: res.accuracy });
          });
        }
      },
      startDeviceMotionListening: (opts) => wx.startDeviceMotionListening(opts),
      stopDeviceMotionListening: () => wx.stopDeviceMotionListening(),
      onDeviceMotionChange: (cb) => {
        this.motionHandler = cb;
        if (!motionBound) {
          motionBound = true;
          wx.onDeviceMotionChange((res) => {
            this.motionHandler?.({ alpha: res.alpha });
          });
        }
      },
    };
    this.headingApi = api;
    return api;
  },

  /** 罗盘与运动传感器均不可用：隐藏箭头并语音降级（不显示错误方向）。 */
  onHeadingUnavailable(): void {
    if (this.headingUnavailableAnnounced) {
      return;
    }
    this.headingUnavailableAnnounced = true;
    log.warn('方向传感器不可用，隐藏导航箭头');
    this.setData({ arrowVisible: false, arrowAlert: false });
    this.speech?.enqueue(HEADING_UNAVAILABLE_TEXT, 'normal');
  },

  /** 同层路线顶点（floorId 为空的桥接点沿用前一点楼层，与绘制逻辑一致）。 */
  routePointsOnFloor(floorId: string): Array<{ x: number; y: number }> {
    const route = this.routeForDisplay;
    if (route === null) {
      return [];
    }
    const points: Array<{ x: number; y: number }> = [];
    let cur: string | null = null;
    for (const p of route.polyline) {
      if (p.floorId !== null && p.floorId !== undefined) {
        cur = p.floorId;
      }
      if ((cur ?? floorId) === floorId) {
        points.push({ x: p.x, y: p.y });
      }
    }
    return points;
  },

  /** 重算箭头角并更新偏离开警/播报（节流：<2° 不重绘、播报 ≥5 s）。 */
  updateArrow(): void {
    if (!this.isArrowEnabled()) {
      return;
    }
    if (this.data.state === 'recovering') {
      return; // 位置不可信：不推进箭头、不播报方向（REQ-NAV 安全策略）
    }
    const heading = this.headingDeg;
    const pos = this.navPosition;
    if (heading === null || pos === null) {
      return;
    }
    const points = this.routePointsOnFloor(pos.floorId);
    const bearing = routeBearing(points, pos);
    if (bearing === null) {
      return;
    }
    const now = Date.now();
    const deg = arrowAngle(bearing, heading);
    const deviation = deviationOfArrow(deg);

    // 偏离警示：>30° 持续 ≥2 s 才亮红；回到 ≤30° 立即解除
    if (deviation > 30) {
      if (this.deviationSince === null) {
        this.deviationSince = now;
      }
    } else {
      this.deviationSince = null;
    }
    const alert = this.deviationSince !== null && now - this.deviationSince >= ARROW_ALERT_HOLD_MS;

    // 对齐：每次导航会话只播一次「方向已对准」
    if (deviation <= 15 && !this.alignedAnnounced) {
      this.alignedAnnounced = true;
      vibrateShort();
      this.speech?.enqueue(ALIGNED_TEXT, 'normal');
    }

    // 播报：偏离持续、节流 ≥5 s，普通优先级（安全提示可打断）
    if (alert && now - this.deviationSpokenAt >= ARROW_SPEAK_THROTTLE_MS) {
      this.deviationSpokenAt = now;
      const side = deg <= 180 ? '右' : '左';
      this.speech?.enqueue(`方向偏离约 ${deviationStep(deviation)} 度，请向${side}转`, 'normal');
    }

    const alertChanged = alert !== this.data.arrowAlert;
    const angleChanged =
      !this.data.arrowVisible || Math.abs(shortestDelta(this.arrowLastDeg, deg)) >= ARROW_REDRAW_DEG;
    if (!alertChanged && !angleChanged) {
      return; // 变化 <2°：不重绘
    }
    if (!alertChanged && now - this.arrowUpdatedAt < ARROW_REDRAW_MIN_MS) {
      return; // ≥20 fps 上限
    }
    this.arrowLastDeg = deg;
    this.arrowUpdatedAt = now;
    this.setData({ arrowDeg: Math.round(deg * 10) / 10, arrowAlert: alert, arrowVisible: true });
  },

  // ------------------------------------------------------------------
  // 覆盖层交互
  // ------------------------------------------------------------------

  onSwitchFloor(e: WechatMiniprogram.TouchEvent): void {
    const floorId = String(e.currentTarget.dataset.floor ?? '');
    log.info('tap 切换楼层', floorId, `current=${this.data.currentFloor}`);
    // 选中即收起楼层菜单（含重复点击当前层的场景）
    if (this.data.floorMenuOpen) {
      this.setData({ floorMenuOpen: false }, () => this.refreshTapExclusions());
    }
    const vp = this.viewport;
    if (floorId === '' || floorId === this.data.currentFloor || vp === null) {
      return;
    }
    const segs = this.wallSegs[floorId];
    if (segs === undefined) {
      return;
    }
    // 切换楼层后回到该层全图模式居中展示（与启动首屏一致）
    vp.setFloor(floorId, flatBounds(segs));
    vp.fit();
    const item = this.floorList.find((f) => f.floorId === floorId);
    this.setData({
      currentFloor: floorId,
      currentFloorText: item !== undefined ? `${item.level}F` : floorId,
    });
    this.requestDraw();
  },

  /** 楼层选择按钮：横向展开/收起可选楼层。 */
  onToggleFloorMenu(): void {
    this.setData({ floorMenuOpen: !this.data.floorMenuOpen }, () => this.refreshTapExclusions());
  },

  onResetView(): void {
    log.info('tap 回到全图');
    this.viewport?.fit();
    this.requestDraw();
  },

  onGoSettings(): void {
    log.info('onGoSettings tap');
    wx.navigateTo({
      url: '/pages/settings/settings',
      fail: (err) => log.warn('navigateTo settings 失败', JSON.stringify(err)),
    });
  },

  // ------------------------------------------------------------------
  // 现场采集工具入口（横向弹出面板：静态标定 / 网格指纹）
  // ------------------------------------------------------------------

  onToggleCollectMenu(): void {
    if (!this.data.collectEntry) {
      return;
    }
    this.setData({ collectMenuOpen: !this.data.collectMenuOpen }, () => this.refreshTapExclusions());
  },

  onGoCollectStatic(): void {
    log.info('进入静态标定采集');
    this.setData({ collectMenuOpen: false });
    wx.navigateTo({
      url: '/pages/collect/static/static',
      fail: (err) => log.warn('navigateTo collect/static 失败', JSON.stringify(err)),
    });
  },

  onGoCollectFingerprint(): void {
    log.info('进入网格指纹采集');
    this.setData({ collectMenuOpen: false });
    wx.navigateTo({
      url: '/pages/collect/fingerprint/fingerprint',
      fail: (err) => log.warn('navigateTo collect/fingerprint 失败', JSON.stringify(err)),
    });
  },

  // ------------------------------------------------------------------
  // 按住说话 → 意图（REQ-PAGE 下端居中按钮 / 居中浮层）
  // ------------------------------------------------------------------

  initSpeech(): void {
    const appendFallback = (text: string) => {
      const next =
        this.data.fallbackText.length > 0 ? `${this.data.fallbackText}\n${text}` : text;
      this.setData({ fallbackText: next });
    };
    this.speech = new SpeechQueue({
      engine: appContext.tts,
      onUnavailable: (lastText) => {
        log.warn('TTS 不可用，降级文本展示', lastText);
        this.setData({ reply: lastText });
        appendFallback('语音播报不可用，已切换为文字提示');
      },
      onTextFallback: appendFallback,
    });
  },

  initPtt(): void {
    this.ptt = new PushToTalk({
      engine: appContext.asr,
      onStateChange: (state) => {
        // 按住瞬间短震动：视障用户据此确认「已开始录音、可以开口」，
        // 无需依赖视觉状态文案（REQ-VOICE 非视觉反馈）
        if (state === 'recording') {
          vibrateShort();
        }
        this.setData({ pttState: state, pttStateText: PTT_TEXT[state] });
      },
      onPartial: (text) => this.setData({ partial: text }),
      onFinal: (text) => void this.handleFinal(text),
      onError: (message) => {
        // 显示反馈的同时进行语音播报（REQ：助手反馈与播报同步）
        this.setData({ reply: message });
        this.speech?.enqueue(message, 'normal');
      },
    });
  },

  onPttStart(): void {
    // 按住说话优先：立即打断启动/提示音，避免与录音抢麦
    appContext.voicePrompts.stop();
    if (this.data.loading || this.data.loadError !== '' || !this.data.mapReady) {
      return; // 地图未就绪不受理语音（原首页 mapReady 门）
    }
    this.onDismissIntent(); // 新一轮对话先收起旧卡，避免上一轮提示残留
    this.ptt?.begin();
  },

  onPttEnd(): void {
    this.ptt?.end();
  },

  onPttCancel(): void {
    this.ptt?.cancel();
  },

  async handleFinal(text: string): Promise<void> {
    if (this.parseInFlight || this.intents === null) {
      return;
    }
    this.parseInFlight = true;
    this.setData({ recognized: text, partial: '', reply: '正在理解…' });
    try {
      const result = await this.intents.parse(text);
      this.applyIntent(result);
    } catch (err) {
      const message = err instanceof ApiClientError ? describeError(err.code) : '网络异常，请重试';
      log.warn('意图解析失败', err);
      this.setData({ reply: message });
      this.speech?.enqueue(message, 'normal');
    } finally {
      this.parseInFlight = false;
    }
  },

  applyIntent(result: IntentParseResponse): void {
    this.setData(
      {
        reply: result.responseText,
        candidates: result.needsClarification ? result.candidates : [],
        destination: result.destination ?? null,
      },
      () => this.refreshTapExclusions(), // 居中卡出现 → 整体作为排除区
    );
    this.speech?.enqueue(result.responseText, 'normal');
    if (result.intent === 'query_location') {
      // responseText 是过渡话术（"请稍等，正在查询…"），随后补播真实位置
      void this.answerLocation();
    }
  },

  /** 「我在哪儿」：单次定位 → 按质量策略播报（FE-01，失败播安全文案）。 */
  async answerLocation(): Promise<void> {
    if (this.locationInFlight || this.locationQuery === null) {
      return;
    }
    this.locationInFlight = true;
    try {
      const text = await this.locationQuery.query();
      if (text === null || !this.data.mapReady) {
        return; // 并发被忽略 / 页面已不可用
      }
      this.setData({ reply: text });
      this.speech?.enqueue(text, 'normal');
    } catch (err) {
      log.warn('位置查询异常', err);
      this.setData({ reply: UNRESOLVED_TEXT });
      this.speech?.enqueue(UNRESOLVED_TEXT, 'normal');
    } finally {
      this.locationInFlight = false;
    }
  },

  /** 澄清候选点选：以候选名重新解析，取得目的地与话术。 */
  onPickCandidate(event: WechatMiniprogram.TouchEvent): void {
    const name = event.currentTarget.dataset.name;
    if (typeof name === 'string' && name.length > 0) {
      void this.handleFinal(name);
    }
  },

  /** 确认目的地 → 进入导航态（同页融合，原 navigateTo 导航页已退役）。 */
  onStartNav(): void {
    const destination = this.data.destination;
    if (destination === null || this.data.navActive) {
      return;
    }
    this.navDestination = destination;
    this.sessionEnded = false;
    this.overviewAnnounced = false;
    this.routeForDisplay = null;
    this.navPosition = null;
    this.lastTapAt = 0;
    // 箭头会话状态复位（每次导航重新计一次「已对准」）
    this.headingUnavailableAnnounced = false;
    this.alignedAnnounced = false;
    this.deviationSince = null;
    this.deviationSpokenAt = 0;
    this.arrowLastDeg = 0;
    this.arrowUpdatedAt = 0;
    this.headingDeg = null;
    this.headingSmoother.reset();
    // 收起意图居中卡，切换到导航 HUD
    this.setData(
      {
        navActive: true,
        ended: false,
        state: 'idle' as NavState,
        stateText: NAV_TEXT.idle,
        instruction: '正在确定您的位置…',
        destinationName: destination.name,
        destination: null,
        candidates: [],
        reply: '',
        partial: '',
        recognized: '',
      },
      () => this.refreshTapExclusions(),
    );
    this.syncArrowLifecycle(); // 视障模式：导航开始即启动朝向传感器
    this.enterNavZoom(); // 视野从全路线收窄到局部（REQ-NAV 展示层）
    void this.initNavigation();
  },

  /**
   * 进入导航：视野过宽（全图/全路线）时以屏心放大到 NAV_VIEW_FACTOR 倍，
   * 只放大不缩小——已手动放大的视角不被抢回；后续跟随只平移。
   */
  enterNavZoom(): void {
    const vp = this.viewport;
    if (vp === null || this.canvasSize.w === 0) {
      return;
    }
    const target = Math.min(vp.fitScale * NAV_VIEW_FACTOR, vp.maxScale);
    if (vp.scale < target) {
      vp.zoomAt(this.canvasSize.w / 2, this.canvasSize.h / 2, target / vp.scale);
      log.info(`导航视野收窄 ×${NAV_VIEW_FACTOR}`, `scale=${vp.scale.toFixed(2)}`);
      this.requestDraw();
    }
  },

  /**
   * 导航编排（自原导航页 bootstrap 平移）：定位管线 + 会话控制器。
   * SpeechQueue 复用页面级实例（onLoad 已建）。
   */
  async initNavigation(): Promise<void> {
    if (this.speech === null) {
      return;
    }
    try {
      await appContext.map.ensureLoaded();
    } catch (err) {
      const message =
        err instanceof ApiClientError ? describeError(err.code) : '地图加载失败，请重试';
      log.warn('导航前地图加载失败', err);
      this.setData({ stateText: message, instruction: message });
      return;
    }

    const source = createBeaconSource(appContext.dataSource, appContext.map);
    this.navSource = source;
    const pipeline = new LocationPipeline({
      client: appContext.http.client,
      map: appContext.map,
      source,
      sequence: new SequenceCounter(),
      onEstimate: (estimate, meta) => this.handleEstimate(estimate, meta),
      onUnusable: (err) => {
        // 导航中信号丢失/信标不足：只报 quality 让服务端转 recovering，
        // 不伪造坐标；进入 recovering 的状态迁移由 onStateChange 负责长震动。
        if (
          this.controller !== null &&
          this.controller.sessionId !== null &&
          !this.sessionEnded
        ) {
          this.controller.reportUnavailable();
        }
        if (!this.locationWarned) {
          this.locationWarned = true;
          log.warn('定位不可用，已进入恢复流程', err.code);
        }
      },
    });
    this.pipeline = pipeline;

    const pipelineControl: NavPipelineControl = {
      stop: () => pipeline.stop(),
      startNavigating: async () => {
        if (pipeline.isRunning) {
          pipeline.setMode('navigating');
        } else {
          await pipeline.start('navigating');
        }
      },
    };
    this.controller = new NavSessionController({
      client: appContext.http.client,
      map: appContext.map,
      speech: this.speech,
      pipeline: pipelineControl,
      onStateChange: (state) => {
        this.setData({ state, stateText: NAV_TEXT[state] });
        if (state === 'recovering') {
          vibrateLong(); // 震动模式：脱离路线需要引起注意
          // 位置不可信：箭头隐藏（不显示可能错误的方向）
          this.setData({ arrowVisible: false, arrowAlert: false });
        } else if (state === 'active') {
          this.syncArrowLifecycle();
        }
      },
      onRoute: (route) => {
        // onRoute 先于 controller 播报首条指令（createSession 内顺序）：
        // 概要先进队列，形成「概要 → 首条指令」的既定顺序（REQ-NAV）
        if (!this.overviewAnnounced && this.navDestination !== null) {
          this.overviewAnnounced = true;
          const overview = buildRouteOverview(
            route,
            this.navDestination.name,
            (floorId) => appContext.map.floors[floorId]?.displayName,
          );
          this.speech?.enqueue(overview, 'normal');
          log.info('路径概要:', overview);
        }
        const text = route.segments[0]?.text;
        if (text !== undefined) {
          this.setData({ instruction: text });
        }
        // mock 模拟导航（类高德）：路线就绪后让 mock 用户沿规划路线
        // 以 1.5m/s 行走，沿途按所在楼层采集信标观测（reroute 同样换新路线）
        if (this.navSource instanceof MockBeaconSource && route.polyline.length > 0) {
          this.navSource.updatePath(
            route.polyline.map((p) => ({ x: p.x, y: p.y, floorId: p.floorId ?? undefined })),
          );
          log.info(
            `模拟导航：沿路线行走 ${route.polyline.length} 点 @${MOCK_WALK_SPEED_MPS}m/s`,
          );
        }
        this.routeForDisplay = route;
        this.requestDraw(); // 导航视觉：重绘路线（REQ-NAV 展示层）
        this.updateArrow(); // 路线就绪后按当前位置算箭头方向
      },
      onEnded: (info) => {
        this.sessionEnded = true;
        pipeline.stop();
        if (!info.byArrival && info.reason !== null) {
          this.setData({
            ended: true,
            instruction: '导航已结束',
            stateText: info.reason === 'user_cancelled' ? '已取消导航' : NAV_TEXT.ended,
          });
        } else {
          this.setData({ ended: true }); // 到达/恢复失效：按钮切为「关闭」
        }
        this.syncArrowLifecycle(); // 导航结束：停传感器并隐藏箭头
      },
    });

    try {
      await pipeline.start('standby');
    } catch (err) {
      log.warn('定位采集启动失败', err);
      this.setData({
        stateText: '定位启动失败，请检查蓝牙后重试',
        instruction: '定位启动失败，请检查蓝牙后重试',
      });
    }
  },

  /**
   * 定位 → 会话（首个可信估计作为起点）；可信位置驱动显示跟随。
   * 不可信估计（unreliable/lost）只上送服务端转 recovering，不推进显示与到达。
   */
  handleEstimate(
    estimate: PositionEstimateResponse,
    meta?: { visibleBeaconCount: number },
  ): void {
    if (meta) {
      this.recordCalibration(estimate, meta.visibleBeaconCount);
    }
    const position = estimate.position;
    if (position !== undefined && position !== null) {
      this.locationWarned = false; // 可信位置恢复：允许下一次不可用重新记日志
      // 只有 reliable/degraded 才带坐标 → 恢复态(unreliable)天然不推进显示
      this.navPosition = {
        floorId: position.floorId ?? this.navPosition?.floorId ?? this.floorList[0]?.floorId ?? '1',
        x: position.x,
        y: position.y,
      };
      this.followPosition();
      this.updateArrow(); // 可信位置更新 → 重算当前折线段方向
    }
    const controller = this.controller;
    if (controller === null || this.sessionEnded) {
      return;
    }
    if (controller.sessionId === null) {
      void this.createSession(estimate);
      return;
    }
    controller.handleEstimate(estimate);
  },

  /**
   * 标定模式：记录可信估计与当前导航状态（只写本地缓冲，不改算法/播报）。
   * 关闭标定模式时为零开销（启动时读取开关）。
   */
  recordCalibration(estimate: PositionEstimateResponse, visibleBeaconCount: number): void {
    if (!appContext.calibrationMode) {
      return;
    }
    const controller = this.controller;
    appContext.calibration.record({
      estimate,
      visibleBeaconCount,
      instructionSequence: controller?.currentInstructionSequence ?? null,
      sessionStatus: controller?.state ?? null,
    });
  },

  /**
   * 类高德跟随：楼层自动切换 + 视口对准当前位置（REQ-NAV 展示层）。
   * 待命态保持全图居中（启动/换层后的观感），不做位置跟随平移；
   * 导航态才把视口中心对准当前位置（进入导航时已按 NAV_VIEW_FACTOR 收窄）。
   */
  followPosition(): void {
    const vp = this.viewport;
    const pos = this.navPosition;
    if (vp === null || pos === null || this.canvasSize.w === 0) {
      return;
    }
    const navActive = this.data.navActive;
    let floorChanged = false;
    if (pos.floorId !== vp.floorId) {
      const segs = this.wallSegs[pos.floorId];
      if (segs !== undefined) {
        vp.setFloor(pos.floorId, flatBounds(segs));
        const item = this.floorList.find((f) => f.floorId === pos.floorId);
        this.setData({
          currentFloor: pos.floorId,
          currentFloorText: item !== undefined ? `${item.level}F` : pos.floorId,
        });
        floorChanged = true;
        // 待命态换层：回到该层全图居中；导航态保持局部跟随视野
        if (!navActive) {
          vp.fit();
        }
      }
    }
    if (!navActive) {
      if (floorChanged) {
        this.requestDraw();
      }
      return;
    }
    // 导航态每次可信估计把视口中心对准位置（2.5s 节奏）
    const { sx, sy } = vp.toScreen(pos.x, pos.y);
    vp.panBy(this.canvasSize.w / 2 - sx, this.canvasSize.h / 2 - sy);
    this.requestDraw();
  },

  async createSession(estimate: PositionEstimateResponse): Promise<void> {
    const controller = this.controller;
    const position = estimate.position;
    const destination = this.navDestination;
    if (controller === null || position === undefined || position === null || destination === null) {
      return;
    }
    try {
      await controller.createSession(destination, {
        x: position.x,
        y: position.y,
        floorId: position.floorId ?? null,
        confidence: estimate.confidence,
        observedAt: estimate.observedAt,
      });
      this.setData({ stateText: NAV_TEXT.active });
    } catch (err) {
      const message = err instanceof ApiClientError ? describeError(err.code) : '无法开始导航，请重试';
      log.warn('会话创建失败', err);
      this.setData({ instruction: message });
      this.speech?.enqueue(message, 'normal');
    }
  },

  /** 双击结束导航（防误触，自原导航页平移）。 */
  onEndTap(): void {
    const controller = this.controller;
    if (controller === null || this.sessionEnded || controller.sessionId === null) {
      return;
    }
    const now = Date.now();
    if (now - this.lastTapAt <= DOUBLE_TAP_WINDOW_MS) {
      this.lastTapAt = 0;
      this.setData({ stateText: '正在结束导航…' });
      controller.endSession('user_finished').catch((err) => {
        log.warn('结束会话失败', err);
        this.setData({ stateText: NAV_TEXT.active });
      });
      return;
    }
    this.lastTapAt = now;
    const hint = '再按一次结束导航';
    this.setData({ stateText: hint });
    this.speech?.enqueue(hint, 'normal');
    setTimeout(() => {
      if (this.lastTapAt !== 0 && !this.sessionEnded) {
        this.setData({ stateText: NAV_TEXT[this.data.state as NavState] });
      }
    }, DOUBLE_TAP_WINDOW_MS);
  },

  /** 收起意图/反馈居中卡：清空全部面板状态，避免关闭后残留遮挡地图（REQ-PAGE）。 */
  onDismissIntent(): void {
    this.setData(
      {
        recognized: '',
        partial: '',
        reply: '',
        fallbackText: '',
        candidates: [],
        destination: null,
      },
      () => this.refreshTapExclusions(),
    );
  },

  /** 导航结束后关闭 HUD，回到待命地图。 */
  onDismissNav(): void {
    this.navDestination = null;
    this.routeForDisplay = null;
    this.navPosition = null;
    this.navSource = null;
    this.pipeline?.stop();
    this.pipeline = null;
    this.controller = null;
    this.setData({
      navActive: false,
      ended: false,
      instruction: '',
      destinationName: '',
      state: 'idle' as NavState,
      stateText: NAV_TEXT.idle,
    });
    // 关键：导航期间累积的 fallbackText/reply 若不清空，
    // navActive=false 后居中卡会因 fallbackText 非空重新弹出遮挡地图
    this.onDismissIntent();
    this.syncArrowLifecycle(); // 关闭导航 HUD：停传感器并隐藏箭头
    this.requestDraw();
  },
});
