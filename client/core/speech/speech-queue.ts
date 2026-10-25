/**
 * 语音播报队列（FE-04）：优先级 safety > turn > normal。
 * - safety（危险警示）：清空全部低优先级待播项并打断正在播的非 safety 项；
 *   正在播 safety 时新 safety 仍排队，不打断。
 * - turn（导航转向）：丢弃待播 normal，正在播 normal 时打断；不打断 safety/turn。
 * - normal（提示类）：FIFO；出队时已过期（expiresAtMs 不晚于当前时间）则丢弃。
 * 引擎失败（speak reject）：视为语音通道永久不可用——清空队列、长震动、
 * onUnavailable 恰一次回调；待播文本与后续 enqueue 均走 onTextFallback
 * （屏幕可访问文本）。第一阶段不做本地语音兜底，安全暂停由上层导航逻辑负责。
 */
import { nowMs } from '../../utils/clock';
import { vibrateLong, vibrateShort } from '../../utils/haptic';
import type { TtsEngine } from './tts-engine';

export type SpeechPriority = 'normal' | 'turn' | 'safety';

interface QueueItem {
  text: string;
  priority: SpeechPriority;
  expiresAtMs: number | null;
}

export interface SpeechQueueOptions {
  engine: TtsEngine;
  /** 引擎不可用时回调（恰一次），参数为失败时正在播报的文本。 */
  onUnavailable: (lastText: string) => void;
  /** 文本降级通道：语音不可用后的屏幕可访问文本展示。 */
  onTextFallback?: (text: string) => void;
}

const PRIORITY_RANK: Record<SpeechPriority, number> = { safety: 0, turn: 1, normal: 2 };

export class SpeechQueue {
  private readonly engine: TtsEngine;
  private readonly notifyUnavailable: (lastText: string) => void;
  private readonly fallbackText: ((text: string) => void) | null;
  private items: QueueItem[] = [];
  private current: QueueItem | null = null;
  private engineBroken = false;

  constructor(options: SpeechQueueOptions) {
    this.engine = options.engine;
    this.notifyUnavailable = options.onUnavailable;
    this.fallbackText = options.onTextFallback ?? null;
  }

  enqueue(
    text: string,
    priority: SpeechPriority = 'normal',
    expiresAtMs: number | null = null,
  ): void {
    if (this.engineBroken) {
      // 语音通道已失效：短震动提示 + 文本降级，不再尝试引擎
      vibrateShort();
      this.fallbackText?.(text);
      return;
    }
    if (priority === 'safety') {
      this.items = this.items.filter((i) => i.priority === 'safety');
      if (this.current !== null && this.current.priority !== 'safety') {
        this.engine.stop(); // 契约：stop 使 speak 以 resolve 结束
      }
    } else if (priority === 'turn') {
      this.items = this.items.filter((i) => i.priority !== 'normal');
      if (this.current !== null && this.current.priority === 'normal') {
        this.engine.stop();
      }
    }
    this.items.push({ text, priority, expiresAtMs });
    this.pump();
  }

  /** 清除待播 normal 项（如离开导航场景）；不影响正在播与更高优先级。 */
  clearNormal(): void {
    this.items = this.items.filter((i) => i.priority !== 'normal');
  }

  /** 清空全部待播项；不打断正在播的项。 */
  clearAll(): void {
    this.items = [];
  }

  private pump(): void {
    if (this.engineBroken || this.current !== null) {
      return;
    }
    const next = this.dequeueNext();
    if (next === null) {
      return;
    }
    this.current = next;
    this.engine
      .speak(next.text)
      .then(() => {
        this.current = null;
        this.pump();
      })
      .catch(() => {
        this.handleFailure(next);
      });
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
    const pending = this.items;
    this.items = [];
    this.current = null;
    this.engineBroken = true;
    vibrateLong();
    this.notifyUnavailable(failed.text);
    for (const item of pending) {
      this.fallbackText?.(item.text);
    }
  }
}
