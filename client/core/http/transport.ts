/**
 * 传输层：wx.request 的 Promise 薄封装。
 * 只做三件事：回调转 Promise、网络失败→客户端合成错误码、超时透传。
 * 不含鉴权/重试/信封解析（分属 api-client 与 errors）。
 */
import { REQUEST_TIMEOUT_MS } from '../../config/env';
import { createLogger } from '../../utils/logger';
import { ApiClientError } from './errors';

const log = createLogger('http');

export interface TransportResponse<T = unknown> {
  statusCode: number;
  header: Record<string, string>;
  data: T | null;
}

export interface TransportOptions<B = unknown> {
  url: string;
  method: 'GET' | 'POST' | 'PUT' | 'DELETE';
  data?: B;
  header?: Record<string, string>;
  timeoutMs?: number;
  /** 地图数据下载需要原始文本：dataType 非 json 时 wx 不做自动 JSON.parse。 */
  responseType?: 'arraybuffer' | 'text';
  dataType?: string;
}

/** 大小写不敏感地读取响应头。 */
export function getHeader(header: Record<string, string>, name: string): string | null {
  const target = name.toLowerCase();
  for (const [key, value] of Object.entries(header)) {
    if (key.toLowerCase() === target) {
      return value;
    }
  }
  return null;
}

export function request<B, T = unknown>(opts: TransportOptions<B>): Promise<TransportResponse<T>> {
  return new Promise((resolve, reject) => {
    // 现场排障依赖这一行：完整 URL 能直接确认「发没发出去、发到哪、耗时多久」。
    // 不记录 header（含 Bearer），避免令牌进日志。
    const startedAt = Date.now();
    const timeoutMs = opts.timeoutMs ?? REQUEST_TIMEOUT_MS;
    wx.request({
      url: opts.url,
      method: opts.method,
      data: opts.data,
      header: opts.header,
      timeout: timeoutMs,
      responseType: opts.responseType,
      dataType: opts.dataType,
      success: (res) => {
        log.info(`${opts.method} ${opts.url} → ${res.statusCode} ${Date.now() - startedAt}ms`);
        resolve({
          statusCode: res.statusCode,
          header: res.header ?? {},
          // 304 等空体响应归一为 null
          data: res.data === '' || res.data === undefined ? null : (res.data as T),
        });
      },
      fail: (err) => {
        const errMsg = err?.errMsg ?? '';
        // 连接没建立（拒绝/不可达/连接超时）与「连上了但等不到响应」要分开报：
        // 前者是链路问题，后者才可能是服务端慢，现场不能一律按“超时”处理。
        const connectFailed =
          /ERR_CONNECTION_TIMED_OUT|ERR_CONNECTION_REFUSED|ERR_ADDRESS_UNREACHABLE|ERR_NAME_NOT_RESOLVED|ERR_INTERNET_DISCONNECTED/.test(
            errMsg,
          );
        const timedOut = !connectFailed && errMsg.includes('timeout');
        log.warn(
          `${opts.method} ${opts.url} 失败 ${Date.now() - startedAt}ms/${
            timeoutMs
          }ms ${connectFailed ? 'CONNECT_FAILED' : timedOut ? 'REQUEST_TIMEOUT' : 'NETWORK_ERROR'} errMsg=${errMsg}`,
        );
        reject(
          new ApiClientError(
            timedOut
              ? { code: 'REQUEST_TIMEOUT', message: `请求超时（${timeoutMs}ms）`, retryable: true }
              : { code: 'NETWORK_ERROR', message: '网络连接不可用', retryable: true },
          ),
        );
      },
    } as WechatMiniprogram.RequestOption);
  });
}
