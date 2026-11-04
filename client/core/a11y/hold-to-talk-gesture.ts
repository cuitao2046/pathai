/**
 * 全屏按住说话的手势仲裁状态机（纯逻辑，零 wx 依赖）。
 *
 * 规则见 `specs/nav-accessibility-ux/spec.md` §4.1–§4.3，仅在「视障模式」启用：
 * - 1 指按下且不在排除区 → `pending`；
 * - `pending` 内位移超过 `movePx` → 转 `pan`（地图拖拽，10px 启动死区）；
 * - `pending` 保持 `holdMs` → 转 `voice`（等价点按底部「按住说话」）；
 * - 双指及以上 → `zoom`（立即，优先级最高）；
 * - `voice` 内抬手 → `voice-end`；`touchcancel` → `voice-cancel`；
 * - `voice` 持续超过 `maxHoldMs` → `voice-timeout`（自动结束并提交）。
 *
 * 计时由页面负责调度（setTimeout），本模块只接受显式时间戳，便于单测边界。
 */

export type HoldGesturePhase = 'idle' | 'pending' | 'pan' | 'zoom' | 'voice';

export type HoldGestureAction =
  | 'none'
  | 'pending'
  | 'pan-start'
  | 'zoom'
  | 'voice-start'
  | 'voice-end'
  | 'voice-cancel'
  | 'voice-timeout';

export interface HoldGestureConfig {
  /** 长按判定阈值（ms）。 */
  holdMs: number;
  /** 拖拽启动死区（px，相对按下点）。 */
  movePx: number;
  /** 单次语音最长时长（ms），到时自动结束。 */
  maxHoldMs: number;
}

/** 默认参数：500 ms / 10 px / 15 s（spec Q2/Q3/Q12 拍板值）。 */
export const HOLD_TO_TALK_DEFAULTS: HoldGestureConfig = {
  holdMs: 500,
  movePx: 10,
  maxHoldMs: 15000,
};

export interface HoldStartInput {
  x: number;
  y: number;
  pointCount: number;
  /** 按下点是否落在排除区（按钮热区等）。 */
  excluded: boolean;
  now: number;
}

export class HoldToTalkGesture {
  private phase: HoldGesturePhase = 'idle';
  private startX = 0;
  private startY = 0;
  private startAt = 0;
  private voiceAt = 0;

  private readonly config: HoldGestureConfig;

  constructor(config: Partial<HoldGestureConfig> = {}) {
    this.config = { ...HOLD_TO_TALK_DEFAULTS, ...config };
  }

  get currentPhase(): HoldGesturePhase {
    return this.phase;
  }

  /** 按下点坐标（页面坐标），pan 起步时用于避免跳变。 */
  get origin(): { x: number; y: number } {
    return { x: this.startX, y: this.startY };
  }

  reset(): void {
    this.phase = 'idle';
  }

  onStart(input: HoldStartInput): HoldGestureAction {
    this.startX = input.x;
    this.startY = input.y;
    this.startAt = input.now;
    this.voiceAt = 0;
    if (input.pointCount >= 2) {
      this.phase = 'zoom';
      return 'zoom';
    }
    if (input.pointCount !== 1) {
      this.phase = 'idle';
      return 'none';
    }
    if (input.excluded) {
      // 排除区内不参与语音判定，直接交还原有的即时拖拽语义
      this.phase = 'pan';
      return 'pan-start';
    }
    this.phase = 'pending';
    return 'pending';
  }

  /** 移动：pending 内超出死区转 pan；voice 内移动一律忽略。 */
  onMove(x: number, y: number, pointCount: number): HoldGestureAction {
    if (pointCount >= 2) {
      this.phase = 'zoom';
      return 'zoom';
    }
    if (this.phase === 'pending') {
      if (Math.hypot(x - this.startX, y - this.startY) > this.config.movePx) {
        this.phase = 'pan';
        return 'pan-start';
      }
      return 'none';
    }
    if (this.phase === 'pan') {
      return 'pan-start';
    }
    if (this.phase === 'zoom') {
      return 'zoom';
    }
    return 'none';
  }

  /** 页面计时器触发：到达长按阈值 → voice；到达上限 → voice-timeout。 */
  onTick(now: number): HoldGestureAction {
    if (this.phase === 'pending' && now - this.startAt >= this.config.holdMs) {
      this.phase = 'voice';
      this.voiceAt = now;
      return 'voice-start';
    }
    if (this.phase === 'voice' && now - this.voiceAt >= this.config.maxHoldMs) {
      this.phase = 'idle';
      return 'voice-timeout';
    }
    return 'none';
  }

  /** 抬手：voice 结束提交；其余情况只复位阶段。 */
  onEnd(pointCount: number): HoldGestureAction {
    if (this.phase === 'voice') {
      this.phase = 'idle';
      return 'voice-end';
    }
    if (this.phase === 'idle') {
      return 'none';
    }
    // 双指抬一根仍保留 pan；全部抬起回到 idle
    this.phase = pointCount > 0 ? 'pan' : 'idle';
    return 'none';
  }

  /** touchcancel：取消本次语音，不提交。 */
  onCancel(): HoldGestureAction {
    const wasVoice = this.phase === 'voice';
    this.phase = 'idle';
    return wasVoice ? 'voice-cancel' : 'none';
  }
}
