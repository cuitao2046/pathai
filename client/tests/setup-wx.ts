/**
 * Jest 全局 wx 桩（setupFiles：每个测试文件执行前重新加载，天然隔离）。
 * 提供内存 storage、可编程 request 应答器、用户目录文件系统（二进制追加要求文件
 * 已存在，与真机 appendFile 语义一致；覆盖地图断点续传）、wx.login 桩与震动记录器。
 */
export interface WxRequestOptionsStub {
  url: string;
  method?: string;
  header?: Record<string, string>;
  data?: unknown;
  timeout?: number;
  responseType?: 'arraybuffer' | 'text';
  dataType?: string;
  success(res: { statusCode: number; header?: Record<string, string>; data?: unknown; errMsg?: string }): void;
  fail(err: { errMsg: string }): void;
}

/** 用户目录文件系统桩（wx FileSystemManager 子集）。 */
export interface WxFsStub {
  readFile(options: {
    filePath: string;
    encoding?: string;
    success(res: { data: string | ArrayBuffer }): void;
    fail(err: { errMsg: string }): void;
  }): void;
  writeFile(options: {
    filePath: string;
    data: string | ArrayBuffer;
    encoding?: string;
    success(): void;
    fail(err: { errMsg: string }): void;
  }): void;
  appendFile(options: {
    filePath: string;
    data: string | ArrayBuffer;
    encoding?: string;
    success(): void;
    fail(err: { errMsg: string }): void;
  }): void;
  stat(options: {
    path: string;
    success(res: { stats: { size: number } }): void;
    fail(err: { errMsg: string }): void;
  }): void;
  rename(options: {
    oldPath: string;
    newPath: string;
    success(): void;
    fail(err: { errMsg: string }): void;
  }): void;
  unlink(options: { filePath: string; success(): void; fail(err: { errMsg: string }): void }): void;
}

export interface WxStub {
  readonly __storage: Map<string, string>;
  /** 用户目录文件内存桩（字节形式，覆盖二进制半成品与文本缓存）。 */
  readonly __files: Map<string, Uint8Array>;
  /** 读取文件为 UTF-8 文本（断言用）；不存在返回 undefined。 */
  __fileText(path: string): string | undefined;
  readonly __vibrationLog: Array<'short' | 'long'>;
  /** 请求记录（不含回调），按调用顺序追加。 */
  readonly __requestLog: Array<
    Pick<WxRequestOptionsStub, 'url' | 'method' | 'header' | 'data' | 'responseType'>
  >;
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
  getFileSystemManager(): WxFsStub;
}

// Node 运行时已有 TextEncoder/TextDecoder；tsconfig 的 lib 未含 DOM，这里局部声明
declare const TextEncoder: { new (): { encode(input: string): Uint8Array } };
declare const TextDecoder: { new (label?: string): { decode(input: Uint8Array): string } };

const storage = new Map<string, string>();
const files = new Map<string, Uint8Array>();
const vibrationLog: Array<'short' | 'long'> = [];
const requestLog: WxStub['__requestLog'] = [];

function encode(text: string): Uint8Array {
  return new TextEncoder().encode(text);
}

function decode(bytes: Uint8Array): string {
  return new TextDecoder('utf-8').decode(bytes);
}

function toBytes(data: string | ArrayBuffer): Uint8Array {
  return typeof data === 'string' ? encode(data) : new Uint8Array(data);
}

function toArrayBuffer(bytes: Uint8Array): ArrayBuffer {
  return bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength) as ArrayBuffer;
}

export const wxStub: WxStub = {
  __storage: storage,
  __files: files,
  __vibrationLog: vibrationLog,
  __requestLog: requestLog,
  __loginCount: 0,
  __loginCode: 'mock-wx-code',
  __requestResponder: null,
  env: { USER_DATA_PATH: '/mock-user-data' },

  __fileText(path: string): string | undefined {
    const bytes = files.get(path);
    return bytes === undefined ? undefined : decode(bytes);
  },

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
        const bytes = files.get(options.filePath);
        if (bytes === undefined) {
          options.fail({ errMsg: 'readFile:fail no such file' });
          return;
        }
        options.success({
          data: options.encoding ? decode(bytes) : toArrayBuffer(bytes),
        });
      },
      writeFile(options) {
        files.set(options.filePath, toBytes(options.data));
        options.success();
      },
      appendFile(options) {
        const prev = files.get(options.filePath);
        // 贴近真机：微信 appendFile 不会隐式建档，目标不存在即失败
        if (prev === undefined) {
          options.fail({ errMsg: 'appendFile:fail no such file or directory' });
          return;
        }
        const next = toBytes(options.data);
        const merged = new Uint8Array(prev.length + next.length);
        merged.set(prev, 0);
        merged.set(next, prev.length);
        files.set(options.filePath, merged);
        options.success();
      },
      stat(options) {
        const bytes = files.get(options.path);
        if (bytes === undefined) {
          options.fail({ errMsg: 'stat:fail no such file' });
          return;
        }
        options.success({ stats: { size: bytes.length } });
      },
      rename(options) {
        const bytes = files.get(options.oldPath);
        if (bytes === undefined) {
          options.fail({ errMsg: 'rename:fail no such file' });
          return;
        }
        files.delete(options.oldPath);
        files.set(options.newPath, bytes);
        options.success();
      },
      unlink(options) {
        if (!files.has(options.filePath)) {
          options.fail({ errMsg: 'unlink:fail no such file' });
          return;
        }
        files.delete(options.filePath);
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
      responseType: options.responseType,
    });
    const responder = this.__requestResponder;
    const success = options.success;
    // responseType=arraybuffer 时把桩里的字符串应答模拟成 ArrayBuffer（贴近 wx 行为）
    const wrapped: WxRequestOptionsStub = {
      ...options,
      success: (res) => {
        const data =
          options.responseType === 'arraybuffer' && typeof res.data === 'string'
            ? toArrayBuffer(encode(res.data))
            : res.data;
        success({ ...res, data });
      },
    };
    if (responder === null) {
      wrapped.success({ statusCode: 200, header: {}, data: {} });
      return;
    }
    responder(wrapped);
  },
};

(globalThis as unknown as { wx: WxStub }).wx = wxStub;
