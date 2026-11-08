/**
 * wx Storage 薄封装：统一 'pathai:' 前缀、字符串/JSON 读写、
 * storage 异常时降级为进程内存（当次会话仍可用，重启后重建）。
 * 用途：访问令牌、地图 ETag、定位 sequence 基线等持久化。
 */
const PREFIX = 'pathai:';
const memoryFallback = new Map<string, string>();

function withPrefix(key: string): string {
  return PREFIX + key;
}

export function getString(key: string): string | null {
  let stored: string | null = null;
  try {
    const value = wx.getStorageSync(withPrefix(key));
    stored = typeof value === 'string' && value.length > 0 ? value : null;
  } catch {
    stored = null;
  }
  // 优先持久层（重启存活）；storage 不可用/无值时回落到本次会话的内存写入
  return stored ?? memoryFallback.get(key) ?? null;
}

/**
 * 写入字符串；返回是否真正落到持久层（false = 仅写进本次会话内存）。
 * 调用方必须处理 false（如地图摘要写失败会导致下次启动重复下载），不得静默。
 */
export function setString(key: string, value: string): boolean {
  memoryFallback.set(key, value);
  try {
    wx.setStorageSync(withPrefix(key), value);
    return true;
  } catch {
    // storage 不可用（磁盘满/权限异常）：内存兜底已写入，下次启动重建
    return false;
  }
}

export function getJson<T>(key: string): T | null {
  const raw = getString(key);
  if (raw === null) {
    return null;
  }
  try {
    return JSON.parse(raw) as T;
  } catch {
    return null; // 损坏数据视同缺失
  }
}

/** 写入 JSON；返回值同 setString（false = 仅内存可用）。 */
export function setJson(key: string, value: unknown): boolean {
  return setString(key, JSON.stringify(value));
}

export function remove(key: string): void {
  memoryFallback.delete(key);
  try {
    wx.removeStorageSync(withPrefix(key));
  } catch {
    // 内存已删除，持久层异常忽略
  }
}
