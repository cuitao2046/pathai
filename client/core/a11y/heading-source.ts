/**
 * 导航方向箭头的朝向数据源（纯逻辑，零 wx 依赖；wx 能力由页面注入 HeadingApi）。
 *
 * 三级降级（spec §5.4）：
 * 1. `compass`：`wx.startCompass` + `onCompassChange` 的绝对方位；
 * 2. `motion-relative`：罗盘超时/失败时改用 `wx.startDeviceMotionListening`
 *    的 `alpha`，以首个回调为基准，只提供相对转动量；
 * 3. `unavailable`：两者均失败 → 页面隐藏箭头并语音提示，不显示错误方向。
 *
 * 生命周期：仅导航态启动；`stop()` 必须同时停止两类监听（省电 + 避免后台限流）。
 */

export type HeadingMode = 'compass' | 'motion-relative' | 'unavailable';

export interface CompassReading {
  direction: number;
  accuracy?: number | string;
}

export interface MotionReading {
  alpha: number;
}

export interface HeadingApi {
  startCompass(opts: { success?: () => void; fail?: (err: unknown) => void }): void;
  stopCompass(): void;
  onCompassChange(cb: (res: CompassReading) => void): void;
  offCompassChange?(cb: (res: CompassReading) => void): void;
  startDeviceMotionListening(opts: {
    interval: 'ui' | 'normal' | 'game';
    success?: () => void;
    fail?: (err: unknown) => void;
  }): void;
  stopDeviceMotionListening(): void;
  onDeviceMotionChange(cb: (res: MotionReading) => void): void;
  offDeviceMotionChange?(cb: (res: MotionReading) => void): void;
}

export interface HeadingSourceOptions {
  api: HeadingApi;
  /** 有效朝向回调；mode 表示数据来源（罗盘绝对 / 运动相对）。 */
  onHeading: (deg: number, mode: HeadingMode) => void;
  /** 数据源切换（降级/不可用）时回调一次。 */
  onModeChange?: (mode: HeadingMode) => void;
  /** 两级都失败时回调一次（页面据此隐藏箭头并语音提示）。 */
  onUnavailable?: () => void;
  /** 罗盘/运动回调等待阈值（ms），超时即降级。 */
  timeoutMs?: number;
}

function finite(value: number | undefined): value is number {
  return typeof value === 'number' && Number.isFinite(value);
}

function normalize(deg: number): number {
  const mod = deg % 360;
  return mod < 0 ? mod + 360 : mod;
}

export class HeadingSource {
  private readonly api: HeadingApi;
  private readonly timeoutMs: number;
  private running = false;
  private mode: HeadingMode = 'unavailable';
  private timer: ReturnType<typeof setTimeout> | null = null;
  private compassCount = 0;
  private motionCount = 0;
  private motionBaseline: number | null = null;
  private announcedUnavailable = false;

  constructor(private readonly options: HeadingSourceOptions) {
    this.api = options.api;
    this.timeoutMs = options.timeoutMs ?? 1500;
  }

  get currentMode(): HeadingMode {
    return this.mode;
  }

  get isRunning(): boolean {
    return this.running;
  }

  start(): void {
    if (this.running) {
      return;
    }
    this.running = true;
    this.compassCount = 0;
    this.motionCount = 0;
    this.announcedUnavailable = false;
    this.mode = 'compass';
    this.clearTimer();
    try {
      this.api.onCompassChange(this.onCompass);
      this.api.startCompass({
        success: () => undefined,
        fail: () => this.fallbackToMotion(),
      });
    } catch {
      this.fallbackToMotion();
      return;
    }
    // 罗盘无回调（部分机型无磁力计或未授权）→ 超时后降级
    this.timer = setTimeout(() => {
      if (this.running && this.mode === 'compass' && this.compassCount === 0) {
        this.fallbackToMotion();
      }
    }, this.timeoutMs);
  }

  stop(): void {
    if (!this.running) {
      return;
    }
    this.running = false;
    this.clearTimer();
    try {
      this.api.stopCompass();
      this.api.offCompassChange?.(this.onCompass);
    } catch {
      // 停止失败不影响页面（异常仅记录在调用方）
    }
    try {
      this.api.stopDeviceMotionListening();
      this.api.offDeviceMotionChange?.(this.onMotion);
    } catch {
      // 同上
    }
    this.mode = 'unavailable';
  }

  private readonly onCompass = (res: CompassReading): void => {
    if (!this.running || this.mode !== 'compass') {
      return;
    }
    if (!finite(res.direction)) {
      return;
    }
    this.compassCount += 1;
    this.clearTimer();
    this.options.onHeading(normalize(res.direction), 'compass');
  };

  private readonly onMotion = (res: MotionReading): void => {
    if (!this.running || this.mode !== 'motion-relative') {
      return;
    }
    if (!finite(res.alpha)) {
      return;
    }
    this.motionCount += 1;
    if (this.motionBaseline === null) {
      this.motionBaseline = res.alpha;
    }
    this.options.onHeading(normalize(res.alpha - this.motionBaseline), 'motion-relative');
  };

  private fallbackToMotion(): void {
    if (!this.running) {
      return;
    }
    this.clearTimer();
    try {
      this.api.stopCompass();
      this.api.offCompassChange?.(this.onCompass);
    } catch {
      // 忽略：降级路径继续尝试运动传感器
    }
    this.mode = 'motion-relative';
    this.motionBaseline = null;
    this.options.onModeChange?.(this.mode);
    try {
      this.api.onDeviceMotionChange(this.onMotion);
      this.api.startDeviceMotionListening({
        interval: 'ui',
        success: () => undefined,
        fail: () => this.markUnavailable(),
      });
    } catch {
      this.markUnavailable();
      return;
    }
    this.timer = setTimeout(() => {
      if (this.running && this.mode === 'motion-relative' && this.motionCount === 0) {
        this.markUnavailable();
      }
    }, this.timeoutMs);
  }

  private markUnavailable(): void {
    if (!this.running) {
      return;
    }
    this.running = false;
    this.clearTimer();
    try {
      this.api.stopCompass();
      this.api.stopDeviceMotionListening();
    } catch {
      // 忽略停止异常
    }
    this.mode = 'unavailable';
    this.options.onModeChange?.(this.mode);
    if (!this.announcedUnavailable) {
      this.announcedUnavailable = true;
      this.options.onUnavailable?.();
    }
  }

  private clearTimer(): void {
    if (this.timer !== null) {
      clearTimeout(this.timer);
      this.timer = null;
    }
  }
}
