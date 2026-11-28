/**
 * 导航会话控制器（C7）：编排 API-06..09、状态机副作用与语音播报。
 * - API-06 创建：Idempotency-Key（uuid）与冻结 body 一起暂存，失败重试复用，2xx 后清除；
 * - API-07 更新：会话内 sequence 单调；在途锁 + 仅保留最新待发估计；
 *   409 STALE_NAV_UPDATE → API-08 重对齐后继续；409 SESSION_NOT_ACTIVE → 查询收尾；
 * - 事件映射：instruction→按优先级入队（tracker 去重）、reroute→clearNormal+
 *   播新首条+更新 route、relocalizing→进入 recovering 时 safety 提示一次
 *   （期间不停止采集，可信位置回来后由服务端恢复 active）、
 *   arrived→按 confidence 播「已到达/已到达附近」+停采集；
 * - 前台恢复（FE-05 次序）：先 API-08 校验 status/mapVersion/sequence →
 *   重启采集 → 重播有效指令；切后台先停采集；
 * - 客户端从不发送 reason=arrived（到达判定权在服务端）。
 */
import { NAV_UPDATE_TIMEOUT_MS } from '../../config/env';
import { ApiClientError } from '../http/errors';
import type { ApiClient } from '../http/api-client';
import type { SpeechIdleReason, SpeechQueue } from '../speech/speech-queue';
import { staticInstructionClip } from '../speech/static-clips';
import { InstructionTracker } from './instruction-tracker';
import type { NavCreateJournal } from './create-journal';
import {
  NavStateMachine,
  type NavEffects,
  type NavState,
} from './nav-state-machine';
import { uuidV4 } from '../../utils/ids';
import { createLogger } from '../../utils/logger';
import type {
  CreateSessionRequest,
  CreateSessionResponse,
  EndReason,
  EndSessionResponse,
  LocationUpdate,
  NavigationInstruction,
  NavUpdateRequest,
  NavUpdateResponse,
  OriginPosition,
  PlaceCandidate,
  PositionEstimateResponse,
  RouteInfo,
  SessionQueryResponse,
  UserMode,
} from '../../typings/api';

const log = createLogger('nav');

interface LocationDiagnostics {
  estimateId?: string;
  requestId?: string | null;
  code?: string;
  reason?: unknown;
  matchedBeaconCount?: unknown;
  submittedBeaconCount?: unknown;
}

/** 到达置信度低于该值播「已到达附近」。 */
const ARRIVAL_NEAR_CONFIDENCE = 0.75;
/** 概要窗口最长等待：引擎/队列异常时不再等，直接进入引导（宁可少播一句概要）。 */
const OVERVIEW_MAX_WAIT_MS = 30_000;
/** 电梯口暂停判定半径（与后端 decision_point 半径一致）。 */
const ELEVATOR_PAUSE_RADIUS_M = 3.0;
/** 目标楼层连续可信估计次数：避免电梯内跨层信号抖动导致误恢复。 */
const ELEVATOR_RESUME_HITS = 2;
/**
 * 同一句 relocalizing 安全提示的最小重播间隔。
 * 定位质量在可信/不可信之间来回跳时，服务端每拍都会下发 relocalizing，
 * 不做间隔就会把同一段音频连播多次，用户感知为"一直提示信号不稳定"。
 */
const RELOCALIZE_REPEAT_MS = 30_000;

/**
 * 建会话专用超时（其余接口仍走默认 10s 快失败）。
 * 后端建会话内含两次大模型调用（指令改写 + 概要），已改并行，最坏约等于单次
 * LLM 读超时（15s）+ 规划开销；30s 覆盖该最坏值并留出网络余量。
 */
const CREATE_SESSION_TIMEOUT_MS = 30_000;

export type { NavState };

export interface NavPipelineControl {
  stop(): void;
  /** 导航中重启采集（已在运行则切换节流档位）。 */
  startNavigating(): Promise<void>;
  /** 清除楼层先验：电梯暂停期间由服务端按可见信标多数判层。 */
  clearFloorHint?(): void;
}

export interface NavSessionControllerOptions {
  client: ApiClient;
  map: { siteId: string; requireVersion(): string };
  speech: SpeechQueue;
  pipeline: NavPipelineControl;
  onStateChange: (state: NavState) => void;
  /** 路线更新（创建/reroute）；overviewText 为后端生成、客户端模板为 null。 */
  onRoute?: (route: RouteInfo, overviewText: string | null) => void;
  onSessionCreated?: (session: CreateSessionResponse) => void;
  /** 当前指令（每次更新都会回调，含重复指令）：页面用它刷新 HUD 文案。 */
  onInstruction?: (instruction: NavigationInstruction | null) => void;
  /** 会话收尾（arrived/end/resume 失效）。 */
  onEnded?: (info: { reason: EndReason | null; byArrival: boolean }) => void;
  /** 解析拓扑节点坐标（电梯口判距用）；缺省时退化为收到进电梯指令即暂停。 */
  resolveNode?: (nodeId: string) => { x: number; y: number; floorId: string } | null;
  /** 到达电梯口：导航暂停（页面更新 HUD；语音由控制器播报）。 */
  onElevatorPause?: (info: { targetFloorId: string; text: string }) => void;
  /** 目标楼层信标已采到：导航恢复（页面更新 HUD；语音由控制器播报）。 */
  onElevatorResume?: (info: { floorId: string; text: string }) => void;
  /** 概要窗口最长等待（毫秒），默认 30s；超时跳过概要直接进入引导。 */
  overviewMaxWaitMs?: number;
  createJournal?: NavCreateJournal;
  experiment?: () => CreateSessionRequest['experiment'];
  /** Maximum silence after a usable navigation response (default 10 seconds). */
  responseSilenceMs?: number;
}

