/**
 * 调试用 Mock ASR 引擎：以注入文本替代真实语音识别（开发者工具无麦克风权限依赖）。
 * stop() 模拟插件 onStop 异步送达最终文本；cancel() 使会话以 AsrCancelledError 终止。
 */
import { AsrCancelledError, type AsrEngine, type AsrHandlers } from './asr-engine';

interface AsrSession {
  resolve: (text: string) => void;
  reject: (err: Error) => void;
  onPartial?: (text: string) => void;
}

export class MockAsrEngine implements AsrEngine {
  /** 下一次会话 stop() 后产出的识别文本（调试注入）。 */
  nextText = '带我去图书馆';
  startCount = 0;
  stopCount = 0;
  cancelCount = 0;

  private session: AsrSession | null = null;

  start(handlers: AsrHandlers): Promise<string> {
    if (this.session !== null) {
      return Promise.reject(new Error('ASR 会话进行中'));
    }
    this.startCount += 1;
    return new Promise<string>((resolve, reject) => {
      this.session = { resolve, reject, onPartial: handlers.onPartial };
    });
  }

  stop(): void {
    this.stopCount += 1;
    const session = this.session;
    if (session === null) {
      return;
    }
    // 模拟插件 onStop 事件异步送达，保证 stop 与 cancel 的竞争行为一致
    setTimeout(() => {
      this.settle(this.nextText);
    }, 0);
  }

  cancel(): void {
    this.cancelCount += 1;
    const session = this.session;
    this.session = null;
    session?.reject(new AsrCancelledError());
  }

  /** 测试辅助：注入中间识别结果。 */
  emitPartial(text: string): void {
    this.session?.onPartial?.(text);
  }

  /** 测试辅助：不经 stop 直接产出最终文本（模拟插件提前 onStop）。 */
  emitResult(text: string): void {
    this.settle(text);
  }

  /** 测试辅助：模拟引擎故障。 */
  emitError(err: Error): void {
    const session = this.session;
    this.session = null;
    session?.reject(err);
  }

  private settle(text: string): void {
    const session = this.session;
    this.session = null;
    session?.resolve(text);
  }
}
