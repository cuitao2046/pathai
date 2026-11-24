"""Apply fix 01 to its isolated checkout only; never edits the source checkout."""
from pathlib import Path

ROOT = Path(r'C:\Users\xinni\.codex\worktrees\fix-wechat-silent-auth\pathai')
assert (ROOT / '.git').is_file(), 'Expected a linked worktree'

def put(name, content):
    target = ROOT / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding='utf-8')

def replace(name, old, new):
    target = ROOT / name
    text = target.read_text(encoding='utf-8')
    if new in text:
        return
    assert old in text, f'Missing anchor in {name}'
    target.write_text(text.replace(old, new), encoding='utf-8')

put('client/core/http/wechat-session.ts', '''/** Silent WeChat identity, scoped to the resolved backend. No profile permissions. */
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
''')

replace('client/core/http/api-client.ts', "import { REQUEST_TIMEOUT_MS }", "import { WechatSession } from './wechat-session';\nimport { ApiClientError } from './errors';\nimport { REQUEST_TIMEOUT_MS }")
replace('client/core/http/api-client.ts', 'export function createHttpStack(baseUrl: BaseUrlProvider): HttpStack {\n  return { client: new ApiClient(baseUrl) };\n}', '''/** Raw by default for diagnostics; production explicitly enables silent login. */
export function createHttpStack(baseUrl: BaseUrlProvider, options: { wechatLogin?: boolean } = {}): HttpStack {
  return { client: new ApiClient(baseUrl, options.wechatLogin === true) };
}''')
replace('client/core/http/api-client.ts', '''  constructor(private readonly baseUrl: BaseUrlProvider) {}

  async request<T>(opts: ApiRequestOptions): Promise<ApiResponse<T>> {
    return this.finish<T>(await this.send(opts));
  }''', '''  private readonly sessions = new Map<string, WechatSession>();
  constructor(private readonly baseUrl: BaseUrlProvider, private readonly wechatLogin = false) {}

  async request<T>(opts: ApiRequestOptions): Promise<ApiResponse<T>> {
    if (!this.wechatLogin) return this.finish<T>(await this.send(opts));
    const base = resolveBaseUrl(this.baseUrl).replace(/\\/$/, '');
    const url = opts.url ?? base + (opts.path ?? '');
    const origin = (value: string) => /^https?:\\/\\/[^/?#]+/i.exec(value)?.[0].toLowerCase();
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
  }''')
replace('client/core/http/api-client.ts', ' * 2026-11-11 决策：私网阶段小程序接口不鉴权，不再注入 Authorization，\n * 也不做 401 重登重放；公网阶段恢复鉴权时在此统一补回。', ' * 生产装配启用微信静默登录；令牌过期只在明确认证拒绝时单次重放。')

target = ROOT / 'client/services/app-context.ts'
content = target.read_text(encoding='utf-8')
if 'createHttpStack(currentServiceBaseUrl)' in content:
    content = content.replace('createHttpStack(currentServiceBaseUrl)', 'createHttpStack(currentServiceBaseUrl, { wechatLogin: true })')
else:
    content = content.replace('createHttpStack(() => currentServiceBaseUrl())', 'createHttpStack(() => currentServiceBaseUrl(), { wechatLogin: true })')
target.write_text(content, encoding='utf-8')