export class NavSessionController {
  private readonly machine = new NavStateMachine();
  private readonly tracker = new InstructionTracker();
  private session: CreateSessionResponse | null = null;
  /** 当前生效路线（创建/重规划时更新）：滑动窗口预取后续指令文本用。 */
  private currentRoute: RouteInfo | null = null;
  private sessionSeq = 0;
  private updateInFlight = false;
  private pendingLocation: { location: LocationUpdate; diagnostics: LocationDiagnostics } | null = null;
  private lastConfidence = 0;
  /** 幂等创建暂存：失败重试复用（key + 冻结 body）。 */
  private pendingIdempotencyKey: string | null = null;
  private frozenCreateBody: CreateSessionRequest | null = null;
  /** 最近一次 relocalizing 安全提示：recovering 态回前台时重播用。 */
  private lastRelocalizeText: string | null = null;
  /** 最近一次真正播放 relocalizing 的时间戳：同一句安全提示的最小重播间隔。 */
  private lastRelocalizePlayedAt = 0;
  /**
   * 概要播报窗口：为 true 时冻结定位上报。
   * 仅延迟 startNavigating 不够——standby 拍仍会经 handleEstimate 上报，
   * 服务端返回的 turn/safety 会导致 SpeechQueue 对 normal 概要调 engine.stop
   * （表现为概要没念完就断），且打断后的空队列会被误判成「播完」。
   */
  private overviewPending = false;
  /** 概要窗口代际：新会话/结束会话使其失效，旧窗口回调不再生效。 */
  private overviewToken = 0;
  private overviewTimer: ReturnType<typeof setTimeout> | null = null;
  /**
   * 在途创建（防并发重入：确认连点/异常重入会各发一次 API-06，产生双会话）。
   */
  private createInFlight: Promise<CreateSessionResponse> | null = null;
  private silenceTimer: ReturnType<typeof setTimeout> | null = null;
  private locallyPaused = false;
  /** 已收到「进电梯」指令，正等待用户走到电梯口。 */
  private pendingElevator: { nodeId: string | null; targetFloorId: string } | null = null;
  /** 已到电梯口：暂停上报，继续扫描等待目标楼层信标。 */
  private elevatorPaused = false;
  private elevatorTargetFloorId: string | null = null;
  /** 目标楼层连续可信估计计数（防跨层信号抖动误恢复）。 */
  private elevatorResumeHits = 0;
  private foreground = true;
  private lifecycle = 0;
  private pausedAt = 0;

  /** Page teardown: late results must never restore guidance. */
  dispose(): void {
    this.foreground = false;
    ++this.lifecycle;
    this.clearSilenceTimer();
    this.clearOverviewTimer();
    ++this.overviewToken;
    this.pendingLocation = null;
    this.resetElevatorState();
  }

  private clearSilenceTimer(): void {
    if (this.silenceTimer !== null) clearTimeout(this.silenceTimer);
    this.silenceTimer = null;
  }

  private armSilenceTimer(): void {
    this.clearSilenceTimer();
    if (!this.foreground || this.locallyPaused || this.elevatorPaused || this.machine.isTerminal) return;
    this.silenceTimer = setTimeout(() => {
      this.silenceTimer = null;
      this.pauseLocally('response_silence');
    }, this.opts.responseSilenceMs ?? 10_000);
  }

  private pauseLocally(reason: string): void {
    if (this.locallyPaused || this.elevatorPaused || !this.foreground || this.machine.isTerminal) return;
    this.locallyPaused = true;
    this.pausedAt = Date.now();
    this.clearSilenceTimer();
    this.tracker.reset();
    this.opts.speech.clearAll('nav:offline');
    const sessionId = this.session?.sessionId;
    const context = sessionId === undefined ? undefined : { sessionId, sequence: this.sessionSeq };
    log.warn('nav_local_pause', { ...context, reason });
    this.transition({ type: 'UPDATE_RESULT', status: 'recovering', event: 'relocalizing' });
    this.opts.onInstruction?.(null);
    this.opts.speech.enqueue(
      '导航连接中断，导航已暂停，请停下并留意周围环境。', 'safety', null, 'nav:offline', context,
    );
  }

  /**
   * 会话已不可用（403 无权访问 / 404 不存在）：服务端永远不会再接受更新或结束请求。
   * 继续按“离线”重试只会一直停在导航态，所以本地终止并明确告知用户。
   */
  private abandonSession(code: string): void {
    if (this.session === null || this.machine.isTerminal) return;
    log.warn('导航会话不可用，终止本地导航', {
      sessionId: this.session.sessionId,
      code,
    });
    this.transition({ type: 'RESUME_FAILED' });
    this.opts.speech.clearAll('nav:unavailable');
    this.opts.speech.enqueue('导航会话已失效，请重新发起导航。', 'safety', null, 'nav:unavailable');
    this.opts.onEnded?.({ reason: 'system_error', byArrival: false });
  }

  private async validateRecovery(lifecycle: number): Promise<boolean> {
    const response = await this.opts.client.request<SessionQueryResponse>({ method: 'GET', path: `/api/v1/nav/sessions/${this.session!.sessionId}` });
    const data = response.data;
    if (!this.foreground || lifecycle !== this.lifecycle) return false;
    if (!data || data.mapVersion !== this.opts.map.requireVersion()) return false;
    this.sessionSeq = Math.max(this.sessionSeq, data.lastProcessedSequence ?? 0);
    if (data.status === 'arrived' || data.status === 'ended') {
      this.transition({ type: 'QUERY_STATUS', status: data.status });
      this.opts.onEnded?.({ reason: data.endReason ?? null, byArrival: data.status === 'arrived' });
      return false;
    }
    return data.status === 'active' || data.status === 'recovering';
  }

  constructor(private readonly opts: NavSessionControllerOptions) {}

