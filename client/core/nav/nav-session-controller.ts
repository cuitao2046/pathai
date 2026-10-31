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
import type { SpeechQueue } from '../speech/speech-queue';
import { InstructionTracker } from './instruction-tracker';
import {
  NavStateMachine,
  type NavEffects,
  type NavState,
} from './nav-state-machine';
import { uuidV4 } from '../../utils/ids';
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

/** 到达置信度低于该值播「已到达附近」。 */
const ARRIVAL_NEAR_CONFIDENCE = 0.75;

export type { NavState };

export interface NavPipelineControl {
  stop(): void;
  /** 导航中重启采集（已在运行则切换节流档位）。 */
  startNavigating(): Promise<void>;
}

export interface NavSessionControllerOptions {
  client: ApiClient;
  map: { siteId: string; requireVersion(): string };
  speech: SpeechQueue;
  pipeline: NavPipelineControl;
  onStateChange: (state: NavState) => void;
  /** 路线更新（创建/reroute）。 */
  onRoute?: (route: RouteInfo) => void;
  /** 会话收尾（arrived/end/resume 失效）。 */
  onEnded?: (info: { reason: EndReason | null; byArrival: boolean }) => void;
}

export class NavSessionController {
  private readonly machine = new NavStateMachine();
  private readonly tracker = new InstructionTracker();
  private session: CreateSessionResponse | null = null;
  private sessionSeq = 0;
  private updateInFlight = false;
  private pendingLocation: LocationUpdate | null = null;
  private lastConfidence = 0;
  /** 幂等创建暂存：失败重试复用（key + 冻结 body）。 */
  private pendingIdempotencyKey: string | null = null;
  private frozenCreateBody: CreateSessionRequest | null = null;
  /** 最近一次 relocalizing 安全提示：recovering 态回前台时重播用。 */
  private lastRelocalizeText: string | null = null;

  constructor(private readonly opts: NavSessionControllerOptions) {}

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
   * 创建会话（API-06）。上一次创建失败后再次调用会复用同一
   * Idempotency-Key 与冻结 body（忽略新的参数差异），2xx 后清除。
   */
  async createSession(
    destination: PlaceCandidate,
    origin: OriginPosition,
    userMode: UserMode = 'blind',
  ): Promise<CreateSessionResponse> {
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
    }
    const resp = await this.opts.client.request<CreateSessionResponse>({
      method: 'POST',
      path: '/api/v1/nav/sessions',
      body: this.frozenCreateBody,
      idempotencyKey: this.pendingIdempotencyKey,
    });
    const data = resp.data;
    if (data === null || typeof data.sessionId !== 'string') {
      throw new ApiClientError({
        code: 'HTTP_ERROR',
        message: '会话创建响应格式异常',
        statusCode: resp.statusCode,
        requestId: resp.requestId,
      });
    }
    // 2xx：幂等暂存清除
    this.pendingIdempotencyKey = null;
    this.frozenCreateBody = null;

