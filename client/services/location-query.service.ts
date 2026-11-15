/**
 * 单次定位查询（docs/24 FE-01 质量策略），供两个动作复用同一条定位链路：
 *  - query_current_location：只答"我在哪"（楼层/区域，不罗列周边）；
 *  - describe_surroundings：定位后在同一响应里取 8m 内周边要素，描述
 *    "左前方 3 米有档案室门"这类内容（不播"您当前在……附近"）。
 *
 * 链路：造观测源（mock 模拟轨迹 / live 实扫）→ LocationPipeline 主动 tick
 * （绕开 standby 8s 节流，轮询等窗口积累出首个估计）→ 按质量生成播报文本：
 *  - reliable：播报匹配区域/楼层，或周边要素清单；
 *  - degraded：仅播报楼层+精度提示（FE-01：低置信度不报精确点位）；
 *  - 请求失败 / unreliable / lost / 超时：统一安全文案，绝不播报伪造位置。
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
import type { PositionEstimateResponse, SurroundingElement } from '../typings/api';

const log = createLogger('loc-query');

/** 定位不可得时的安全文案（与后端 LOCATION_UNRELIABLE 话术一致）。 */
export const UNRESOLVED_TEXT = '暂时无法可靠确定当前位置。';

/** 查周边：定位不可得（无信标/超时/请求失败）时的安全文案。 */
export const SURROUNDINGS_UNRESOLVED_TEXT = '暂时无法确定您的位置，没法查看周围环境。';
/** 查周边：定位精度一般时，不描述具体方位距离。 */
export const SURROUNDINGS_UNCERTAIN_TEXT = '定位精度一般，暂时无法准确描述周围环境。';
/** 查周边：定位成功但附近 8m 内没有可描述的要素。 */
export const SURROUNDINGS_EMPTY_TEXT = '附近没有检测到可以描述的门或设施。';
/** 查周边：服务端未返回周边清单（旧服务端/字段缺失）。 */
export const SURROUNDINGS_UNAVAILABLE_TEXT = '暂时无法获取周围环境信息。';

export type LocationQueryKind = 'location' | 'surroundings';

const DEFAULT_TICK_INTERVAL_MS = 1200;
const DEFAULT_TIMEOUT_MS = 10_000;
const WINDOW_MS = 2000;

/**
 * 时钟方位 → 8 个左右扇区（播报侧转换，服务端仍下发 clockDirection 数据口径）。
 * 每个扇区 45°，按最近扇区中心归档：12→前、3→右、6→后、9→左，其余落在两两之间。
 * 播报不再出现“X 点钟方向”，避免与左右方位混用。
 */
const CLOCK_SECTOR: Record<number, string> = {
  12: '前方',
  1: '右前方',
  2: '右前方',
  3: '右方',
  4: '右后方',
  5: '右后方',
  6: '后方',
  7: '左后方',
  8: '左后方',
  9: '左方',
  10: '左前方',
  11: '左前方',
};

/** 播报最多列几条周边要素，避免行走时播报过长（合并重复项后再截断）。 */
const SURROUNDINGS_SPEAK_LIMIT = 3;

/** 同一位置的判定阈值：同方位扇区且距离差 ≤ 1m。 */
const MERGE_DISTANCE_M = 1.0;

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
  /** 指纹库开关（默认关闭）；关闭时请求体与旧版完全一致。 */
  useFingerprintDb?: () => boolean;
  /** 当前朝向（度）；查周边时上报给服务端计算时钟方位。 */
  headingDeg?: () => number | null;
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

function formatDistance(distance: number): string {
  return distance >= 1 ? `${Math.round(distance)}` : distance.toFixed(1);
}

function normalizeName(name: string): string {
  return name.replace(/\s+/g, '').toLowerCase();
}

/** 名称相同或一方是另一方的前缀（≥2 字）视为同一实体，如“电梯”与“电梯口风险点”。 */
function isSameElementName(left: string, right: string): boolean {
  const a = normalizeName(left);
  const b = normalizeName(right);
  if (a === b) {
    return true;
  }
  const shorter = a.length <= b.length ? a : b;
  const longer = a.length <= b.length ? b : a;
  return shorter.length >= 2 && longer.startsWith(shorter);
}

/** 时钟方位 → 8 扇区；服务端数据口径不变，仅在播报侧转换。 */
function sectorOf(clockDirection: number): string {
  return CLOCK_SECTOR[clockDirection] ?? '';
}

/**
 * 合并同一位置的重复要素（如“电梯”与“电梯口风险点”）。
 *
 * 判定：名称相同/前缀包含 + 同一方位扇区 + 距离差 ≤ 1m（朝向不可用时忽略扇区）。
 * 保留：较短名称（设施本名）；距离取最近；无障碍提示取并集（有提示者优先，
 * 冲突时用“；”都保留，避免丢掉安全信息）。合并先于截断，重复项不占播报名额。
 */
