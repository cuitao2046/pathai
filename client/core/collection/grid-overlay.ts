/**
 * 指纹网格覆盖层（spec：calibration-console §7.3.3）。
 * - 内置 client/data/fingerprint_grid.ts 是设计层坐标 + 兜底快照；
 * - 服务端 overlay.json 只下发会随 A/n 变化的标记字段，按 fpId 合并；
 * - 基线不一致严格拒绝；拉取失败回退缓存，再失败回退内置；
 * - 覆盖层只做内存合并，不改内置数据结构、不影响 LOD/筛选逻辑。
 * 纯逻辑 + 注入式 API 客户端，wx 依赖仅 kv（jest 可直接测）。
 */
import type { ApiClient } from '../http/api-client';
import { getJson, remove, setJson } from '../../utils/kv';
import { createLogger } from '../../utils/logger';
import { FINGERPRINT_GRID_VERSION, type FpGridPoint } from '../../data/fingerprint_grid';

const log = createLogger('grid-overlay');

/** 覆盖层缓存键（spec §8.2）。 */
export const OVERLAY_CACHE_KEY = 'grid:overlay:v1';

/** 当前认识的覆盖层 schema。 */
export const OVERLAY_SCHEMA_VERSIONS = ['1.0', 'fingerprint_grid_overlay_v1'] as const;

export interface OverlayPoint {
  fpId: string;
  collectable?: boolean;
  collectable15m?: boolean;
  collectableWithSim?: boolean;
  simAddedBy?: string[];
  visible10m?: number;
  strongestRssi?: number | null;
}

export interface OverlayPayload {
  schemaVersion?: string;
  baseGridVersion?: string | null;
  gridVersion?: string;
  paramsVersion?: string;
  generatedAt?: string;
  counts?: Record<string, number>;
  points?: OverlayPoint[];
}

/** 服务端明确表示没有可用覆盖层。 */
export interface OverlayUnavailable {
  available: false;
  reason: string;
  baseGridVersion: null;
}

export interface OverlayCacheEntry {
  gridVersion: string;
  paramsVersion: string | null;
  etag: string | null;
  fetchedAt: number;
  payload: OverlayPayload;
}

export type GridOverlaySource = 'builtin' | 'cache' | 'online';

/** 合并后的运行时网格（页面直接消费）。 */
export interface RuntimeGrid {
  points: FpGridPoint[];
  /** 展示用版本：在线/缓存时为覆盖层 gridVersion，否则内置版本。 */
  gridVersion: string;
  baseGridVersion: string;
  paramsVersion: string | null;
  source: GridOverlaySource;
  /** 覆盖层拉取时刻；内置快照为 null。 */
  fetchedAt: number | null;
  /** 覆盖层里无法与内置基线匹配、被忽略的点数。 */
  unknownPoints: number;
  /** 页面顶部来源/降级说明（空串=无额外提示）。 */
  message: string;
}

/** 校验覆盖层是否可合并；返回不可用原因（null=可用）。 */
export function overlayInvalidReason(
  payload: OverlayPayload,
  baseGridVersion: string,
): string | null {
  if (
    payload.schemaVersion !== undefined &&
    !(OVERLAY_SCHEMA_VERSIONS as readonly string[]).includes(payload.schemaVersion)
  ) {
    return `覆盖层 schema ${payload.schemaVersion} 不受支持`;
  }
  if (!Array.isArray(payload.points)) {
    return '覆盖层缺少 points 数组';
  }
  if (payload.baseGridVersion !== baseGridVersion) {
    return `网格基线不一致（服务端 ${payload.baseGridVersion ?? '未知'}，内置 ${baseGridVersion}），请升级小程序`;
  }
  return null;
}

/** 按 fpId 覆盖标记字段；返回合并结果与未知点计数。 */
export function mergeOverlay(
  base: FpGridPoint[],
  payload: OverlayPayload,
): { points: FpGridPoint[]; applied: number; unknownPoints: number } {
  const byId = new Map<string, OverlayPoint>();
  for (const p of payload.points ?? []) {
    if (typeof p.fpId === 'string') {
      byId.set(p.fpId, p);
    }
  }
  let applied = 0;
  let unknownPoints = 0;
  const baseIds = new Set(base.map((p) => p.fpId));
  const points = base.map((point) => {
    const over = byId.get(point.fpId);
    if (over === undefined) {
      return point;
    }
    applied += 1;
    return {
      ...point,
      ...(typeof over.collectable === 'boolean' ? { collectable: over.collectable } : {}),
      ...(typeof over.collectable15m === 'boolean'
        ? { collectable15m: over.collectable15m }
        : {}),
      ...(typeof over.collectableWithSim === 'boolean'
        ? { collectableWithSim: over.collectableWithSim }
        : {}),
      ...(Array.isArray(over.simAddedBy) ? { simAddedBy: over.simAddedBy } : {}),
      ...(typeof over.visible10m === 'number' ? { visible10m: over.visible10m } : {}),
      ...(over.strongestRssi === null || typeof over.strongestRssi === 'number'
        ? { strongestRssi: over.strongestRssi }
        : {}),
    };
  });
  for (const fpId of byId.keys()) {
    if (!baseIds.has(fpId)) {
      unknownPoints += 1;
    }
  }
  return { points, applied, unknownPoints };
}

/** 读取缓存覆盖层；传入 baseGridVersion 时不匹配返回 null。 */
export function readOverlayCache(baseGridVersion?: string): OverlayCacheEntry | null {
  const entry = getJson<OverlayCacheEntry>(OVERLAY_CACHE_KEY);
  if (entry === null || entry.payload === undefined || !Array.isArray(entry.payload.points)) {
    return null;
  }
  if (baseGridVersion !== undefined && entry.payload.baseGridVersion !== baseGridVersion) {
    return null;
  }
  return entry;
}

