import { PushToTalk, type PushToTalkOptions, type PttState } from './push-to-talk';
/** 两个适配器共享同一录音状态机；不注册全局触摸/系统手势。 */
export class VoiceInteraction extends PushToTalk {
  private reader = false;
  private timer: ReturnType<typeof setTimeout> | null = null;
  constructor(options: PushToTalkOptions, private readonly maxMs = 15000) {
    super({ ...options, onStateChange: (state) => {
      this.clearTimer();
      if (state === 'recognizing') {
        this.timer = setTimeout(() => {
          this.timer = null;
          this.cancel();
          options.onError?.('语音识别超时，请重试');
        }, 30000);
      }
      options.onStateChange(state);
    } });
  }
  setScreenReaderMode(enabled: boolean): void {
    if (enabled === this.reader) return;
    this.cancel();
    this.reader = enabled;
  }
  touchStart(): void { if (!this.reader) this.begin(); }
  touchEnd(): void { if (!this.reader) this.end(); }
  touchCancel(): void { if (!this.reader) this.cancel(); }
  activate(): void {
    if (!this.reader) return;
    if (this.currentState === 'recording') this.end();
    else if (this.currentState === 'idle' || this.currentState === 'error') this.begin();
  }
  begin(): void {
    if (this.currentState !== 'idle' && this.currentState !== 'error') return;
    this.clearTimer();
    super.begin();
    if ((this.currentState as PttState) === 'recording') {
      this.timer = setTimeout(() => { this.timer = null; this.end(); }, this.maxMs);
    }
  }
  end(): void { this.clearTimer(); super.end(); }
  cancel(): void { this.clearTimer(); super.cancel(); }
  private clearTimer(): void {
    if (this.timer !== null) clearTimeout(this.timer);
    this.timer = null;
  }
}