export function mergeSurroundings(
  items: SurroundingElement[],
  withDirection = true,
): SurroundingElement[] {
  const merged: SurroundingElement[] = [];
  for (const item of items) {
    const same = merged.find(
      (kept) =>
        isSameElementName(kept.name, item.name) &&
        (!withDirection || sectorOf(kept.clockDirection) === sectorOf(item.clockDirection)) &&
        Math.abs(kept.distanceMeters - item.distanceMeters) <= MERGE_DISTANCE_M,
    );
    if (same === undefined) {
      merged.push({ ...item });
      continue;
    }
    if (item.name.length < same.name.length) {
      same.name = item.name;
    }
    same.distanceMeters = Math.min(same.distanceMeters, item.distanceMeters);
    const notes = [same.accessibilityNote, item.accessibilityNote].filter(
      (note): note is string => typeof note === 'string' && note.length > 0,
    );
    if (notes.length > 0) {
      same.accessibilityNote = Array.from(new Set(notes)).join('；');
    }
  }
  return merged;
}

/**
 * 查周边播报：只描述周边要素及左右方位距离（"左前方3米有档案室门"），
 * 不播报"您当前在……附近"。朝向不可用时不猜左右，改为“暂时无法判断方向”+
 * 纯距离描述；全文不出现时钟方位。定位质量不足时明确降级，不编造方位。
 */
export function describeSurroundingsText(
  estimate: PositionEstimateResponse,
  options: { headingAvailable?: boolean } = {},
): string {
  if (estimate.quality === 'degraded') {
    return SURROUNDINGS_UNCERTAIN_TEXT;
  }
  if (estimate.quality !== 'reliable') {
    return SURROUNDINGS_UNRESOLVED_TEXT;
  }
  if (estimate.surroundings === null || estimate.surroundings === undefined) {
    return SURROUNDINGS_UNAVAILABLE_TEXT;
  }
  const headingAvailable = options.headingAvailable !== false;
  const merged = mergeSurroundings(estimate.surroundings, headingAvailable);
  if (merged.length === 0) {
    return SURROUNDINGS_EMPTY_TEXT;
  }
  const parts = merged.slice(0, SURROUNDINGS_SPEAK_LIMIT).map((item) => {
    const base = `${formatDistance(item.distanceMeters)}米有${item.name}`;
    const withNote = item.accessibilityNote ? `${base}（${item.accessibilityNote}）` : base;
    return headingAvailable ? `${sectorOf(item.clockDirection)}${withNote}` : withNote;
  });
  const text = `${parts.join('；')}。`;
  return headingAvailable ? text : `暂时无法判断方向，${text}`;
}

function sleep(ms: number): Promise<void> {
  return new Promise<void>((resolve) => setTimeout(resolve, ms));
}

export class LocationQueryService {
  private inFlight = false;

  constructor(private readonly deps: LocationQueryDeps) {}

  /**
   * 单次查询；失败/超时按查询类型收敛为安全文案。
   * 并发进行中返回 null（调用方应忽略，不播报）。
   */
  async query(kind: LocationQueryKind = 'location'): Promise<string | null> {
    if (this.inFlight) {
      log.warn('位置查询进行中，忽略并发请求');
      return null;
    }
    this.inFlight = true;
    try {
      return await this.run(kind);
    } finally {
      this.inFlight = false;
    }
  }

  private async run(kind: LocationQueryKind): Promise<string> {
    const { client, map, makeSource, floorName } = this.deps;
    const tickIntervalMs = this.deps.tickIntervalMs ?? DEFAULT_TICK_INTERVAL_MS;
    const timeoutMs = this.deps.timeoutMs ?? DEFAULT_TIMEOUT_MS;
    const fallbackText =
      kind === 'surroundings' ? SURROUNDINGS_UNRESOLVED_TEXT : UNRESOLVED_TEXT;
    // 朝向只取一次：同一值既用于请求（服务端算时钟方位），也用于判断能否说左右
    const heading = this.deps.headingDeg?.();
    const headingAvailable =
      heading !== null && heading !== undefined && Number.isFinite(heading);

    let settled = false;
    let text: string | null = null;
    const pipeline = new LocationPipeline({
      client,
      map,
      source: makeSource(),
      sequence: new SequenceCounter(),
      windowMs: this.deps.windowMs ?? WINDOW_MS,
      useFingerprintDb: this.deps.useFingerprintDb,
      // 查周边：同一次定位请求内让服务端附上周边清单，避免串两次请求
      includeSurroundings: kind === 'surroundings' ? () => true : undefined,
      headingDeg: () => (headingAvailable ? (heading as number) : null),
      onEstimate: (estimate) => {
        settled = true;
        if (kind === 'surroundings') {
          text = describeSurroundingsText(estimate, { headingAvailable });
        } else {
          // 管线会把任意质量都交上来：unreliable/lost 只给安全文案，不报点位
          text =
            estimate.quality === 'reliable' || estimate.quality === 'degraded'
              ? describePositionText(estimate, floorName)
              : UNRESOLVED_TEXT;
        }
      },
      onUnusable: (err) => {
        settled = true;
        log.warn('定位不可用', err.code);
        text = fallbackText;
      },
    });
    const guard = setTimeout(() => {
      settled = true;
      text = fallbackText;
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
    const result = text ?? fallbackText;
    log.info(kind === 'surroundings' ? '查周边结果:' : '位置查询结果:', result);
    return result;
  }
}
