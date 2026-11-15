/**
 * 业务根地址来源：编译期固定字符串，或启动时解析的动态地址（局域网发现）。
 * 统一由装配层注入，避免登录、业务 API、地图下载各自持有不同地址。
 *
 * 解析结果可能为 null（启动解析未成功）：此时不允许发出请求，
 * 统一抛 ENDPOINT_UNRESOLVED，由 UI 走“未连接服务”的提示与重试路径。
 */
import { ApiClientError } from './errors';

export type BaseUrlProvider = string | (() => string | null);

export function resolveBaseUrl(baseUrl: BaseUrlProvider): string {
  const resolved = typeof baseUrl === 'string' ? baseUrl : baseUrl();
  if (typeof resolved !== 'string' || resolved.length === 0) {
    throw new ApiClientError({
      code: 'ENDPOINT_UNRESOLVED',
      message: '服务端地址未解析',
      retryable: true,
    });
  }
  return resolved;
}
