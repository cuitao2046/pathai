/**
 * 调试/兜底用 Mock TTS 引擎：记录播报文本，立即完成。
 * 开发者工具联调（无讯飞网络环境）由应用装配层选用（C8 引擎自检）。
 */
import type { TtsEngine } from './tts-engine';

export class MockTtsEngine implements TtsEngine {
  /** 已请求播放的文本（按请求顺序）。 */
  readonly spoken: string[] = [];
  stopCount = 0;
  /** 可注入的自定义行为（延迟/失败模拟）；默认立即完成。 */
  speakImpl: (text: string) => Promise<void> = () => Promise.resolve();

  speak(text: string): Promise<void> {
    this.spoken.push(text);
    return this.speakImpl(text);
  }

  stop(): void {
    this.stopCount += 1;
  }
}