    this.session = data;
    this.sessionSeq = 0;
    this.lastConfidence = 0;
    this.lastRelocalizeText = null;
    this.tracker.reset();
    this.transition({ type: 'SESSION_CREATED', status: data.status });
    this.opts.onRoute?.(data.route);
    this.announce(data.route.segments[0] ?? null);
    void this.opts.pipeline.startNavigating();
    return data;
  }

  /** 定位估计入口（LocationPipeline.onEstimate）；不可信质量只报 quality。 */
  handleEstimate(estimate: PositionEstimateResponse): void {
    if (this.session === null || this.machine.isTerminal) {
      return;
    }
    const trusted = estimate.quality === 'reliable' || estimate.quality === 'degraded';
    const location: LocationUpdate = {
      x: trusted ? (estimate.position?.x ?? null) : null,
      y: trusted ? (estimate.position?.y ?? null) : null,
      floorId: trusted ? (estimate.position?.floorId ?? null) : null,
      confidence: estimate.confidence,
      quality: estimate.quality,
      observedAt: estimate.observedAt,
    };
    if (trusted) {
      this.lastConfidence = estimate.confidence;
    }
    // 服务端规则：unreliable/lost 只带 quality（服务端转 recovering），坐标不报
    void this.sendUpdate(location);
  }

  /**
   * 采集侧没有可用估计（导航中信标不足 / API-05 请求失败）：
   * 按服务端规则只报 quality=unreliable，让会话进入 recovering 并播安全提示，
   * 不伪造坐标。
   */
  reportUnavailable(): void {
    if (this.session === null || this.machine.isTerminal) {
      return;
    }
    void this.sendUpdate({
      x: null,
      y: null,
      floorId: null,
      confidence: 0,
      quality: 'unreliable',
      observedAt: new Date().toISOString(),
    });
  }

  /** 切后台（FE-05）：停采集；恢复流程交给 handleForeground。 */
  handleBackground(): void {
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
      if (err instanceof ApiClientError && err.code === 'SESSION_NOT_FOUND') {
        this.transition({ type: 'RESUME_FAILED' });
        this.opts.speech.enqueue('导航会话已失效，请重新发起导航。', 'safety');
        this.opts.onEnded?.({ reason: 'system_error', byArrival: false });
        return;
      }
      // 网络/服务暂不可用：保持现状，恢复采集后继续
      this.opts.speech.enqueue('网络恢复中，正在重新连接导航。', 'normal');
      await this.opts.pipeline.startNavigating();
      return;
    }

    // 校验地图版本：不一致则结束当前引导
    if (query.mapVersion !== this.opts.map.requireVersion()) {
      this.transition({ type: 'RESUME_FAILED' });
      this.opts.speech.enqueue('地图已更新，请重新发起导航。', 'safety');
      this.opts.onEnded?.({ reason: 'system_error', byArrival: false });
      return;
    }

    const prevState = this.machine.state;
    this.transition({ type: 'QUERY_STATUS', status: query.status });
    // 服务端可能已处理更多更新：序号对齐（只增不减）
    const serverSeq = query.lastProcessedSequence ?? 0;
    if (serverSeq > this.sessionSeq) {
      this.sessionSeq = serverSeq;
    }

    // 重启采集：active/recovering 都继续扫（recovering 需新可信位置才能回 active）
    if (this.machine.state === 'active' || this.machine.state === 'recovering') {
      await this.opts.pipeline.startNavigating();
    }

    // 重播有效指令（过期/缺失则不播）
    if (prevState !== this.machine.state && this.machine.isTerminal) {
      this.opts.onEnded?.({ reason: query.endReason ?? null, byArrival: query.status === 'arrived' });
      return;
    }
    // 重播：active 重播当前转向指令；recovering 重播安全提示（不播旧方向）
    if (this.machine.state === 'active') {
      const replay = this.tracker.replayText();
      if (replay !== null) {
        this.opts.speech.enqueue(replay, 'normal');
      }
    } else if (this.machine.state === 'recovering' && this.lastRelocalizeText !== null) {
      this.opts.speech.enqueue(this.lastRelocalizeText, 'safety');
    }
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
      if (!(err instanceof ApiClientError && err.code === 'SESSION_NOT_ACTIVE')) {
        throw err;
      }
      // 已终态：视为结束成功
    }
    this.transition({ type: 'END_ACK' });
    this.opts.speech.clearAll();
    this.opts.onEnded?.({ reason, byArrival: false });
  }

  // ------------------------------------------------------------------
  // 内部
  // ------------------------------------------------------------------

  private transition(input: Parameters<NavStateMachine['handle']>[0]): void {
    const prevState = this.machine.state;
    const effects = this.machine.handle(input);
    if (this.machine.state !== prevState) {
      this.opts.onStateChange(this.machine.state);
    }
    this.applyEffects(effects);
  }

  private applyEffects(effects: NavEffects): void {
    if (effects.stopPipeline) {
      this.opts.pipeline.stop();
    }
    if (effects.startPipeline) {
      void this.opts.pipeline.startNavigating();
    }
    if (effects.clearNormal) {
      this.opts.speech.clearNormal();
    }
  }

  private async sendUpdate(location: LocationUpdate): Promise<void> {
    if (this.session === null || this.machine.isTerminal) {
      return;
    }
    if (this.updateInFlight) {
      this.pendingLocation = location; // 只保留最新
      return;
    }
    this.updateInFlight = true;
    const sequence = this.sessionSeq + 1;
    const body: NavUpdateRequest = { sequence, location };
    try {
      const resp = await this.opts.client.request<NavUpdateResponse>({
        method: 'POST',
        path: `/api/v1/nav/sessions/${this.session.sessionId}/updates`,
        body,
        timeoutMs: NAV_UPDATE_TIMEOUT_MS,
      });
      this.sessionSeq = sequence;
      this.applyUpdateResponse(resp.data);
    } catch (err) {
      if (err instanceof ApiClientError && err.code === 'STALE_NAV_UPDATE') {
        await this.resyncSequence();
      } else if (err instanceof ApiClientError && err.code === 'SESSION_NOT_ACTIVE') {
        await this.confirmTerminal();
      }
      // 其余（网络/超时）：丢弃本拍，等下一个估计
    } finally {
      this.updateInFlight = false;
    }
    const pending = this.pendingLocation;
    this.pendingLocation = null;
    if (pending !== null && this.session !== null && !this.machine.isTerminal) {
      await this.sendUpdate(pending);
    }
  }

  private applyUpdateResponse(data: NavUpdateResponse | null): void {
    if (data === null || typeof data.status !== 'string') {
      return;
    }
    const prevState = this.machine.state;
    this.transition({ type: 'UPDATE_RESULT', status: data.status, event: data.event });

    if (data.event === 'reroute') {
      this.opts.speech.clearNormal(); // 旧路线提示作废
      if (data.route !== undefined && data.route !== null) {
        this.opts.onRoute?.(data.route);
      }
      this.announce(data.instruction); // 新首条
      return;
    }
    if (data.event === 'arrived') {
      const near = this.lastConfidence < ARRIVAL_NEAR_CONFIDENCE;
      this.opts.speech.enqueue(
        near ? '已到达目的地附近，请留意周围环境。' : '已到达目的地。',
        'turn',
      );
      this.opts.onEnded?.({ reason: 'arrived', byArrival: true });
      return;
    }
    if (data.event === 'relocalizing') {
      // 仅在刚进入 recovering 时播报一次，避免持续重播
      if (prevState !== 'recovering' && this.machine.state === 'recovering') {
        const text = data.instruction?.text ?? '正在重新定位，请原地稍候。';
        this.lastRelocalizeText = text;
        this.opts.speech.enqueue(text, 'safety');
      }
      return;
    }
    this.announce(data.instruction);
  }

  private announce(instruction: NavigationInstruction | null): void {
    if (instruction === null) {
      return;
    }
    if (!this.tracker.observe(instruction)) {
      return; // 重复指令：不重复播报
    }
    this.opts.speech.enqueue(instruction.text, instruction.priority);
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
