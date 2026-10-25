/**
 * API 客户端：Bearer 注入、401 作废重登+同 body 重放一次、
 * 错误信封→ApiClientError、requestId 透出。
 * 客户端按 code 分支不按 message（docs/24 §7.2）。
 */
import { REQUEST_TIMEOUT_MS } from '../../config/env';
import { createLogger } from '../../utils/logger';
import {
  AUTH_RETRYABLE_CODES,
  extractErrorCode,
  parseErrorEnvelope,
} from './errors';
import { getHeader, request as transportRequest, type TransportResponse } from './transport';
import { WechatTokenManager, type TokenManager } from './token-manager';

const log = createLogger('api');

export interface ApiResponse<T> {
  statusCode: number;
  data: T | null;
  /** 优先取错误信封 requestId，否则取 X-Request-Id 响应头。 */
  requestId: string | null;
}

export interface ApiRequestOptions<B = unknown> {
  method: 'GET' | 'POST' | 'PUT' | 'DELETE';
  /** 以 / 开头的 API 路径。 */
  path: string;
  body?: B;
  /** 默认 true；登录端点等匿名请求传 false。 */
  auth?: boolean;
  timeoutMs?: number;
  /** API-06 幂等键；与冻结的 body 一起重试复用（C7）。 */
  idempotencyKey?: string;
}

export interface HttpStack {
  client: ApiClient;
  tokens: TokenManager;
}

export function createHttpStack(baseUrl: string): HttpStack {
  const tokens = new WechatTokenManager(baseUrl);
  return { tokens, client: new ApiClient(baseUrl, tokens) };
}

interface SendResult {
  resp: TransportResponse;
  requestId: string | null;
  errorCode: string | null;
}

export class ApiClient {
  constructor(
    private readonly baseUrl: string,
    private readonly tokens: TokenManager,
  ) {}

  async request<T>(opts: ApiRequestOptions): Promise<ApiResponse<T>> {
    const useAuth = opts.auth !== false;
    const first = await this.send(opts, useAuth);

    const shouldReplay =
      first.resp.statusCode === 401 &&
      useAuth &&
      first.errorCode !== null &&
      AUTH_RETRYABLE_CODES.has(first.errorCode);
    if (shouldReplay) {
      log.warn('令牌失效，重登后重放一次', opts.path);
      await this.tokens.forceRefresh();
      const second = await this.send(opts, useAuth);
      return this.finish<T>(second);
    }
    return this.finish<T>(first);
  }

  private async send(opts: ApiRequestOptions, auth: boolean): Promise<SendResult> {
    const header: Record<string, string> = { 'content-type': 'application/json' };
    if (auth) {
      header.Authorization = `Bearer ${await this.tokens.getAccessToken()}`;
    }
    if (opts.idempotencyKey !== undefined) {
      header['Idempotency-Key'] = opts.idempotencyKey;
    }
    const resp = await transportRequest({
      url: this.baseUrl + opts.path,
      method: opts.method,
      data: opts.body,
      header,
      timeoutMs: opts.timeoutMs ?? REQUEST_TIMEOUT_MS,
    });
    return {
      resp,
      requestId: getHeader(resp.header, 'X-Request-Id'),
      errorCode: resp.statusCode >= 400 ? extractErrorCode(resp.data) : null,
    };
  }

  private finish<T>(result: SendResult): ApiResponse<T> {
    const { resp, requestId } = result;
    const ok = (resp.statusCode >= 200 && resp.statusCode <= 299) || resp.statusCode === 304;
    if (!ok) {
      throw parseErrorEnvelope({ statusCode: resp.statusCode, data: resp.data, requestId });
    }
    return { statusCode: resp.statusCode, data: resp.data as T | null, requestId };
  }
}