  private clearPendingCreate(): void {
    this.opts.createJournal?.clear();
    this.pendingIdempotencyKey = null;
    this.frozenCreateBody = null;
  }

  get state(): NavState {
    return this.machine.state;
  }

  get sessionId(): string | null {
    return this.session?.sessionId ?? null;
  }

  /** 当前生效指令序号（标定模式记录用）；无会话/无指令时为 null。 */
  get currentInstructionSequence(): number | null {
    return this.tracker.currentInstruction?.instructionSequence ?? null;
  }

  /**
   * 创建会话（API-06）。网络/服务端 5xx 失败后再次调用会复用同一
   * Idempotency-Key 与冻结 body（忽略新的参数差异），2xx 后清除；
   * 收到 4xx（ROUTE_NOT_FOUND 等）说明请求已确定失败，清空暂存——
   * 用户改选目的地后再次创建必须使用新参数，不得静默复用旧目的地。
   * 在途创建复用同一请求：确认连点不能各发一次（会产生双会话）。
   */
  async createSession(
    destination: PlaceCandidate,
    origin: OriginPosition,
    userMode: UserMode = 'blind',
    opts: { overviewPlayed?: boolean; transitionClip?: string | null } = {},
  ): Promise<CreateSessionResponse> {
    if (this.createInFlight !== null) {
      log.info('会话创建在途：复用同一请求', destination.placeId);
      return this.createInFlight;
    }
    const task = this.doCreateSession(destination, origin, userMode, opts);
    this.createInFlight = task;
    try {
      return await task;
    } finally {
      if (this.createInFlight === task) {
        this.createInFlight = null;
      }
    }
  }

  private async doCreateSession(
    destination: PlaceCandidate,
    origin: OriginPosition,
    userMode: UserMode,
    opts: { overviewPlayed?: boolean; transitionClip?: string | null },
  ): Promise<CreateSessionResponse> {
    // 新会话是语音通道的重试窗口：上次 TTS 失败后这里给引擎第二次机会
    this.opts.speech.resetForRetry();
    const unresolved = this.opts.createJournal?.load();
    if (unresolved) {
      this.pendingIdempotencyKey = unresolved.key;
      this.frozenCreateBody = unresolved.body;
    }
    if (this.pendingIdempotencyKey === null || this.frozenCreateBody === null) {
      this.pendingIdempotencyKey = uuidV4();
      this.frozenCreateBody = {
        siteId: this.opts.map.siteId,
        mapVersion: this.opts.map.requireVersion(),
        origin,
        destinationId: destination.placeId,
        userMode,
        locale: 'zh-CN',
      };
      const experiment = this.opts.experiment?.();
      if (experiment) this.frozenCreateBody.experiment = experiment;
    }
    this.opts.createJournal?.save({ key: this.pendingIdempotencyKey, body: this.frozenCreateBody });
    let resp;
    try {
      resp = await this.opts.client.request<CreateSessionResponse>({
        method: 'POST',
        path: '/api/v1/nav/sessions',
        body: this.frozenCreateBody,
        idempotencyKey: this.pendingIdempotencyKey,
        timeoutMs: CREATE_SESSION_TIMEOUT_MS,
      });
    } catch (err) {
      if (
        err instanceof ApiClientError &&
        err.statusCode !== null &&
        err.statusCode >= 400 &&
        err.statusCode < 500
      ) {
        this.clearPendingCreate();
      }
      throw err;
    }
    const data = resp.data;
    if (data === null || typeof data.sessionId !== 'string') {
      throw new ApiClientError({
        code: 'HTTP_ERROR',
        message: '会话创建响应格式异常',
        statusCode: resp.statusCode,
        requestId: resp.requestId,
      });
    }
    if (unresolved) {
      // Replay confirms the old transaction, but its saved route/origin may now be stale.
      // Close that session before allowing a fresh create for the user's current intent.
      try {
        await this.opts.client.request({ method: 'POST', path: `/api/v1/nav/sessions/${data.sessionId}/end`, body: { reason: 'system_error' } });
      } catch (error) {
        if (!(error instanceof ApiClientError && error.code === 'SESSION_NOT_ACTIVE')) throw error;
      }
      this.clearPendingCreate();
      return this.doCreateSession(destination, origin, userMode, opts);
    }
    this.clearPendingCreate();

    this.session = data;
    this.opts.onSessionCreated?.(data);
    this.currentRoute = data.route;
    this.sessionSeq = 0;
    this.lastConfidence = 0;
    this.lastRelocalizeText = null;
    this.tracker.reset();
    this.resetElevatorState();
    this.prefetchRouteTexts(data.route);
    this.transition({ type: 'SESSION_CREATED', status: data.status });
    if (opts.overviewPlayed === true) {
      // 概要在 API-10 预览阶段已播完：直接进入逐步引导，不重播概要。
      this.beginGuidedNavigation(data, opts.transitionClip ?? null);
    } else {
      this.beginOverviewWindow(data);
    }
    return data;
  }

  /**
   * 概要在预览阶段已播完（确认后才建会话）：先给路线与首条指令，立即开始上报定位。
   * 不创建概要窗口，避免「确认 → 又播一遍概要」的重复体验。
   *
   * transitionClip：进入逐步引导前的短过渡（本地音频 `nav-start.mp3`）。与首条指令
   * 同用 normal 优先级 FIFO 排队，播完音频才继续，天然串行不重叠；safety 首条仍可依法抢占。
   */
  private beginGuidedNavigation(
    data: CreateSessionResponse,
    transitionClip: string | null,
  ): void {
    this.clearOverviewTimer();
    this.overviewPending = false;
    this.overviewToken += 1; // 作废旧窗口：迟到的队列回调不得再推进
    log.info('概要已在预览阶段播完：直接进入逐步引导');
    this.opts.onRoute?.(data.route, null);
    const first = data.route.segments[0] ?? null;
    this.armElevatorFromRoute(data.route);
    const clip = transitionClip?.trim() ?? '';
    if (clip !== '') {
      this.opts.speech.enqueueClip(clip, 'normal', 'nav:start-transition');
    }
    this.opts.onInstruction?.(first);
    this.announce(first);
    this.armSilenceTimer();
    void this.opts.pipeline.startNavigating();
  }

