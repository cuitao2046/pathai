/**
 * Jest 全局 wx 桩（setupFiles：每个测试文件执行前重新加载，天然隔离）。
 * 提供内存 storage、可编程 request 应答器、wx.login 桩与震动记录器。
 * 蓝牙桩在 C6 按需扩展。
 */
export interface WxRequestOptionsStub {
  url: string;
  method?: string;
  header?: Record<string, string>;
  data?: unknown;
  timeout?: number;
  success(res: { statusCode: number; header?: Record<string, string>; data?: unknown; errMsg?: string }): void;
  fail(err: { errMsg: string }): void;
}

export interface WxStub {
  readonly __storage: Map<string, string>;
  /** 用户目录文件内存桩（地图文件缓存用）。 */
  readonly __files: Map<string, string>;
  readonly __vibrationLog: Array<'short' | 'long'>;
  /** 请求记录（不含回调），按调用顺序追加。 */
  readonly __requestLog: Array<Pick<WxRequestOptionsStub, 'url' | 'method' | 'header' | 'data'>>;
  /** wx.login 调用计数。 */
  __loginCount: number;
  /** wx.login 返回的 code（测试可改写）。 */
  __loginCode: string;
  /** 可编程应答器；为 null 时默认成功返回空对象。 */
  __requestResponder: ((options: WxRequestOptionsStub) => void) | null;
  readonly env: { USER_DATA_PATH: string };
  setStorageSync(key: string, value: string): void;
  getStorageSync(key: string): string;
  removeStorageSync(key: string): void;
  vibrateShort(option?: { type?: string }): void;
  vibrateLong(option?: Record<string, never>): void;
  login(option?: { success(res: { code: string }): void; fail(err: { errMsg: string }): void }): void;
  request(options: WxRequestOptionsStub): void;
  getFileSystemManager(): {
    readFile(options: { filePath: string; encoding: string; success(res: { data: string }): void; fail(err: { errMsg: string }): void }): void;
    writeFile(options: { filePath: string; encoding: string; data: string; success(): void; fail(err: { errMsg: string }): void }): void;
  };
}

const storage = new Map<string, string>();
const files = new Map<string, string>();
const vibrationLog: Array<'short' | 'long'> = [];
const requestLog: WxStub['__requestLog'] = [];

export const wxStub: WxStub = {
  __storage: storage,
  __files: files,
  __vibrationLog: vibrationLog,
  __requestLog: requestLog,
  __loginCount: 0,
  __loginCode: 'mock-wx-code',
  __requestResponder: null,
  env: { USER_DATA_PATH: '/mock-user-data' },

  setStorageSync(key, value) {
    storage.set(key, value);
  },

  getStorageSync(key) {
    return storage.get(key) ?? '';
  },

  removeStorageSync(key) {
    storage.delete(key);
  },

  getFileSystemManager() {
    return {
      readFile(options) {
        const data = files.get(options.filePath);
        if (data === undefined) {
          options.fail({ errMsg: 'readFile:fail no such file' });
          return;
        }
        options.success({ data });
      },
      writeFile(options) {
        files.set(options.filePath, options.data);
        options.success();
      },
    };
  },

  vibrateShort() {
    vibrationLog.push('short');
  },

  vibrateLong() {
    vibrationLog.push('long');
  },

  login(option) {
    this.__loginCount += 1;
    option?.success({ code: this.__loginCode });
  },

  request(options) {
    requestLog.push({
      url: options.url,
      method: options.method,
      header: options.header,
      data: options.data,
    });
    const responder = this.__requestResponder;
    if (responder === null) {
      options.success({ statusCode: 200, header: {}, data: {} });
      return;
    }
    responder(options);
  },
};

(globalThis as unknown as { wx: WxStub }).wx = wxStub;
