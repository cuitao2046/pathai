/**
 * 语音播报队列（FE-04）：优先级 safety > turn > normal。
 * - safety（危险警示）：清空全部低优先级待播项并打断正在播的非 safety 项；
 *   正在播 safety 时新 safety 仍排队，不打断。
 * - turn（导航转向）：丢弃待播 normal，正在播 normal 时打断；不打断 safety/turn。
 * - normal（提示类）：FIFO；出队时已过期（expiresAtMs 不晚于当前时间）则丢弃。
 * 引擎失败（speak reject）：视为语音通道不可用——清空队列、长震动、
 * onUnavailable 恰一次回调；待播文本与后续 enqueue 均走 onTextFallback
 * （屏幕可访问文本）。resetForRetry() 提供恢复路径（新导航会话等时机
 * 重新尝试引擎），避免单次网络抖动导致整段使用期永久哑音。
 * 第一阶段不做本地语音兜底，安全暂停由上层导航逻辑负责。
 * 引擎无响应（speak 既不 resolve 也不 reject，如播放器不回调 onEnded/onError）：
 * 每条播报都有超时看门狗，超时按「被打断」处理并继续排队列，避免整条导航被一句
 * 播报挂死（真机现象：HUD 永远停在「正在播报路线概要…」）。
 */
import { nowMs } from '../../utils/clock';
import { vibrateLong, vibrateShort } from '../../utils/haptic';
import { createLogger } from '../../utils/logger';
import type { TtsEngine } from './tts-engine';

const log = createLogger('speech');

export type SpeechPriority = 'normal' | 'turn' | 'safety';

/**
 * 队列安静的原因：自然播完（finished）/ 被抢占或清空（interrupted）/
 * 引擎无响应被看门狗放手（stalled，播报内容未完成，但队列已恢复）。
 */
export type SpeechIdleReason = 'finished' | 'interrupted' | 'stalled';

interface QueueItem {
  /** text=云端 TTS 文本；clip=本地音频片段（离线可播，不经引擎）。 */
  kind: 'text' | 'clip';
  /** 文本内容；clip 项为 `[clip]名称`，仅用于日志与看门狗度量。 */
  text: string;
  /** clip 项的资源名（如 nav-start）。 */
  clip?: string;
  priority: SpeechPriority;
  expiresAtMs: number | null;
  /** 入队来源标记（排查"谁打断了概要"用）。 */
  source: string;
}

export interface SpeechQueueOptions {
  engine: TtsEngine;
  /** 引擎不可用时回调（恰一次），参数为失败时正在播报的文本。 */
  onUnavailable: (lastText: string) => void;
  /** 文本降级通道：语音不可用后的屏幕可访问文本展示。 */
  onTextFallback?: (text: string) => void;
  /**
   * 单条播报的超时上限（毫秒）；不传按文本长度估算。
   * 超时视为引擎无响应：停止该项、按打断处理并继续队列。
   */
  stallTimeoutMs?: (text: string) => number;
  /**
   * 本地音频片段播放器（固定提示语用，离线可播、不依赖云端合成）。
   * 必须在播完、出错或被打断时 resolve；未提供时片段按已播完处理。
   */
  playClip?: (clip: string) => Promise<void>;
  /** 打断正在播的本地音频片段（片段被抢占/看门狗放手时调用）。 */
  stopClip?: () => void;
}

/** 播报看门狗：基础时长 + 每字时长，封顶 60s（正常一句 60 字 ≈ 32s）。 */
const STALL_BASE_MS = 8_000;
const STALL_PER_CHAR_MS = 400;
const STALL_MAX_MS = 60_000;
/** 本地音频片段看门狗：固定提示音远小于该值；播放器无回调时兜底放手。 */
const CLIP_STALL_MS = 15_000;

function defaultStallTimeout(text: string): number {
  return Math.min(STALL_MAX_MS, STALL_BASE_MS + text.length * STALL_PER_CHAR_MS);
}

const PRIORITY_RANK: Record<SpeechPriority, number> = { safety: 0, turn: 1, normal: 2 };

export class SpeechQueue {
  private readonly engine: TtsEngine;
  private readonly notifyUnavailable: (lastText: string) => void;
  private readonly fallbackText: ((text: string) => void) | null;
  private readonly stallTimeout: (text: string) => number;
  private readonly playClip: ((clip: string) => Promise<void>) | null;
  private readonly stopClip: (() => void) | null;
  private items: QueueItem[] = [];
  private current: QueueItem | null = null;
  /** 当前项的看门狗（引擎无响应时强制放手）。 */
  private stallTimer: ReturnType<typeof setTimeout> | null = null;
  private engineBroken = false;
  /** 队列排空（含打断/丢弃/引擎失效）后回调，用于「概要播完再进入引导」。 */
  private idleWaiters: Array<(reason: SpeechIdleReason) => void> = [];
  /** 本次播放期间是否发生过抢占：用于区分「播完」与「被打断」。 */
  private interrupted = false;
  /** 本次播放期间是否发生过看门狗放手（优先级高于 interrupted：引擎故障更值得上报）。 */
  private stalled = false;
  /** 最近一次入队文本：「再说一遍」的重播源。 */
  private lastText: string | null = null;

