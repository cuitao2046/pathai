/**
 * API 客户端：错误信封→ApiClientError、requestId 透出。
 * 客户端按 code 分支不按 message（docs/24 §7.2）。
 *
 * 生产装配启用微信静默登录；令牌过期只在明确认证拒绝时单次重放。
 */
import { WechatSession } from './wechat-session';
import { ApiClientError } from './errors';
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

/** Raw by default for diagnostics; production explicitly enables silent login. */
export interface ConnectionRecovery {
  prepare(): Promise<unknown>;
  failed(base: string): Promise<void>;
}
export function createHttpStack(baseUrl: BaseUrlProvider, options: { wechatLogin?: boolean; recovery?: ConnectionRecovery } = {}): HttpStack {
  return { client: new ApiClient(baseUrl, options.wechatLogin === true, options.recovery) };
}

interface SendResult {
  resp: TransportResponse;
  requestId: string | null;
  etag: string | null;
}

export class ApiClient {
  private readonly sessions = new Map<string, WechatSession>();
  constructor(private readonly baseUrl: BaseUrlProvider, private readonly wechatLogin = false, private readonly recovery?: ConnectionRecovery) {}

  async request<T>(opts: ApiRequestOptions): Promise<ApiResponse<T>> {
    let attempted: string | null = null;
    try {
      if (this.recovery) {
        await this.recovery.prepare();
        attempted = resolveBaseUrl(this.baseUrl);
      }
      return await this.requestOnce<T>(opts);
    } catch (error) {
      if (attempted && error instanceof ApiClientError && ['NETWORK_ERROR', 'REQUEST_TIMEOUT'].includes(error.code)) {
        try { await this.recovery!.failed(attempted); } catch { /* Preserve the original request error. */ }
      }
      // Never replay an uncertain write after a connection failure or address change.
      throw error;
    }
  }

  private async requestOnce<T>(opts: ApiRequestOptions): Promise<ApiResponse<T>> {
    if (!this.wechatLogin) return this.finish<T>(await this.send(opts));
    const base = resolveBaseUrl(this.baseUrl).replace(/\/$/, '');
    const url = opts.url ?? base + (opts.path ?? '');
    const origin = (value: string) => /^https?:\/\/[^/?#]+/i.exec(value)?.[0].toLowerCase();
    // Never disclose a backend token to a foreign download URL or caller header.
    if (!origin(base) || origin(url) !== origin(base) ||
        Object.keys(opts.headers ?? {}).some(key => key.toLowerCase() === 'authorization')) {
      throw new ApiClientError({ code: 'HTTP_ERROR', message: '身份请求地址或请求头不合法' });
    }
    let session = this.sessions.get(base);
    if (!session) { session = new WechatSession(base); this.sessions.set(base, session); }
    const token = await session.ensure();
    const authenticated = (value: string) => ({ ...opts, url, headers: { ...opts.headers, Authorization: `Bearer ${value}` } });
    const first = await this.send(authenticated(token));
    if (first.resp.statusCode === 401) {
      const error = parseErrorEnvelope({ statusCode: 401, data: first.resp.data, requestId: first.requestId });
      if (error.code === 'TOKEN_EXPIRED' || error.code === 'UNAUTHENTICATED') {
        // Only authentication-dependency rejection is replayed, once, with the same body/key.
        session.invalidate(token);
        const fresh = await session.ensure();
        return this.finish<T>(await this.send(authenticated(fresh)));
      }
    }
    return this.finish<T>(first);
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
