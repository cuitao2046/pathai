import { SERVICE_PORT } from '../config/env';
import type { CancelToken } from '../core/discovery/cancel-token';
import { isP0CandidateUrl } from '../core/discovery/p0-candidate';
import {
  parseServiceIdentity,
  type EndpointProbeResult,
} from '../core/discovery/service-identity';
import { createLogger } from '../utils/logger';
import { getString, remove, setString } from '../utils/kv';

const log = createLogger('probe');

const CACHE_KEY = 'discovery:p0-candidate-url';
/** 与地址一起缓存设备身份：换 IP 后仍以 device_id 识别同一台服务端。 */
const DEVICE_KEY = 'discovery:p0-candidate-device-id';
const TIMEOUT_MS = 1500;

function allowedCandidate(url: string): boolean {
  return isP0CandidateUrl(url) && url.endsWith(`:${SERVICE_PORT}`);
}

export function getP0Candidate(): string | null {
  const stored = getString(CACHE_KEY);
  if (stored !== null && allowedCandidate(stored)) return stored;
  // 无缓存就是无缓存：不再把编译期地址当作“已缓存候选”写回，
  // 否则换网后旧 IP 会被优先探活并伪装成有效缓存。
  return null;
}

/** 上次成功解析到的设备身份；用于扫描时只接受同一台服务端。 */
export function getP0DeviceId(): string | null {
  return getString(DEVICE_KEY);
}

/** 保存地址与设备身份；未提供设备身份时清掉旧身份，避免地址与身份错配。 */
export function saveP0Candidate(url: string, deviceId?: string | null): boolean {
  if (!allowedCandidate(url)) return false;
  setString(CACHE_KEY, url);
  if (deviceId) {
    setString(DEVICE_KEY, deviceId);
  } else {
    remove(DEVICE_KEY);
  }
  return getString(CACHE_KEY) === url;
}

/**
 * 探测一个业务根地址的 `/healthz`，校验服务名与 device_id。
 * 区分「连不上」与「有响应但不是目标服务」，供扫描统计失败原因；
 * 传入取消信号时，取消会立即 abort 在途请求并返回 `cancelled`。
 */
export function probeEndpoint(
  url: string,
  timeoutMs = TIMEOUT_MS,
  cancel?: CancelToken,
): Promise<EndpointProbeResult> {
  return new Promise((resolve) => {
    const startedAt = Date.now();
    let finished = false;
    let request: WechatMiniprogram.RequestTask | undefined;
    let timer: ReturnType<typeof setTimeout> | null = null;
    let unlink: (() => void) | null = null;
    const finish = (result: EndpointProbeResult) => {
      if (finished) return;
      finished = true;
      if (timer !== null) clearTimeout(timer);
      unlink?.();
      if (result.kind === 'unreachable' || result.kind === 'cancelled') request?.abort();
      log.debug(
        'GET /healthz',
        url,
        `结果=${result.kind}`,
        `耗时=${Date.now() - startedAt}ms`,
      );
      resolve(result);
    };
    if (cancel?.aborted) {
      finish({ kind: 'cancelled' });
      return;
    }
    timer = setTimeout(() => finish({ kind: 'unreachable' }), timeoutMs + 100);
    unlink = cancel?.onCancel(() => finish({ kind: 'cancelled' })) ?? null;
    try {
      request = wx.request({
        url: `${url}/healthz`,
        method: 'GET',
        timeout: timeoutMs,
        success: ({ statusCode, data }) => {
          const identity = parseServiceIdentity(statusCode, data);
          finish(identity ? { kind: 'identified', identity } : { kind: 'unidentified' });
        },
        fail: () => finish({ kind: 'unreachable' }),
      });
    } catch {
      finish({ kind: 'unreachable' });
    }
  });
}

/** 无凭据 `/healthz` 可达性探活（模拟器回环地址与旧调用方使用）。 */
export async function checkEndpointHealth(url: string, timeoutMs = TIMEOUT_MS): Promise<boolean> {
  return (await probeEndpoint(url, timeoutMs)).kind === 'identified';
}

/**
 * 缓存候选验活：地址合法且响应身份匹配。
 * 已知设备身份时必须一致，防止同网另一台后端占用旧 IP 后被误认为原服务。
 */
export async function checkP0Candidate(
  url: string,
  expectedDeviceId?: string | null,
  cancel?: CancelToken,
): Promise<boolean> {
  if (!allowedCandidate(url)) return false;
  const result = await probeEndpoint(url, undefined, cancel);
  return result.kind === 'identified' &&
    (expectedDeviceId === undefined || expectedDeviceId === null ||
      result.identity.deviceId === expectedDeviceId);
}
