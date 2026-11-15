/**
 * 运行时服务端根地址（单一来源）。
 *
 * 启动门禁：`map` 页 bootstrap 必须先 `await ensureServiceEndpoint()` 成功，
 * 才允许初始化语音、加载地图与发起任何业务请求。解析一次、全程复用。
 *
 * 解析顺序（真机/桌面端）：kv 缓存候选 → `/healthz` 探活 → UDP 广播发现。
 * 开发者工具模拟器：只走本机回环直连（`127.0.0.1` + 后端端口）并做 `/healthz`
 * 探活，不发起 UDP 广播——模拟器内 `wx.createUDPSocket` 会因单例/UDP 能力限制
 * 直接失败（真机日志：`UDPSocket(socketId = 1) is already created`）。
 * **未解析成功时不再回退编译期地址**：`currentServiceBaseUrl()` 返回 null，
 * `requireServiceBaseUrl()` / HTTP 栈直接抛 ENDPOINT_UNRESOLVED，请求不会发出。
 * 说明：`/healthz` 与免配对 OFFER 只证明“某个私网主机可达”，不证明服务端
 * 身份；HTTP 也不提供链路保密。按 specs/local-service-discovery/spec.md
 * v0.2.0（用户确认）仅用于本地/自建服务与受控联调网络。
 */
import { API_BASE_URL } from '../config/env';
import { ApiClientError } from '../core/http/errors';
import { createLogger } from '../utils/logger';
import { checkEndpointHealth, checkP0Candidate, getP0Candidate, saveP0Candidate } from './p0-candidate';
import { discoverBusinessCandidate } from './business-discovery';
import { isDevtoolsRuntime } from './runtime-platform';

const log = createLogger('endpoint');

/** 解析失败原因：区分模拟器专用路径与真机局域网发现。 */
export type EndpointFailureReason =
  | 'devtools-backend-unreachable'
  | 'devtools-port-missing'
  | 'lan-service-not-found';

let current: string | null = null;
let inflight: Promise<string | null> | null = null;
let lastFailure: EndpointFailureReason | null = null;

/** 当前生效的业务根地址；未解析成功返回 null（不回退编译期配置）。 */
export function currentServiceBaseUrl(): string | null {
  return current;
}

/**
 * 已解析的业务根地址；未解析直接抛 ENDPOINT_UNRESOLVED。
 * 供“必须在发出请求前失败”的调用方使用（HTTP 栈统一走这里）。
 */
export function requireServiceBaseUrl(): string {
  if (current === null) {
    throw new ApiClientError({
      code: 'ENDPOINT_UNRESOLVED',
      message: '服务端地址未解析',
      retryable: true,
    });
  }
  return current;
}

/** 本轮已解析出的地址（未解析返回 null，供页面区分“已发现”与“未就绪”）。 */
export function resolvedServiceBaseUrl(): string | null {
  return current;
}

/** 最近一轮解析失败原因；解析成功或尚未解析时为 null。 */
export function lastEndpointFailure(): EndpointFailureReason | null {
  return lastFailure;
}

/**
 * 启动解析（缓存优先，发现兜底）；并发调用共享同一轮任务。
 * 返回 null 表示缓存不可达且本轮发现也失败；调用方必须提示重试，
 * 不得改用旧地址发起业务请求。
 */
export async function ensureServiceEndpoint(): Promise<string | null> {
  if (current !== null) return current;
  if (inflight === null) {
    inflight = resolveEndpoint()
      .catch(() => null)
      .finally(() => {
        inflight = null;
      });
  }
  return inflight;
}

/**
 * 模拟器回环地址：只复用编译期配置里的后端端口，主机固定 `127.0.0.1`。
 * 模拟器与后端同机运行，这是明确可辨识的专用路径，不是“拿不到 IP 就回退编译期地址”。
 */
function devtoolsLoopbackBaseUrl(): string | null {
  const port = /:(\d+)\/?$/.exec(API_BASE_URL)?.[1];
  return port === undefined ? null : `http://127.0.0.1:${port}`;
}

async function resolveEndpoint(): Promise<string | null> {
  lastFailure = null;
  if (isDevtoolsRuntime()) {
    const loopback = devtoolsLoopbackBaseUrl();
    if (loopback === null) {
      lastFailure = 'devtools-port-missing';
      log.warn('开发者工具模拟器：编译期配置缺少后端端口，无法直连本机回环');
      return null;
    }
    if (await checkEndpointHealth(loopback)) {
      current = loopback;
      log.info('开发者工具模拟器：已连接本机回环后端', loopback, '（跳过局域网 UDP 广播）');
      return loopback;
    }
    lastFailure = 'devtools-backend-unreachable';
    log.warn('开发者工具模拟器：本机回环后端不可达', loopback,
      '；模拟器内 UDP 发现不可用，无法改用局域网广播寻找后端');
    return null;
  }

  const cached = getP0Candidate();
  if (cached !== null && (await checkP0Candidate(cached))) {
    current = cached;
    return cached;
  }
  const discovered = await discoverBusinessCandidate();
  if (discovered !== null && saveP0Candidate(discovered)) {
    current = discovered;
    return discovered;
  }
  lastFailure = 'lan-service-not-found';
  log.warn('局域网未发现服务：缓存候选不可达，UDP 广播也未获得可用 OFFER');
  return null;
}
