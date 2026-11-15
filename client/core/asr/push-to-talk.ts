/**
 * 按住说话状态机（docs/24 FE-02）：idle/recording/recognizing/error。
 * - touchstart → begin()：recording，启动 ASR 会话；
 * - touchend → end()：recognizing，等待最终文本 → onFinal 提交；
 * - touchcancel / 切后台 → cancel()：取消本次，不提交（回 idle）；
 * - 引擎故障（start 抛错/reject）→ error 状态，可重新 begin。
 */
import { AsrCancelledError, type AsrEngine } from './asr-engine';
import { createLogger } from '../../utils/logger';

const log = createLogger('asr-ptt');

export type PttState = 'idle' | 'recording' | 'recognizing' | 'error';

/**
 * 面向视障用户的口播文案：短、可直接听懂、不含 errMsg/错误码。
 * 原始错误只进日志（log.warn），不念给用户。
 */
export const ASR_EMPTY_TEXT = '没听清，请再说一遍';
export const ASR_PERMISSION_TEXT = '需要麦克风权限，请在设置中开启';
export const ASR_RECORD_FAILED_TEXT = '录音没有成功，请重试';
export const ASR_RECOGNIZE_FAILED_TEXT = '语音识别失败，请重试';

/** 微信/系统录音权限被拒的典型 errMsg（auth deny / authorize fail / 录音权限）。 */
const MIC_DENIED_PATTERN = /auth\s*den|authoriz|permission|mic|麦克风|录音权限/i;

/** 把引擎原始错误收敛为用户可听懂的文案；原文只用于日志。 */
export function describeAsrError(raw: string, fallback: string): string {
  if (raw.length > 0 && MIC_DENIED_PATTERN.test(raw)) {
    return ASR_PERMISSION_TEXT;
  }
  return fallback;
}

export interface PushToTalkOptions {
  engine: AsrEngine;
  /** 最终识别文本产出（已过滤取消与空白文本）。 */
  onFinal: (text: string) => void;
  /** 状态变化通知（页面渲染）。 */
  onStateChange: (state: PttState) => void;
  /** 中间识别结果（可选回显）。 */
  onPartial?: (text: string) => void;
  /** 引擎错误提示（可展示）；已是面向用户的中文文案，不含原始 errMsg。 */
  onError?: (message: string) => void;
  /** 空识别（未录到音频/识别结果为空）时提示，供页面做播报与防抖。 */
  onEmpty?: () => void;
  /**
   * 麦克风真正就绪（录音 onStart）后才回调：页面据此震动/提示「可以开口了」。
   * 不能改用 onStateChange('recording')——那是请求开始录音的瞬间，
   * 首次开麦可能需要数百毫秒，此时开口会丢音（首次按住说话失败的成因）。
   */
  onRecordingReady?: () => void;
}

export class PushToTalk {
  private state: PttState = 'idle';
  private generation = 0;

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
    const generation = ++this.generation;
    this.setState('recording');
    let session: Promise<string>;
    try {
      session = this.opts.engine.start({
        onPartial: (text) => { if (generation === this.generation) this.opts.onPartial?.(text); },
        onReady: () => {
          // 已松手/已失败时不补震动：提示只在「仍可说话」时给出
          if (generation === this.generation && this.state === 'recording') {
            this.opts.onRecordingReady?.();
          }
        },
      });
    } catch (err) {
      this.fail(err, ASR_RECORD_FAILED_TEXT);
      return;
    }
    void session.then(
      (text) => {
        if (generation !== this.generation) return;
        this.setState('idle');
        const trimmed = text.trim();
        if (trimmed.length > 0) {
          this.opts.onFinal(trimmed);
        } else {
          // 快速点按无音频 / 云端返回空串：不能静默回待机，必须给可感知提示
          this.opts.onEmpty?.();
        }
      },
      (err: unknown) => {
        if (generation !== this.generation) return;
        if (err instanceof AsrCancelledError) {
          this.setState('idle'); // 取消不提交
          return;
        }
        this.fail(err, ASR_RECOGNIZE_FAILED_TEXT);
      },
    );
  }

  /** onTouchEnd：松手提交识别。 */
  end(): void {
    if (this.state !== 'recording') {
      return;
    }
    this.setState('recognizing');
    try { this.opts.engine.stop(); } catch (err) {
      ++this.generation;
      try { this.opts.engine.cancel(); } catch (_) { /* best effort */ }
      this.fail(err, ASR_RECOGNIZE_FAILED_TEXT);
    }
  }

  /** onTouchCancel：取消本次，不提交。 */
  cancel(): void {
    if (this.state !== 'recording' && this.state !== 'recognizing') {
      return;
    }
    ++this.generation; // 立即隔离旧会话，取消后的迟到结果不能提交
    try { this.opts.engine.cancel(); } catch (err) { log.warn('取消录音失败', err); } finally { this.setState('idle'); }
  }

  /** app onHide：切后台取消并释放当前会话。 */
  handleAppHide(): void {
    this.cancel();
  }

  private fail(err: unknown, fallback: string): void {
    const raw = err instanceof Error ? err.message : typeof err === 'string' ? err : '';
    const message = describeAsrError(raw, fallback);
    // 原始 errMsg / 错误码只进日志，绝不念给用户
    if (raw.length > 0) {
      log.warn('ASR 会话失败', { raw, userText: message });
    }
    this.opts.onError?.(message);
    this.setState('error');
  }
}
