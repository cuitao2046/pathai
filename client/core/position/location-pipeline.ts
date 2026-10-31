/**
 * 定位管线：BeaconSource → ScanWindowAggregator → API-05 → 节流回调。
 * - 节流：待命 8s / 导航 2.5s（NAV_UPDATE_TIMEOUT_MS 快速失败）；
 * - 窗口内可解析信标不足 3 个：待命静默跳过（等样本积累）；导航档
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
  type ResolvedObservation,
} from '../ble/scan-window';
import { nowMs } from '../../utils/clock';
import type {
  LocationEstimateRequest,
  PositionEstimateResponse,
} from '../../typings/api';

export type PipelineMode = 'standby' | 'navigating';

const STANDBY_INTERVAL_MS = 8000;
const NAVIGATING_INTERVAL_MS = 2500;

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
  sequence: { next(): number };
  /** 已完成的一拍估计（任意 quality；unreliable/lost 时 position 可能为空）。 */
  onEstimate: (estimate: PositionEstimateResponse, meta: { visibleBeaconCount: number }) => void;
  /** 本拍没有可用估计（请求失败/响应异常；导航档信标不足也走这里）。 */
  onUnusable: (err: ApiClientError) => void;
  /** 窗口时长（毫秒），默认 2000；测试可缩短。 */
  windowMs?: number;
}

export class LocationPipeline {
  private readonly aggregator: ScanWindowAggregator;
  private timer: ReturnType<typeof setTimeout> | null = null;
  private mode: PipelineMode = 'standby';
  private running = false;
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
    this.running = true;
    this.mode = mode;
    await this.opts.source.start((sample) => this.aggregator.push(sample));
    this.schedule();
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

  stop(): void {
    if (!this.running) {
      return;
    }
    this.running = false;
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
    try {
      await this.collectAndReport();
    } finally {
      this.schedule();
    }
  }

  private schedule(): void {
    if (!this.running) {
      return;
    }
    const interval =
      this.mode === 'navigating' ? NAVIGATING_INTERVAL_MS : STANDBY_INTERVAL_MS;
    this.timer = setTimeout(() => {
      void this.tick();
    }, interval);
  }

  private async collectAndReport(): Promise<void> {
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
      this.opts.onUnusable(
        err instanceof ApiClientError
          ? err
          : new ApiClientError({ code: 'HTTP_ERROR', message: '定位请求失败' }),
      );
      return;
    }
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
