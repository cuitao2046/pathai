/** Silent WeChat identity, scoped to the resolved backend. No profile permissions. */
import { REQUEST_TIMEOUT_MS } from '../../config/env';
import { getJson, setJson, remove } from '../../utils/kv';
import type { AuthResponse } from '../../typings/api';
import { ApiClientError, parseErrorEnvelope } from './errors';
import { request } from './transport';

interface StoredSession { token: string; userId: string; expiresAt: number }

export class WechatSession {
  private inflight: Promise<string> | null = null;
  constructor(private readonly base: string) {}
  private get key(): string { return `auth:wechat:${this.base}`; }

  invalidate(token: string): void {
    if (getJson<StoredSession>(this.key)?.token === token) remove(this.key);
  }

  ensure(): Promise<string> {
    const cached = getJson<StoredSession>(this.key);
    if (cached && typeof cached.token === 'string' && cached.token.length > 0 &&
        typeof cached.userId === 'string' && cached.expiresAt > Date.now() + 30_000) {
      return Promise.resolve(cached.token);
    }
    if (this.inflight === null) {
      this.inflight = this.login().finally(() => { this.inflight = null; });
    }
    return this.inflight;
  }

  private async login(): Promise<string> {
    const code = await new Promise<string>((resolve, reject) => {
      let finished = false;
      const finish = (value: string | null) => {
        if (finished) return;
        finished = true;
        clearTimeout(timer);
        if (value) resolve(value);
        else reject(new ApiClientError({ code: 'AUTH_PROVIDER_UNAVAILABLE', message: '身份连接暂不可用，请重试', retryable: true }));
      };
      const timer = setTimeout(() => finish(null), REQUEST_TIMEOUT_MS);
      try {
        wx.login({ timeout: REQUEST_TIMEOUT_MS, success: res => finish(res.code || null), fail: () => finish(null) });
      } catch { finish(null); }
    });
    const response = await request({ url: `${this.base}/api/v1/auth/wechat`, method: 'POST', data: { code }, header: { 'content-type': 'application/json' } });
    if (response.statusCode !== 200) {
      throw parseErrorEnvelope({ statusCode: response.statusCode, data: response.data, requestId: null });
    }
    const body = response.data as AuthResponse | null;
    if (!body || typeof body.accessToken !== 'string' || !body.accessToken ||
        typeof body.userId !== 'string' || !/^anon_[0-9a-f]{16}$/.test(body.userId) ||
        !Number.isFinite(body.expiresIn) || body.expiresIn <= 0 || body.tokenType !== 'Bearer') {
      throw new ApiClientError({ code: 'HTTP_ERROR', message: '身份响应格式异常' });
    }
    setJson(this.key, { token: body.accessToken, userId: body.userId, expiresAt: Date.now() + body.expiresIn * 1000 });
    return body.accessToken;
  }
}
