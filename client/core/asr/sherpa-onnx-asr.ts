/**
 * Sherpa-Onnx 端侧 ASR 引擎（AsrEngine 契约实现，live 数据源使用）。
 *
 * 流式链路（对应原 Wechatsi 插件的会话语义）：
 *  start → 确保运行时（模型/分包/wasm，首启含一次下载）→ 开始录音，
 *          帧边录边喂在线识别器，onPartial 节流回吐中间结果；
 *  stop  → 等最后一帧 → flush 解码 → 以最终文本 resolve；
 *  cancel→ 摘除会话 + 停录 + AsrCancelledError reject（不提交）。
 *
 * runtime/recorder 均可注入（单测用假件，不碰 wx 与 wasm）。
 */
import { AsrCancelledError, type AsrEngine, type AsrHandlers } from './asr-engine';
import { PcmRecorder, type RecorderLike } from './pcm-recorder';
import { ensureAsrRuntime, type AsrRuntime, type OnlineRecognizerLike, type OnlineStreamLike } from './sherpa-runtime';

/** 中间结果回吐节流：每累计 0.5s 采样且文本有变化才发。 */
const PARTIAL_INTERVAL_S = 0.5;
const SAMPLE_RATE = 16000;

interface PendingSession {
  resolve: (text: string) => void;
  reject: (err: Error) => void;
  handlers: AsrHandlers;
  /** 已发出的最新 partial 文本（变化才再发）。 */
  lastPartial: string;
  /** 距上次 partial 的累计采样数。 */
  samplesSincePartial: number;
  /** stop() 已调用（等待会话收尾）。 */
  stopped: boolean;
}

export interface SherpaEngineDeps {
  /** 识别运行时提供者（默认 ensureAsrRuntime；单测注入假 recognizer）。 */
  runtime?: () => Promise<AsrRuntime>;
  /** PCM 录音器（默认真实；单测注入假 recorder）。 */
  recorder?: RecorderLike;
}

export class SherpaOnnxAsrEngine implements AsrEngine {
  private readonly runtime: () => Promise<AsrRuntime>;
  private readonly recorder: RecorderLike;
  private pending: PendingSession | null = null;
  private recognizer: OnlineRecognizerLike | null = null;
  private stream: OnlineStreamLike | null = null;

  constructor(deps: SherpaEngineDeps = {}) {
    this.runtime = deps.runtime ?? ensureAsrRuntime;
    this.recorder = deps.recorder ?? new PcmRecorder();
  }

  /** 预热（装配后调用）：提前完成模型下载与 wasm 初始化，失败仅记日志、首按时重试。 */
  warmup(): void {
    void this.runtime().catch(() => {
      // 首次失败不致命：start() 会再次尝试并把错误交 PTT error 态
    });
  }

  start(handlers: AsrHandlers): Promise<string> {
    if (this.pending !== null) {
      throw new Error('ASR 会话进行中'); // 契约：重复 start 同步抛错
    }
    const session: PendingSession = {
      resolve: () => undefined,
      reject: () => undefined,
      handlers,
      lastPartial: '',
      samplesSincePartial: 0,
      stopped: false,
    };
    const promise = new Promise<string>((resolve, reject) => {
      session.resolve = resolve;
      session.reject = reject;
    });
    this.pending = session;
    void this.beginSession(session);
    return promise;
  }

  stop(): void {
    const session = this.pending;
    if (session === null || session.stopped) {
      return;
    }
    session.stopped = true;
    void this.finalize(session);
  }

  cancel(): void {
    const session = this.pending;
    if (session === null) {
      return;
    }
    this.pending = null; // 先摘除：其后的录音/解码回调不再 settle 本会话
    this.recorder.cancel();
    this.teardownSession();
    session.reject(new AsrCancelledError());
  }

  // ------------------------------------------------------------------
  // 会话内部
  // ------------------------------------------------------------------

  private async beginSession(session: PendingSession): Promise<void> {
    try {
      const { recognizer } = await this.runtime();
      if (this.pending !== session) {
        return; // 会话已被 cancel
      }
      this.recognizer = recognizer;
      this.stream = recognizer.createStream();
      if (session.stopped) {
        // runtime 就绪前用户已松手：无可录内容，直接空文本收尾
        await this.finalize(session);
        return;
      }
      this.recorder.start({ onFrame: (samples) => this.feed(samples) });
    } catch (err) {
      this.failSession(session, err);
    }
  }

  private feed(samples: Float32Array): void {
    const session = this.pending;
    const recognizer = this.recognizer;
    const stream = this.stream;
    if (session === null || session.stopped || recognizer === null || stream === null) {
      return;
    }
    stream.acceptWaveform(SAMPLE_RATE, samples);
    while (recognizer.isReady(stream)) {
      recognizer.decode(stream);
    }
    session.samplesSincePartial += samples.length;
    if (session.samplesSincePartial >= SAMPLE_RATE * PARTIAL_INTERVAL_S) {
      session.samplesSincePartial = 0;
      const text = recognizer.getResult(stream).text;
      if (text.length > 0 && text !== session.lastPartial) {
        session.lastPartial = text;
        session.handlers.onPartial?.(text);
      }
    }
  }

  private async finalize(session: PendingSession): Promise<void> {
    try {
      await this.recorder.stop(); // 等 onStop：最后一帧已 onFrame 推送
    } catch (err) {
      this.failSession(session, err);
      return;
    }
    if (this.pending !== session) {
      return; // 收尾期间被 cancel
    }
    let text = '';
    const recognizer = this.recognizer;
    const stream = this.stream;
    if (recognizer !== null && stream !== null) {
      // flush：喂 1s 静音再解码，保证尾部字词完整
      stream.acceptWaveform(SAMPLE_RATE, new Float32Array(SAMPLE_RATE));
      while (recognizer.isReady(stream)) {
        recognizer.decode(stream);
      }
      text = recognizer.getResult(stream).text;
    }
    this.pending = null;
    this.teardownSession();
    session.resolve(text);
  }

  private failSession(session: PendingSession, err: unknown): void {
    if (this.pending !== session) {
      return;
    }
    this.pending = null;
    this.teardownSession();
    session.reject(err instanceof Error ? err : new Error('语音识别失败'));
  }

  private teardownSession(): void {
    this.stream = null;
    // recognizer 属运行时常驻单例（模型/wasm 常驻内存），不销毁
  }
}
