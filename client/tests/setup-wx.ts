/**
 * Jest 全局 wx 桩（setupFiles：每个测试文件执行前重新加载，天然隔离）。
 * 提供内存 storage 与震动记录器；request/蓝牙桩在 C2/C6 按需扩展。
 */
export interface WxStub {
  readonly __storage: Map<string, string>;
  readonly __vibrationLog: Array<'short' | 'long'>;
  readonly __requestLog: Array<Record<string, unknown>>;
  setStorageSync(key: string, value: string): void;
  getStorageSync(key: string): string;
  removeStorageSync(key: string): void;
  vibrateShort(option?: { type?: string }): void;
  vibrateLong(option?: Record<string, never>): void;
}

const storage = new Map<string, string>();
const vibrationLog: Array<'short' | 'long'> = [];
const requestLog: Array<Record<string, unknown>> = [];

export const wxStub: WxStub = {
  __storage: storage,
  __vibrationLog: vibrationLog,
  __requestLog: requestLog,

  setStorageSync(key, value) {
    storage.set(key, value);
  },

  getStorageSync(key) {
    return storage.get(key) ?? '';
  },

  removeStorageSync(key) {
    storage.delete(key);
  },

  vibrateShort() {
    vibrationLog.push('short');
  },

  vibrateLong() {
    vibrationLog.push('long');
  },
};

(globalThis as unknown as { wx: WxStub }).wx = wxStub;