  constructor(options: SpeechQueueOptions) {
    this.engine = options.engine;
    this.notifyUnavailable = options.onUnavailable;
    this.fallbackText = options.onTextFallback ?? null;
    this.stallTimeout = options.stallTimeoutMs ?? defaultStallTimeout;
    this.playClip = options.playClip ?? null;
    this.stopClip = options.stopClip ?? null;
  }

  /** 录音抢占：清空队列并停止播放，迟到播放回调被 current 身份隔离。 */
  interrupt(): void {
    this.items = [];
    this.clearStallTimer();
    const current = this.current;
    this.current = null;
    if (current?.kind === 'clip') this.stopClip?.();
    else if (current) this.engine.stop();
    this.interrupted = true;
    this.flushIdle('interrupted');
  }

  /** 最近一次入队/播报的文本；无播报历史时为 null。 */
  get lastSpokenText(): string | null {
    return this.lastText;
  }

  enqueue(
    text: string,
    priority: SpeechPriority = 'normal',
    expiresAtMs: number | null = null,
    source = 'unknown',
  ): void {
    this.lastText = text; // 先记重播源：降级文本也算一次播报
    log.info('[enqueue]', source, priority, `len=${text.length}`, text, {
      playing: this.current?.text ?? null,
      pending: this.items.length,
    });
    if (this.engineBroken) {
      // 语音通道已失效：短震动提示 + 文本降级，不再尝试引擎
      vibrateShort();
      this.fallbackText?.(text);
      return;
    }
    this.push({ kind: 'text', text, priority, expiresAtMs, source });
  }

  /**
   * 入队一段本地音频（固定提示语，如「现在开始导航。」）。
   *
   * 与 text 项共用一个队列与同一套优先级/抢占规则，因此与概要和首条指令
   * 严格串行、不会重叠；本地文件不经云端合成，TTS 引擎不可用时仍可播。
   */
  enqueueClip(clip: string, priority: SpeechPriority = 'normal', source = 'unknown'): void {
    const label = `[clip]${clip}`;
    // 不更新 lastText：固定提示音不作为「再说一遍」的重播源
    log.info('[enqueue-clip]', source, priority, clip, {
      playing: this.current?.text ?? null,
      pending: this.items.length,
    });
    this.push({ kind: 'clip', text: label, clip, priority, expiresAtMs: null, source });
  }

  private push(item: QueueItem): void {
    if (item.priority === 'safety') {
      this.items = this.items.filter((i) => i.priority === 'safety');
      if (this.current !== null && this.current.priority !== 'safety') {
        log.warn('[preempt]', item.source, 'safety 抢占正在播的', this.current.priority, this.current.text);
        this.interrupted = true;
        this.stopCurrent(); // 契约：stop 使 play/speak 以 resolve 结束
      }
    } else if (item.priority === 'turn') {
      this.items = this.items.filter((i) => i.priority !== 'normal');
      if (this.current !== null && this.current.priority === 'normal') {
        log.warn('[preempt]', item.source, 'turn 抢占正在播的 normal', this.current.text);
        this.interrupted = true;
        this.stopCurrent();
      }
    }
    this.items.push(item);
    this.pump();
  }

  /** 打断正在播的项：本地音频片段与云端播报走各自的停止通道。 */
  private stopCurrent(): void {
    if (this.current === null) {
      return;
    }
    if (this.current.kind === 'clip') {
      this.stopClip?.();
      return;
    }
    this.engine.stop();
  }

  /** 清除待播 normal 项（如离开导航场景）；不影响正在播与更高优先级。 */
  clearNormal(source = 'unknown'): void {
    const dropped = this.items.filter((i) => i.priority === 'normal');
    this.items = this.items.filter((i) => i.priority !== 'normal');
    log.info('[clearNormal]', source, { dropped: dropped.map((i) => i.text), playing: this.current?.text ?? null });
    this.flushIdle('interrupted');
  }

  /** 清空全部待播项；不打断正在播的项。 */
  clearAll(source = 'unknown'): void {
    log.info('[clearAll]', source, { dropped: this.items.map((i) => i.text), playing: this.current?.text ?? null });
    this.items = [];
    this.flushIdle('interrupted');
  }

  /**
   * 队列彻底安静（无正在播、无待播）后回调一次。
   * reason=finished 表示自然播完；interrupted 表示被抢占/清空/引擎失效提前结束，
   * 调用方不能用它当作「已播完」（如概要被打断时不应视为首条指令已就绪）。
   * 已安静时异步回调（finished），保证调用方先完成 enqueue 再触发。
   */
  notifyWhenIdle(callback: (reason: SpeechIdleReason) => void): void {
    log.info('[idle-wait]', { playing: this.current?.text ?? null, pending: this.items.length });
    if (this.current === null && this.items.length === 0) {
      void Promise.resolve().then(() => callback('finished'));
      return;
    }
    this.idleWaiters.push(callback);
  }

