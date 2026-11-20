import { VoiceInteraction } from '../../core/asr/voice-interaction';
import { getScreenReaderVoice } from '../../core/a11y/voice-mode';
/**
 * 楼层地图启动首屏：墙体平面图 + 1F/2F 切换 + 单指拖动/双指缩放 + mock 位置点。
 *
 * 视口数学在 core/map/floor-map-viewport.ts（纯逻辑，单测覆盖），本文件只做
 * 页面编排、canvas 绘制与手势采集；地图数据经 MapService 单例（本地文件缓存
 * 秒开，未缓存时前台下载，失败走 loadError + 重试）。
 */
import { appContext, BEACON_UUIDS_KEY } from '../../services/app-context';
import { FieldNavigationRecorder, takeFieldNavigation } from '../../services/field-navigation';
import { getJson, setJson } from '../../utils/kv';
import {
  pickStartupFloor,
  probeStartupFloorSamples,
  type StartupFloorSample,
} from '../../core/ble/startup-floor-probe';
import { ensureServiceEndpoint, lastEndpointFailure, currentServiceBaseUrl } from '../../services/service-endpoint';
import { describeEndpointFailure } from '../../core/discovery/failure';
import { createNavJournal } from '../../core/nav/create-journal';
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
import {
  ASR_EMPTY_TEXT,
  type PttState,
} from '../../core/asr/push-to-talk';
import { createBeaconSource } from '../../core/ble/beacon-source-factory';
import type { BeaconSourceLike } from '../../core/ble/beacon-source';
import { MockBeaconSource } from '../../core/ble/mock-beacon-source';
import { getScanProfile } from '../../core/ble/scan-profile';
import { MOCK_POSITION, MOCK_WALK_SPEED_MPS } from '../../core/map/mock-position';
import { SpeechQueue } from '../../core/speech/speech-queue';
import type { LocalVoiceName } from '../../core/speech/local-voice';
import type { MapServiceEvent } from '../../core/map/map-service';
import { IntentService, resolveIntentTarget } from '../../services/intent.service';
import { matchLocalIntent } from '../../core/intent/local-rules';
import {
  LocationQueryService,
  SURROUNDINGS_UNRESOLVED_TEXT,
  UNRESOLVED_TEXT,
  type LocationQueryKind,
} from '../../services/location-query.service';
import {
  NavSessionController,
  type NavPipelineControl,
  type NavState,
} from '../../core/nav/nav-session-controller';
import { LocationPipeline } from '../../core/position/location-pipeline';
import { SequenceCounter } from '../../core/position/sequence-counter';
import { buildRouteOverview } from '../../core/nav/route-overview';
import {
  TurnHaptics,
  remainingRouteDistanceM,
  resolveTurnNode,
  turnDistanceM,
} from '../../core/nav/turn-haptics';
import { vibrateDoubleShort, vibrateLong, vibrateShort } from '../../utils/haptic';
import { startPerfTrace, type PerfTrace } from '../../utils/perf-trace';
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
import {
  HeadingSource,
  applyHeadingOffset,
  readCompassOnce,
  type HeadingApi,
  type HeadingMode,
} from '../../core/a11y/heading-source';
import { arrowClipPath } from '../../core/a11y/nav-arrow-shape';
import type { A11yMode } from '../../services/app-context';
import type {
  IntentLocationPayload,
  IntentParseResponse,
  NavigationInstruction,
  OriginPosition,
  PlaceCandidate,
  PositionEstimateResponse,
  PreviewRouteResponse,
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

/** 已请求开录、麦克风尚未就绪时的文案（就绪后才切到 PTT_TEXT.recording）。 */
const PTT_TEXT_PREPARING = '正在打开麦克风…';

/** 导航态文案（自原导航页平移）。 */
const NAV_TEXT: Record<NavState, string> = {
  idle: '正在定位…',
  active: '导航中',
  recovering: '附近定位信标不足或信号不稳定，导航已暂停；请原地稍候或移至信号覆盖较好处',
  arrived: '已到达目的地',
  ended: '导航结束',
};

const DOUBLE_TAP_WINDOW_MS = 500;

/** 进入逐步引导前的短过渡（本地音频，离线可播）：场景一/场景二共用，位于概要与首条指令之间。 */
const NAV_START_TRANSITION_CLIP = 'nav-start';

/**
 * 启动楼层探测窗口：与地图加载并行扫描蓝牙。
 * 窗口内采到信标 → 按多数楼层显示；未采到 → 默认首层。
 */
const STARTUP_FLOOR_PROBE_MS = 2_000;

/** 预览起点等待上限：取不到可信位置就不规划（绝不建会话）。 */
const PREVIEW_ORIGIN_TIMEOUT_MS = 12_000;
/** 预览接口超时：内部含一次大模型概要调用，与建会话同级放宽。 */
const PREVIEW_TIMEOUT_MS = 30_000;
/** 概要播完后等待用户确认的时长；超时视为拒绝（不建会话）。 */
const PREVIEW_CONFIRM_TIMEOUT_MS = 30_000;
/**
 * 「开始导航」复用近期预览的窗口：自「应用说完最后一句话」（previewFreshAt）起算。
 * 概要与「是否开始导航」询问播完各刷新一次——应用自身的播报不得消耗用户的反应时间。
 */
const PREVIEW_REUSE_WINDOW_MS = 10_000;
/**
 * 建会话前重取起点的等待上限（API-05 单次超时 5 s，留 1 s 余量）。
 * 10 秒复用窗口只管「预览还值不值得复用」，管不住起点观测的年龄：
 * 服务端口径是观测龄 ≤ 15 s，概要与确认两段播报加起来就会超窗。
 */
const START_NAV_ORIGIN_TIMEOUT_MS = 6_000;
/** 场景二：语音说「开始导航」后等待用户说出目的地的上限。 */
const AWAIT_DESTINATION_TIMEOUT_MS = 30_000;

/** 语音静默失败提示的最小间隔：连续误触只播一次，避免叠音。 */
const VOICE_HINT_DEBOUNCE_MS = 3000;

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
    /** 语音入口的无障碍名称：读屏开启时切换为「双击」语义（随状态变化）。 */
    pttA11yLabel: '按住说话，松开结束',
    partial: '',
    recognized: '',
    reply: '',
    fallbackText: '',
    candidates: [] as PlaceCandidate[],
    destination: null as PlaceCandidate | null,
    /** 概要播完后才出现「开始导航」：避免与概要播报叠音、避免提前确认。 */
    navConfirmReady: false,
    isMockSource: appContext.dataSource === 'mock',
    // 现场采集工具（设置页开关控制入口可见性，即时生效；导航中隐藏）
    collectEntry: false,
    collectMenuOpen: false,
    /** 采集候选（静态标定 / 网格指纹，4 字文案）统一宽度（rpx），与 toolBtnWidth 同一套估算 */
    collectOptWidth: toolBtnWidthRpx(['静态标定']),
    // 导航态（REQ-PAGE：导航融合进地图页，原独立导航页退役）
    navActive: false,
    fieldMethod: '',
    fieldRunId: '',
    fieldCheckpoint: '',
    fieldUndo: false,
    fieldRunFinished: false,
    fieldStorageIssue: '',
    fieldSaveRetry: false,
    fieldSessionReady: false,
    fieldActualArrived: false,
    fieldSystemArrived: false,
    fieldSaving: false,
    state: 'idle' as NavState,
    stateText: NAV_TEXT.idle,
    instruction: '',
    /** 距终点剩余距离（HUD 动态文案；'' 表示暂无可用位置/路线）。 */
    remainingText: '',
    destinationName: '',
    ended: false,
    // 无障碍（specs/nav-accessibility-ux）：模式 + 大箭头
    /** 无障碍模式：视障模式（默认）/ 普通模式；普通模式回归既有地图手势。 */
    a11yMode: 'a11y' as A11yMode,
    /** 全屏按住说话开关（仅视障模式生效，默认开）。 */
    fullScreenPtt: true,
    screenReaderVoice: false,
    voiceStatus: '',
    voiceParsing: false,
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
    /** 箭头楔形轮廓（clip-path 单一真源，见 core/a11y/nav-arrow-shape.ts）。 */
    arrowClipPath: arrowClipPath(),
  },

  canvas: null as WechatMiniprogram.Canvas | null,
  speech: null as SpeechQueue | null,
  /** 地图下载/就绪事件订阅（启动播报序列用）。 */
  mapUnsubscribe: null as (() => void) | null,
  ptt: null as VoiceInteraction | null,
  intents: null as IntentService | null,
  locationQuery: null as LocationQueryService | null,
  controller: null as NavSessionController | null,
  pipeline: null as LocationPipeline | null,
  /** 当前导航的观测源（mock 时为 MockBeaconSource，路线就绪后切换行走轨迹）。 */
  navSource: null as BeaconSourceLike | null,
  navDestination: null as PlaceCandidate | null,
  routeForDisplay: null as RouteInfo | null,
  /**
   * 转弯逼近震动（REQ-A11Y-TURN-HAPTIC，仅视障模式）：
   * 语音负责距拐点约 4m 的提前播报，震动在最后 6m 内先接管逼近节拍，两者分工。
   */
  turnHaptics: null as TurnHaptics | null,
  /** 当前导航指令（每次 update 刷新，转弯震动按它取拐点）。 */
  currentInstruction: null as NavigationInstruction | null,
  fieldRecorder: null as FieldNavigationRecorder | null,
  /** 正在震动的转弯 instructionId；变化即视为新转弯，复位“已到达”。 */
  turnHapticInstructionId: null as string | null,
  /** 导航态最近一次可信位置（unreliable/lost 不更新 → 恢复态不推进显示）。 */
  navPosition: null as { floorId: string; x: number; y: number } | null,
  /** 与 navPosition 同时更新的定位置信度（意图请求随带 location 用）。 */
  navConfidence: 0,
  overviewAnnounced: false,
  /**
   * 导航阶段：idle 待命 / awaiting-destination 等待用户说目的地 /
   * previewing 规划+播概要 / awaiting-confirm 等待确认 / navigating 逐步引导。
   * 创建会话只允许发生在确认之后（场景二由「开始导航」语音意图视为已确认）。
   */
  navPhase: 'idle' as
    | 'idle'
    | 'awaiting-destination'
    | 'previewing'
    | 'awaiting-confirm'
    | 'navigating',
  /** 预览代际：取消/重来使在途回调失效。 */
  previewToken: 0,
  previewOrigin: null as OriginPosition | null,
  /** 等待首个可信估计作为规划起点。 */
  previewOriginResolve: null as ((origin: OriginPosition | null) => void) | null,
  previewOriginTimer: null as ReturnType<typeof setTimeout> | null,
  /** 概要播完后等待用户确认的超时。 */
  previewConfirmTimer: null as ReturnType<typeof setTimeout> | null,
  /** 建会话前重取起点的等待器与超时（确认后立刻取一拍新定位）。 */
  freshOriginResolve: null as ((origin: OriginPosition | null) => void) | null,
  freshOriginTimer: null as ReturnType<typeof setTimeout> | null,
  /** 防止确认入口（按钮/语音/自动开始）在等待新定位期间重入，建出两个会话。 */
  navStarting: false,
  /**
   * 预览新鲜度起点（毫秒）：由语音队列排空事件写入（概要播完、确认询问播完各一次），
   * 是「开始导航」复用预览的 10 秒窗口的唯一起算点（不从预览返回或开始播报起算）。
   */
  previewFreshAt: null as number | null,
  /** true：概要播完后直接开始导航（用户已用语音表达「开始导航」，不再二次确认）。 */
  previewAutoStart: false,
  /** 场景二：等待用户说出目的地的超时计时器。 */
  awaitDestinationTimer: null as ReturnType<typeof setTimeout> | null,
  lastTapAt: 0,
  /** 工具条按钮按下时间：bindtouchstart 先于 bindtap，测「手指按下→界面响应」的真实间隔。 */
  toolTouchAt: 0,
  /** 全屏按住说话的交互追踪（touchstart 起点，录音态渲染回调收尾）。 */
  pttTrace: null as PerfTrace | null,
  /**
   * 读屏（iOS 旁白 / Android TalkBack）是否开启：开启时旁白会接管单指触摸，
   * 全屏长按不可用，语音入口改为「双击切换录音」。
   */
  screenReaderOn: false,
  sessionEnded: false,
  /** 电梯口暂停中：不上报、不推进箭头，等目标楼层信标后自动恢复。 */
  elevatorPaused: false,
  parseInFlight: false,
  voiceEpoch: 0,
  locationInFlight: false,
  /** 上次语音失败提示时刻（空识别/录音失败），用于防连续误触叠音。 */
  voiceHintAt: 0,
  /** 定位不可用日志去噪：只在连续不可用的第一拍打日志（恢复后重置）。 */
  locationWarned: false,
  ctx: null as Canvas2d | null,
  viewport: null as FloorMapViewport | null,
  wallSegs: {} as Record<string, Float32Array>,
  /** 房间填色组（按类型聚合，draw 时每色一次 fill）。 */
  roomFills: {} as Record<string, RoomFillGroup[]>,
  placeLabels: {} as Record<string, PlaceLabel[]>,
  floorList: [] as FloorItem[],
  /** 启动楼层探测（与地图加载并行）：运行代号 / 完成标志 / 原始样本 / 是否已应用。 */
  startupFloorProbeRun: 0,
  startupFloorProbeFinished: false,
  startupFloorSamples: [] as StartupFloorSample[],
  startupFloorApplied: false,
  /** 探测判定出的楼层：首绘视口直接使用；null = 按首层显示。 */
  startupFloorId: null as string | null,
  canvasRect: { left: 0, top: 0 },
  canvasReady: false,
  canvasSize: { w: 0, h: 0 },
  dpr: 1,
  dirty: false,
  rafPending: false,
  /**
   * 复杂对象一律在 onLoad 中创建：Page 定义期的自定义属性会被开发者工具深拷贝，
   * 既打印 `Free data with key ... seems not a simple value` 告警，也会丢掉类原型方法。
   */
  gesture: null as GestureState | null,
  /** 全屏按住说话手势状态机（仅视障模式使用；普通模式不走该机）。 */
  holdGesture: null as HoldToTalkGesture | null,
  holdTimer: null as ReturnType<typeof setTimeout> | null,
  holdEndTimer: null as ReturnType<typeof setTimeout> | null,
  /** 排除矩形（canvas 坐标系；采样失败为空集合）。 */
  tapExclusions: [] as Rect[],
  /** 朝向传感器（仅导航态启动，结束/后台/卸载停止）。 */
  headingSource: null as HeadingSource | null,
  headingSmoother: null as HeadingSmoother | null,
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
    this.gesture = { mode: null, lastX: 0, lastY: 0, baseDist: 0, lastMidX: 0, lastMidY: 0 };
    this.holdGesture = new HoldToTalkGesture(HOLD_TO_TALK_DEFAULTS);
    this.headingSmoother = new HeadingSmoother();
    // 语音初始化是启动门禁的一部分：必须等 bootstrap 解析出服务端地址后再建，
    // 避免解析完成前按住说话把 ASR/TTS 凭据请求发到未解析地址。
    this.refreshScreenReaderState();
    this.applyA11ySettings(); // 模式与全屏语音开关（设置页改动通过 onShow 回读）
    this.intents = new IntentService(appContext.http.client, appContext.map);
    this.locationQuery = new LocationQueryService({
      client: appContext.http.client,
      map: appContext.map,
      makeSource: () => createBeaconSource(appContext.dataSource, appContext.map),
      floorName: (floorId) => (floorId ? appContext.map.floors[floorId]?.displayName : undefined),
      // 指纹库开关（默认关闭）：每次查询时读取，设置页改动即时生效
      useFingerprintDb: () => appContext.getFingerprintDbSetting(),
      // 查周边时上报当前朝向，服务端据此计算"左前方/右前方"等时钟方位
      headingDeg: () => this.headingDeg,
    });
    // 启动播报序列由地图事件驱动（下载开始/就绪/过旧），必须在 bootstrap 之前订阅
    this.mapUnsubscribe = appContext.map.subscribe((event) => this.onMapEvent(event));
    // 启动楼层探测与地图加载并行：只缓存原始样本，地图就绪后再解析楼层
    this.startStartupFloorProbe();
    void this.bootstrap();
  },

  onHide(): void {
    this.fieldRecorder?.persistDraft();
    ++this.voiceEpoch;
    this.parseInFlight = false;
    this.setData({ voiceParsing: false });
    this.speech?.interrupt();
    this.ptt?.handleAppHide(); // 切后台：取消识别，不提交
    this.clearHoldTimers();
    this.holdGesture!.reset();
    this.stopHeading(); // 传感器不进后台（省电 + 避免系统限流）
    this.stopTurnHaptics(); // 转弯逼近节拍不进后台，定时器一并清理
    this.controller?.handleBackground(); // 导航中：停采集，恢复交给 onShow
    if (this.navPhase === 'previewing' || this.navPhase === 'awaiting-confirm') {
      this.pipeline?.stop(); // 预览/等待确认：后台停采集，确认时按需重启
    }
  },

  onShow(): void {
    const fieldRun = takeFieldNavigation();
    if (fieldRun) {
      try {
        this.fieldRecorder = new FieldNavigationRecorder(fieldRun, (text) => {
          this.setData({ fieldStorageIssue: text, fieldSaveRetry: this.fieldRecorder?.needsSaveRetry ?? false });
        });
        this.setData({
          fieldMethod: fieldRun.selection.method, fieldRunId: fieldRun.selection.runId,
          fieldSessionReady: false, fieldActualArrived: false, fieldSystemArrived: false, fieldSaving: false,
        });
        this.requestDraw();
      } catch (error) { wx.showToast({ title: String(error), icon: 'none' }); }
    }
    // 从设置页返回后刷新采集入口可见性与无障碍模式（开关即时生效）
    this.setData({ collectEntry: appContext.getFieldCollectionToolSetting() });
    this.applyA11ySettings();
    // 读屏可能在本页停留期间被开关（设置页/快捷手势），每次回前台重查一次
    this.refreshScreenReaderState();
    const controller = this.controller;
    if (controller !== null && controller.sessionId !== null && !this.sessionEnded) {
      void controller.handleForeground();
      this.syncTurnHaptics(); // 回前台：按最近位置/指令恢复转弯逼近节拍
    } else if (this.fieldRecorder && this.data.fieldSystemArrived && this.pipeline && !this.pipeline.isRunning) {
      void this.pipeline.start('navigating').catch((error) => log.warn('实验到达后恢复采集失败', error));
    }
  },

  onUnload(): void {
    this.fieldRecorder?.finish('aborted', '导航页退出');
    // 终止启动楼层探测：迟到的样本不再用于判层
    this.startupFloorProbeRun += 1;
    this.controller?.dispose();
    ++this.voiceEpoch;
    this.parseInFlight = false;
    this.setData({ voiceParsing: false });
    this.ptt?.cancel();
    this.speech?.interrupt();
    this.mapUnsubscribe?.();
    this.mapUnsubscribe = null;
    this.clearHoldTimers();
    this.stopHeading();
    this.stopTurnHaptics();
    this.navPhase = 'idle';
    this.previewToken += 1;
    this.clearPreviewTimers();
    this.clearAwaitDestinationTimer();
    this.previewOrigin = null;
    this.previewFreshAt = null;
    this.previewAutoStart = false;
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
        if (!getScreenReaderVoice()) void appContext.voicePrompts.play('map-loading');
      }
      return;
    }
    if (event.type !== 'ready') {
      return;
    }
    if (event.source === 'download') {
      if (!getScreenReaderVoice()) void appContext.voicePrompts.play('map-loaded');
      vibrateShort(); // 加载成功短震动
    }
    if (!getScreenReaderVoice()) void appContext.voicePrompts.play('app-started');
    if (event.stale) {
      if (!getScreenReaderVoice()) void appContext.voicePrompts.play('map-stale');
    }
  },

  onReady(): void {
    this.initCanvas();
  },

  // ------------------------------------------------------------------
  // 启动楼层探测（与地图加载并行）
  // ------------------------------------------------------------------

  /** 上次成功加载地图时缓存下来的信标 UUID 清单（地图未加载前即可开扫）。 */
  cachedBeaconUuids(): string[] {
    const stored = getJson<string[]>(BEACON_UUIDS_KEY);
    if (!Array.isArray(stored)) {
      return [];
    }
    return stored.filter((uuid) => typeof uuid === 'string' && uuid.length > 0);
  },

  /**
   * 启动时与地图加载并行扫描蓝牙：只收集原始样本，楼层留到地图就绪后解析。
   * 2 秒内无样本（或无缓存 UUID / 蓝牙不可用）→ 保持首层显示。
   */
  startStartupFloorProbe(): void {
    if (appContext.dataSource !== 'live') {
      // mock 模拟位置固定在首层，无需实扫
      this.startupFloorProbeFinished = true;
      return;
    }
    const uuids = this.cachedBeaconUuids();
    if (uuids.length === 0) {
      log.info('启动楼层探测：无缓存信标清单，按首层显示');
      this.startupFloorProbeFinished = true;
      return;
    }
    const run = ++this.startupFloorProbeRun;
    log.info(
      '启动楼层探测：与地图加载并行扫描',
      `${uuids.length} 个 UUID / ${STARTUP_FLOOR_PROBE_MS}ms`,
    );
    void probeStartupFloorSamples(uuids, STARTUP_FLOOR_PROBE_MS).then((samples) => {
      if (run !== this.startupFloorProbeRun) {
        return; // 已完成新探测 / 页面已卸载 / 导航接管
      }
      this.startupFloorSamples = samples;
      this.startupFloorProbeFinished = true;
      log.info('启动楼层探测完成', `样本=${samples.length}`);
      this.maybeApplyStartupFloor();
    });
  },

  /** 地图就绪且探测完成后应用一次：按信标多数楼层换层，无样本则保持首层。 */
  maybeApplyStartupFloor(): void {
    if (this.startupFloorApplied || !this.startupFloorProbeFinished || !this.data.mapReady) {
      return;
    }
    this.startupFloorApplied = true;
    if (this.startupFloorSamples.length === 0) {
      log.info('启动楼层探测：规定时间内未采到信标，显示首层');
      return;
    }
    if (this.data.navActive || this.navPhase !== 'idle') {
      log.info('启动楼层探测：已进入预览/导航，楼层交给定位跟随');
      return;
    }
    const winner = pickStartupFloor(this.startupFloorSamples, (uuid, major, minor) =>
      appContext.map.beaconRegistry.resolve(uuid, major, minor),
    );
    if (winner === null) {
      log.info('启动楼层探测：样本未命中台账，显示首层');
      return;
    }
    log.info(
      '启动楼层探测：按信标楼层显示',
      `${winner.floorId}（${winner.count} 帧，均 ${winner.avgRssi.toFixed(1)}dBm）`,
    );
    this.startupFloorId = winner.floorId;
    this.applyStartupFloor(winner.floorId);
  },

  /** 把地图切到启动探测出的楼层；视口未建好时只记录，首绘按该层创建。 */
  applyStartupFloor(floorId: string): void {
    const item = this.floorList.find((f) => f.floorId === floorId);
    const floorText = item !== undefined ? `${item.level}F` : floorId;
    const segs = this.wallSegs[floorId];
    const vp = this.viewport;
    if (vp === null || segs === undefined) {
      this.setData({ currentFloor: floorId, currentFloorText: floorText });
      return;
    }
    if (vp.floorId === floorId) {
      return;
    }
    vp.setFloor(floorId, flatBounds(segs));
    vp.fit();
    this.setData({ currentFloor: floorId, currentFloorText: floorText }, () =>
      this.requestDraw(),
    );
  },

  // ------------------------------------------------------------------
  // 数据加载（照 index 页 bootstrap 范式）
  // ------------------------------------------------------------------

  async bootstrap(): Promise<void> {
    this.setData({ loading: true, loadError: '' });
    try {
      // 先解析业务地址：真机走缓存 + UDP 发现；模拟器走本机回环直连。
      const endpoint = await ensureServiceEndpoint();
      if (endpoint === null) {
        const failure = lastEndpointFailure();
        log.warn('未解析业务地址，停止加载地图', failure ?? 'unknown');
        this.setData({
          loading: false,
          mapReady: false,
          // 按失败原因区分：模拟器限制、无掩码、网段过大、扫描无命中等，不压成同一个文案。
          loadError: describeEndpointFailure(failure),
        });
        if (!getScreenReaderVoice()) void appContext.voicePrompts.play('map-load-failed');
        vibrateLong();
        return;
      }
      log.info('业务地址已就绪', endpoint);
      // 门禁通过：此后才初始化语音（ASR/TTS 凭据请求只可能打到已解析地址）。
      this.initVoiceOnce();
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
      // 缓存信标 UUID：下次启动可在地图加载完成前就开扫，判定所在楼层
      const scanUuids = appContext.map.beaconRegistry.scanUuids;
      if (scanUuids.length > 0) {
        setJson(BEACON_UUIDS_KEY, scanUuids);
      }
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
      // 地图可能晚于探测就绪：就绪后补一次应用（无样本则保持首层）
      this.maybeApplyStartupFloor();
      this.refreshTapExclusions();
      // 启动集中申请权限：地图就绪后再弹，避免与加载叠加
      this.requestStartupPermissions();
    } catch (err) {
      const message =
        err instanceof ApiClientError ? describeError(err.code) : '地图加载失败，请检查网络';
      log.warn('地图加载失败', err);
      this.setData({ loading: false, loadError: message, mapReady: false });
      // 无可用地图 → 本地提示音 + 长震动（不播「程序已启动」）
      if (!getScreenReaderVoice()) void appContext.voicePrompts.play('map-load-failed');
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
    // 启动楼层探测已判定出楼层时直接以该层建视口，避免先画首层再跳层
    const initialFloorId = this.startupFloorId ?? this.floorList[0]?.floorId;
    const firstFloor = this.floorList.find((f) => f.floorId === initialFloorId);
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
    if (firstFloor.floorId !== this.data.currentFloor) {
      this.setData({
        currentFloor: firstFloor.floorId,
        currentFloorText: `${firstFloor.level}F`,
      });
    }
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

    // 路线展示：预览（只读规划）与导航态都画（route 由预览/onRoute 存入）
    const route = this.routeForDisplay;
    if (route !== null && route.polyline.length >= 2) {
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

    const fieldPlan = this.fieldRecorder?.run.plan;
    const nextFieldIndex = this.fieldRecorder?.count;
    for (const [index, point] of (fieldPlan?.points ?? []).entries()) {
      if (point.floorId !== vp.floorId) continue;
      const { sx, sy } = vp.toScreen(point.x, point.y);
      ctx.save();
      ctx.fillStyle = point.pointId.startsWith('P-') ? '#b45309' : '#1d4ed8';
      ctx.strokeStyle = '#fff';
      ctx.lineWidth = 2;
      ctx.beginPath();
      ctx.arc(sx, sy, index === nextFieldIndex ? 8 : 5, 0, Math.PI * 2);
      ctx.fill();
      ctx.stroke();
      ctx.font = 'bold 11px sans-serif';
      ctx.textAlign = 'center';
      ctx.strokeText(point.pointId, sx, sy - 11);
      ctx.fillText(point.pointId, sx, sy - 11);
      ctx.restore();
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
    if (this.screenReaderOn) return;
    const ts = e.touches;
    // 普通模式：回归既有地图手势（按下即可拖拽，无长按判定、无死区）
    if (this.data.a11yMode !== 'a11y') {
      this.startLegacyTouch(ts);
      return;
    }
    if (ts.length >= 2) {
      this.clearHoldTimers();
      this.holdGesture!.reset();
      this.startZoomGesture(ts);
      return;
    }
    if (ts.length !== 1) {
      return;
    }
    const p = this.toCanvasPoint(ts[0]);
    const action = this.holdGesture!.onStart({
      x: p.x,
      y: p.y,
      pointCount: 1,
      excluded: this.isTapExcluded(p.x, p.y),
      now: Date.now(),
    });
    if (action === 'pending' && this.isFullScreenPttEnabled()) {
      // 先挂起：500 ms 静止转语音，移动超过 10px 转拖拽
      this.pttTrace = startPerfTrace('map', 'hold-to-talk');
      this.gesture = { mode: 'pending', lastX: p.x, lastY: p.y, baseDist: 0, lastMidX: 0, lastMidY: 0 };
      this.scheduleHold();
      return;
    }
    // 排除区 / 地图未就绪 / 开关关闭：保持原有「按下即拖拽」
    this.holdGesture!.reset();
    this.gesture = { mode: 'pan', lastX: p.x, lastY: p.y, baseDist: 0, lastMidX: 0, lastMidY: 0 };
  },

  onTouchMove(e: WechatMiniprogram.TouchEvent): void {
    if (this.screenReaderOn) return;
    const vp = this.viewport;
    if (vp === null) {
      return;
    }
    const g = this.gesture!;
    const ts = e.touches;
    if (this.data.a11yMode === 'a11y' && g.mode === 'pending') {
      if (ts.length >= 2) {
        // 1→2 指：缩放优先，取消语音候选
        this.clearHoldTimers();
        this.holdGesture!.reset();
        this.startZoomGesture(ts);
      } else if (ts.length === 1) {
        const p = this.toCanvasPoint(ts[0]);
        const action = this.holdGesture!.onMove(p.x, p.y, 1);
        if (action !== 'pan-start') {
          return; // 仍在死区内：不拖拽、不重绘
        }
        // 超出 10px 死区：从按下点开始拖拽，避免首帧跳变
        this.clearHoldTimers();
        this.pttTrace = null; // 转为拖拽，不再等待录音态
        const origin = this.holdGesture!.origin;
        g.mode = 'pan';
        g.lastX = origin.x;
        g.lastY = origin.y;
      }
    }
    if (this.data.a11yMode === 'a11y' && this.holdGesture!.currentPhase === 'voice') {
      return; // 语音态内移动不取消（沿用按住说话容错）
    }
    const cur = this.gesture!; // 1→2 指可能已重建 gesture 对象
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
    if (this.screenReaderOn) return;
    const ts = e.touches;
    if (this.data.a11yMode === 'a11y') {
      const action = this.holdGesture!.onEnd(ts.length);
      if (action === 'voice-end') {
        this.clearHoldTimers();
        this.gesture!.mode = null;
        this.onPttEnd();
        return;
      }
      this.clearHoldTimers();
      this.pttTrace = null; // 未进入录音态就抬手/取消：结束本次追踪
    }
    if (ts.length === 1) {
      // 双指抬一根 → 剩余单指继续平移
      const p = this.toCanvasPoint(ts[0]);
      this.gesture = { mode: 'pan', lastX: p.x, lastY: p.y, baseDist: 0, lastMidX: 0, lastMidY: 0 };
    } else if (ts.length === 0) {
      this.gesture!.mode = null;
    }
  },

  onTouchCancel(e: WechatMiniprogram.TouchEvent): void {
    if (this.screenReaderOn) return;
    if (this.data.a11yMode === 'a11y') {
      const action = this.holdGesture!.onCancel();
      this.clearHoldTimers();
      this.gesture!.mode = null;
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

  /** 生效条件：视障模式 + 全屏开关打开 + 读屏未开启 + 地图已就绪。 */
  isFullScreenPttEnabled(): boolean {
    return (
      this.data.a11yMode === 'a11y' &&
      this.data.fullScreenPtt &&
      // 读屏接管单指触摸后长按收不到事件，入口改由底部按钮的双击切换承担
      !this.screenReaderOn &&
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
      if (this.holdGesture!.currentPhase !== 'pending' || !this.isFullScreenPttEnabled()) {
        this.holdGesture!.reset();
        return;
      }
      if (this.holdGesture!.onTick(Date.now()) === 'voice-start') {
        this.gesture!.mode = null; // 语音态不再拖拽
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
      if (this.holdGesture!.currentPhase !== 'voice') {
        return;
      }
      if (this.holdGesture!.onTick(Date.now()) === 'voice-timeout') {
        this.gesture!.mode = null;
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

  /**
   * 转弯逼近震动（REQ-A11Y-TURN-HAPTIC）。
   *
   * 只在「视障模式 + 导航中(active) + 当前指令是转弯 + 未录音 + 有可信位置 + 有路线」
   * 时喂距离；其余情况（直行/到达/恢复/偏航/结束/页面隐藏/录音）一律停表，
   * 不叠加既有的直行、到达、偏航震动语义。
   */
  syncTurnHaptics(): void {
    const haptics = this.turnHaptics;
    if (haptics === null) {
      return;
    }
    const instruction = this.currentInstruction;
    const route = this.routeForDisplay;
    const pos = this.navPosition;
    const isTurn =
      instruction !== null &&
      (instruction.action === 'turn_left' || instruction.action === 'turn_right');
    if (
      this.data.a11yMode !== 'a11y' ||
      this.navPhase !== 'navigating' ||
      this.sessionEnded ||
      this.data.state !== 'active' ||
      this.data.pttState !== 'idle' ||
      !isTurn ||
      route === null ||
      pos === null
    ) {
      this.stopTurnHaptics();
      return;
    }
    const turn = resolveTurnNode(appContext.map.floors, instruction.trigger.nodeId ?? null);
    const distance = turnDistanceM(route.polyline, pos, turn);
    if (distance === null) {
      this.stopTurnHaptics();
      return;
    }
    if (this.turnHapticInstructionId !== instruction.instructionId) {
      this.turnHapticInstructionId = instruction.instructionId;
      haptics.beginTurn(distance);
    } else {
      haptics.update(distance);
    }
  },

  /** 中断转弯逼近节拍（结束/到达/偏航/切后台/录音），定时器在这里统一清理。 */
  stopTurnHaptics(): void {
    this.turnHaptics?.stop();
    this.turnHapticInstructionId = null;
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
        // 仅罗盘是绝对方位，需要叠加地图北偏移；运动相对量不做修正。
        const corrected =
          mode === 'compass' ? applyHeadingOffset(deg, appContext.getHeadingOffsetSetting()) : deg;
        this.headingDeg = this.headingSmoother!.update(corrected, Date.now());
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
    this.headingSmoother!.reset();
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
    this.speech?.enqueue(HEADING_UNAVAILABLE_TEXT, 'normal', null, 'map:heading-unavailable');
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
    if (!this.isArrowEnabled() || this.elevatorPaused) {
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
      this.speech?.enqueue(ALIGNED_TEXT, 'normal', null, 'map:arrow-aligned');
    }

    // 播报：偏离持续、节流 ≥5 s，普通优先级（安全提示可打断）
    if (alert && now - this.deviationSpokenAt >= ARROW_SPEAK_THROTTLE_MS) {
      this.deviationSpokenAt = now;
      const side = deg <= 180 ? '右' : '左';
      this.speech?.enqueue(
        `方向偏离约 ${deviationStep(deviation)} 度，请向${side}转`,
        'normal',
        null,
        'map:arrow-deviation',
      );
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
    const trace = this.startToolTrace('tap-floor-switch');
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
    this.setData(
      {
        currentFloor: floorId,
        currentFloorText: item !== undefined ? `${item.level}F` : floorId,
      },
      () => trace.mark('floor-setData'),
    );
    this.requestDraw();
    trace.mark('draw-requested');
  },

  /** 楼层选择按钮：横向展开/收起可选楼层。 */
  onToggleFloorMenu(): void {
    const trace = this.startToolTrace('tap-floor-trigger');
    this.setData({ floorMenuOpen: !this.data.floorMenuOpen }, () => {
      this.refreshTapExclusions();
      trace.mark('menu-rendered');
    });
  },

  /** 工具条按钮按下（bindtouchstart）：记录真实手指起点，供 bindtap 计算总延迟。 */
  onToolTouchStart(): void {
    this.toolTouchAt = Date.now();
  },

  /** 取用按下时间并开启一次交互追踪；没有 touchstart（如读屏触发）时退回当前时间。 */
  startToolTrace(action: string): PerfTrace {
    const startedAtMs = this.toolTouchAt > 0 ? this.toolTouchAt : undefined;
    this.toolTouchAt = 0;
    return startPerfTrace('map', action, { startedAtMs });
  },

  onResetView(): void {
    log.info('tap 回到全图');
    this.viewport?.fit();
    this.requestDraw();
  },

  onGoSettings(): void {
    const trace = this.startToolTrace('tap-settings');
    log.info('onGoSettings tap');
    wx.navigateTo({
      url: `/pages/settings/settings?perfAt=${trace.startedAtMs}&perfId=${trace.traceId}`,
      success: () => trace.mark('navigateTo', 1000),
      fail: (err) => {
        trace.mark('navigateTo-fail', 0);
        log.warn('navigateTo settings 失败', JSON.stringify(err));
      },
    });
  },

  // ------------------------------------------------------------------
  // 现场采集工具入口（横向弹出面板：静态标定 / 网格指纹）
  // ------------------------------------------------------------------

  onToggleCollectMenu(): void {
    if (!this.data.collectEntry) {
      return;
    }
    const trace = this.startToolTrace('tap-collect-trigger');
    this.setData({ collectMenuOpen: !this.data.collectMenuOpen }, () => {
      this.refreshTapExclusions();
      trace.mark('menu-rendered');
    });
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

  onGoCollectLogdist(): void {
    log.info('进入对数等距测量');
    this.setData({ collectMenuOpen: false });
    wx.navigateTo({
      url: '/pages/collect/logdist/logdist',
      fail: (err) => log.warn('navigateTo collect/logdist 失败', JSON.stringify(err)),
    });
  },

  // ------------------------------------------------------------------
  // 按住说话 → 意图（REQ-PAGE 下端居中按钮 / 居中浮层）
  // ------------------------------------------------------------------

  /**
   * 语音只初始化一次：bootstrap 解析成功后调用；重试时复用已有实例，
   * 避免重复建 SpeechQueue/PushToTalk 造成叠音与监听泄漏。
   */
  initVoiceOnce(): void {
    if (this.speech !== null) {
      return;
    }
    this.initSpeech();
    this.initPtt();
  },

  initSpeech(): void {
    const appendFallback = (text: string) => {
      const next =
        this.data.fallbackText.length > 0 ? `${this.data.fallbackText}\n${text}` : text;
      this.setData({ fallbackText: next });
    };
    this.speech = new SpeechQueue({
      onPlayback: (phase, item) => {
        if (item.source.startsWith('nav:')) {
          this.fieldRecorder?.event(`speech_${phase}`, { source: item.source, text: item.text });
        }
      },
      engine: {
        speak: (text, onPlay) => {
          if (this.data.pttState === 'recording') return Promise.resolve();
          // 文本展示不交给系统旁白朗读；导航提示走同一 TTS 队列，避免 HUD 抢焦点。
          return appContext.tts.speak(text, onPlay);
        },
        stop: () => appContext.tts.stop(),
      },
      onUnavailable: (lastText) => {
        log.warn('TTS 不可用，降级文本展示', lastText);
        this.setData({ reply: lastText });
        appendFallback('语音播报不可用，已切换为文字提示');
      },
      onTextFallback: appendFallback,
      // 本地提示音（如 nav-start）与 TTS 共用同一队列/优先级，串行不重叠；
      // 文件缺失或解码失败时 LocalVoice 也会 resolve，不会卡住后续首条指令。
      playClip: (clip, onPlay) => this.screenReaderOn || this.data.pttState === 'recording'
        ? Promise.resolve() : appContext.voicePrompts.play(clip as LocalVoiceName, onPlay),
      stopClip: () => appContext.voicePrompts.stop(),
    });
  },

  initPtt(): void {
    this.ptt = new VoiceInteraction({
      engine: appContext.asr,
      onStateChange: (state) => {
        const texts = this.pttTextsFor(state, false);
        this.setData(
          {
            voiceStatus: state === 'recognizing' ? '正在识别，请稍候' : state === 'recording' ? '正在打开麦克风，请稍候' : this.data.voiceStatus,
            pttState: state,
            // 请求开录 ≠ 可以说话：麦克风就绪（onRecordingReady）前不给「松开结束」，
            // 否则首次开麦较慢时用户按提示开口会丢掉开头
            pttStateText: texts.text,
            pttA11yLabel: texts.label,
          },
          () => {
            if (state === 'recording') {
              this.pttTrace?.mark('recording-ui', 600);
              this.pttTrace = null;
            } else if (state === 'idle' || state === 'error') {
              this.pttTrace = null;
            }
            // 录音会抢占音频与震动通道：开录立即停转弯逼近节拍，回到 idle 再恢复
            this.syncTurnHaptics();
          },
        );
      },
      // 震动必须等麦克风真正打开（RecorderManager.onStart）再给：
      // 首次录音音频会话初始化较慢，若在状态切换时就震，用户按提示开口的头几百毫秒
      // 麦克风还没开，会整句丢音（首次按住说话失败的成因，REQ-VOICE 非视觉反馈）
      onRecordingReady: () => {
        vibrateShort();
        if (this.data.pttState === 'recording') {
          const texts = this.pttTextsFor('recording', true);
          this.setData({ pttStateText: texts.text, pttA11yLabel: texts.label, voiceStatus: '开始录音，请说出目的地，说完激活结束语音输入' });
        }
      },
      onPartial: (text) => this.setData({ partial: text }),
      onFinal: (text) => void this.handleFinal(text),
      onEmpty: () => this.announceVoiceHint(ASR_EMPTY_TEXT),
      onError: (message) => {
        // 显示反馈的同时进行语音播报（REQ：助手反馈与播报同步）
        this.announceVoiceHint(message);
      },
    });
    this.ptt.setScreenReaderMode(this.screenReaderOn);
  },

  /**
   * 语音静默失败提示（空识别 / 录音失败 / 权限被拒）：
   * 同步显示 + 播报，并做时间窗防抖——连续快速误触只提示一次，避免叠音。
   * 正常识别/意图播报不经过这里，顺序与文案不变。
   */
  announceVoiceHint(message: string): void {
    const now = Date.now();
    if (now - this.voiceHintAt < VOICE_HINT_DEBOUNCE_MS) {
      return;
    }
    this.voiceHintAt = now;
    this.setData({ reply: message });
    this.speech?.enqueue(message, 'normal', null, 'map:voice-hint');
  },

  onPttStart(): void {
    // 读屏开启时触摸被旁白接管，语音入口只走 onPttToggle（避免双击时触摸与 tap 双触发）
    if (this.screenReaderOn) {
      return;
    }
    this.beginVoiceInput();
  },

  onPttEnd(): void {
    if (this.screenReaderOn) {
      return;
    }
    this.ptt?.end();
  },

  onVoiceCancel(): void {
    this.ptt?.cancel();
    ++this.voiceEpoch;
    this.parseInFlight = false;
    this.setData({ voiceParsing: false, partial: '', voiceStatus: '已取消语音输入' });
  },

  onPttCancel(): void {
    if (this.screenReaderOn) {
      return;
    }
    this.ptt?.cancel();
  },

  /** 开始一次语音输入：全屏长按与读屏双击共用（收卡/抢麦/追踪逻辑只此一份）。 */
  beginVoiceInput(): void {
    // 底部「按住说话」按钮直接 touchstart：无全屏手势起点时在此补一条追踪
    if (this.pttTrace === null) {
      this.pttTrace = startPerfTrace('map', this.screenReaderOn ? 'voice-toggle' : 'hold-to-talk');
    }
    // 按住说话优先：立即打断启动/提示音，避免与录音抢麦
    if (this.parseInFlight || this.data.pttState === 'recognizing') return;
    this.speech?.interrupt();
    appContext.voicePrompts.stop();
    if (this.data.loading || this.data.loadError !== '' || !this.data.mapReady) {
      // 读屏下保持语音入口可聚焦，开录仍受就绪门禁保护。
      if (this.screenReaderOn) {
        this.setData({ voiceStatus: this.data.loadError !== '' ? '地图加载失败，请重试' : '地图正在加载，请稍候再开始语音输入' });
      }
      return; // 地图未就绪不受理语音（原首页 mapReady 门）
    }
    // 新一轮对话收起旧卡；预览/等待确认阶段的确认卡保留，避免用户失去点选入口
    if (this.navPhase === 'idle') {
      this.clearIntentPanels();
    }
    this.ptt?.begin();
  },

  /**
   * 读屏双击切换：第一次双击开始录音，再双击结束（等同松手提交）。
   * 没有 touchend 可依赖，必须由 15 秒计时器兜底，收尾调用同一个 end()。
   */
  onPttToggle(): void {
    if (!this.screenReaderOn) {
      return; // 普通模式的快速点按保持原行为，不抢「按住说话」手势
    }
    const state = this.ptt?.currentState ?? 'idle';
    if (state === 'recording') {
      this.ptt?.end();
      vibrateDoubleShort(); // 双短振＝已停止（单短振是麦克风就绪）
      return;
    }
    if (state === 'idle' || state === 'error') {
      log.info('读屏双击开始语音');
      this.beginVoiceInput();
    }
  },

  /**
   * 语音入口的状态文案与无障碍名称：
   * - 读屏：双击语义（开始/结束/识别/失败），让用户不依赖视觉也知道下一步动作；
   * - 非读屏：保持原有「按住说话 / 松开结束」。
   */
  pttTextsFor(state: PttState, micReady: boolean): { text: string; label: string } {
    if (!this.screenReaderOn) {
      return {
        text: state === 'recording' ? (micReady ? PTT_TEXT.recording : PTT_TEXT_PREPARING) : PTT_TEXT[state],
        label: '按住说话，松开结束',
      };
    }
    if (state === 'recording') {
      return micReady
        ? { text: '结束语音输入', label: '语音输入，双击开始或结束，振动后说话' }
        : { text: PTT_TEXT_PREPARING, label: '语音输入，双击开始或结束，振动后说话' };
    }
    if (state === 'recognizing') {
      return { text: PTT_TEXT.recognizing, label: '正在识别' };
    }
    if (state === 'error') {
      return { text: '识别失败，双击重试', label: '语音识别失败，双击重试' };
    }
    return { text: '开始语音输入', label: '语音输入，双击开始或结束，振动后说话' };
  },

  /** 用户设置为唯一真源，不依赖系统检测或事件形态推测。 */
  refreshScreenReaderState(): void {
    this.applyScreenReaderState(getScreenReaderVoice());
  },

  /** 应用读屏检测结果：状态变化时同步入口文案与无障碍名称。 */
  applyScreenReaderState(open: boolean): void {
    if (this.screenReaderOn === open) {
      return;
    }
    this.clearHoldTimers();
    this.holdGesture?.reset();
    this.ptt?.setScreenReaderMode(open);
    this.speech?.interrupt();
    appContext.voicePrompts.stop();
    this.screenReaderOn = open;
    this.setData({ screenReaderVoice: open });
    const state = this.ptt?.currentState ?? 'idle';
    const micReady = state === 'recording' && this.data.pttStateText !== PTT_TEXT_PREPARING;
    const texts = this.pttTextsFor(state, micReady);
    this.setData({ pttStateText: texts.text, pttA11yLabel: texts.label });
  },

  async handleFinal(text: string): Promise<void> {
    if (this.parseInFlight || this.intents === null) {
      return;
    }
    this.parseInFlight = true;
    const epoch = ++this.voiceEpoch;
    this.setData({ voiceParsing: true, voiceStatus: '正在理解，请稍候' });
    this.setData({ recognized: text, partial: '', reply: '正在理解…' });
    try {
      // 本地规则优先：命中即分发既有动作路径，不产生网络请求（决策 D1）
      const local = matchLocalIntent(text, appContext.map.places);
      if (local !== null) {
        log.info(
          local.needsClarification
            ? '本地模糊命中，直接给候选卡'
            : '本地规则命中',
          {
            text,
            intent: local.intent,
            action: local.action?.name ?? null,
            candidates: local.candidates.length,
          },
        );
        if (epoch !== this.voiceEpoch) return;
        this.applyIntent(local);
        return;
      }
      log.info('本地零命中，转服务端大模型', { text });
      const result = await this.intents.parse(text, undefined, this.intentLocation());
      if (epoch !== this.voiceEpoch) return;
      log.info('服务端意图结果', {
        text,
        intent: result.intent,
        action: result.action?.name ?? null,
        needsClarification: result.needsClarification,
      });
      this.applyIntent(result);
    } catch (err) {
      if (epoch !== this.voiceEpoch) return;
      const message = err instanceof ApiClientError ? describeError(err.code) : '网络异常，请重试';
      log.warn('意图解析失败', err);
      this.setData({ reply: message });
      this.speech?.enqueue(message, 'normal', null, 'map:intent-error');
    } finally {
      if (epoch === this.voiceEpoch) {
        this.parseInFlight = false;
        this.setData({ voiceParsing: false });
      }
    }
  },

  /** 意图请求随带的定位：仅导航中有可信位置时提供（spec REQ-VOICE-12）。 */
  intentLocation(): IntentLocationPayload | null {
    const pos = this.navPosition;
    if (pos === null) {
      return null;
    }
    return {
      x: pos.x,
      y: pos.y,
      floorId: pos.floorId,
      confidence: this.navConfidence > 0 ? this.navConfidence : 0.5,
      headingDeg: this.headingDeg ?? null,
    };
  },

  applyIntent(result: IntentParseResponse): void {
    // 先取上一条播报：responseText 入队后会覆盖重播源（「再说一遍」用）
    const previousSpeech = this.speech?.lastSpokenText ?? null;
    const target = resolveIntentTarget(result);
    const destination = result.destination ?? null;
    // 「开始导航」语音命令：是否复用预览由客户端状态决定，不走通用答复播报
    if (target === 'start') {
      this.setData(
        {
          reply: result.responseText,
          candidates: [],
          destination: null,
          navConfirmReady: false,
        },
        () => this.refreshTapExclusions(),
      );
      this.handleStartNavigationIntent();
      return;
    }
    // 目的地唯一命中：先只读规划 + 播概要，概要先于「是否开始导航」的确认询问
    const previewNavigate =
      target === null &&
      destination !== null &&
      !result.needsClarification &&
      !this.data.navActive;
    const inPreview = this.navPhase === 'previewing' || this.navPhase === 'awaiting-confirm';
    // 场景二等待目的地期间：用户改问其他事情（且不是在澄清候选）时放弃等待，
    // 让本轮答复正常播报；取消/结束仍交给 endNavByVoice 播报。
    if (
      this.navPhase === 'awaiting-destination' &&
      !result.needsClarification &&
      destination === null &&
      target !== 'repeat' &&
      target !== 'cancel' &&
      target !== 'end'
    ) {
      this.clearAwaitDestinationTimer();
      this.navPhase = 'idle';
    }
    // 「再说一遍」保留预览（重播当前概要/询问）；取消/结束交给 endNavByVoice 播报；
    // 其余意图接管本次预览：静默清理，避免确认按钮悬空且在超时后才消失。
    const keepPreview = inPreview && target === 'repeat';
    let handoff = false;
    if (inPreview && !previewNavigate && !keepPreview && target !== 'cancel' && target !== 'end') {
      this.cancelPreviewSilently();
      handoff = true;
    }
    this.setData(
      {
        reply: result.responseText,
        candidates: result.needsClarification ? result.candidates : [],
        // 预览阶段不展示「开始导航」按钮（navConfirmReady 控制），避免提前确认
        destination: previewNavigate ? null : keepPreview ? this.data.destination : destination,
        navConfirmReady: keepPreview ? this.data.navConfirmReady : false,
      },
      () => this.refreshTapExclusions(), // 居中卡出现 → 整体作为排除区
    );
    if (previewNavigate && destination !== null) {
      // 不再播「请确认」：由预览流程接管（规划提示 → 概要 → 确认询问）
      void this.beginRoutePreview(destination);
      return;
    }
    // 接管预览时用 turn 抢占正在播的概要，保证新意图的答复立即播报
    this.speech?.enqueue(result.responseText, handoff ? 'turn' : 'normal', null, 'map:intent-reply');
    if (target === 'location') {
      // responseText 是过渡话术（"请稍等，正在查询…"），随后补播真实位置
      void this.answerLocation();
    } else if (target === 'surroundings') {
      // 查周边：现场定位后补播"左前方3米有…"，与查位置是两条不同话术
      void this.answerSurroundings();
    } else if (target === 'cancel') {
      this.endNavByVoice('user_cancelled');
    } else if (target === 'end') {
      this.endNavByVoice('user_finished');
    } else if (target === 'repeat') {
      this.repeatSpeech(previousSpeech);
    }
  },

  /**
   * 语音结束导航：取消/结束共用一条执行路径。
   * 预览/等待确认阶段尚未建会话：直接清理预览（不创建、不上报）。
   * 无进行中会话时明确告知，避免"回了话却没动作"的悬空反馈。
   */
  endNavByVoice(reason: 'user_cancelled' | 'user_finished'): void {
    if (
      this.navPhase === 'awaiting-destination' ||
      this.navPhase === 'previewing' ||
      this.navPhase === 'awaiting-confirm'
    ) {
      this.finishPreview('已取消导航。如需导航，请重新说出目的地。');
      return;
    }
    const controller = this.controller;
    if (controller === null || this.sessionEnded || controller.sessionId === null) {
      this.announceVoiceHint('当前没有进行中的导航');
      return;
    }
    this.setData({
      stateText: reason === 'user_cancelled' ? '正在取消导航…' : '正在结束导航…',
    });
    controller.endSession(reason).catch((err) => {
      log.warn('语音结束导航失败', err);
      this.setData({ stateText: NAV_TEXT.active });
      this.announceVoiceHint('结束导航失败，请重试');
    });
  },

  /** 「再说一遍」：重播上一条播报（不重播本轮过渡话术本身）。 */
  repeatSpeech(previous: string | null): void {
    if (previous === null || previous.trim().length === 0) {
      this.announceVoiceHint('暂时没有可重复的播报');
      return;
    }
    // 直接入队：SpeechQueue 会把重播源更新为该条，连续「再说一遍」仍播同一条
    this.speech?.enqueue(previous, 'normal', null, 'map:replay');
  },

  /** 「我在哪儿」：单次定位 → 按质量策略播报（FE-01，失败播安全文案）。 */
  async answerLocation(): Promise<void> {
    return this.answerPosition('location');
  },

  /** 「周边有什么」：同一条定位链路，服务端附带 8m 内周边清单后播报。 */
  async answerSurroundings(): Promise<void> {
    return this.answerPosition('surroundings');
  },

  async answerPosition(kind: LocationQueryKind): Promise<void> {
    if (this.locationInFlight || this.locationQuery === null) {
      return;
    }
    this.locationInFlight = true;
    const fallback =
      kind === 'surroundings' ? SURROUNDINGS_UNRESOLVED_TEXT : UNRESOLVED_TEXT;
    // 朝向传感器只在导航态常驻；单次查周边需要临时读一次绝对方位，
    // 否则 LocationQueryService 只能降级为“暂时无法判断方向”。
    let temporaryHeading = false;
    try {
      if (kind === 'surroundings' && this.headingDeg === null && this.headingSource === null) {
        const raw = await readCompassOnce(this.getHeadingApi());
        const heading =
          raw === null ? null : applyHeadingOffset(raw, appContext.getHeadingOffsetSetting());
        log.info(
          '查周边罗盘朝向',
          heading === null ? '不可用' : `${Math.round(heading)}°（原始 ${Math.round(raw!)}°）`,
        );
        if (heading !== null && this.headingSource === null && this.headingDeg === null) {
          this.headingDeg = heading;
          temporaryHeading = true;
        }
      }
      const text = await this.locationQuery.query(kind);
      if (text === null || !this.data.mapReady) {
        return; // 并发被忽略 / 页面已不可用
      }
      this.setData({ reply: text });
      this.speech?.enqueue(text, 'normal', null, 'map:location-query');
    } catch (err) {
      log.warn(kind === 'surroundings' ? '周边查询异常' : '位置查询异常', err);
      this.setData({ reply: fallback });
      this.speech?.enqueue(fallback, 'normal', null, 'map:location-query-fallback');
    } finally {
      if (temporaryHeading && this.headingSource === null) {
        this.headingDeg = null;
      }
      this.locationInFlight = false;
    }
  },

  /** 澄清候选点选：优先按 placeId 直选（同名地点如三间「教师办公室」靠名字会再次歧义）。 */
  onPickCandidate(event: WechatMiniprogram.TouchEvent): void {
    const placeId = event.currentTarget.dataset.placeid;
    const name = event.currentTarget.dataset.name;
    if (typeof placeId === 'string' && placeId.length > 0) {
      const place = appContext.map.places.find((p) => p.placeId === placeId);
      if (place !== undefined) {
        // 点选澄清：与语音唯一命中同一条路径——先规划 + 播概要，再确认是否开始导航
        void this.beginRoutePreview({
          placeId: place.placeId,
          name: place.name,
          floorId: place.floorId,
        });
        return;
      }
    }
    if (typeof name === 'string' && name.length > 0) {
      void this.handleFinal(name);
    }
  },

  /**
   * 用户确认开始导航（概要已播完、确认卡已出现）：创建会话 → 首条指令。
   * 概要不重播（已在预览阶段播完），会话创建后由 controller 直接进入引导。
   */
  onStartNav(): void {
    if (this.navPhase !== 'awaiting-confirm' || this.data.navActive) {
      return;
    }
    const destination = this.navDestination;
    if (destination === null || this.previewOrigin === null) {
      return;
    }
    void this.startNavigation(destination);
  },

  /**
   * 「开始导航」语音命令（视障模式主入口，普通模式也可用）：
   * - 场景一：存在 10 秒内的有效预览 → 直接建会话，不再询问目的地；
   * - 场景二：没有可复用预览 → 语音询问目的地，之后「规划 → 概要 → 直接开始」。
   * 过期预览一律作废，绝不用过期路线启动导航。
   */
  handleStartNavigationIntent(): void {
    if (this.data.navActive || this.navPhase === 'navigating') {
      this.announceVoiceHint('当前已在导航中');
      return;
    }
    // 判定口径与差值留痕：真机上「为什么不复用预览」只看这一条即可定位
    log.info('开始导航意图', {
      phase: this.navPhase,
      sinceLastPromptMs:
        this.previewFreshAt === null ? null : Date.now() - this.previewFreshAt,
      canReuse: this.canReusePreview(),
    });
    // 概要仍在播：不打断播报，标记「播完即开始」（用户已明确表达开始意愿）
    if (
      this.navPhase === 'previewing' &&
      this.navDestination !== null &&
      this.previewOrigin !== null
    ) {
      this.previewAutoStart = true;
      this.setData({ reply: '路线概要播报完毕后将直接开始导航。' });
      log.info('开始导航意图到达时概要仍在播报：播完直接开始');
      return;
    }
    // 确认询问阶段：预览仍有效（30s 确认超时未到），直接开始。
    // 用户开口说「开始导航」会打断询问播报，此时 10s 窗口不会刷新；
    // 若仍按 canReusePreview 判定，会把刚规划好的预览作废并重新追问目的地。
    if (
      this.navPhase === 'awaiting-confirm' &&
      this.navDestination !== null &&
      this.previewOrigin !== null
    ) {
      const destination = this.navDestination;
      log.info('开始导航意图到达于确认询问阶段：直接开始', destination.name);
      void this.startNavigation(destination);
      return;
    }
    if (this.canReusePreview()) {
      const destination = this.navDestination;
      if (destination !== null) {
        log.info('开始导航：复用近期预览', destination.name);
        void this.startNavigation(destination);
        return;
      }
    }
    // 预览已过期/未完成：作废，重新询问目的地
    if (this.navPhase === 'previewing' || this.navPhase === 'awaiting-confirm') {
      this.cancelPreviewSilently();
    }
    this.awaitNavigationDestination();
  },

  /**
   * 预览可复用判定：10 秒窗口自「应用说完最后一句话」起算（previewFreshAt），
   * 与预览返回、开始播报的时刻无关；同时要求起点与目的地完整。
   */
  canReusePreview(): boolean {
    return (
      this.navPhase === 'awaiting-confirm' &&
      this.previewFreshAt !== null &&
      this.previewOrigin !== null &&
      this.navDestination !== null &&
      Date.now() - this.previewFreshAt <= PREVIEW_REUSE_WINDOW_MS
    );
  },

  /** 场景二：语音询问目的地，等待用户下一句；超时/取消走正常清理。 */
  awaitNavigationDestination(): void {
    this.clearAwaitDestinationTimer();
    this.navPhase = 'awaiting-destination';
    this.setData(
      {
        reply: '您要去哪里？',
        candidates: [],
        destination: null,
        navConfirmReady: false,
      },
      () => this.refreshTapExclusions(),
    );
    this.speech?.enqueue('好的，您要去哪里？', 'turn', null, 'map:await-destination');
    this.awaitDestinationTimer = setTimeout(() => {
      this.awaitDestinationTimer = null;
      if (this.navPhase !== 'awaiting-destination') {
        return;
      }
      this.finishPreview('长时间未听到目的地，已取消。如需导航，请重新说“开始导航”。');
    }, AWAIT_DESTINATION_TIMEOUT_MS);
  },

  clearAwaitDestinationTimer(): void {
    if (this.awaitDestinationTimer !== null) {
      clearTimeout(this.awaitDestinationTimer);
      this.awaitDestinationTimer = null;
    }
  },

  /**
   * 目的地已确定（语音唯一命中或候选点选）：先只读规划 + 播报概要，再询问是否开始。
   * 本阶段只读：不创建会话、不上报 updates；取不到可信起点或规划失败则清理并提示重来。
   */
  async beginRoutePreview(destination: PlaceCandidate): Promise<void> {
    if (this.data.navActive || !this.data.mapReady) {
      return;
    }
    // 导航接管：启动楼层探测的迟到结果不得再切层
    this.startupFloorProbeRun += 1;
    // 场景二：用户先说「开始导航」再给目的地 → 概要播完直接开始，不再二次确认
    const autoStart = this.navPhase === 'awaiting-destination';
    this.clearAwaitDestinationTimer();
    const token = (this.previewToken += 1);
    this.clearPreviewTimers();
    this.previewAutoStart = autoStart;
    this.previewFreshAt = null;
    this.navDestination = destination;
    this.previewOrigin = null;
    this.navPhase = 'previewing';
    // 新一轮预览开始：必须清掉上一段会话的结束标记。否则 handleEstimate 会在
    // 「sessionEnded」守卫处提前返回，起点永远不会交出去，12s 后误报无法定位。
    this.sessionEnded = false;
    this.overviewAnnounced = false; // 概要由预览流程播报
    this.routeForDisplay = null;
    this.setData(
      {
        reply: `正在为您规划到${destination.name}的路线，请稍候。`,
        recognized: '',
        partial: '',
        fallbackText: '',
        candidates: [],
        destination: null,
        navConfirmReady: false,
      },
      () => this.refreshTapExclusions(),
    );
    // 用 turn 抢占上一目标的概要/确认询问（normal）并清掉待播项，避免叠音
    this.speech?.enqueue(
      `正在为您规划到${destination.name}的路线。`,
      'turn',
      null,
      'map:preview-planning',
    );
    this.requestDraw();

    const ready = await this.initNavigation();
    if (token !== this.previewToken) {
      return;
    }
    if (!ready) {
      this.finishPreview('路线规划失败，请稍后重试。');
      return;
    }
    const origin = await this.waitPreviewOrigin();
    if (token !== this.previewToken) {
      return;
    }
    if (origin === null) {
      this.finishPreview('暂时无法确定您的位置，无法规划路线。请移动几步后重试。');
      return;
    }

    let preview: PreviewRouteResponse | null = null;
    try {
      const resp = await appContext.http.client.request<PreviewRouteResponse>({
        method: 'POST',
        path: '/api/v1/nav/preview',
        body: {
          siteId: appContext.map.siteId,
          mapVersion: appContext.map.requireVersion(),
          origin,
          destinationId: destination.placeId,
          userMode: 'blind',
          locale: 'zh-CN',
        },
        timeoutMs: PREVIEW_TIMEOUT_MS,
      });
      preview = resp.data;
    } catch (err) {
      if (token !== this.previewToken) {
        return;
      }
      const message =
        err instanceof ApiClientError ? describeError(err) : '路线规划失败，请重试';
      log.warn('路线预览失败', err);
      this.finishPreview(message);
      return;
    }
    if (token !== this.previewToken) {
      return;
    }
    if (preview === null || typeof preview.route?.routeId !== 'string') {
      this.finishPreview('路线规划响应异常，请重试。');
      return;
    }

    this.previewOrigin = origin;
    this.routeForDisplay = preview.route;
    this.overviewAnnounced = true;
    this.requestDraw();
    this.updateArrow();

    const overview =
      preview.overviewText ??
      buildRouteOverview(preview.route, destination.name, (floorId) =>
        appContext.map.floors[floorId]?.displayName,
      );
    log.info(
      '路线概要(预览):',
      overview,
      preview.overviewText == null ? '(模板兜底)' : '(LLM)',
    );
    this.setData({ reply: '正在播报路线概要…' });
    // 预生成概要音频：与入队/过渡音并行，长概要首次播放少等一次合成
    this.speech?.prepare(overview);
    this.speech?.enqueue(overview, 'normal', null, 'map:overview');
    // 以语音队列排空事件判定「概要播完」，再询问是否开始导航（不用固定延时）。
    // 取消/改目的地会推进 previewToken，作废本回调；不需要依赖 idle reason。
    this.speech?.notifyWhenIdle(() => {
      if (token !== this.previewToken || this.navPhase !== 'previewing') {
        return;
      }
      // 10 秒窗口的第一个起算点：概要（含其前排队的播报）全部播完的这一刻；
      // 确认询问播完后还会再刷新一次（见 askStartNavigation），窗口不被应用播报占用。
      this.previewFreshAt = Date.now();
      if (this.previewAutoStart) {
        // 场景二：用户已用「开始导航」表达意愿 → 概要播完直接开始，不再二次确认
        this.previewAutoStart = false;
        log.info('概要播报结束：直接开始导航', destination.name);
        void this.startNavigation(destination);
        return;
      }
      this.askStartNavigation(token);
    });
  },

  /** 概要播完：展示确认卡并语音询问是否开始导航；超时未确认视为拒绝（不建会话）。 */
  askStartNavigation(token: number): void {
    const destination = this.navDestination;
    if (destination === null) {
      return;
    }
    // 视障模式不显示按钮卡，只保留语音入口；普通模式按钮 + 语音两种方式都可用
    const a11y = this.data.a11yMode === 'a11y';
    this.navPhase = 'awaiting-confirm';
    this.setData(
      {
        reply: a11y
          ? '路线概要已播报完毕。说“开始导航”立即开始，说“取消”放弃。'
          : '路线概要已播报完毕，是否开始导航？',
        destination,
        navConfirmReady: !a11y,
      },
      () => this.refreshTapExclusions(),
    );
    // 普通模式不做语音确认，只展示确认卡；视障模式保留语音确认。
    if (a11y) {
      this.speech?.enqueue(
        '是否现在开始导航？说“开始导航”开始，说“取消”放弃。',
        'normal',
        null,
        'map:preview-confirm',
      );
      // 窗口起点刷新到「这句询问播完」：询问本身要播 6-8 秒，若沿用概要结束时刻，
      // 用户听完再开口时 10 秒预算已被应用自己的播报吃掉（真机现象：落到场景二）。
      this.speech?.notifyWhenIdle((reason) => {
        if (reason !== 'finished') {
          return; // 被抢占/看门狗放手不算「问完了」，不续窗口
        }
        if (token !== this.previewToken || this.navPhase !== 'awaiting-confirm') {
          return;
        }
        this.previewFreshAt = Date.now();
        log.info('确认询问播报结束：开始导航窗口重新起算');
      });
    } else {
      // 普通模式没有语音确认：窗口从确认卡展示起算。
      this.previewFreshAt = Date.now();
      log.info('普通模式无语音确认：开始导航窗口从确认卡展示起算');
    }
    this.previewConfirmTimer = setTimeout(() => {
      this.previewConfirmTimer = null;
      if (token === this.previewToken && this.navPhase === 'awaiting-confirm') {
        this.finishPreview('已超时未确认，导航已取消。如需导航，请重新说出目的地。');
      }
    }, PREVIEW_CONFIRM_TIMEOUT_MS);
  },

  /** 等待首个可信定位估计作为规划起点；超时返回 null（不规划、不建会话）。 */
  waitPreviewOrigin(): Promise<OriginPosition | null> {
    return new Promise((resolve) => {
      this.previewOriginResolve = resolve;
      this.previewOriginTimer = setTimeout(() => {
        this.previewOriginTimer = null;
        this.resolvePreviewOrigin(null);
      }, PREVIEW_ORIGIN_TIMEOUT_MS);
    });
  },

  resolvePreviewOrigin(origin: OriginPosition | null): void {
    if (this.previewOriginTimer !== null) {
      clearTimeout(this.previewOriginTimer);
      this.previewOriginTimer = null;
    }
    const resolve = this.previewOriginResolve;
    this.previewOriginResolve = null;
    resolve?.(origin);
  },

  clearPreviewTimers(): void {
    if (this.previewConfirmTimer !== null) {
      clearTimeout(this.previewConfirmTimer);
      this.previewConfirmTimer = null;
    }
    this.resolvePreviewOrigin(null);
  },

  /**
   * 建会话前重取一次当前定位：强制触发一拍立即采集，拿到可信坐标即返回；
   * 超时或只拿到不可信质量返回 null（调用方据此提示重试，绝不用旧起点建会话）。
   */
  requestFreshOrigin(timeoutMs = START_NAV_ORIGIN_TIMEOUT_MS): Promise<OriginPosition | null> {
    const pipeline = this.pipeline;
    if (pipeline === null || !pipeline.isRunning) {
      return Promise.resolve(null);
    }
    // 上一次等待作废（重复调用只保留最后一次）
    if (this.freshOriginTimer !== null) {
      clearTimeout(this.freshOriginTimer);
      this.freshOriginTimer = null;
    }
    this.freshOriginResolve?.(null);
    return new Promise((resolve) => {
      this.freshOriginResolve = resolve;
      this.freshOriginTimer = setTimeout(() => {
        this.freshOriginTimer = null;
        const pending = this.freshOriginResolve;
        this.freshOriginResolve = null;
        pending?.(null);
      }, timeoutMs);
      void pipeline.tick();
    });
  },

  resolveFreshOrigin(origin: OriginPosition): void {
    if (this.freshOriginTimer !== null) {
      clearTimeout(this.freshOriginTimer);
      this.freshOriginTimer = null;
    }
    const resolve = this.freshOriginResolve;
    this.freshOriginResolve = null;
    resolve?.(origin);
  },

  /** 静默清理预览（被其他意图接管时用，不播取消提示）。 */
  cancelPreviewSilently(): void {
    this.previewToken += 1;
    this.clearPreviewTimers();
    this.clearAwaitDestinationTimer();
    this.navPhase = 'idle';
    this.previewOrigin = null;
    this.navDestination = null;
    this.routeForDisplay = null;
    this.overviewAnnounced = false;
    this.previewFreshAt = null;
    this.previewAutoStart = false;
    this.pipeline?.stop();
    this.pipeline = null;
    this.controller?.dispose();
    this.controller = null;
    this.navSource = null;
    this.setData({ destination: null, navConfirmReady: false }, () => this.requestDraw());
  },

  /** 取消/失败/超时：不建会话，清预览状态与采集，给出可重来的提示。 */
  finishPreview(message: string): void {
    this.previewToken += 1;
    this.clearPreviewTimers();
    this.clearAwaitDestinationTimer();
    this.navPhase = 'idle';
    this.previewOrigin = null;
    this.navDestination = null;
    this.routeForDisplay = null;
    this.overviewAnnounced = false;
    this.previewFreshAt = null;
    this.previewAutoStart = false;
    this.pipeline?.stop();
    this.pipeline = null;
    this.controller?.dispose();
    this.controller = null;
    this.navSource = null;
    this.setData(
      {
        reply: message,
        candidates: [],
        destination: null,
        navConfirmReady: false,
      },
      () => {
        this.refreshTapExclusions();
        this.requestDraw();
      },
    );
    // 用 turn 抢占正在播的概要（normal）并清掉待播的确认询问，避免叠音
    this.speech?.enqueue(message, 'turn', null, 'map:preview-cancel');
  },

  /** 确认后：重取当前定位作为起点 → 创建会话 → 首条指令（概要不重播）。 */
  async startNavigation(destination: PlaceCandidate): Promise<void> {
    const controller = this.controller;
    if (controller === null || this.navStarting) {
      return;
    }
    if (this.fieldRecorder && appContext.map.places.find((p) => p.placeId === destination.placeId)?.nodeId !== 'F1-TR-0003') {
      this.finishPreview('实验目的地固定为资源教室。返回地图后请从实验入口开始。');
      return;
    }
    // 预览起点是预览早期冻结的观测：10 秒复用窗口只看「预览还剩多少价值」，
    // 不看观测年龄；服务端只接受 15 s 内的观测，确认后必须重新取一次。
    this.navStarting = true;
    const token = this.previewToken;
    this.setData({ reply: '正在获取当前位置…' });
    const freshOrigin = await this.requestFreshOrigin();
    this.navStarting = false;
    if (token !== this.previewToken) {
      return; // 等待期间用户取消/改目的地
    }
    if (freshOrigin === null) {
      this.finishPreview('没能获取到当前位置，请稍作停留后重试。');
      return;
    }
    if (this.fieldRecorder !== null) {
      try {
        this.fieldRecorder.start();
        this.updateFieldCheckpoint();
      } catch (error) {
        this.finishPreview(String(error));
        return;
      }
    }
    this.previewToken += 1; // 确认后：迟到的预览回调不再生效
    this.clearPreviewTimers();
    this.clearAwaitDestinationTimer();
    this.navPhase = 'navigating';
    this.sessionEnded = false;
    this.elevatorPaused = false;
    this.overviewAnnounced = true; // 概要已在预览阶段播完，不重播
    this.previewFreshAt = null;
    this.previewAutoStart = false;
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
    this.headingSmoother!.reset();
    // 收起意图居中卡，切换到导航 HUD
    this.setData(
      {
        navActive: true,
        ended: false,
        state: 'idle' as NavState,
        stateText: NAV_TEXT.idle,
        instruction: '正在确定您的位置…',
        remainingText: '',
        destinationName: destination.name,
        destination: null,
        candidates: [],
        reply: '',
        partial: '',
        recognized: '',
        navConfirmReady: false,
      },
      () => this.refreshTapExclusions(),
    );
    this.syncArrowLifecycle(); // 视障模式：导航开始即启动朝向传感器
    this.enterNavZoom(); // 视野从全路线收窄到局部（REQ-NAV 展示层）

    try {
      await controller.createSession(destination, freshOrigin, 'blind', {
        overviewPlayed: true,
        // 场景一（确认后启动）与场景二（概要后自动启动）共用：首条指令前先播过渡
        transitionClip: NAV_START_TRANSITION_CLIP,
      });
      this.setData({ stateText: NAV_TEXT.active });
    } catch (err) {
      this.finishFieldNavigation('aborted', '导航会话创建失败');
      const message = err instanceof ApiClientError ? describeError(err) : '无法开始导航，请重试';
      log.warn('会话创建失败', err);
      this.sessionEnded = true;
      this.onDismissNav();
      this.setData({ reply: message });
      this.speech?.enqueue(message, 'turn', null, 'map:create-failed');
    }
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
   * 预览/导航共用的定位管道 + 会话控制器（幂等）。
   * 预览阶段以 preview 档采集规划起点；确认后由 controller 切到 navigating 档。
   * 返回 false 表示地图/采集启动失败（调用方据此清理预览并提示重试）。
   */
  async initNavigation(): Promise<boolean> {
    if (this.speech === null) {
      return false;
    }
    try {
      await appContext.map.ensureLoaded();
    } catch (err) {
      const message =
        err instanceof ApiClientError ? describeError(err.code) : '地图加载失败，请重试';
      log.warn('导航前地图加载失败', err);
      this.setData({ reply: message });
      return false;
    }

    // 复用已建好的管道（用户取消预览后再次说目的地时，不必重建）
    if (this.controller !== null && this.pipeline !== null) {
      const running = this.pipeline;
      if (running.isRunning) {
        running.setMode('preview');
      } else {
        try {
          await running.start('preview');
        } catch (err) {
          log.warn('定位采集启动失败', err);
          return false;
        }
      }
      return true;
    }

    const source = createBeaconSource(appContext.dataSource, appContext.map);
    this.navSource = source;
    const pipeline = new LocationPipeline({
      client: appContext.http.client,
      map: appContext.map,
      source,
      sequence: new SequenceCounter(),
      // 按机型选择聚合窗口（iOS 2s / 安卓 1.5s）：扫描节拍由系统决定，窗口是可控量。
      windowMs: getScanProfile().windowMs,
      // 指纹库开关（默认关闭）：每拍读取，关闭时请求体与旧版完全一致
      useFingerprintDb: () => appContext.getFingerprintDbSetting(),
      experiment: () => this.fieldRecorder?.run.selection ?? null,
      onRawSample: (sample) => this.fieldRecorder?.addSample(sample),
      onEstimate: (estimate, meta) => this.handleEstimate(estimate, meta),
      onUnusable: (err) => {
        this.fieldRecorder?.event('unavailable', { code: err.code });
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
      stop: () => {
        // The navigation session may end before the participant reaches the physical finish line.
        if (this.fieldRecorder && this.controller?.state === 'arrived') return;
        pipeline.stop();
      },
      startNavigating: async () => {
        if (pipeline.isRunning) {
          pipeline.setMode('navigating');
        } else {
          await pipeline.start('navigating');
        }
      },
      // 电梯暂停期间清楼层先验：由服务端按可见信标多数判层，地图随扫描楼层自动切换
      clearFloorHint: () => pipeline.clearFloorHint(),
    };
    this.turnHaptics = new TurnHaptics({
      vibrateShort,
      vibrateLong: () => {
        const instructionId = this.turnHapticInstructionId;
        if (instructionId) this.fieldRecorder?.event('turn_haptic', { instructionId });
        vibrateLong();
      },
    });
    this.controller = new NavSessionController({
      createJournal: createNavJournal(currentServiceBaseUrl(), appContext.map.siteId),
      client: appContext.http.client,
      map: appContext.map,
      speech: this.speech,
      pipeline: pipelineControl,
      experiment: () => this.fieldRecorder?.run.selection,
      onSessionCreated: (created) => {
        if (!this.fieldRecorder) return;
        this.fieldRecorder.event('session', {
          sessionId: created.sessionId, route: created.route, turnHapticsEnabled: this.data.a11yMode === 'a11y',
        });
        this.setData({ fieldSessionReady: true });
        this.updateFieldCheckpoint();
      },
      // 电梯口判距：把指令触发节点解析成坐标（地图包节点表）
      resolveNode: (nodeId) => resolveTurnNode(appContext.map.floors, nodeId),
      onElevatorPause: ({ text }) => {
        this.elevatorPaused = true;
        this.setData({
          stateText: '导航已暂停（电梯）',
          instruction: text,
          arrowVisible: false,
          arrowAlert: false,
        });
      },
      onElevatorResume: ({ text }) => {
        this.elevatorPaused = false;
        this.setData({ stateText: NAV_TEXT.active, instruction: text });
        this.updateArrow();
      },
      onStateChange: (state) => {
        this.setData({ state, stateText: NAV_TEXT[state] }, () => this.syncTurnHaptics());
        if (state === 'recovering') {
          vibrateLong(); // 震动模式：脱离路线需要引起注意
          // 位置不可信：箭头隐藏（不显示可能错误的方向）
          this.setData({ arrowVisible: false, arrowAlert: false });
        } else if (state === 'active') {
          this.syncArrowLifecycle();
        }
      },
      onRoute: (route, overviewText) => {
        this.fieldRecorder?.event('route', { route });
        // 新流程：概要在 API-10 预览阶段已播完（overviewAnnounced=true），此处不重播；
        // 无预览的历史/异常路径才在此入队概要，形成「概要 → 首条指令」顺序。
        if (!this.overviewAnnounced && this.navDestination !== null) {
          this.overviewAnnounced = true;
          // 后端大模型文案优先；未配置/超时/校验失败时回退本地模板，保证概要必播
          const overview =
            overviewText ??
            buildRouteOverview(route, this.navDestination.name, (floorId) =>
              appContext.map.floors[floorId]?.displayName,
            );
          this.speech?.prepare(overview);
          this.speech?.enqueue(overview, 'normal', null, 'map:overview');
          // 概要播放期间 HUD 显示状态占位；首条指令等队列播完再由 onInstruction 写入
          this.setData({ instruction: '正在播报路线概要…' });
          log.info('路径概要:', overview, overviewText === null ? '(模板兜底)' : '(LLM)');
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
        this.stopTurnHaptics(); // 新路线：旧转弯的逼近节拍立即作废，等新指令重新装载
        this.updateRemainingDistance(); // 路线就绪：按当前位置刷新剩余距离
        this.requestDraw(); // 导航视觉：重绘路线（REQ-NAV 展示层）
        this.updateArrow(); // 路线就绪后按当前位置算箭头方向
      },
      // 每次更新都刷新 HUD 的当前指令（此前只在 onRoute 里写 segments[0]，
      // 导致起步后文案一直停在第一条，后续转弯/直行指令只播不显示）
      onInstruction: (instruction) => {
        if (instruction !== null && this.currentInstruction?.instructionId !== instruction.instructionId) {
          const turn = instruction.trigger.nodeId ?
            resolveTurnNode(appContext.map.floors, instruction.trigger.nodeId) : null;
          this.fieldRecorder?.event('instruction', { instruction, turn });
        }
        this.currentInstruction = instruction;
        if (instruction !== null) {
          this.setData({ instruction: instruction.text });
        }
        this.syncTurnHaptics(); // 仅转弯指令会启动逼近节拍，其余指令在这里停表
      },
      onEnded: ({ reason, byArrival }) => {
        if (!this.sessionEnded) this.fieldRecorder?.event('end', { reason, byArrival });
        this.sessionEnded = true;
        this.stopTurnHaptics(); // 导航结束/到达：立即停节拍并清理定时器
        this.currentInstruction = null;
        if (byArrival && this.fieldRecorder) {
          this.stopHeading();
          this.setData({ fieldSystemArrived: true, fieldSaving: false, instruction: '', remainingText: '', arrowVisible: false });
          return;
        }
        pipeline.stop();
        const completed = reason === 'user_finished' && this.data.fieldSaving && this.data.fieldActualArrived;
        if (!this.finishFieldNavigation(completed ? 'complete' : 'aborted', reason ?? '导航结束')) {
          this.setData({ fieldSaving: false });
          return;
        }
        // 到达播报由 controller 入队；此处只清理界面，不打断播报，直接回待命。
        this.onDismissNav();
      },
    });

    try {
      await pipeline.start('preview');
    } catch (err) {
      log.warn('定位采集启动失败', err);
      this.setData({ reply: '定位启动失败，请检查蓝牙后重试' });
      return false;
    }
    return true;
  },

  /**
   * 定位入口：可信位置驱动显示跟随。
   * - 预览阶段：只把首个可信估计交给预览流程作为规划起点（不建会话、不上报）；
   * - 导航阶段：交给 controller 上报（不可信估计只报 quality 转 recovering）。
   */
  handleEstimate(
    estimate: PositionEstimateResponse,
    meta?: { visibleBeaconCount: number; requestMs?: number },
  ): void {
    if (!this.data.fieldSystemArrived) {
      this.fieldRecorder?.event('estimate', {
        position: estimate.position, source: estimate.positionSource,
        paramsVersion: estimate.paramsVersion, fallbackReason: estimate.fallbackReason,
        observedAt: estimate.observedAt, quality: estimate.quality, confidence: estimate.confidence,
        visibleBeaconCount: meta?.visibleBeaconCount, requestMs: meta?.requestMs,
      });
    }
    if (meta) {
      this.recordCalibration(estimate, meta.visibleBeaconCount);
    }
    const position = estimate.position;
    // 建会话前重取起点：只要能拿到可信坐标就交出去（预览/已确认/导航阶段均可能等到）
    if (
      this.freshOriginResolve !== null &&
      position !== undefined &&
      position !== null &&
      (estimate.quality === 'reliable' || estimate.quality === 'degraded')
    ) {
      this.resolveFreshOrigin({
        x: position.x,
        y: position.y,
        floorId: position.floorId ?? null,
        confidence: estimate.confidence,
        observedAt: estimate.observedAt,
      });
    }
    if (position !== undefined && position !== null) {
      this.locationWarned = false; // 可信位置恢复：允许下一次不可用重新记日志
      // 只有 reliable/degraded 才带坐标 → 恢复态(unreliable)天然不推进显示
      this.navPosition = {
        floorId: position.floorId ?? this.navPosition?.floorId ?? this.floorList[0]?.floorId ?? '1',
        x: position.x,
        y: position.y,
      };
      this.navConfidence = estimate.confidence;
      this.followPosition();
      this.updateArrow(); // 可信位置更新 → 重算当前折线段方向
      this.syncTurnHaptics(); // 1s 一拍：刷新到当前转弯的剩余沿线距离（两次上报之间由节拍器外推）
      this.updateRemainingDistance(); // HUD 剩余距离随定位动态更新
    } else {
      // 本拍没有可信坐标：停表并清掉剩余距离（避免展示过期数值）
      this.stopTurnHaptics();
      this.setRemainingText('');
    }
    const controller = this.controller;
    if (controller === null || this.sessionEnded) {
      return;
    }
    if (this.navPhase === 'previewing') {
      // 预览只取规划起点：确认前绝不创建会话、不上报 updates
      if (
        (estimate.quality === 'reliable' || estimate.quality === 'degraded') &&
        position !== undefined &&
        position !== null
      ) {
        this.resolvePreviewOrigin({
          x: position.x,
          y: position.y,
          floorId: position.floorId ?? null,
          confidence: estimate.confidence,
          observedAt: estimate.observedAt,
        });
      }
      return;
    }
    if (this.navPhase !== 'navigating' || controller.sessionId === null) {
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
        log.info('定位楼层变化：地图自动切换', `${vp.floorId} → ${pos.floorId}`);
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
    // 导航态每次可信估计把视口中心对准位置（1s 节奏）
    const { sx, sy } = vp.toScreen(pos.x, pos.y);
    vp.panBy(this.canvasSize.w / 2 - sx, this.canvasSize.h / 2 - sy);
    this.requestDraw();
  },

  /**
   * HUD 剩余距离：当前位置沿路线投影到终点，按整米节流更新。
   * 位置/路线缺失或无法投影时清空，绝不展示过期数值。
   */
  updateRemainingDistance(): void {
    if (!this.data.navActive) {
      this.setRemainingText('');
      return;
    }
    const route = this.routeForDisplay;
    const pos = this.navPosition;
    if (route === null || pos === null) {
      this.setRemainingText('');
      return;
    }
    const remaining = remainingRouteDistanceM(route.polyline, pos);
    this.setRemainingText(
      remaining === null ? '' : `剩余 ${Math.max(0, Math.round(remaining))} 米`,
    );
  },

  setRemainingText(text: string): void {
    if (this.data.remainingText !== text) {
      this.setData({ remainingText: text });
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
    this.speech?.enqueue(hint, 'normal', null, 'map:end-hint');
    setTimeout(() => {
      if (this.lastTapAt !== 0 && !this.sessionEnded) {
        this.setData({ stateText: NAV_TEXT[this.data.state as NavState] });
      }
    }, DOUBLE_TAP_WINDOW_MS);
  },

  /**
   * 收起意图/反馈居中卡：清空面板状态，避免关闭后残留遮挡地图（REQ-PAGE）。
   * 若处于路线预览/等待确认，关闭卡片等同放弃本次导航：不建会话，清理预览。
   */
  onDismissIntent(): void {
    if (
      this.navPhase === 'awaiting-destination' ||
      this.navPhase === 'previewing' ||
      this.navPhase === 'awaiting-confirm'
    ) {
      this.finishPreview('已取消导航。如需导航，请重新说出目的地。');
      return;
    }
    this.clearIntentPanels();
  },

  /** 仅清空意图面板文案（不触碰进行中的预览/导航）。 */
  clearIntentPanels(): void {
    this.setData(
      {
        recognized: '',
        partial: '',
        reply: '',
        fallbackText: '',
        candidates: [],
        destination: null,
        navConfirmReady: false,
      },
      () => this.refreshTapExclusions(),
    );
  },

  /** 导航结束后关闭 HUD，回到待命地图。 */
  onDismissNav(): void {
    this.navPhase = 'idle';
    this.elevatorPaused = false;
    this.stopTurnHaptics();
    this.currentInstruction = null;
    this.previewToken += 1;
    this.clearPreviewTimers();
    this.clearAwaitDestinationTimer();
    this.previewOrigin = null;
    this.navDestination = null;
    this.routeForDisplay = null;
    this.navPosition = null;
    this.navSource = null;
    this.previewFreshAt = null;
    this.previewAutoStart = false;
    this.pipeline?.stop();
    this.pipeline = null;
    this.controller?.dispose();
    this.controller = null;
    this.setData({
      navActive: false,
      ended: false,
      instruction: '',
      remainingText: '',
      destinationName: '',
      state: 'idle' as NavState,
      stateText: NAV_TEXT.idle,
      navConfirmReady: false,
    });
    // 关键：导航期间累积的 fallbackText/reply 若不清空，
    // navActive=false 后居中卡会因 fallbackText 非空重新弹出遮挡地图
    this.clearIntentPanels();
    this.syncArrowLifecycle(); // 关闭导航 HUD：停传感器并隐藏箭头
    this.requestDraw();
  },

  onBeginFieldNavigation(): void {
    if (!this.fieldRecorder || this.data.fieldSaveRetry || !this.data.mapReady || this.navPhase !== 'idle') return;
    const place = appContext.map.places.find((row) => row.nodeId === 'F1-TR-0003');
    if (!place || appContext.map.mapVersion !== this.fieldRecorder.run.plan.mapVersion) {
      wx.showToast({ title: '实验路线或冻结地图不可用', icon: 'none' });
      return;
    }
    void this.beginRoutePreview({ placeId: place.placeId, name: place.name, floorId: place.floorId });
  },

  onReturnFieldExperiment(): void {
    if (this.data.fieldSaveRetry) return;
    this.fieldRecorder = null;
    this.onDismissIntent();
    this.setData({ fieldMethod: '', fieldRunFinished: false });
    wx.navigateTo({ url: `/pages/collect/experiment/experiment?task=NV&runId=${this.data.fieldRunId}` });
  },

  updateFieldCheckpoint(): void {
    const recorder = this.fieldRecorder;
    if (!recorder) return;
    const total = recorder.run.plan.points.length;
    const next = recorder.run.plan.points[recorder.count]?.pointId;
    this.setData({ fieldCheckpoint: `${recorder.count}/${total}${next ? ` · 下一点 ${next}` : ''}`,
      fieldUndo: recorder.canUndo });
    this.requestDraw();
  },

  onFieldMark(): void {
    const recorder = this.fieldRecorder;
    if (!recorder || !this.data.fieldSessionReady || this.data.fieldSaving ||
      (this.sessionEnded && !this.data.fieldSystemArrived)) return;
    const count = recorder.count;
    recorder.mark();
    if (recorder.count === count) return;
    vibrateShort();
    this.updateFieldCheckpoint();
    setTimeout(() => this.updateFieldCheckpoint(), 2050);
  },

  onFieldUndo(): void {
    this.fieldRecorder?.undo();
    this.updateFieldCheckpoint();
  },

  onConfirmFieldArrival(): void {
    if (!this.data.fieldSessionReady || this.data.fieldSaving || !this.fieldRecorder?.confirmArrival()) return;
    this.setData({ fieldActualArrived: true });
    vibrateShort();
  },

  onCompleteFieldRoute(): void {
    const controller = this.controller;
    if (!this.fieldRecorder || !this.data.fieldActualArrived || this.data.fieldSaving) return;
    if (this.sessionEnded) {
      if (this.finishFieldNavigation('complete', this.data.fieldSystemArrived ? 'arrived' : 'user_finished')) {
        this.onDismissNav();
      }
      return;
    }
    if (!controller?.sessionId) return;
    this.setData({ fieldSaving: true });
    void controller.endSession('user_finished').catch(() => {
      this.setData({ fieldSaving: false });
      wx.showToast({ title: '尚未结束路线，请重试', icon: 'none' });
    });
  },

  finishFieldNavigation(status: 'complete' | 'aborted', reason: string): boolean {
    if (!this.fieldRecorder) return true;
    if (!this.fieldRecorder.finish(status, reason)) return false;
    this.fieldRecorder = null;
    this.setData({ fieldRunFinished: true, fieldUndo: false, fieldSessionReady: false, fieldSaving: false,
      fieldStorageIssue: '', fieldSaveRetry: false });
    return true;
  },

  onRetryFieldSave(): void {
    if (this.data.fieldSaveRetry && this.finishFieldNavigation('aborted', '重试本地保存')) {
      this.onDismissNav();
    }
  },
});