export function writeOverlayCache(
  payload: OverlayPayload,
  etag: string | null,
  fetchedAt: number = Date.now(),
): void {
  const entry: OverlayCacheEntry = {
    gridVersion: payload.gridVersion ?? '',
    paramsVersion: payload.paramsVersion ?? null,
    etag,
    fetchedAt,
    payload,
  };
  setJson(OVERLAY_CACHE_KEY, entry);
}

export function clearOverlayCache(): void {
  remove(OVERLAY_CACHE_KEY);
}

function builtinGrid(message: string, unknownPoints = 0): RuntimeGrid {
  return {
    points: [], // 由调用方传入内置点集，见 resolveRuntimeGrid
    gridVersion: FINGERPRINT_GRID_VERSION,
    baseGridVersion: FINGERPRINT_GRID_VERSION,
    paramsVersion: null,
    source: 'builtin',
    fetchedAt: null,
    unknownPoints,
    message,
  };
}

function fromPayload(
  base: FpGridPoint[],
  payload: OverlayPayload,
  source: GridOverlaySource,
  fetchedAt: number,
  extraMessage = '',
): RuntimeGrid {
  const merged = mergeOverlay(base, payload);
  const message =
    merged.unknownPoints > 0
      ? `覆盖层有 ${merged.unknownPoints} 个点不在内置基线中，已忽略`
      : extraMessage;
  return {
    points: merged.points,
    gridVersion: payload.gridVersion ?? FINGERPRINT_GRID_VERSION,
    baseGridVersion: payload.baseGridVersion ?? FINGERPRINT_GRID_VERSION,
    paramsVersion: payload.paramsVersion ?? null,
    source,
    fetchedAt,
    unknownPoints: merged.unknownPoints,
    message,
  };
}

export type OverlayFetchResult =
  | { status: 'ok'; payload: OverlayPayload; etag: string | null }
  | { status: 'unchanged'; entry: OverlayCacheEntry }
  | { status: 'unavailable'; reason: string };

/**
 * 拉取覆盖层；304 复用缓存，服务端明确不可用返回 unavailable。
 * 网络/协议错误会抛出，由 resolveRuntimeGrid 统一降级。
 */
export async function fetchGridOverlay(
  client: ApiClient,
  baseGridVersion: string = FINGERPRINT_GRID_VERSION,
): Promise<OverlayFetchResult> {
  const cache = readOverlayCache(baseGridVersion);
  const headers: Record<string, string> = {};
  if (cache !== null && cache.etag !== null) {
    headers['If-None-Match'] = cache.etag;
  }
  const resp = await client.request<OverlayPayload | OverlayUnavailable>({
    method: 'GET',
    path: `/api/v1/config/fingerprint-grid/overlay?baseGridVersion=${encodeURIComponent(
      baseGridVersion,
    )}`,
    auth: false,
    headers,
  });
  if (resp.statusCode === 304) {
    if (cache !== null) {
      writeOverlayCache(cache.payload, cache.etag, Date.now());
      return { status: 'unchanged', entry: { ...cache, fetchedAt: Date.now() } };
    }
    return { status: 'unavailable', reason: 'not_modified_without_cache' };
  }
  const data = resp.data;
  if (data !== null && typeof data === 'object' && 'available' in data && data.available === false) {
    return { status: 'unavailable', reason: data.reason };
  }
  if (data === null || typeof data !== 'object') {
    return { status: 'unavailable', reason: 'empty_overlay' };
  }
  const payload = data as OverlayPayload;
  if (!Array.isArray(payload.points)) {
    return { status: 'unavailable', reason: 'invalid_overlay' };
  }
  return { status: 'ok', payload, etag: resp.etag };
}

/**
 * 解析运行时网格：在线 → 缓存 → 内置，任何失败都不阻塞采集页。
 * @param base 内置设计层点集（FINGERPRINT_GRID）。
 */
export async function resolveRuntimeGrid(
  client: ApiClient,
  base: FpGridPoint[],
  options: { baseGridVersion?: string; fetch?: boolean } = {},
): Promise<RuntimeGrid> {
  const baseVersion = options.baseGridVersion ?? FINGERPRINT_GRID_VERSION;
  const fallback = (message: string): RuntimeGrid => ({
    ...builtinGrid(message),
    points: base,
  });

  if (options.fetch !== false) {
    try {
      const result = await fetchGridOverlay(client, baseVersion);
      if (result.status === 'ok') {
        const invalid = overlayInvalidReason(result.payload, baseVersion);
        if (invalid !== null) {
          return fallback(invalid);
        }
        writeOverlayCache(result.payload, result.etag, Date.now());
        return fromPayload(base, result.payload, 'online', Date.now());
      }
      if (result.status === 'unchanged') {
        return fromPayload(base, result.entry.payload, 'cache', result.entry.fetchedAt);
      }
      log.info('覆盖层不可用', result.reason);
    } catch (err) {
      log.warn('覆盖层拉取失败，回退缓存/内置', err);
    }
  }

  const cached = readOverlayCache(baseVersion);
  if (cached !== null) {
    const invalid = overlayInvalidReason(cached.payload, baseVersion);
    if (invalid === null) {
      return fromPayload(base, cached.payload, 'cache', cached.fetchedAt, '离线：标记可能不是最新');
    }
  }
  return fallback('覆盖层不可用，使用内置网格快照');
}
