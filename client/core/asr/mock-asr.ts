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
  startCount = 0;
  stopCount = 0;
  cancelCount = 0;

  private session: AsrSession | null = null;
  private rawText = '带我去图书馆';
  /** 用 `|` 分隔的多句脚本；每 stop 一次取下一句，取完后停在最后一句。 */
  private script: string[] = [];
  private scriptIndex = 0;

  /**
   * 识别文本（调试注入）。
   * 支持用 `|` 分隔多句，例如「我要去2楼的历史教室。|开始导航」，
   * 使语音确认这类多轮流程也能在 mock 下走通；单句行为不变。
   */
  get nextText(): string {
    return this.rawText;
  }

  set nextText(text: string) {
    this.rawText = text;
    this.script = text.split('|').map((part) => part.trim());
    this.scriptIndex = 0;
  }

  start(handlers: AsrHandlers): Promise<string> {
    if (this.session !== null) {
      return Promise.reject(new Error('ASR 会话进行中'));
    }
    this.startCount += 1;
    return new Promise<string>((resolve, reject) => {
      this.session = { resolve, reject, onPartial: handlers.onPartial };
      // 模拟真实录音「麦克风稍后就绪」：异步通知，页面据此震动/提示
      setTimeout(() => {
        if (this.session !== null) {
          handlers.onReady?.();
        }
      }, 0);
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
      this.settle(this.takeText());
    }, 0);
  }

  /** 取本句话术：脚本按次推进，耗尽后停在最后一句；未配置脚本时用单句默认值。 */
  private takeText(): string {
    if (this.script.length === 0) {
      return this.rawText;
    }
    const index = Math.min(this.scriptIndex, this.script.length - 1);
    this.scriptIndex += 1;
    return this.script[index];
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
