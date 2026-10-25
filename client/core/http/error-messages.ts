/**
 * 错误码 → 本地话术映射（播报/UI 展示用）。
 * 客户端逻辑分支只认 code，话术仅面向用户，与后端 message 解耦。
 */
import type { ApiErrorCode } from '../../typings/api';

const MESSAGES: Partial<Record<ApiErrorCode, string>> = {
  // 认证
  AUTH_CODE_INVALID: '微信登录失败，请重试',
  AUTH_PROVIDER_UNAVAILABLE: '登录服务暂不可用，请稍后再试',
  UNAUTHENTICATED: '登录状态已失效，正在重新登录',
  TOKEN_EXPIRED: '登录状态已过期，正在重新登录',
  // 网络
  NETWORK_ERROR: '网络连接不可用，请检查网络',
  REQUEST_TIMEOUT: '请求超时，请重试',
  // 地图
  SITE_NOT_FOUND: '场所数据不可用，请联系管理员',
  MAP_NOT_PUBLISHED: '地图数据不可用，请联系管理员',
  MAP_VERSION_CONFLICT: '地图已更新，正在同步新版本',
  // 意图/地点
  PLACE_NOT_FOUND: '没有找到这个地点，请换个说法',
  // 定位
  STALE_LOCATION_BATCH: '位置数据滞后，正在重新定位',
  LOCATION_UNRELIABLE: '当前位置信号不稳，请稍作停留',
  // 路线与会话
  ROUTE_NOT_FOUND: '当前位置到目的地之间没有可行路线',
  ACCESSIBLE_ROUTE_NOT_FOUND: '盲模式下没有可达的电梯路线',
  SESSION_NOT_FOUND: '导航会话不存在，请重新开始',
  SESSION_NOT_ACTIVE: '导航会话已结束',
  STALE_NAV_UPDATE: '位置更新滞后，正在同步',
  IDEMPOTENCY_CONFLICT: '请求冲突，请重新操作',
  // 兜底
  FORBIDDEN: '没有权限执行该操作',
  INTERNAL_ERROR: '服务暂时不可用，请稍后再试',
  HTTP_ERROR: '服务响应异常，请重试',
  INVALID_REQUEST: '请求格式异常，请重试',
  NOT_FOUND: '请求的资源不存在',
  METHOD_NOT_ALLOWED: '请求方式不支持',
  PAYLOAD_TOO_LARGE: '请求数据过大',
};

export function describeError(code: ApiErrorCode): string {
  return MESSAGES[code] ?? '出了点问题，请重试';
}
