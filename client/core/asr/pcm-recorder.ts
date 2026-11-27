/**
 * PCM 录音器（wx.getRecorderManager → 16kHz 单声道裸 PCM 流）。
 *
 * - format:'PCM' + frameSize:1KB（=512 采样 ≈32ms/帧）→ onFrameRecorded 逐帧回调；
 * - 帧字节按小端 Int16 读出并归一化为 Float32 [-1,1]，识别器 acceptWaveform
 *   直接可喂；
 * - start() 注册回调并启动；manager.onStart 触发时回调 onReady（麦克风已就绪）；
 *   cancel() 停止且后续帧丢弃（会话取消语义由引擎负责 reject pending）；
 * - duration 30s 自动停（单次上限，与原同声传译插件一致），到点触发 onStop；
 * - manager 构造注入：单测用假 manager，默认 wx.getRecorderManager()（单例）。
 */
import { createLogger } from '../../utils/logger';

const log = createLogger('asr-pcm');

export interface PcmFrameHandlers {
  onFrame(samples: Float32Array): void;
  /** 麦克风真正开始工作（manager.onStart 或首帧到达）；页面据此提示「可以开口了」。 */
  onReady?: () => void;
  /**
   * 录音启动/运行失败：与 onError 同步回调，供上层立刻中断会话并提示。
   * 只靠 stop() 补抛会让用户在按住期间一直停在「正在打开麦克风…」。
   */
  onFailure?: (err: Error) => void;
}

/** 录音器依赖的微信 manager 最小面（便于单测注入）。 */
export interface RecorderManagerLike {
  start(option: Record<string, unknown>): void;
  stop(): void;
  onFrameRecorded(listener: (res: { frameBuffer?: ArrayBuffer }) => void): void;
  /** res 具体类型（OnStopListenerResult）无 index signature，用 unknown 兼容。 */
  onStop(listener: (res: unknown) => void): void;
  onError(listener: (err: { errMsg?: string }) => void): void;
  /** 录音真正开始（麦克风已打开）；旧基础库/测试假件可能没有，按可选处理。 */
  onStart?(listener: () => void): void;
}

/** 单次录音上限（毫秒）。 */
export const MAX_RECORD_MS = 30000;

/**
 * 开麦超时（毫秒）：超过此时长既没有 onStart 也没有任何音频帧，判定麦克风没打开。
 * iOS 上音频会话被 TTS/上一轮录音占用时，start() 可能既不回调也不报错，
 * 没有这个兜底页面会永远停在「正在打开麦克风…」。
 */
const MIC_START_TIMEOUT_MS = 2500;

/** 开麦超时的错误消息（describeAsrError 据此给出「麦克风打开失败」）。 */
const MIC_START_TIMEOUT_MESSAGE = '麦克风启动超时';

/**
 * 启动失败后的重试等待（毫秒）。
 * iOS 音频会话是进程级独占：TTS 刚播完/上一轮录音刚停时，start() 会直接失败。
 * 等一小段时间再试一次，通常就能拿到麦克风。
 */
const MIC_RETRY_DELAY_MS = 250;

/** 可恢复的启动失败（音频通道被占用/系统繁忙）：重试一次有意义。 */
const MIC_RETRYABLE_PATTERN = /busy|occup|system\s*error/i;

/** 录音启动参数（首启与重试共用）。 */
const START_OPTION: Record<string, unknown> = {
  format: 'PCM',
  sampleRate: 16000,
  numberOfChannels: 1,
  frameSize: 1, // 1KB = 512 采样 ≈ 32ms/帧
  duration: MAX_RECORD_MS,
  // Android 语音识别输入源（iOS 忽略该字段）
  audioSource: 'voice_recognition',
};

