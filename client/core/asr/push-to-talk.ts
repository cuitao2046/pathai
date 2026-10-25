/**
 * 按住说话状态机（docs/24 FE-02）：idle/recording/recognizing/error。
 * - touchstart → begin()：recording，启动 ASR 会话；
 * - touchend → end()：recognizing，等待最终文本 → onFinal 提交；
 * - touchcancel / 切后台 → cancel()：取消本次，不提交（回 idle）；
 * - 引擎故障（start 抛错/reject）→ error 状态，可重新 begin。
 */
import { AsrCancelledError, type AsrEngine } from './asr-engine';

export type PttState = 'idle' | 'recording' | 'recognizing' | 'error';

export interface PushToTalkOptions {
  engine: AsrEngine;
  /** 最终识别文本产出（已过滤取消与空白文本）。 */
  onFinal: (text: string) => void;
  /** 状态变化通知（页面渲染）。 */
  onStateChange: (state: PttState) => void;
  /** 中间识别结果（可选回显）。 */
  onPartial?: (text: string) => void;
  /** 引擎错误提示（可展示）。 */
  onError?: (message: string) => void;
}

export class PushToTalk {
  private state: PttState = 'idle';

  constructor(private readonly opts: PushToTalkOptions) {}

  get currentState(): PttState {
    return this.state;
  }

  private setState(next: PttState): void {
    if (this.state === next) {
      return;
    }
    this.state = next;
    this.opts.onStateChange(next);
  }

  /** onTouchStart：开始聆听。 */
  begin(): void {
    if (this.state !== 'idle' && this.state !== 'error') {
      return;
    }
    this.setState('recording');
    let session: Promise<string>;
    try {
      session = this.opts.engine.start({
        onPartial: (text) => this.opts.onPartial?.(text),
      });
    } catch (err) {
      this.fail(err, '语音识别启动失败');
      return;
    }
    void session.then(
      (text) => {
        this.setState('idle');
        const trimmed = text.trim();
        if (trimmed.length > 0) {
          this.opts.onFinal(trimmed);
        }
      },
      (err: unknown) => {
        if (err instanceof AsrCancelledError) {
          this.setState('idle'); // 取消不提交
          return;
        }
        this.fail(err, '语音识别失败');
      },
    );
  }

  /** onTouchEnd：松手提交识别。 */
  end(): void {
    if (this.state !== 'recording') {
      return;
    }
    this.setState('recognizing');
    this.opts.engine.stop();
  }

  /** onTouchCancel：取消本次，不提交。 */
  cancel(): void {
    if (this.state !== 'recording' && this.state !== 'recognizing') {
      return;
    }
    this.opts.engine.cancel(); // AsrCancelledError → 会话回调中回 idle
  }

  /** app onHide：切后台取消并释放当前会话。 */
  handleAppHide(): void {
    this.cancel();
  }

  private fail(err: unknown, fallback: string): void {
    const message = err instanceof Error && err.message.length > 0 ? err.message : fallback;
    this.opts.onError?.(message);
    this.setState('error');
  }
}
