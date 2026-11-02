/**
 * 运行时服务端根地址（单一来源）。
 *
 * 启动解析：先检查上次缓存的候选地址，不可达则发起 UDP 广播发现，
 * 发现地址经业务端口 `/healthz` 可达后写入缓存并生效。登录换码、Bearer
 * 业务请求、地图摘要/Range 下载与语音凭据签发都从 currentServiceBaseUrl()
 * 取同一地址。
 *
 * 说明：`/healthz` 与免配对 OFFER 只证明“某个私网主机可达”，不证明服务端
 * 身份；HTTP 也不提供链路保密。按 specs/local-service-discovery/spec.md
 * v0.2.0（用户确认）仅用于本地/自建服务与受控联调网络。
 */
import { API_BASE_URL } from '../config/env';
import { checkP0Candidate, getP0Candidate, saveP0Candidate } from './p0-candidate';
import { discoverBusinessCandidate } from './business-discovery';

let current: string | null = null;
let inflight: Promise<string | null> | null = null;

/** 当前生效的业务根地址；未完成发现时退回编译期配置（仅用于构造 URL）。 */
export function currentServiceBaseUrl(): string {
  return current ?? API_BASE_URL;
}

/** 本轮已解析出的地址（未解析返回 null，供页面区分“已发现”与“回退值”）。 */
export function resolvedServiceBaseUrl(): string | null {
  return current;
}

/**
 * 启动解析（缓存优先，发现兜底）；并发调用共享同一轮任务。
 * 返回 null 表示缓存不可达且本轮发现也失败；调用方应提示重试，
 * 不得静默改用旧地址发起业务请求。
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

async function resolveEndpoint(): Promise<string | null> {
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
  return null;
}