/** 微信对未启动录音机操作的 errMsg（时序竞态产物，语义等同"已停止"，非故障）。 */
const RECORDER_NOT_START_PATTERN = /recorder not start/i;

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
  /**
   * 监听代际：manager 是单例，onXxx 每次 start 都会再注册一个监听且无法注销。
   * 每次开始录音递增，旧监听据此失效——否则第二次录音起每帧被投递多次，
   * 重复音频会显著劣化识别。
   */
  private generation = 0;
  /** 本次 start 的时刻与首帧时刻（定位「开口过早/麦克风起得慢」用）。 */
  private startedAt = 0;
  private firstFrameLogged = false;
  /** 是否已通知过「麦克风就绪」：onStart 或首帧，先到者生效。 */
  private readyFired = false;
  /** 开麦超时计时器。 */
  private startTimer: ReturnType<typeof setTimeout> | null = null;
  /** 启动失败后的重试计时器；非 null 表示正处于重试等待窗口。 */
  private retryTimer: ReturnType<typeof setTimeout> | null = null;
  /** 本轮是否已重试过（只重试一次，避免反复失败时无限循环）。 */
  private retried = false;

  constructor(private readonly manager: RecorderManagerLike = wx.getRecorderManager()) {}

  get isRecording(): boolean {
    return this.recording;
  }

  start(handlers: PcmFrameHandlers): void {
    if (this.recording) {
      return; // 幂等：录音中重复 start 忽略（引擎层已保证会话互斥）
    }
    const gen = (this.generation += 1);
    this.cancelled = false;
    this.lastError = null;
    this.handlers = handlers;
    this.startedAt = Date.now();
    this.firstFrameLogged = false;
    this.readyFired = false;
    this.retried = false;
    this.clearStartTimer();
    this.clearRetryTimer();
    this.stopped = new Promise<void>((resolve, reject) => {
      this.settle = { resolve, reject };
    });
    // 提前 onError 而无人取走 stop() 时避免 unhandled rejection（等待者经 stop() 仍可收到）
    this.stopped.catch(() => undefined);
    this.manager.onFrameRecorded((res) => {
      if (gen === this.generation) {
        this.handleFrame(res.frameBuffer);
      }
    });
    this.manager.onStop(() => {
      if (gen !== this.generation) {
        return;
      }
      if (this.retryTimer !== null) {
        return; // 重试窗口内微信的停止事件属于上一次失败，不代表本轮收尾
      }
      this.finish(null);
    });
    this.manager.onError((err) => {
      if (gen !== this.generation) {
        return;
      }
      const raw = err.errMsg ?? '';
      // 竞态窗口内微信可能报 "recorder not start"：录音机实际已停，
      // 按正常收尾处理，避免把会话误判成录音失败
      if (RECORDER_NOT_START_PATTERN.test(raw)) {
        this.finish(null);
        return;
      }
      // 启动阶段（尚未就绪）遇到通道占用/系统繁忙时先重试一次：iOS 上音频会话
      // 未及时交还（TTS 刚播完、上一轮录音刚停）是最常见成因，等通道释放后即可开麦。
      // 运行中错误不重试——那会掩盖中途中断。
      if (!this.retried && !this.readyFired && MIC_RETRYABLE_PATTERN.test(raw)) {
        this.retried = true;
        log.warn('录音启动失败，稍后重试一次', raw);
        this.retryTimer = setTimeout(() => {
          this.retryTimer = null;
          if (gen !== this.generation || !this.recording) {
            return;
          }
          this.manager.start(START_OPTION);
        }, MIC_RETRY_DELAY_MS);
        return;
      }
      this.finish(new Error(raw.length > 0 ? raw : '录音失败'));
    });
    // 「麦克风已就绪」的唯一可信信号：onStart 之后才会真正产生音频帧
    this.manager.onStart?.(() => {
      if (gen === this.generation) {
        this.handleStart();
      }
    });
    this.recording = true;
    log.info('请求开始录音');
    this.startTimer = setTimeout(() => {
      if (gen !== this.generation || !this.recording || this.readyFired) {
        return;
      }
      log.warn('麦克风启动超时，判定开麦失败');
      this.finish(new Error(MIC_START_TIMEOUT_MESSAGE));
      // finish 已把 recording 置 false，cancel() 不会再停：这里自行释放录音机
      try {
        this.manager.stop();
      } catch {
        // 录音机本就未启动时 stop 可能抛错，忽略即可
      }
    }, MIC_START_TIMEOUT_MS);
    this.manager.start(START_OPTION);
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
    this.clearStartTimer();
    this.clearRetryTimer();
    this.readyFired = false;
    if (!this.recording) {
      return;
    }
    // 同步复位状态：微信 onStop 异步到达前用户可能再次按下（快速连按），
    // 若等异步回调复位，start() 会被幂等守卫跳过，新会话将收不到任何音频
    this.recording = false;
    this.handlers = null;
    this.manager.stop();
    const settle = this.settle;
    this.settle = null;
    this.stopped = null;
    if (settle !== null) {
      settle.resolve(); // cancel 语义：不 reject 等待者，静默收尾
    }
  }

  /**
   * 麦克风真正打开：通知上层「可以开口了」，并记录首次开麦耗时。
   * 以 onStart 与首帧先到者为准——部分 iOS 版本 onStart 不回调，但音频帧照常产生，
   * 只认 onStart 会让界面永远停在「正在打开麦克风…」。
   */
  private handleStart(): void {
    if (this.readyFired) {
      return;
    }
    this.readyFired = true;
    this.clearStartTimer();
    const cost = Date.now() - this.startedAt;
    log.info(`麦克风已就绪 ${cost}ms`);
    this.handlers?.onReady?.();
  }

  private clearStartTimer(): void {
    if (this.startTimer !== null) {
      clearTimeout(this.startTimer);
      this.startTimer = null;
    }
  }

  private clearRetryTimer(): void {
    if (this.retryTimer !== null) {
      clearTimeout(this.retryTimer);
      this.retryTimer = null;
    }
  }

  private handleFrame(buffer: ArrayBuffer | undefined): void {
    if (this.cancelled || this.handlers === null || buffer === undefined || buffer.byteLength < 2) {
      return;
    }
    this.handleStart(); // 首帧是麦克风已在工作的最强证据
    if (!this.firstFrameLogged) {
      this.firstFrameLogged = true;
      log.info(`首帧到达 ${Date.now() - this.startedAt}ms`);
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
    this.clearStartTimer();
    this.clearRetryTimer();
    this.readyFired = false;
    const handlers = this.handlers;
    this.handlers = null;
    if (err !== null) {
      this.lastError = err; // 保留错误供 stop() 补抛（引擎正在等待收尾的场景）
      // 立即上报：否则按住期间界面一直停在「正在打开麦克风…」，松手才看到失败
      handlers?.onFailure?.(err);
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
