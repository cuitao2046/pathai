/**
 * 本地提示音播放器（REQ-MAP 启动提示）。
 *
 * 为什么不用 TtsEngine：启动提示恰恰出现在首启/弱网场景，云端合成可能失败，
 * 必须离线可播；音频文件随代码包分发（`client/assets/voice/`），
 * 用 InnerAudioContext 直接播放，不经讯飞。
 *
 * 契约：
 * - play(name) 入队并按调用顺序串行播放；返回的 Promise 在播完、出错或被打断时
 *   resolve（**失败不 reject**：文件缺失/解码失败一律静默跳过，不能阻塞启动）；
 * - stop() 立即打断当前提示音并清空待播队列（按住说话时抢占语音通道）；
 * - core 零 wx：音频句柄由 createAudio 注入，便于单测。
 */
import { LOCAL_VOICE_DIR } from '../../config/env';

export type LocalVoiceName =
  | 'app-started'
  | 'map-loading'
  | 'map-loaded'
  | 'map-load-failed'
  | 'map-stale';

/** 音频句柄（InnerAudioContext 的最小契约）。 */
export interface LocalVoiceClipHandle {
  play(): void;
  stop(): void;
  destroy(): void;
  onEnded(listener: () => void): void;
  onError(listener: () => void): void;
}

export interface LocalVoiceOptions {
  createAudio: (src: string) => LocalVoiceClipHandle;
  /** 覆盖资源路径解析（默认代码包目录 + `.mp3`）。 */
  srcOf?: (name: LocalVoiceName) => string;
}

interface QueueItem {
  name: LocalVoiceName;
  resolve: () => void;
}

interface ActiveItem {
  handle: LocalVoiceClipHandle;
  finish: () => void;
}

export class LocalVoice {
  private readonly createAudio: (src: string) => LocalVoiceClipHandle;
  private readonly srcOf: (name: LocalVoiceName) => string;
  private queue: QueueItem[] = [];
  private active: ActiveItem | null = null;

  constructor(options: LocalVoiceOptions) {
    this.createAudio = options.createAudio;
    this.srcOf = options.srcOf ?? ((name) => `${LOCAL_VOICE_DIR}/${name}.mp3`);
  }

  /** 入队播放一段提示音。 */
  play(name: LocalVoiceName): Promise<void> {
    return new Promise<void>((resolve) => {
      this.queue.push({ name, resolve });
      this.pump();
    });
  }

  /** 打断当前播报并清空队列（按住说话抢占语音通道）。 */
  stop(): void {
    const pending = this.queue;
    this.queue = [];
    for (const item of pending) {
      item.resolve();
    }
    const active = this.active;
    if (active === null) {
      return;
    }
    this.active = null;
    try {
      active.handle.stop();
    } catch {
      // 停止失败按已结束处理
    }
    active.finish();
  }

  private pump(): void {
    if (this.active !== null) {
      return;
    }
    const item = this.queue.shift();
    if (item === undefined) {
      return;
    }
    const handle = this.createAudio(this.srcOf(item.name));
    let settled = false;
    const entry: ActiveItem = {
      handle,
      finish: () => {
        if (settled) {
          return;
        }
        settled = true;
        if (this.active === entry) {
          this.active = null;
        }
        try {
          handle.destroy();
        } catch {
          // 句柄销毁失败忽略
        }
        item.resolve();
        this.pump();
      },
    };
    this.active = entry;
    handle.onEnded(() => entry.finish());
    handle.onError(() => entry.finish());
    try {
      handle.play();
    } catch {
      entry.finish();
    }
  }
}
