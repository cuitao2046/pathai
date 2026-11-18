/**
 * 运行时定位参数缓存（spec：calibration-console §6.7、§9.1）。
 * - 来源：GET /api/v1/config/location-params（只读、无副作用、无需登录）；
 * - 缓存键 cfg:runtimeParams；拉取失败回退缓存，缓存也没有则由调用方用内置常量兜底；
 * - 指纹采集页记录版本、设置页展示版本、计算页读取当前 active 版本都用这里。
 */
import type { ApiClient } from '../http/api-client';
import { getJson, setJson } from '../../utils/kv';
import { createLogger } from '../../utils/logger';

const log = createLogger('runtime-config');

/** 运行时参数缓存键（spec §8.2 的 cfg:* 约定）。 */
export const RUNTIME_PARAMS_KEY = 'cfg:runtimeParams';

/** 客户端缓存有效期：服务端 TTL ≤2s，客户端无需频繁拉取。 */
const DEFAULT_MAX_AGE_MS = 60_000;

export interface RuntimeParams {
  siteId: string;
  /** 当前生效参数版本；无 active 版本时为 null。 */
  paramsVersion: string | null;
  /** 当前生效网格版本；无 active 版本时为 null。 */
  gridVersion: string | null;
  /** 参数来源：db / env / default（服务端口径）。 */
  source: string;
  rssiAt1mDbm: number;
  pathLossN: number;
  residualReliableM: number;
  residualDegradedM: number;
  /** 导航判定阈值（服务端生效值，米）：到达半径与转向提前播报距离。 */
  navArrivalRadiusM: number;
  navTurnPromptDistanceM: number;
  /** 导航阈值来源：db（设置页保存过）/ env（环境冻结默认）。 */
  navPolicySource: string;
  ttlS: number;
  /** 客户端拉取时刻（毫秒），用于本地过期判断。 */
  fetchedAt: number;
}

interface LocationParamsResponse {
  siteId: string;
  paramsVersion: string | null;
  gridVersion: string | null;
  source: string;
  rssiAt1mDbm: number;
  pathLossN: number;
  residualReliableM: number;
  residualDegradedM: number;
  navArrivalRadiusM: number;
  navTurnPromptDistanceM: number;
  navPolicySource: string;
  ttlS: number;
}

/** 读取最近一次成功拉取的运行时参数（无缓存返回 null）。 */
export function cachedRuntimeParams(): RuntimeParams | null {
  const cached = getJson<RuntimeParams>(RUNTIME_PARAMS_KEY);
  // 旧版本缓存没有导航阈值字段，直接视为无缓存，避免设置页显示 undefined。
  if (
    cached === null ||
    typeof cached.rssiAt1mDbm !== 'number' ||
    typeof cached.navArrivalRadiusM !== 'number' ||
    typeof cached.navTurnPromptDistanceM !== 'number'
  ) {
    return null;
  }
  return cached;
}

/** 清除运行时参数缓存（apply/回滚后强制重新拉取）。 */
export function clearRuntimeParams(): void {
  setJson(RUNTIME_PARAMS_KEY, null);
}

/**
 * 拉取运行时参数：命中本地有效期直接返回缓存；失败返回旧缓存（不抛错）。
 * @returns 最新或缓存参数；两者都没有时返回 null。
 */
export async function refreshRuntimeParams(
  client: ApiClient,
  options: { maxAgeMs?: number; force?: boolean } = {},
): Promise<RuntimeParams | null> {
  const cached = cachedRuntimeParams();
  const maxAgeMs = options.maxAgeMs ?? DEFAULT_MAX_AGE_MS;
  if (
    options.force !== true &&
    cached !== null &&
    Date.now() - cached.fetchedAt <= maxAgeMs
  ) {
    return cached;
  }
  try {
    const resp = await client.request<LocationParamsResponse>({
      method: 'GET',
      path: '/api/v1/config/location-params',
    });
    const data = resp.data;
    if (data === null || typeof data.rssiAt1mDbm !== 'number') {
      return cached;
    }
    const fresh: RuntimeParams = {
      siteId: data.siteId,
      paramsVersion: data.paramsVersion ?? null,
      gridVersion: data.gridVersion ?? null,
      source: data.source,
      rssiAt1mDbm: data.rssiAt1mDbm,
      pathLossN: data.pathLossN,
      residualReliableM: data.residualReliableM,
      residualDegradedM: data.residualDegradedM,
      navArrivalRadiusM: data.navArrivalRadiusM,
      navTurnPromptDistanceM: data.navTurnPromptDistanceM,
      navPolicySource: data.navPolicySource,
      ttlS: data.ttlS,
      fetchedAt: Date.now(),
    };
    setJson(RUNTIME_PARAMS_KEY, fresh);
    return fresh;
  } catch (err) {
    log.warn('运行时参数拉取失败，回退缓存', err);
    return cached;
  }
}
