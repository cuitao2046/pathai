import { isP0CandidateUrl } from '../core/discovery/p0-candidate';
import { API_BASE_URL } from '../config/env';
import { getString, setString } from '../utils/kv';

const CACHE_KEY = 'discovery:p0-candidate-url';
const TIMEOUT_MS = 1500;
const expectedPort = API_BASE_URL.match(/:(\d+)$/)?.[1];

function allowedCandidate(url: string): boolean {
  return isP0CandidateUrl(url) && expectedPort !== undefined && url.endsWith(`:${expectedPort}`);
}

export function getP0Candidate(): string | null {
  const stored = getString(CACHE_KEY);
  if (stored !== null && allowedCandidate(stored)) return stored;
  // 无缓存就是无缓存：不再把编译期地址当作“已缓存候选”写回，
  // 否则换网后旧 IP 会被优先探活并伪装成有效缓存。
  return null;
}

export function saveP0Candidate(url: string): boolean {
  if (!allowedCandidate(url)) return false;
  setString(CACHE_KEY, url);
  return getString(CACHE_KEY) === url;
}

/** 无凭据 `/healthz` 可达性探活（候选缓存与模拟器回环地址共用）。 */
export function checkEndpointHealth(url: string, timeoutMs = TIMEOUT_MS): Promise<boolean> {
  return new Promise((resolve) => {
    let finished = false;
    let request: WechatMiniprogram.RequestTask | undefined;
    const finish = (reachable: boolean) => {
      if (finished) return;
      finished = true;
      clearTimeout(timer);
      if (!reachable) request?.abort();
      resolve(reachable);
    };
    const timer = setTimeout(() => finish(false), timeoutMs + 100);
    try {
      request = wx.request({
        url: `${url}/healthz`,
        method: 'GET',
        timeout: timeoutMs,
        success: ({ statusCode, data }) => {
          const body = data as { status?: unknown };
          finish(statusCode === 200 && body?.status === 'ok');
        },
        fail: () => finish(false),
      });
    } catch {
      finish(false);
    }
  });
}

export function checkP0Candidate(url: string): Promise<boolean> {
  if (!allowedCandidate(url)) return Promise.resolve(false);
  return checkEndpointHealth(url);
}
