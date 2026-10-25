/**
 * 令牌管理：wx.login → API-01 换取 opaque 令牌，storage 持久化。
 * 规则（docs/24 §8 API-01 + 计划 C2）：
 * - 过期前 300 秒即视为不可用，静默无感刷新；
 * - 并发请求单飞（共享同一 inflight Promise，只发一次登录）；
 * - 登录走 API 同源（本地联调 PATHAI_WECHAT_FAKE=1 时 code 为 fake-<openid>）。
 */
import { REQUEST_TIMEOUT_MS } from '../../config/env';
import { nowMs } from '../../utils/clock';
import { createLogger } from '../../utils/logger';
import { getJson, remove, setJson } from '../../utils/kv';
import type { AuthResponse } from '../../typings/api';
import { ApiClientError, parseErrorEnvelope } from './errors';
import { request as transportRequest } from './transport';

const log = createLogger('token');

const STORAGE_KEY = 'auth:token';
const LOGIN_PATH = '/api/v1/auth/wechat';
/** 提前刷新余量：过期前 300 秒。 */
const REFRESH_MARGIN_MS = 300_000;

export interface StoredToken {
  accessToken: string;
  userId: string;
  /** 本地时钟下的绝对过期时间（毫秒）。 */
  expiresAtMs: number;
}

export interface TokenManager {
  /** 取可用令牌；必要时静默刷新。 */
  getAccessToken(): Promise<string>;
  /** 作废当前令牌并强制重登（401 重放路径使用）。 */
  forceRefresh(): Promise<string>;
  /** 仅作废缓存与持久化，不立即重登。 */
  invalidate(): void;
  /** 当前匿名主体 ID（未登录返回 null）。 */
  currentUserId(): string | null;
}

function wxLoginCode(): Promise<string> {
  return new Promise((resolve, reject) => {
    wx.login({
      success: (res) => {
        if (res.code) {
          resolve(res.code);
        } else {
          reject(
            new ApiClientError({
              code: 'NETWORK_ERROR',
              message: 'wx.login 未返回 code',
              retryable: true,
            }),
          );
        }
      },
      fail: () =>
        reject(
          new ApiClientError({ code: 'NETWORK_ERROR', message: 'wx.login 失败', retryable: true }),
        ),
    });
  });
}

export class WechatTokenManager implements TokenManager {
  private cached: StoredToken | null;
  private inflight: Promise<StoredToken> | null = null;

  constructor(private readonly baseUrl: string) {
    this.cached = getJson<StoredToken>(STORAGE_KEY);
  }

  getAccessToken(): Promise<string> {
    if (this.isUsable(this.cached)) {
      return Promise.resolve(this.cached.accessToken);
    }
    return this.forceRefresh();
  }

  forceRefresh(): Promise<string> {
    if (this.inflight === null) {
      this.inflight = this.login().finally(() => {
        this.inflight = null;
      });
    }
    return this.inflight.then((token) => token.accessToken);
  }

  invalidate(): void {
    this.cached = null;
    remove(STORAGE_KEY);
  }

  currentUserId(): string | null {
    return this.cached?.userId ?? null;
  }

  private isUsable(token: StoredToken | null): token is StoredToken {
    return token !== null && token.expiresAtMs - REFRESH_MARGIN_MS > nowMs();
  }

  private async login(): Promise<StoredToken> {
    const code = await wxLoginCode();
    const resp = await transportRequest({
      url: this.baseUrl + LOGIN_PATH,
      method: 'POST',
      data: { code },
      timeoutMs: REQUEST_TIMEOUT_MS,
    });
    if (resp.statusCode >= 400) {
      throw parseErrorEnvelope({
        statusCode: resp.statusCode,
        data: resp.data,
        requestId: null,
      });
    }
    const auth = resp.data as AuthResponse | null;
    if (auth === null || typeof auth.accessToken !== 'string') {
      throw new ApiClientError({
        code: 'INTERNAL_ERROR',
        message: '登录响应格式异常',
        retryable: true,
        statusCode: resp.statusCode,
      });
    }
    const stored: StoredToken = {
      accessToken: auth.accessToken,
      userId: auth.userId,
      expiresAtMs: nowMs() + auth.expiresIn * 1000,
    };
    this.cached = stored;
    setJson(STORAGE_KEY, stored);
    log.info('登录成功', stored.userId);
    return stored;
  }
}
