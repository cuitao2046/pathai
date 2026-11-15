/**
 * API 客户端：错误信封→ApiClientError、requestId 透出。
 * 客户端按 code 分支不按 message（docs/24 §7.2）。
 *
 * 2026-11-11 决策：私网阶段小程序接口不鉴权，不再注入 Authorization，
 * 也不做 401 重登重放；公网阶段恢复鉴权时在此统一补回。
 */
import { REQUEST_TIMEOUT_MS } from '../../config/env';
import { parseErrorEnvelope } from './errors';
import { getHeader, request as transportRequest, type TransportResponse } from './transport';
import { resolveBaseUrl, type BaseUrlProvider } from './base-url';

export interface ApiResponse<T> {
  statusCode: number;
  data: T | null;
  /** 优先取错误信封 requestId，否则取 X-Request-Id 响应头。 */
  requestId: string | null;
  /** ETag 响应头（地图端点缓存用）；缺失为 null。 */
  etag: string | null;
  /** 原始响应头（地图分片下载需读 Content-Range）。 */
  header: Record<string, string>;
}

export interface ApiRequestOptions<B = unknown> {
  method: 'GET' | 'POST' | 'PUT' | 'DELETE';
  /** 以 / 开头的 API 路径；与 url 二选一。 */
  path?: string;
  /** 完整 URL 覆盖（如服务端 dataUrl）。 */
  url?: string;
  body?: B;
  timeoutMs?: number;
  /** API-06 幂等键。 */
  idempotencyKey?: string;
  /** 附加请求头（如 If-None-Match）。 */
  headers?: Record<string, string>;
  /** 透传 wx.request 的响应形态（地图数据下载用 text）。 */
  responseType?: 'arraybuffer' | 'text';
  dataType?: string;
}

export interface HttpStack {
  client: ApiClient;
}

export function createHttpStack(baseUrl: BaseUrlProvider): HttpStack {
  return { client: new ApiClient(baseUrl) };
}

interface SendResult {
  resp: TransportResponse;
  requestId: string | null;
  etag: string | null;
}

export class ApiClient {
  constructor(private readonly baseUrl: BaseUrlProvider) {}

  async request<T>(opts: ApiRequestOptions): Promise<ApiResponse<T>> {
    return this.finish<T>(await this.send(opts));
  }

  private async send(opts: ApiRequestOptions): Promise<SendResult> {
    const header: Record<string, string> = { 'content-type': 'application/json' };
    if (opts.idempotencyKey !== undefined) {
      header['Idempotency-Key'] = opts.idempotencyKey;
    }
    if (opts.headers !== undefined) {
      Object.assign(header, opts.headers);
    }
    const url = opts.url ?? resolveBaseUrl(this.baseUrl) + (opts.path ?? '');
    const resp = await transportRequest({
      url,
      method: opts.method,
      data: opts.body,
      header,
      timeoutMs: opts.timeoutMs ?? REQUEST_TIMEOUT_MS,
      responseType: opts.responseType,
      dataType: opts.dataType,
    });
    return {
      resp,
      requestId: getHeader(resp.header, 'X-Request-Id'),
      etag: getHeader(resp.header, 'ETag'),
    };
  }

  private finish<T>(result: SendResult): ApiResponse<T> {
    const { resp, requestId, etag } = result;
    const ok = (resp.statusCode >= 200 && resp.statusCode <= 299) || resp.statusCode === 304;
    if (!ok) {
      throw parseErrorEnvelope({ statusCode: resp.statusCode, data: resp.data, requestId });
    }
    return { statusCode: resp.statusCode, data: resp.data as T | null, requestId, etag, header: resp.header };
  }
}
