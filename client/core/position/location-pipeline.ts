/**
 * 定位管线：BeaconSource → ScanWindowAggregator → API-05 → 节流回调。
 * - 节流：待命 8s / 路线预览 2.5s / 导航 1s（NAV_UPDATE_TIMEOUT_MS 快速失败）；
 * - 窗口内可解析信标不足 3 个：待命/预览静默跳过（等样本积累）；导航档
 *   视为信号丢失，走 onUnusable（上层据此上报服务端进入 recovering）；
 * - 任一 quality（含 unreliable/lost，position 可能为空）→ onEstimate，
 *   由上层决定能否驱动导航（导航中不可信位置只报 quality，不报坐标）；
 * - 请求失败/响应异常 → onUnusable（含 STALE_LOCATION_BATCH 等
 *   ApiClientError，交由上层导航状态机处置）。
 */
import { NAV_UPDATE_TIMEOUT_MS } from '../../config/env';
import type { ApiClient } from '../http/api-client';
import { ApiClientError } from '../http/errors';
import type { BeaconRegistry } from '../ble/beacon-registry';
import type { BeaconSourceLike } from '../ble/beacon-source';
import {
  ScanWindowAggregator,
  type BeaconSample,
  type ResolvedObservation,
} from '../ble/scan-window';
import { nowMs } from '../../utils/clock';
import type {
  LocationEstimateRequest,
  PositionEstimateResponse,
} from '../../typings/api';

export type PipelineMode = 'standby' | 'preview' | 'navigating';

const STANDBY_INTERVAL_MS = 8000;
/**
 * 导航档 1s：步行 1.5m/s 对应 1.5m/拍。后端转弯提前量 8~10m、每拍最多推进一条，
 * 2.5s（3.75m/拍）时相邻转弯间隔仅 5.7m 的场景会把第二个转弯拖到拐点之后才播；
 * 1s 可让该类转弯提前约 4m 播到（仿真：3.75m 步长 -0.5m → 1.5m 步长 +4.0m）。
 */
const NAVIGATING_INTERVAL_MS = 1000;
/** 路线预览 2.5s：只需尽快拿到规划起点，不随导航档提高频率。 */
const PREVIEW_INTERVAL_MS = 2500;

/** 定位管线消费的地图服务最小切片（便于测试注入）。 */
export interface LocationMapProvider {
  siteId: string;
  requireVersion(): string;
  beaconRegistry: BeaconRegistry;
}

export interface LocationPipelineOptions {
  client: ApiClient;
  map: LocationMapProvider;
  source: BeaconSourceLike;
  sequence: { next(): number; advanceTo?(lastSequence: number): void };
  /** 已完成的一拍估计（任意 quality；unreliable/lost 时 position 可能为空）。 */
  onEstimate: (estimate: PositionEstimateResponse, meta: { visibleBeaconCount: number }) => void;
  /** 本拍没有可用估计（请求失败/响应异常；导航档信标不足也走这里）。 */
  onUnusable: (err: ApiClientError) => void;
  /** 窗口时长（毫秒），默认 2000；测试可缩短。 */
  windowMs?: number;
  /**
   * 指纹库开关（设置页，默认关闭）。仅在返回 true 时给请求体加
   * useFingerprintDb=true；关闭时请求体与旧版逐字节一致。
   */
  useFingerprintDb?: () => boolean;
  experiment?: () => { configId: string; method: 'A' | 'B' | 'C' } | null;
  onRawSample?: (sample: BeaconSample) => void;
  /**
   * 查周边动作：仅在返回 true 时给请求体加 includeSurroundings=true，
   * 服务端在同一次响应内附上周边要素清单；默认不发送。
   */
  includeSurroundings?: () => boolean;
  /** 当前朝向（度）；提供时随 imu 上报，服务端据此计算时钟方位。 */
  headingDeg?: () => number | null;
}

export class LocationPipeline {
  private aggregator: ScanWindowAggregator;
  private timer: ReturnType<typeof setTimeout> | null = null;
  private mode: PipelineMode = 'standby';
  private running = false;
  private generation = 0;
  private inFlight: { generation: number; promise: Promise<void> } | null = null;
  private lastFloorId: string | null = null;

  constructor(private readonly opts: LocationPipelineOptions) {
    this.aggregator = new ScanWindowAggregator(opts.windowMs);
  }

  get isRunning(): boolean {
    return this.running;
  }

  async start(mode: PipelineMode = 'standby'): Promise<void> {
    if (this.running) {
      return;
    }
    const generation = ++this.generation;
    this.running = true;
    this.aggregator = new ScanWindowAggregator(this.opts.windowMs);
    this.lastFloorId = null;
    this.mode = mode;
    try {
      await this.opts.source.start((sample) => {
        if (this.isCurrent(generation)) {
          this.aggregator.push(sample);
          this.opts.onRawSample?.(sample);
        }
      });
    } catch (err) {
      // 蓝牙未开/权限不足等启动失败必须可重试：否则 running 卡在 true，
      // 后续 onShow/重试全部变成空操作，页面永远收不到样本。
      if (!this.isCurrent(generation)) return;
      this.running = false;
      throw err;
    }
    if (this.isCurrent(generation)) this.schedule();
  }

  private isCurrent(generation: number): boolean {
    return this.running && this.generation === generation;
  }

  /** 切换节流档位并重置下一拍计时（导航接管/解除时立即生效）。 */
  setMode(mode: PipelineMode): void {
    this.mode = mode;
    if (this.running && this.timer !== null) {
      clearTimeout(this.timer);
      this.timer = null;
      this.schedule();
    }
  }

