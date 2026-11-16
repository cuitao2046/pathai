/**
 * 运行时服务端根地址（单一来源）。
 *
 * 启动门禁：`map` 页 bootstrap 必须先 `await ensureServiceEndpoint()` 成功，
 * 才允许初始化语音、加载地图与发起任何业务请求。解析一次、全程复用。
 *
 * 真机/桌面端策略由设置项「服务发现方式」决定（默认自动）：
 * - auto：缓存探活 → UDP 广播 → HTTP 单播扫描，任一成功即停；
 * - broadcast：缓存探活 → UDP 广播；
 * - scan：缓存探活 → HTTP 单播扫描。
 * 缓存探活与所有发现结果都校验 `/healthz` 的 `service` 与 `device_id`，
 * 设备身份以 `device_id` 为准、IP 只作地址。
 *
 * 开发者工具模拟器：只走本机回环直连（`127.0.0.1` + 后端端口）并做 `/healthz`
 * 探活，不进入广播或扫描——模拟器内 `wx.createUDPSocket` 会因单例/UDP 能力限制
 * 直接失败（真机日志：`UDPSocket(socketId = 1) is already created`）。
 *
 * **未解析成功时不再回退编译期地址**：`currentServiceBaseUrl()` 返回 null，
 * `requireServiceBaseUrl()` / HTTP 栈直接抛 ENDPOINT_UNRESOLVED，请求不会发出。
 * 说明：`/healthz`、免配对 OFFER 与扫描响应只证明“某个私网主机可达”，
 * HTTP 也不提供链路保密。按 specs/local-service-discovery/spec.md
 * v0.2.0（用户确认）仅用于本地/自建服务与受控联调网络。
 */
import { API_BASE_URL } from '../config/env';
import type { EndpointFailureReason } from '../core/discovery/failure';
import { ApiClientError } from '../core/http/errors';
import { createLogger } from '../utils/logger';
import { discoverBusinessCandidate } from './business-discovery';
import { getDiscoveryMode } from './discovery-mode';
import { scanLanForService } from './http-discovery';
import {
  checkEndpointHealth,
  getP0Candidate,
  getP0DeviceId,
  probeEndpoint,
  saveP0Candidate,
} from './p0-candidate';
import { isDevtoolsRuntime } from './runtime-platform';

export type { EndpointFailureReason } from '../core/discovery/failure';

const log = createLogger('endpoint');

let current: string | null = null;
let inflight: Promise<string | null> | null = null;
let lastFailure: EndpointFailureReason | null = null;
let generation = 0;

/** Invalidate without allowing a late failure from an old address to erase a new one. */
export function invalidateServiceEndpoint(expected?: string): void {
  if (expected !== undefined && current !== expected) return;
  ++generation;
  current = null;
  inflight = null;
}

export async function recoverServiceEndpoint(failedBase: string): Promise<void> {
  invalidateServiceEndpoint(failedBase);
  await ensureServiceEndpoint();
}

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
    const version = generation;
    const task = resolveEndpoint(version)
      .then(candidate => {
        if (version !== generation) return null;
        current = candidate;
        return candidate;
      })
      .catch(() => null)
      .finally(() => {
        if (inflight === task) inflight = null;
      });
    inflight = task;
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

/** 缓存候选验活：必须身份匹配；旧缓存没有设备身份时补写。 */
async function verifyCachedEndpoint(
  cached: string,
  knownDeviceId: string | null,
  version: number,
): Promise<boolean> {
  const startedAt = Date.now();
  const probe = await probeEndpoint(cached);
  const cost = Date.now() - startedAt;
  if (version !== generation) {
    log.debug('缓存探活结果已过期（地址已被替换）', cached, `耗时=${cost}ms`);
    return false;
  }
  if (probe.kind !== 'identified') {
    log.info('缓存探活未命中', cached, `结果=${probe.kind}`, `耗时=${cost}ms`);
    return false;
  }
  if (knownDeviceId !== null && probe.identity.deviceId !== knownDeviceId) {
    log.warn('缓存地址的设备身份已变化，按新设备重新发现', cached,
      `缓存=${knownDeviceId} 实际=${probe.identity.deviceId}`, `耗时=${cost}ms`);
    return false;
  }
  if (knownDeviceId === null) {
    saveP0Candidate(cached, probe.identity.deviceId);
  }
  log.info('缓存探活命中', cached, `device_id=${probe.identity.deviceId}`, `耗时=${cost}ms`);
  return true;
}

async function resolveEndpoint(version: number): Promise<string | null> {
  lastFailure = null;
  const startedAt = Date.now();
  const elapsed = () => Date.now() - startedAt;
  if (isDevtoolsRuntime()) {
    const loopback = devtoolsLoopbackBaseUrl();
    if (loopback === null) {
      lastFailure = 'devtools-port-missing';
      log.warn('开发者工具模拟器：编译期配置缺少后端端口，无法直连本机回环');
      return null;
    }
    const healthy = await checkEndpointHealth(loopback);
    if (version !== generation) return null;
    if (healthy) {
      log.info('服务地址已确定', loopback, '来源=模拟器回环', `耗时=${elapsed()}ms`,
        '（不进入广播/扫描）');
      return loopback;
    }
    lastFailure = 'devtools-backend-unreachable';
    log.warn('开发者工具模拟器：本机回环后端不可达', loopback,
      `耗时=${elapsed()}ms`,
      '；模拟器内 UDP 发现不可用，也不会回退到局域网广播或扫描');
    return null;
  }

  const cached = getP0Candidate();
  const knownDeviceId = getP0DeviceId();
  if (cached !== null) {
    if (await verifyCachedEndpoint(cached, knownDeviceId, version)) {
      log.info('服务地址已确定', cached, '来源=缓存', `耗时=${elapsed()}ms`);
      return cached;
    }
    if (version !== generation) return null;
    log.debug('缓存地址不可用，进入服务发现', cached, `已耗时=${elapsed()}ms`);
  } else {
    log.info('无缓存地址，直接进入服务发现');
  }

  const mode = getDiscoveryMode();
  log.info(`服务发现方式=${mode}`);
  if (mode === 'scan') log.info('跳过 UDP 广播：仅扫描模式');

  if (mode !== 'scan') {
    const broadcast = await discoverBusinessCandidate(knownDeviceId);
    if (version !== generation) return null;
    if (broadcast !== null && saveP0Candidate(broadcast.url, broadcast.identity.deviceId)) {
      log.info('服务地址已确定', broadcast.url, '来源=广播',
        `device_id=${broadcast.identity.deviceId}`, `耗时=${elapsed()}ms`);
      return broadcast.url;
    }
    if (mode === 'broadcast') {
      lastFailure = 'lan-service-not-found';
      log.warn('仅广播模式：缓存不可达且 UDP 广播未发现可用服务', `总耗时=${elapsed()}ms`);
      return null;
    }
  }

  const scanned = await scanLanForService(knownDeviceId);
  if (version !== generation) return null;
  if (scanned.service !== null && saveP0Candidate(scanned.service.url, scanned.service.identity.deviceId)) {
    log.info('服务地址已确定', scanned.service.url, '来源=扫描',
      `device_id=${scanned.service.identity.deviceId}`, `耗时=${elapsed()}ms`);
    return scanned.service.url;
  }
  lastFailure = scanned.reason ?? 'scan-requests-failed';
  log.warn('扫描发现未解析到服务地址', lastFailure, `总耗时=${elapsed()}ms`);
  return null;
}