  private flushIdle(reason: SpeechIdleReason): void {
    if (this.current !== null || this.items.length > 0) {
      return; // 还没安静：interrupted 标记留到真正排空时消费
    }
    // 无论有没有等待者都消费掉抢占/看门狗标记，避免污染下一次排空判定。
    const effective: SpeechIdleReason = this.stalled
      ? 'stalled'
      : this.interrupted
        ? 'interrupted'
        : reason;
    this.stalled = false;
    this.interrupted = false;
    if (this.idleWaiters.length === 0) {
      return;
    }
    const waiters = this.idleWaiters;
    this.idleWaiters = [];
    log.info('[idle]', effective, `waiters=${waiters.length}`);
    for (const waiter of waiters) {
      waiter(effective);
    }
  }

  /**
   * 语音通道重试窗口：解除失效状态，后续 enqueue 重新尝试引擎。
   * 供新导航会话创建等时机调用；若引擎仍不可用会再次走失败流程。
   * @returns 重置前是否处于失效状态（供调用方提示「语音已恢复」）。
   */
  resetForRetry(): boolean {
    const wasBroken = this.engineBroken;
    this.engineBroken = false;
    return wasBroken;
  }

  private pump(): void {
    if (this.current !== null) {
      return;
    }
    const next = this.dequeueNext();
    if (next === null) {
      this.flushIdle('finished');
      return;
    }
    this.current = next;
    const isClip = next.kind === 'clip';
    log.info('[play]', next.source, next.priority, next.text);
    this.stallTimer = setTimeout(
      () => {
        this.handleStall(next);
      },
      isClip ? CLIP_STALL_MS : Math.max(1, this.stallTimeout(next.text)),
    );
    const playback =
      next.kind === 'clip'
        ? this.playClip !== null
          ? this.playClip(next.clip ?? '')
          : Promise.resolve() // 未注入播放器：片段按已播完跳过，绝不回退成 TTS 读文件名
        : this.engine.speak(next.text);
    playback
      .then(() => {
        if (this.current !== next) {
          // 已被看门狗/抢占放手：迟到的结束信号不能再清空或推进队列
          log.warn('[play-late]', next.text);
          return;
        }
        this.clearStallTimer();
        log.info('[play-end]', next.text);
        this.current = null;
        this.pump();
      })
      .catch(() => {
        if (this.current !== next) {
          log.warn('[engine-failure-stale]', next.text);
          return;
        }
        if (next.kind === 'clip') {
          // 本地片段失败（文件缺失/解码失败）：静默跳过，不影响后续播报
          this.clearStallTimer();
          log.warn('[clip-failure]', next.text);
          this.current = null;
          this.pump();
          return;
        }
        this.handleFailure(next);
      });
  }

  /**
   * 看门狗：引擎既不 resolve 也不 reject（播放器无回调/无音频设备/会话 0），
   * 强制放手当前项，按「被打断」继续队列，避免队列永远认为「在播」。
   */
  private handleStall(stalled: QueueItem): void {
    this.stallTimer = null;
    if (this.current !== stalled) {
      return; // 已正常结束或被抢占
    }
    log.error('[stall]', stalled.source, stalled.priority, `len=${stalled.text.length}`, stalled.text);
    this.stalled = true;
    this.interrupted = true;
    this.current = null;
    try {
      // 本地片段用 stopClip，文本用引擎 stop；即使 stop 也唤不醒也不再等待
      if (stalled.kind === 'clip') {
        this.stopClip?.();
      } else {
        this.engine.stop();
      }
    } catch (err) {
      log.warn('[stall] engine.stop 异常', err instanceof Error ? err.message : err);
    }
    this.pump();
  }

  private clearStallTimer(): void {
    if (this.stallTimer !== null) {
      clearTimeout(this.stallTimer);
      this.stallTimer = null;
    }
  }

  /** 出队下一项：先丢弃过期项，再按 safety > turn > normal 取（同级 FIFO）。 */
  private dequeueNext(): QueueItem | null {
    const now = nowMs();
    this.items = this.items.filter((i) => i.expiresAtMs === null || i.expiresAtMs > now);
    if (this.items.length === 0) {
      return null;
    }
    let best = 0;
    for (let i = 1; i < this.items.length; i += 1) {
      if (PRIORITY_RANK[this.items[i].priority] < PRIORITY_RANK[this.items[best].priority]) {
        best = i;
      }
    }
    const [item] = this.items.splice(best, 1);
    return item;
  }

  private handleFailure(failed: QueueItem): void {
    log.error('[engine-failure]', failed.source, failed.text);
    this.clearStallTimer();
    const pending = this.items;
    this.items = [];
    this.current = null;
    this.engineBroken = true;
    vibrateLong();
    this.notifyUnavailable(failed.text);
    for (const item of pending) {
      if (item.kind === 'text') {
        this.fallbackText?.(item.text);
      }
    }
    this.flushIdle('interrupted');
  }
}
