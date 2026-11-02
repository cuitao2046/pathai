/**
 * 业务根地址来源：编译期固定字符串，或启动时解析的动态地址（局域网发现）。
 * 统一由装配层注入，避免登录、业务 API、地图下载各自持有不同地址。
 */
export type BaseUrlProvider = string | (() => string);

export function resolveBaseUrl(baseUrl: BaseUrlProvider): string {
  return typeof baseUrl === 'string' ? baseUrl : baseUrl();
}
