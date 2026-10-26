/**
 * PCM 录音器（wx.getRecorderManager → 16kHz 单声道裸 PCM 流）。
 *
 * - format:'PCM' + frameSize:1KB（≈64ms/帧）→ onFrameRecorded 逐帧回调；
 * - 帧字节按小端 Int16 读出并归一化为 Float32 [-1,1]，识别器 acceptWaveform
 *   直接可喂；
 * - start() 注册回调并启动；stop() 停止并等待 onStop（保证最后一帧已推送）；
 *   cancel() 停止且后续帧丢弃（会话取消语义由引擎负责 reject pending）；
 * - duration 30s 自动停（单次上限，与原同声传译插件一致），到点触发 onStop；
 * - manager 构造注入：单测用假 manager，默认 wx.getRecorderManager()（单例）。
 */
export interface PcmFrameHandlers {
  onFrame(samples: Float32Array): void;
}

/** 录音器依赖的微信 manager 最小面（便于单测注入）。 */
export interface RecorderManagerLike {
  start(option: Record<string, unknown>): void;
  stop(): void;
  onFrameRecorded(listener: (res: { frameBuffer?: ArrayBuffer }) => void): void;
  /** res 具体类型（OnStopListenerResult）无 index signature，用 unknown 兼容。 */
  onStop(listener: (res: unknown) => void): void;
  onError(listener: (err: { errMsg?: string }) => void): void;
}

/** 单次录音上限（毫秒）。 */
export const MAX_RECORD_MS = 30000;

/** 引擎依赖的录音器结构面（PcmRecorder 实现；单测可注入假件）。 */
export interface RecorderLike {
  start(handlers: PcmFrameHandlers): void;
  stop(): Promise<void>;
  cancel(): void;
}

export class PcmRecorder implements RecorderLike {
  private cancelled = false;
  private recording = false;
  private lastError: Error | null = null;
  private handlers: PcmFrameHandlers | null = null;
  private settle: { resolve: () => void; reject: (err: Error) => void } | null = null;
  private stopped: Promise<void> | null = null;

  constructor(private readonly manager: RecorderManagerLike = wx.getRecorderManager()) {}

  get isRecording(): boolean {
    return this.recording;
  }

  start(handlers: PcmFrameHandlers): void {
    if (this.recording) {
      return; // 幂等：录音中重复 start 忽略（引擎层已保证会话互斥）
    }
    this.cancelled = false;
    this.lastError = null;
    this.handlers = handlers;
    this.stopped = new Promise<void>((resolve, reject) => {
      this.settle = { resolve, reject };
    });
    // 提前 onError 而无人取走 stop() 时避免 unhandled rejection（等待者经 stop() 仍可收到）
    this.stopped.catch(() => undefined);
    this.manager.onFrameRecorded((res) => this.handleFrame(res.frameBuffer));
    this.manager.onStop(() => this.finish(null));
    this.manager.onError((err) => this.finish(new Error(err.errMsg ?? '录音失败')));
    this.recording = true;
    this.manager.start({
      format: 'PCM',
      sampleRate: 16000,
      numberOfChannels: 1,
      frameSize: 1, // 1KB ≈ 64ms/帧
      duration: MAX_RECORD_MS,
      // Android 语音识别输入源（iOS 忽略该字段）
      audioSource: 'voice_recognition',
    });
  }

  /** 停止录音并等待 onStop（最后一帧已推完）；未在录音时立即 resolve。 */
  stop(): Promise<void> {
    if (this.lastError !== null) {
      // 录音期间发生过的错误不能被吞成空结果：stop 时补抛给调用方
      return Promise.reject(this.lastError);
    }
    if (!this.recording || this.stopped === null) {
      return Promise.resolve();
    }
    this.manager.stop();
    return this.stopped;
  }

  /** 取消：停止录音并丢弃其后帧回调。 */
  cancel(): void {
    this.cancelled = true;
    if (this.recording) {
      this.manager.stop();
    }
  }

  private handleFrame(buffer: ArrayBuffer | undefined): void {
    if (this.cancelled || this.handlers === null || buffer === undefined || buffer.byteLength < 2) {
      return;
    }
    const int16 = new Int16Array(buffer, 0, Math.floor(buffer.byteLength / 2));
    const samples = new Float32Array(int16.length);
    for (let i = 0; i < int16.length; i += 1) {
      samples[i] = int16[i] / 32768;
    }
    this.handlers.onFrame(samples);
  }

  private finish(err: Error | null): void {
    this.recording = false;
    this.handlers = null;
    if (err !== null) {
      this.lastError = err; // 保留错误供 stop() 补抛（引擎正在等待收尾的场景）
    }
    const settle = this.settle;
    this.settle = null;
    this.stopped = null;
    if (settle === null) {
      return; // 无人等待（如 cancel 场景）：静默收尾
    }
    if (err !== null) {
      settle.reject(err);
    } else {
      settle.resolve();
    }
  }
}
