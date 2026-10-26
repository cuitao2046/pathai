/**
 * 「我在哪儿」单次定位查询（意图 query_location；docs/24 FE-01 质量策略）。
 *
 * 链路：造观测源（mock 模拟轨迹 / live 实扫）→ LocationPipeline 主动 tick
 * （绕开 standby 8s 节流，轮询等窗口积累出首个估计）→ 按质量生成播报文本：
 *  - reliable：播报匹配区域/楼层（"您当前在X附近"）；
 *  - degraded：仅播报楼层+精度提示（FE-01：低置信度不报精确点位）；
 *  - 请求失败 / unreliable / 超时：统一安全文案，绝不播报伪造位置。
 *
 * query() 永不 reject（失败收敛为安全文案），调用方只负责播报；
 * 内部防并发：上一次查询未结束时再次 query 直接返回 null（调用方忽略）。
 */
import type { BeaconSourceLike } from '../core/ble/beacon-source';
import type { ApiClient } from '../core/http/api-client';
import {
  LocationPipeline,
  type LocationMapProvider,
} from '../core/position/location-pipeline';
import { SequenceCounter } from '../core/position/sequence-counter';
import { createLogger } from '../utils/logger';
import type { PositionEstimateResponse } from '../typings/api';

const log = createLogger('loc-query');

/** 定位不可得时的安全文案（与后端 LOCATION_UNRELIABLE 话术一致）。 */
export const UNRESOLVED_TEXT = '暂时无法可靠确定当前位置。';

const DEFAULT_TICK_INTERVAL_MS = 1200;
const DEFAULT_TIMEOUT_MS = 10_000;
const WINDOW_MS = 2000;

export interface LocationQueryDeps {
  client: ApiClient;
  map: LocationMapProvider;
  /** 观测源工厂（惰性调用，须在地图就绪后）。 */
  makeSource: () => BeaconSourceLike;
  /** 楼层显示名（undefined 表示楼层未知）。 */
  floorName: (floorId?: string | null) => string | undefined;
  /** 两轮 tick 间隔（给观测积累时间），测试可缩短。 */
  tickIntervalMs?: number;
  /** 查询总超时兜底，测试可缩短。 */
  timeoutMs?: number;
  /** 聚合窗口毫秒（传给管线），测试可缩短。 */
  windowMs?: number;
}

/** 质量策略播报文本（FE-01；estimate 仅会是 reliable/degraded）。 */
export function describePositionText(
  estimate: PositionEstimateResponse,
  floorName: (floorId?: string | null) => string | undefined,
): string {
  const floor = floorName(estimate.position?.floorId);
  if (estimate.quality === 'reliable') {
    const area = estimate.matchedArea?.name;
    if (area !== undefined && area.length > 0) {
      return floor ? `您当前在${floor}${area}附近。` : `您当前在${area}附近。`;
    }
    if (floor) {
      return `您当前在${floor}。`;
    }
    return '当前位置已确定。';
  }
  // degraded：仅楼层/区域，不报精确点位
  if (floor) {
    return `您当前在${floor}，定位精度一般。`;
  }
  return '已确认所在区域，定位精度一般。';
}

function sleep(ms: number): Promise<void> {
  return new Promise<void>((resolve) => setTimeout(resolve, ms));
}

export class LocationQueryService {
  private inFlight = false;

  constructor(private readonly deps: LocationQueryDeps) {}

  /**
   * 单次查询；失败/超时收敛为 UNRESOLVED_TEXT。
   * 并发进行中返回 null（调用方应忽略，不播报）。
   */
  async query(): Promise<string | null> {
    if (this.inFlight) {
      log.warn('位置查询进行中，忽略并发请求');
      return null;
    }
    this.inFlight = true;
    try {
      return await this.run();
    } finally {
      this.inFlight = false;
    }
  }

  private async run(): Promise<string> {
    const { client, map, makeSource, floorName } = this.deps;
    const tickIntervalMs = this.deps.tickIntervalMs ?? DEFAULT_TICK_INTERVAL_MS;
    const timeoutMs = this.deps.timeoutMs ?? DEFAULT_TIMEOUT_MS;

    let settled = false;
    let text: string | null = null;
    const pipeline = new LocationPipeline({
      client,
      map,
      source: makeSource(),
      sequence: new SequenceCounter(),
      windowMs: this.deps.windowMs ?? WINDOW_MS,
      onEstimate: (estimate) => {
        settled = true;
        text = describePositionText(estimate, floorName);
      },
      onUnusable: (err) => {
        settled = true;
        log.warn('定位不可用', err.code);
        text = UNRESOLVED_TEXT;
      },
    });
    const guard = setTimeout(() => {
      settled = true;
      text = UNRESOLVED_TEXT;
      log.warn(`位置查询超时（${timeoutMs}ms）`);
    }, timeoutMs);

    try {
      await pipeline.start('standby');
      while (!settled) {
        await sleep(tickIntervalMs);
        if (settled) {
          break; // 超时兜底可能在 sleep 期间置位
        }
        await pipeline.tick(); // 窗口内信标不足时静默跳过，下一轮再试
      }
    } finally {
      clearTimeout(guard);
      pipeline.stop(); // 停观测源与节拍
    }
    const result = text ?? UNRESOLVED_TEXT;
    log.info('位置查询结果:', result);
    return result;
  }
}
