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

export function setString(key: string, value: string): void {
  memoryFallback.set(key, value);
  try {
    wx.setStorageSync(withPrefix(key), value);
  } catch {
    // storage 不可用（磁盘满/权限异常）：内存兜底已写入，下次启动重建
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

export function setJson(key: string, value: unknown): void {
  setString(key, JSON.stringify(value));
}

export function remove(key: string): void {
  memoryFallback.delete(key);
  try {
    wx.removeStorageSync(withPrefix(key));
  } catch {
    // 内存已删除，持久层异常忽略
  }
}