  /**
   * 概要先行（REQ-NAV）：整个概要窗口内冻结定位上报（见 overviewPending 注释），
   * 队列彻底播完后首条指令的语音与 HUD 文案同时出现，再开始上报定位。
   *
   * 兜底不卡死：队列可能因引擎无响应（play 既不 resolve 也不 reject）永远不安静，
   * 因此设最长等待；超时后照常切到首条指令、恢复上报，宁可少播一句概要。
   */
  private beginOverviewWindow(data: CreateSessionResponse): void {
    this.clearOverviewTimer();
    this.overviewPending = true;
    const token = (this.overviewToken += 1);
    log.info('概要先行：暂停定位上报', data.overviewText === null ? '(模板兜底)' : '(LLM)');
    this.opts.onRoute?.(data.route, data.overviewText ?? null);
    this.armElevatorFromRoute(data.route);

    const finish = (reason: SpeechIdleReason | 'timeout'): void => {
      if (token !== this.overviewToken || !this.overviewPending) {
        return; // 旧会话窗口 / 已放行
      }
      this.clearOverviewTimer();
      this.overviewPending = false;
      log.info('概要窗口结束', reason);
      if (this.machine.isTerminal || this.session?.sessionId !== data.sessionId) {
        return;
      }
      if (reason !== 'interrupted') {
        // finished：正常播完补播首条；timeout/stalled：概要没播成（引擎无响应），
        // 也必须把首条指令写到 HUD（宁可少播一句概要，不能让导航卡住）。
        // interrupted：有更高优先级播报接管，不能再叠一遍。
        this.opts.onInstruction?.(data.route.segments[0] ?? null);
        this.announce(data.route.segments[0] ?? null);
      }
      void this.opts.pipeline.startNavigating();
      this.armSilenceTimer();
    };

    this.opts.speech.notifyWhenIdle(finish);
    const waitMs = this.opts.overviewMaxWaitMs ?? OVERVIEW_MAX_WAIT_MS;
    this.overviewTimer = setTimeout(() => {
      this.overviewTimer = null;
      log.warn('概要窗口超时：队列未安静（引擎可能无响应），跳过概要进入引导', `${waitMs}ms`);
      finish('timeout');
    }, waitMs);
  }

  private clearOverviewTimer(): void {
    if (this.overviewTimer !== null) {
      clearTimeout(this.overviewTimer);
      this.overviewTimer = null;
    }
  }

  /** 电梯状态复位（新会话/重规划/结束/销毁）。 */
  private resetElevatorState(): void {
    this.pendingElevator = null;
    this.elevatorPaused = false;
    this.elevatorTargetFloorId = null;
    this.elevatorResumeHits = 0;
  }

  /** 从「请进入电梯，前往 2 楼」提取目标楼层；失败回落目的地楼层。 */
  private elevatorTargetFloor(text: string): string | null {
    const match = /前往\s*([^\s，。]+?)\s*楼/.exec(text);
    return match?.[1] ?? this.session?.destination.floorId ?? null;
  }

  private elevatorPauseText(targetFloorId: string): string {
    return `导航已暂停，请上电梯，到 ${targetFloorId} 楼出电梯后导航自动恢复。`;
  }

  private elevatorResumeText(floorId: string): string {
    return `已到达 ${floorId} 楼，导航继续。`;
  }

  /** 电梯提示是安全文案：拿到目标楼层就预生成，避免暂停瞬间才开始合成。 */
  private prefetchElevatorSpeech(targetFloorId: string): void {
    this.opts.speech.prepare(this.elevatorPauseText(targetFloorId));
    this.opts.speech.prepare(this.elevatorResumeText(targetFloorId));
  }

  /** 记录「进电梯」指令：等用户走到电梯口附近再暂停，避免提前 10 米停住。 */
  private noteElevatorInstruction(instruction: NavigationInstruction | null): void {
    if (
      instruction === null ||
      instruction.action !== 'enter_elevator' ||
      this.elevatorPaused ||
      this.pendingElevator !== null
    ) {
      return;
    }
    const targetFloorId = this.elevatorTargetFloor(instruction.text);
    if (targetFloorId === null) {
      return;
    }
    this.pendingElevator = {
      nodeId: instruction.trigger.nodeId ?? null,
      targetFloorId,
    };
    this.prefetchElevatorSpeech(targetFloorId);
    log.info('收到进电梯指令：接近电梯口后暂停导航', targetFloorId);
  }

  /**
   * 从整条路线预置电梯暂停点。
   * 服务端在用户进入电梯口 3m 内时会把「进电梯 → 出电梯 → 下一层直行」一次推进完，
   * 客户端可能一帧都收不到 enter_elevator；因此不能只依赖更新响应里的指令，
   * 建会话/重规划拿到路线时就应先找到电梯入口节点与目标楼层。
   */
  private armElevatorFromRoute(route: RouteInfo): void {
    if (this.elevatorPaused) {
      return;
    }
    const enter = route.segments.find((segment) => segment.action === 'enter_elevator');
    if (enter === undefined) {
      return;
    }
    const targetFloorId = this.elevatorTargetFloor(enter.text);
    if (targetFloorId === null) {
      return;
    }
    this.pendingElevator = {
      nodeId: enter.trigger.nodeId ?? null,
      targetFloorId,
    };
    this.prefetchElevatorSpeech(targetFloorId);
    log.info('路线含电梯：接近电梯口后暂停导航', targetFloorId);
  }

