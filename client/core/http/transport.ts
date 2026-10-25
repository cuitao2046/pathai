/**
 * 传输层：wx.request 的 Promise 薄封装。
 * 只做三件事：回调转 Promise、网络失败→客户端合成错误码、超时透传。
 * 不含鉴权/重试/信封解析（分属 api-client 与 errors）。
 */
import { REQUEST_TIMEOUT_MS } from '../../config/env';
import { ApiClientError } from './errors';

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
    wx.request({
      url: opts.url,
      method: opts.method,
      data: opts.data,
      header: opts.header,
      timeout: opts.timeoutMs ?? REQUEST_TIMEOUT_MS,
      responseType: opts.responseType,
      dataType: opts.dataType,
      success: (res) => {
        resolve({
          statusCode: res.statusCode,
          header: res.header ?? {},
          // 304 等空体响应归一为 null
          data: res.data === '' || res.data === undefined ? null : (res.data as T),
        });
      },
      fail: (err) => {
        const errMsg = err?.errMsg ?? '';
        reject(
          new ApiClientError(
            errMsg.includes('timeout')
              ? { code: 'REQUEST_TIMEOUT', message: '请求超时', retryable: true }
              : { code: 'NETWORK_ERROR', message: '网络连接不可用', retryable: true },
          ),
        );
      },
    } as WechatMiniprogram.RequestOption);
  });
}