  /**
   * 清除楼层先验：下一拍起由服务端按可见信标多数判层。
   * 电梯跨层期间必须调用——否则 lastFloorId 会把楼层永久钉在当前层，
   * 目标层信标再多也会被服务端忽略，导航与地图都无法自动换层。
   */
  clearFloorHint(): void {
    this.lastFloorId = null;
  }

  stop(): void {
    if (!this.running) {
      return;
    }
    this.running = false;
    ++this.generation;
    this.inFlight = null;
    if (this.timer !== null) {
      clearTimeout(this.timer);
      this.timer = null;
    }
    this.opts.source.stop();
  }

  /** 手动触发一拍（测试/立即刷新用）；空闲时忽略。 */
  async tick(): Promise<void> {
    if (!this.running) {
      return;
    }
    const generation = this.generation;
    if (this.inFlight?.generation === generation) return this.inFlight.promise;
    if (this.timer !== null) clearTimeout(this.timer);
    this.timer = null;
    const operation = { generation, promise: Promise.resolve() };
    this.inFlight = operation;
    operation.promise = this.collectAndReport(generation).finally(() => {
      if (this.inFlight === operation) this.inFlight = null;
      if (this.isCurrent(generation)) this.schedule();
    });
    return operation.promise;
  }

  private schedule(): void {
    if (!this.running || this.inFlight?.generation === this.generation) return;
    if (this.timer !== null) clearTimeout(this.timer);
    const interval =
      this.mode === 'navigating'
        ? NAVIGATING_INTERVAL_MS
        : this.mode === 'preview'
          ? PREVIEW_INTERVAL_MS
          : STANDBY_INTERVAL_MS;
    this.timer = setTimeout(() => {
      this.timer = null;
      void this.tick();
    }, interval);
  }

  private async collectAndReport(generation: number, retryOnStale = true): Promise<void> {
    if (!this.isCurrent(generation)) return;
    const snapshot = this.aggregator.snapshot(
      this.opts.map.beaconRegistry,
      nowMs(),
    );
    if (snapshot === null) {
      // 待命：等样本积累，不视为错误；导航中：持续无解即信号丢失，交给上层上报
      if (this.mode === 'navigating') {
        this.opts.onUnusable(
          new ApiClientError({
            code: 'LOCATION_UNRELIABLE',
            message: '窗口内可解析信标不足',
            retryable: true,
          }),
        );
      }
      return;
    }
    const body: LocationEstimateRequest = {
      siteId: this.opts.map.siteId,
      mapVersion: this.opts.map.requireVersion(),
      sequence: this.opts.sequence.next(),
      collectedAt: new Date(nowMs()).toISOString(),
      floorHint: this.lastFloorId,
      beacons: snapshot.map(toObservation),
    };
    // 关闭（默认）时不写该字段：请求体与旧版完全一致，服务端行为不变
    const experiment = this.opts.experiment?.();
    if (experiment) {
      body.experimentConfigId = experiment.configId;
      body.experimentMethod = experiment.method;
    } else if (this.opts.useFingerprintDb?.() === true) {
      body.useFingerprintDb = true;
    }
    if (this.opts.includeSurroundings?.() === true) {
      body.includeSurroundings = true;
    }
    const heading = this.opts.headingDeg?.();
    if (heading !== null && heading !== undefined && Number.isFinite(heading)) {
      body.imu = { samples: [], headingDeg: ((heading % 360) + 360) % 360 };
    }
    let estimate: PositionEstimateResponse;
    try {
      const resp = await this.opts.client.request<PositionEstimateResponse>({
        method: 'POST',
        path: '/api/v1/locations/estimate',
        body,
        timeoutMs: NAV_UPDATE_TIMEOUT_MS,
      });
      const data = resp.data;
      if (data === null || typeof data.quality !== 'string') {
        throw new ApiClientError({
          code: 'HTTP_ERROR',
          message: '定位响应格式异常',
          statusCode: resp.statusCode,
          requestId: resp.requestId,
        });
      }
      estimate = data;
    } catch (err) {
      if (!this.isCurrent(generation)) return;
      const failure =
        err instanceof ApiClientError
          ? err
          : new ApiClientError({ code: 'HTTP_ERROR', message: '定位请求失败' });
      // 序号水位被推进（其他客户端/历史脏数据）：追平服务端已处理水位后立刻重试一拍。
      // 不重试就得等下一个拍点（待命 8s / 预览 2.5s），用户会看到「定位不可用」。
      const lastSequence = failure.details.lastSequence;
      if (
        retryOnStale &&
        failure.code === 'STALE_LOCATION_BATCH' &&
        typeof lastSequence === 'number' &&
        this.opts.sequence.advanceTo
      ) {
        this.opts.sequence.advanceTo(lastSequence);
        await this.collectAndReport(generation, false);
        return;
      }
      this.opts.onUnusable(failure);
      return;
    }
    if (!this.isCurrent(generation)) return;
    this.lastFloorId = estimate.position?.floorId ?? this.lastFloorId;
    // 质量交给上层：导航只用 trusted 坐标推进，unreliable/lost 仅上报 quality
    this.opts.onEstimate(estimate, { visibleBeaconCount: body.beacons.length });
  }
}

function toObservation(o: ResolvedObservation): LocationEstimateRequest['beacons'][number] {
  return {
    beaconId: o.beaconId,
    rssi: o.rssi,
    observedAt: new Date(o.observedAtMs).toISOString(),
  };
}