  /** 到达电梯口：暂停上报并播报；采集继续，等目标楼层信标自动恢复。 */
  private pauseForElevator(targetFloorId: string): void {
    if (this.elevatorPaused) {
      return;
    }
    this.elevatorPaused = true;
    this.elevatorTargetFloorId = targetFloorId;
    this.elevatorResumeHits = 0;
    this.pendingElevator = null;
    this.clearSilenceTimer();
    // 关键：清掉楼层先验。否则服务端始终按当前层解算，目标层信标再多也会被忽略，
    // 地图也就无法随扫描到的楼层自动切换。
    this.opts.pipeline.clearFloorHint?.();
    const text = this.elevatorPauseText(targetFloorId);
    this.opts.onElevatorPause?.({ targetFloorId, text });
    this.opts.speech.enqueue(text, 'safety', null, 'nav:elevator-pause');
    log.info('到达电梯口：暂停导航，等待目标楼层信标', targetFloorId);
  }

  /** 目标楼层信标已采到：恢复上报与引导。 */
  private resumeFromElevator(floorId: string): void {
    this.elevatorPaused = false;
    this.elevatorTargetFloorId = null;
    this.elevatorResumeHits = 0;
    const text = this.elevatorResumeText(floorId);
    this.opts.onElevatorResume?.({ floorId, text });
    this.opts.speech.enqueue(text, 'safety', null, 'nav:elevator-resume');
    log.info('检测到目标楼层信标：恢复导航', floorId);
  }

  /** 定位估计入口（LocationPipeline.onEstimate）；不可信质量只报 quality。 */
  handleEstimate(estimate: PositionEstimateResponse): void {
    if (this.session === null || this.machine.isTerminal) {
      return;
    }
    const trusted = estimate.quality === 'reliable' || estimate.quality === 'degraded';
    const position = trusted ? estimate.position ?? null : null;

    // 电梯暂停：不上报，但继续扫描并按拍清除楼层先验，让服务端按可见信标多数判层
    if (this.elevatorPaused) {
      this.opts.pipeline.clearFloorHint?.();
      const onTargetFloor =
        position !== null &&
        position.floorId !== null &&
        position.floorId !== undefined &&
        position.floorId === this.elevatorTargetFloorId;
      if (onTargetFloor) {
        this.elevatorResumeHits += 1;
        if (this.elevatorResumeHits >= ELEVATOR_RESUME_HITS) {
          this.resumeFromElevator(position.floorId as string);
        }
      } else {
        this.elevatorResumeHits = 0;
      }
      if (this.elevatorPaused) {
        return;
      }
    }

    const location: LocationUpdate = {
      x: trusted ? (position?.x ?? null) : null,
      y: trusted ? (position?.y ?? null) : null,
      floorId: trusted ? (position?.floorId ?? null) : null,
      confidence: estimate.confidence,
      quality: estimate.quality,
      observedAt: estimate.observedAt,
    };
    if (trusted) {
      this.lastConfidence = estimate.confidence;
    } else {
      // 位置不可信时放开楼层先验：允许服务端按可见信标重新判层。
      // 兜底「跨层未触发暂停」或从楼梯换层后 hint 仍钉在旧楼层的情况，
      // 否则会永久 recovering，地图也无法跟随扫描到的楼层。
      this.opts.pipeline.clearFloorHint?.();
    }

    // 进电梯指令已下发：走到电梯口（触发节点 3m 内）即暂停，等目标楼层信标
    if (trusted && position !== null && this.pendingElevator !== null) {
      const node =
        this.pendingElevator.nodeId === null
          ? null
          : this.opts.resolveNode?.(this.pendingElevator.nodeId) ?? null;
      const atEntrance =
        node === null ||
        (node.floorId === position.floorId &&
          Math.hypot(node.x - position.x, node.y - position.y) <= ELEVATOR_PAUSE_RADIUS_M);
      if (atEntrance) {
        this.pauseForElevator(this.pendingElevator.targetFloorId);
        return;
      }
    }

    // 服务端规则：unreliable/lost 只带 quality（服务端转 recovering），坐标不报
    void this.sendUpdate(location, { estimateId: estimate.estimateId });
  }

  /**
   * 采集侧没有可用估计（导航中信标不足 / API-05 请求失败）：
   * 按服务端规则只报 quality=unreliable，让会话进入 recovering 并播安全提示，
   * 不伪造坐标。
   */
  reportUnavailable(error?: ApiClientError): void {
    if (
      this.session === null ||
      this.machine.isTerminal ||
      this.overviewPending ||
      this.elevatorPaused
    ) {
      // 概要窗口内不报 unavailable：否则服务端转 recovering 会下发 safety 打断概要。
      // 电梯暂停期间同样不报：会话保持 active，等目标楼层信标后直接恢复上报。
      return;
    }
    void this.sendUpdate({
      x: null,
      y: null,
      floorId: null,
      confidence: 0,
      quality: 'unreliable',
      observedAt: new Date().toISOString(),
    }, {
      requestId: error?.requestId,
      code: error?.code,
      reason: error?.details.reason,
      matchedBeaconCount: error?.details.matchedBeaconCount,
      submittedBeaconCount: error?.details.submittedBeaconCount,
    });
  }

  /** 切后台（FE-05）：停采集；恢复流程交给 handleForeground。 */
  handleBackground(): void {
    this.foreground = false;
    ++this.lifecycle;
    this.clearSilenceTimer();
    if (this.session === null) {
      return;
    }
    this.transition({ type: 'BACKGROUND' });
  }

