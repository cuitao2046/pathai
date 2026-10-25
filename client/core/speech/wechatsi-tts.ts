/**
 * WechatSI（微信同声传译插件）TTS 引擎实现。
 * 合成 → 音频文件 → InnerAudioContext 播放；stop() 经 onStop 结束播放。
 * 真机行为差异（配额、后台限制）在 C8 真机清单覆盖；单测用 Mock 引擎。
 */
import { loadPlugin } from '../../utils/plugin-loader';
import type { TtsEngine } from './tts-engine';

interface WechatSiPlugin {
  textToSpeech(options: {
    lang: string;
    tts: boolean;
    content: string;
    success(res: { filename?: string; retcode?: number }): void;
    fail(err: unknown): void;
  }): void;
}

function toError(err: unknown, fallback: string): Error {
  return err instanceof Error ? err : new Error(fallback);
}

export class WechatsiTts implements TtsEngine {
  private audio: WechatMiniprogram.InnerAudioContext | null = null;

  async speak(text: string): Promise<void> {
    const filename = await this.synthesize(text);
    await this.play(filename);
  }

  stop(): void {
    this.audio?.stop();
  }

  private synthesize(text: string): Promise<string> {
    return new Promise<string>((resolve, reject) => {
      let plugin: WechatSiPlugin;
      try {
        plugin = loadPlugin<WechatSiPlugin>('WechatSI');
      } catch (err) {
        reject(toError(err, 'WechatSI 插件加载失败'));
        return;
      }
      plugin.textToSpeech({
        lang: 'zh_CN',
        tts: true,
        content: text,
        success: (res) => {
          if (res && typeof res.filename === 'string' && res.filename.length > 0) {
            resolve(res.filename);
          } else {
            reject(new Error('TTS 合成未返回有效音频文件'));
          }
        },
        fail: (err) => reject(toError(err, 'TTS 合成失败')),
      });
    });
  }

  private play(url: string): Promise<void> {
    return new Promise<void>((resolve, reject) => {
      const audio = wx.createInnerAudioContext();
      this.audio = audio;
      let settled = false;
      const settle = (err: Error | null) => {
        if (settled) {
          return;
        }
        settled = true;
        audio.destroy();
        if (this.audio === audio) {
          this.audio = null;
        }
        if (err === null) {
          resolve();
        } else {
          reject(err);
        }
      };
      audio.onEnded(() => settle(null));
      audio.onStop(() => settle(null)); // stop() 视为播完，交还队列控制权
      audio.onError((err) => settle(toError(err, 'TTS 音频播放失败')));
      audio.src = url;
      audio.play();
    });
  }
}
