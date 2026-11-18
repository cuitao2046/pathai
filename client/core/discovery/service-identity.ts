/**
 * 服务身份校验（纯函数）：`/healthz` 的 additive 身份字段。
 *
 * 设备身份以 `device_id` 为准、IP 只作地址：同网段里的其它 PathAI 后端
 * 即使健康检查可达，也不能被当作同一台服务端。
 */
import { BUSINESS_SERVICE } from './business-protocol';
import type { CancelToken } from './cancel-token';

/** 服务端持久化的稳定设备标识格式（与后端 LocalDeviceIdentity 一致）。 */
const DEVICE_ID_PATTERN = /^srv-[0-9a-f]{16,32}$/;

export interface ServiceIdentity {
  service: string;
  deviceId: string;
  version: string | null;
  apiVersion: string | null;
}

/** 单次探测结果：区分“连不上”“连上了但不是目标服务”与“被主动取消”。 */
export type EndpointProbeResult =
  | { kind: 'identified'; identity: ServiceIdentity }
  | { kind: 'unidentified' }
  | { kind: 'unreachable' }
  | { kind: 'cancelled' };

export type EndpointProbe = (url: string, cancel?: CancelToken) => Promise<EndpointProbeResult>;

export function isDeviceId(value: unknown): value is string {
  return typeof value === 'string' && DEVICE_ID_PATTERN.test(value);
}

/**
 * 校验 `/healthz` 响应是否属于期望的 PathAI 服务。
 * `version` / `api_version` 是 additive 元数据，缺失时不影响身份判定。
 */
export function parseServiceIdentity(statusCode: number, data: unknown): ServiceIdentity | null {
  if (statusCode !== 200 || typeof data !== 'object' || data === null) {
    return null;
  }
  const body = data as {
    status?: unknown;
    service?: unknown;
    device_id?: unknown;
    version?: unknown;
    api_version?: unknown;
  };
  if (body.status !== 'ok' || body.service !== BUSINESS_SERVICE) {
    return null;
  }
  if (!isDeviceId(body.device_id)) {
    return null;
  }
  return {
    service: body.service,
    deviceId: body.device_id,
    version: typeof body.version === 'string' ? body.version : null,
    apiVersion: typeof body.api_version === 'string' ? body.api_version : null,
  };
}