  /**
   * 回前台（FE-05 次序）：先 API-08 校验 status/mapVersion/sequence →
   * 重启采集 → 重播有效指令。
   */
  async handleForeground(): Promise<void> {
    this.foreground = true;
    const lifecycle = this.lifecycle;
    if (this.session === null) {
      return;
    }
    let query: SessionQueryResponse;
    try {
      const resp = await this.opts.client.request<SessionQueryResponse>({
        method: 'GET',
        path: `/api/v1/nav/sessions/${this.session.sessionId}`,
      });
      if (resp.data === null) {
        throw new ApiClientError({ code: 'HTTP_ERROR', message: '会话查询为空' });
      }
      query = resp.data;
    } catch (err) {
      if (!this.foreground || lifecycle !== this.lifecycle) return;
      if (err instanceof ApiClientError && (err.code === 'SESSION_NOT_FOUND' || err.code === 'FORBIDDEN')) {
        this.transition({ type: 'RESUME_FAILED' });
        this.opts.speech.enqueue('导航会话已失效，请重新发起导航。', 'safety', null, 'nav:foreground');
        this.opts.onEnded?.({ reason: 'system_error', byArrival: false });
        return;
      }
      this.pauseLocally('foreground_query_failed');
      if (!this.overviewPending) {
        await this.opts.pipeline.startNavigating();
      }
      return;
    }

    if (!this.foreground || lifecycle !== this.lifecycle) return;
    // 校验地图版本：不一致则结束当前引导
    if (query.mapVersion !== this.opts.map.requireVersion()) {
      this.transition({ type: 'RESUME_FAILED' });
      this.opts.speech.enqueue('地图已更新，请重新发起导航。', 'safety', null, 'nav:foreground');
      this.opts.onEnded?.({ reason: 'system_error', byArrival: false });
      return;
    }

    const prevState = this.machine.state;
    if (this.locallyPaused && (query.status === 'active' || query.status === 'recovering')) {
      this.sessionSeq = Math.max(this.sessionSeq, query.lastProcessedSequence ?? 0);
      await this.opts.pipeline.startNavigating();
      return; // Query alone cannot restore executable guidance; require a fresh update.
    }
    this.transition({ type: 'QUERY_STATUS', status: query.status });
    // 服务端可能已处理更多更新：序号对齐（只增不减）
    const serverSeq = query.lastProcessedSequence ?? 0;
    if (serverSeq > this.sessionSeq) {
      this.sessionSeq = serverSeq;
    }

    // 重启采集：active/recovering 都继续扫（recovering 需新可信位置才能回 active）
    if (
      (this.machine.state === 'active' || this.machine.state === 'recovering') &&
      !this.overviewPending
    ) {
      await this.opts.pipeline.startNavigating();
    }

    // 重播有效指令（过期/缺失则不播）
    if (prevState !== this.machine.state && this.machine.isTerminal) {
      this.opts.onEnded?.({ reason: query.endReason ?? null, byArrival: query.status === 'arrived' });
      return;
    }
    if (this.overviewPending) {
      // 概要还没播完：不重播旧指令、不提前上报，统一由排队事件放行
      return;
    }
    if (this.elevatorPaused) {
      // 电梯暂停跨前后台保持：已重启采集，等目标楼层信标后自动恢复；
      // 不重播「请进入电梯」，避免回前台再打断一次。
      log.info('回前台：电梯暂停中，等待目标楼层信标');
      return;
    }
    // 重播：active 重播当前转向指令；recovering 重播安全提示（不播旧方向）
    if (this.machine.state === 'active') {
      this.armSilenceTimer();
      const replay = this.tracker.replayText();
      if (replay !== null) {
        this.opts.speech.enqueue(replay, 'normal', null, 'nav:foreground-replay');
      }
    } else if (this.machine.state === 'recovering' && this.lastRelocalizeText !== null) {
      // 回前台由用户主动触发，不受冷却限制：停在外面很久再回来必须能听到当前状态
      const context = { sessionId: query.sessionId, sequence: this.sessionSeq };
      log.info('nav_relocalize_foreground_replay', context);
      this.opts.speech.enqueueClip('nav-relocalizing', 'safety', 'nav:foreground-relocalize', context);
    }
  }

  /** 领取一次 relocalizing 播报额度：冷却窗口内返回 false（不出声）。 */
  private claimRelocalizeSlot(): boolean {
    const now = Date.now();
    if (now - this.lastRelocalizePlayedAt < RELOCALIZE_REPEAT_MS) {
      return false;
    }
    this.lastRelocalizePlayedAt = now;
    return true;
  }

  /** 结束会话（API-09）；客户端只发送用户/系统原因，从不发送 arrived。 */
  async endSession(reason: Exclude<EndReason, 'arrived'>): Promise<void> {
    if (this.session === null) {
      return;
    }
    try {
      await this.opts.client.request<EndSessionResponse>({
        method: 'POST',
        path: `/api/v1/nav/sessions/${this.session.sessionId}/end`,
        body: { reason },
      });
    } catch (err) {
      if (!(err instanceof ApiClientError)) throw err;
      if (err.code === 'SESSION_NOT_FOUND' || err.code === 'FORBIDDEN') {
        // 身份切换（如本地 fake 登录续期）或会话被清理后，服务端已不再认可本会话；
        // 继续抛错会把用户困在导航态，按本地结束处理。
        log.warn('结束会话时服务端拒绝，按本地结束处理', {
          sessionId: this.session.sessionId,
          code: err.code,
        });
      } else if (err.code === 'SESSION_NOT_ACTIVE') {
        if (this.opts.experiment?.()) {
          if (this.machine.state === 'arrived') return;
          const { data } = await this.opts.client.request<SessionQueryResponse>({
            method: 'GET', path: `/api/v1/nav/sessions/${this.session.sessionId}`,
          });
          if (this.state === 'arrived') return;
          if (data?.status === 'arrived') {
            this.transition({ type: 'QUERY_STATUS', status: data.status });
            this.opts.onEnded?.({ reason: 'arrived', byArrival: true });
            return;
          }
          if (data?.status !== 'ended') throw err;
        }
        // 已终态：视为结束成功
      } else {
        throw err;
      }
    }
    this.transition({ type: 'END_ACK' });
    this.overviewToken += 1; // 概要窗口作废：迟到的队列回调/超时不得再推进
    this.overviewPending = false;
    this.clearOverviewTimer();
    this.resetElevatorState();
    this.opts.speech.clearAll('nav:end-session');
    this.opts.onEnded?.({ reason, byArrival: false });
  }