put('client/tests/wechat-session.test.ts', '''import { createHttpStack } from '../core/http/api-client';
import { remove } from '../utils/kv';
import { wxStub } from './setup-wx';

const BASE = 'http://localhost:8000';
const USER = 'anon_0123456789abcdef';
beforeEach(() => {
  remove(`auth:wechat:${BASE}`); remove('auth:wechat:http://localhost:8001');
  wxStub.__storage.clear(); wxStub.__requestLog.length = 0; wxStub.__loginCount = 0;
});
function responder(expired = false) {
  let logins = 0;
  wxStub.__requestResponder = o => {
    if (o.url.endsWith('/auth/wechat')) {
      logins++;
      o.success({ statusCode: 200, data: { accessToken: `token-${logins}`, userId: USER, tokenType: 'Bearer', expiresIn: 7200 } });
    } else if (expired && o.header?.Authorization === 'Bearer token-1') {
      o.success({ statusCode: 401, data: { error: { code: 'TOKEN_EXPIRED', message: 'expired', retryable: false, details: {} }, requestId: 'r' } });
    } else o.success({ statusCode: 200, data: { ok: true } });
  };
}
test('first request silently logs in; subsequent request reuses the cached identity', async () => {
  responder();
  const { client } = createHttpStack(BASE, { wechatLogin: true });
  await client.request({ method: 'GET', path: '/api/v1/sites/s/map' });
  await client.request({ method: 'GET', path: '/api/v1/voice/asr-url' });
  expect(wxStub.__loginCount).toBe(1);
  expect(wxStub.__requestLog.slice(1).every(o => o.header?.Authorization === 'Bearer token-1')).toBe(true);
});
test('concurrent expired requests share a refresh and retain POST body/idempotency key', async () => {
  responder(true);
  const { client } = createHttpStack(BASE, { wechatLogin: true });
  await Promise.all([1, 2].map(() => client.request({ method: 'POST', path: '/api/v1/nav/sessions', body: { value: 1 }, idempotencyKey: 'same' })));
  expect(wxStub.__loginCount).toBe(2);
  for (const req of wxStub.__requestLog.filter(o => o.url.endsWith('/nav/sessions'))) {
    expect(req.data).toEqual({ value: 1 });
    expect(req.header?.['Idempotency-Key']).toBe('same');
  }
});
test('foreign URLs are rejected before login without leaking a token', async () => {
  responder();
  const { client } = createHttpStack(BASE, { wechatLogin: true });
  await expect(client.request({ method: 'GET', url: 'https://foreign.test/map' })).rejects.toThrow();
  expect(wxStub.__requestLog).toHaveLength(0);
});
test('caller cannot override authorization header', async () => {
  responder();
  const { client } = createHttpStack(BASE, { wechatLogin: true });
  await expect(client.request({ method: 'GET', path: '/', headers: { authorization: 'other' } })).rejects.toThrow();
});
test('403 is not retried or turned into another login', async () => {
  responder();
  const { client } = createHttpStack(BASE, { wechatLogin: true });
  await client.request({ method: 'GET', path: '/' });
  wxStub.__requestResponder = o => o.success({ statusCode: 403, data: { error: { code: 'FORBIDDEN', message: 'no', details: {} } } });
  await expect(client.request({ method: 'POST', path: '/', body: {} })).rejects.toMatchObject({ statusCode: 403 });
  expect(wxStub.__loginCount).toBe(1);
});
test('malformed login is rejected before business request', async () => {
  wxStub.__requestResponder = o => o.success({ statusCode: 200, data: {} });
  const { client } = createHttpStack(BASE, { wechatLogin: true });
  await expect(client.request({ method: 'GET', path: '/' })).rejects.toThrow();
  expect(wxStub.__requestLog).toHaveLength(1);
});
test('credentials are isolated when the resolved backend changes', async () => {
  responder();
  let base = BASE;
  const { client } = createHttpStack(() => base, { wechatLogin: true });
  await client.request({ method: 'GET', path: '/' });
  base = 'http://localhost:8001';
  await client.request({ method: 'GET', path: '/' });
  expect(wxStub.__loginCount).toBe(2);
});
test('repeated 401 stops after one replay', async () => {
  responder();
  const { client } = createHttpStack(BASE, { wechatLogin: true });
  await client.request({ method: 'GET', path: '/' });
  const loginResponder = wxStub.__requestResponder!;
  wxStub.__requestResponder = o => o.url.endsWith('/auth/wechat') ? loginResponder(o) : o.success({ statusCode: 401, data: { error: { code: 'TOKEN_EXPIRED', message: 'expired', details: {} } } });
  await expect(client.request({ method: 'GET', path: '/' })).rejects.toMatchObject({ statusCode: 401 });
  expect(wxStub.__loginCount).toBe(2);
});
''')

put('client/review-artifacts/execution-plan.md', '''修复执行计划（2026-11-15）

每项独立分支、独立提交、独立验证。不修改 master 上的既有文档，不自动合并。

01 codex/fix-wechat-silent-auth：无感登录、身份缓存、单飞刷新和请求重放边界（当前）。
02 codex/fix-tester-permissions：依赖 01；普通用户/测试人员及配置应用独立开关，后台授权页面、数据库迁移、客户端权限展示。
03 codex/fix-nav-text-invariants：导航动作使用确定性模板。
04 codex/fix-nav-offline-safety：本地失联安全暂停。
05 codex/fix-location-lifecycle：异步代际隔离、单飞。
06 codex/fix-map-version-session：活跃会话固定地图。
07 codex/fix-route-projection：点到线段投影。
08 codex/fix-location-freshness：时间窗口及到达确认。
09 codex/fix-nav-create-idempotency：保留创建结果未知的键及请求。
10 codex/fix-surroundings-contract：周边查询后端契约。
11 codex/fix-location-sequence：水位详情及原子更新。
12 codex/fix-endpoint-recovery：地址失效与受控重发现。
13 codex/fix-map-integrity：下载摘要校验。
14 codex/fix-map-publish-atomicity：发布并发与 worker 同步。
15 codex/fix-admin-bootstrap-security：初始化凭据和登录限流。

每项先补充回归测试，再执行针对性验证；阶段性运行全量 client/backend。真机微信、BLE、PostgreSQL 并发及多 worker 另外验收，不以 mock 替代。
''')
print('Applied silent-login fix and tests to', ROOT)
