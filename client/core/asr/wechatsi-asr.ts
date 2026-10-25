/**
 * WechatSI（微信同声传译插件）ASR 引擎实现。
 * getRecordRecognitionManager 单例管理器；stop() 后最终文本经 onStop 送达。
 * 真机权限/配额问题在 C8 真机清单覆盖；单测不覆盖本文件（PTT 用 Mock 引擎）。
 */
import { loadPlugin } from '../../utils/plugin-loader';
import { AsrCancelledError, type AsrEngine, type AsrHandlers } from './asr-engine';

interface RecognitionManager {
  onRecognize(cb: (res: { result?: string }) => void): void;
  onStop(cb: (res: { result?: string }) => void): void;
  onError(cb: (res: { msg?: string }) => void): void;
  start(option: { duration: number; lang: string }): void;
  stop(): void;
}

interface WechatSiPlugin {
  getRecordRecognitionManager(): RecognitionManager;
}

const ASR_LANG = 'zh_CN';
/** 单次识别最长时长（毫秒）：插件上限 30s。 */
const MAX_DURATION_MS = 30000;

interface AsrPending {
  resolve: (text: string) => void;
  reject: (err: Error) => void;
  onPartial?: (text: string) => void;
}

export class WechatsiAsrEngine implements AsrEngine {
  private manager: RecognitionManager | null = null;
  private pending: AsrPending | null = null;

  start(handlers: AsrHandlers): Promise<string> {
    if (this.pending !== null) {
      return Promise.reject(new Error('ASR 会话进行中'));
    }
    return new Promise<string>((resolve, reject) => {
      this.pending = { resolve, reject, onPartial: handlers.onPartial };
      try {
        this.ensureManager().start({ duration: MAX_DURATION_MS, lang: ASR_LANG });
      } catch (err) {
        this.pending = null;
        reject(err instanceof Error ? err : new Error('语音识别启动失败'));
      }
    });
  }

  stop(): void {
    this.manager?.stop(); // 最终文本由 onStop 事件 settle
  }

  cancel(): void {
    const pending = this.pending;
    this.pending = null; // 先摘除，避免插件 stop 事件 settle
    this.manager?.stop();
    pending?.reject(new AsrCancelledError());
  }

  private ensureManager(): RecognitionManager {
    if (this.manager === null) {
      const plugin = loadPlugin<WechatSiPlugin>('WechatSI');
      const manager = plugin.getRecordRecognitionManager();
      manager.onRecognize((res) => {
        if (typeof res.result === 'string' && res.result.length > 0) {
          this.pending?.onPartial?.(res.result);
        }
      });
      manager.onStop((res) => {
        const pending = this.pending;
        this.pending = null;
        pending?.resolve(typeof res.result === 'string' ? res.result : '');
      });
      manager.onError((res) => {
        const pending = this.pending;
        this.pending = null;
        pending?.reject(new Error(res.msg ?? '语音识别错误'));
      });
      this.manager = manager;
    }
    return this.manager;
  }
}