  // ------------------------------------------------------------------
  // 内部
  // ------------------------------------------------------------------

  private transition(input: Parameters<NavStateMachine['handle']>[0]): void {
    const prevState = this.machine.state;
    const effects = this.machine.handle(input);
    if (this.machine.isTerminal) this.clearSilenceTimer();
    if (this.machine.state !== prevState) {
      this.opts.onStateChange(this.machine.state);
    }
    this.applyEffects(effects);
  }

  private applyEffects(effects: NavEffects): void {
    if (effects.stopPipeline) {
      this.opts.pipeline.stop();
    }
    if (effects.startPipeline && !this.overviewPending) {
      void this.opts.pipeline.startNavigating();
    }
    if (effects.clearNormal) {
      this.opts.speech.clearNormal('nav:state-recovering');
    }
  }

  private async sendUpdate(location: LocationUpdate, diagnostics: LocationDiagnostics): Promise<void> {
    if (this.session === null || this.machine.isTerminal || !this.foreground) {
      return;
    }
    if (this.elevatorPaused) {
      // 电梯暂停：会话保持 active，不发送定位更新；恢复后由目标楼层估计重新开始
      return;
    }
    if (this.overviewPending) {
      // 概要播报窗口：一律不上报。否则 standby 拍也会推进服务端状态并下发
      // turn/safety 指令，SpeechQueue 会 stop 正在播的 normal 概要（截断）。
      // 放行后由下一拍估计带最新位置上报。
      return;
    }
    if (this.updateInFlight) {
      this.pendingLocation = { location, diagnostics }; // 只保留最新
      return;
    }
    this.updateInFlight = true;
    const lifecycle = this.lifecycle;
    const recoveryAttempt = this.locallyPaused;
    const sessionId = this.session.sessionId;
    let sentSequence: number | null = null;
    try {
      if (this.locallyPaused) {
        const observed = Date.parse(location.observedAt);
        if (!Number.isFinite(observed) || observed < this.pausedAt - 2000 || observed > Date.now() + 2000 || Date.now() - observed > 10_000) return;
        if (!await this.validateRecovery(lifecycle)) return;
      }
      if (!this.foreground || lifecycle !== this.lifecycle) return;
      const sequence = this.sessionSeq + 1;
      sentSequence = sequence;
      const body: NavUpdateRequest = { sequence, location };
      const resp = await this.opts.client.request<NavUpdateResponse>({
        method: 'POST',
        path: `/api/v1/nav/sessions/${this.session.sessionId}/updates`,
        body,
        timeoutMs: NAV_UPDATE_TIMEOUT_MS,
      });
      if (!this.foreground || lifecycle !== this.lifecycle || this.machine.isTerminal) return;
      if (!resp.data || !['active', 'recovering', 'arrived', 'ended'].includes(resp.data.status)) {
        log.warn('nav_update_invalid_response', { sessionId, sequence, requestId: resp.requestId });
        this.pauseLocally('invalid_update_response');
        return;
      }
      this.sessionSeq = sequence;
      const details = {
        sessionId, sequence, requestId: resp.requestId, location: diagnostics,
        quality: location.quality, floorId: location.floorId, observedAt: location.observedAt,
        previousState: this.machine.state, status: resp.data.status, event: resp.data.event,
      };
      if (this.machine.state !== resp.data.status) {
        log.info('nav_update_state_change', details);
      } else {
        log.debug('nav_update_result', details);
      }
      const trusted = location.quality === 'reliable' || location.quality === 'degraded';
      if (this.locallyPaused && (!recoveryAttempt || Date.now() - Date.parse(location.observedAt) > 10_000)) return;
      if (this.locallyPaused && (!trusted || resp.data?.status !== 'active')) {
        if (resp.data?.status === 'arrived' || resp.data?.status === 'ended') this.applyUpdateResponse(resp.data);
        return;
      }
      this.locallyPaused = false;
      this.applyUpdateResponse(resp.data);
      this.armSilenceTimer();
    } catch (err) {
      if (!this.foreground || lifecycle !== this.lifecycle) return;
      log.warn('nav_update_failed', {
        sessionId, sequence: sentSequence, location: diagnostics,
        requestId: err instanceof ApiClientError ? err.requestId : null,
        code: err instanceof ApiClientError ? err.code : 'unknown',
      });
      if (err instanceof ApiClientError && err.code === 'STALE_NAV_UPDATE') {
        await this.resyncSequence();
      } else if (err instanceof ApiClientError && err.code === 'SESSION_NOT_ACTIVE') {
        await this.confirmTerminal();
      } else if (
        err instanceof ApiClientError &&
        (err.code === 'FORBIDDEN' || err.code === 'SESSION_NOT_FOUND')
      ) {
        this.abandonSession(err.code);
      } else if (lifecycle === this.lifecycle) this.pauseLocally('update_request_failed');
    } finally {
      this.updateInFlight = false;
    }
    const pending = this.pendingLocation;
    this.pendingLocation = null;
    if (pending !== null && this.session !== null && !this.machine.isTerminal) {
      await this.sendUpdate(pending.location, pending.diagnostics);
    }
  }

  private applyUpdateResponse(data: NavUpdateResponse | null): void {
    if (data === null || typeof data.status !== 'string') {
      return;
    }
    const prevState = this.machine.state;
    this.transition({ type: 'UPDATE_RESULT', status: data.status, event: data.event });

    if (data.event === 'reroute') {
      this.opts.speech.clearNormal('nav:reroute'); // 旧路线提示作废
      this.resetElevatorState(); // 新路线：旧的电梯等待作废
      if (data.route !== undefined && data.route !== null) {
        this.currentRoute = data.route;
        this.opts.onRoute?.(data.route, null); // 重规划不重播概要
        this.armElevatorFromRoute(data.route); // 新路线可能仍含电梯
        this.prefetchRouteTexts(data.route);
      }
      this.noteElevatorInstruction(data.instruction);
      this.opts.onInstruction?.(data.instruction);
      this.announce(data.instruction); // 新首条
      return;
    }
    if (data.event === 'arrived') {
      this.resetElevatorState();
      const near = this.lastConfidence < ARRIVAL_NEAR_CONFIDENCE;
      // 到达是导航收尾的唯一提示，必须最高优先级：safety 会清空待播指令并打断
      // 正在播的普通/转向播报，避免被后续残留指令占用语音通道。
      this.opts.speech.enqueue(
        near ? '已到达目的地附近，请留意周围环境。' : '已到达目的地。',
        'safety',
        null,
        'nav:arrived',
      );
      this.opts.onEnded?.({ reason: 'arrived', byArrival: true });
      return;
    }
    if (data.event === 'relocalizing') {
      // 仅在刚进入 recovering 时播报一次，避免持续重播
      if (prevState !== 'recovering' && this.machine.state === 'recovering') {
        const context = { sessionId: data.sessionId, sequence: data.processedSequence };
        const text = data.instruction?.text ?? '正在重新定位，请原地稍候。';
        this.lastRelocalizeText = text;
        this.opts.onInstruction?.(data.instruction);
        // 定位不足/信号不稳定是固定安全文案，走本地离线音频，避免弱网时 TTS 失败。
        if (!this.claimRelocalizeSlot()) {
          log.info('nav_relocalize_suppressed', { ...context, reason: 'cooldown' });
          return; // 冷却窗口内：状态和文案已更新，但不重复出声
        }
        if (data.instruction?.action === 'relocalize') {
          this.opts.speech.enqueueClip('nav-relocalizing', 'safety', 'nav:relocalizing', context);
        } else {
          this.opts.speech.enqueue(text, 'safety', null, 'nav:relocalizing', context);
        }
      }
      return;
    }
    this.noteElevatorInstruction(data.instruction);
    this.opts.onInstruction?.(data.instruction);
    this.announce(data.instruction);
  }

  private announce(instruction: NavigationInstruction | null): void {
    if (instruction === null) {
      return;
    }
    if (!this.tracker.observe(instruction)) {
      return; // 重复指令：不重复播报
    }
    // 滑动窗口预取：正在播第 N 条时，后台合成第 N+1、N+2 条动态文本
    this.prefetchFollowing(instruction);
    // 当前条也先发起预生成：与正在播的上一条/过渡音并行，队列空闲时直接命中缓存
    this.opts.speech.prepare(instruction.text);
    // 固定文案优先走本地离线音频；动态文案（距离/楼层/地点）继续走 TTS。
    const clip = staticInstructionClip(instruction.text);
    if (clip !== null) {
      this.opts.speech.enqueueClip(clip, instruction.priority, `nav:instruction:${instruction.instructionId}`);
      return;
    }
    this.opts.speech.enqueue(instruction.text, instruction.priority, null, `nav:instruction:${instruction.instructionId}`);
  }

  /** 路线确定时先预生成电梯提示（进出各一条），避免跨层时才开始合成。 */
  private prefetchRouteTexts(route: RouteInfo): void {
    for (const segment of route.segments) {
      if (
        segment.action === 'enter_elevator' ||
        segment.action === 'exit_elevator'
      ) {
        this.opts.speech.prepare(segment.text);
      }
    }
  }

  /** 预取当前指令之后的 2 条动态文本（固定转向/到达文案由本地音频负责，队列会跳过）。 */
  private prefetchFollowing(instruction: NavigationInstruction): void {
    const route = this.currentRoute;
    if (route === null) {
      return;
    }
    const index = route.segments.findIndex(
      (segment) => segment.instructionSequence === instruction.instructionSequence,
    );
    if (index < 0) {
      return;
    }
    for (const next of route.segments.slice(index + 1, index + 3)) {
      this.opts.speech.prepare(next.text);
    }
  }

  /** 409 STALE_NAV_UPDATE 恢复：从 API-08 取 lastProcessedSequence 对齐。 */
  private async resyncSequence(): Promise<void> {
    if (this.session === null) {
      return;
    }
    try {
      const resp = await this.opts.client.request<SessionQueryResponse>({
        method: 'GET',
        path: `/api/v1/nav/sessions/${this.session.sessionId}`,
      });
      const serverSeq = resp.data?.lastProcessedSequence ?? null;
      if (serverSeq !== null && serverSeq > this.sessionSeq) {
        this.sessionSeq = serverSeq;
      }
    } catch {
      // 查询失败：保持当前序号，下一拍再试
    }
  }

  /** 409 SESSION_NOT_ACTIVE 恢复：查询确认终态并收尾。 */
  private async confirmTerminal(): Promise<void> {
    if (this.session === null) {
      return;
    }
    try {
      const resp = await this.opts.client.request<SessionQueryResponse>({
        method: 'GET',
        path: `/api/v1/nav/sessions/${this.session.sessionId}`,
      });
      const status = resp.data?.status;
      if (status === 'arrived' || status === 'ended') {
        this.transition({ type: 'QUERY_STATUS', status });
        this.opts.onEnded?.({
          reason: resp.data?.endReason ?? null,
          byArrival: status === 'arrived',
        });
      }
    } catch {
      // 查询失败：保持现状
    }
  }
}
