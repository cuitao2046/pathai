/**
 * TokenManager：单飞登录、过期前 300s 刷新、持久化、作废。
 */
import { createHttpStack } from '../core/http/api-client';
import { setNowSource, resetNowSource } from '../utils/clock';
import { getJson, remove, setJson } from '../utils/kv';
import type { StoredToken } from '../core/http/token-manager';
import { wxStub } from './setup-wx';

const BASE = 'http://localhost:8000';
const LOGIN_URL = `${BASE}/api/v1/auth/wechat`;

function respondAuth(token: string): void {
  wxStub.__requestResponder = (o) => {
    if (o.url === LOGIN_URL) {
      o.success({
        statusCode: 200,
        header: {},
        data: { accessToken: token, tokenType: 'Bearer', expiresIn: 7200, userId: 'anon_abc' },
      });
      return;
    }
    o.success({ statusCode: 200, header: {}, data: {} });
  };
}

beforeEach(() => {
  wxStub.__storage.clear();
  wxStub.__loginCount = 0;
  wxStub.__requestLog.length = 0;
  wxStub.__requestResponder = null;
  // kv 模块级内存降级缓存跨测试存活，需一并清除
  remove('auth:token');
});

afterEach(() => {
  resetNowSource();
});

test('并发 getAccessToken 单飞：wx.login 与登录 POST 各只一次', async () => {
  respondAuth('tok-1');
  const { tokens } = createHttpStack(BASE);
  const [a, b] = await Promise.all([tokens.getAccessToken(), tokens.getAccessToken()]);
  expect(a).toBe('tok-1');
  expect(b).toBe('tok-1');
  expect(wxStub.__loginCount).toBe(1);
  const loginPosts = wxStub.__requestLog.filter((r) => r.url === LOGIN_URL);
  expect(loginPosts.length).toBe(1);
  expect(loginPosts[0]?.data).toEqual({ code: 'fake-mock-wx-code' }); // 联调开关加 fake- 前缀
});

test('登录成功后令牌持久化（含 7200s 过期）', async () => {
  setNowSource(() => 1_700_000_000_000);
  respondAuth('tok-p');
  const { tokens } = createHttpStack(BASE);
  await tokens.getAccessToken();
  const stored = getJson<StoredToken>('auth:token');
  expect(stored?.accessToken).toBe('tok-p');
  expect(stored?.userId).toBe('anon_abc');
  expect(stored?.expiresAtMs).toBe(1_700_000_000_000 + 7_200_000);
  expect(tokens.currentUserId()).toBe('anon_abc');
});

test('令牌距过期不足 300 秒时自动刷新', async () => {
  const now = 1_700_000_000_000;
  setNowSource(() => now);
  // 有效期仅剩 100s < 300s 余量 → 不可用，需刷新
  setJson('auth:token', {
    accessToken: 'tok-old',
    userId: 'anon_old',
    expiresAtMs: now + 100_000,
  });
  respondAuth('tok-new');
  const { tokens } = createHttpStack(BASE);
  const token = await tokens.getAccessToken();
  expect(token).toBe('tok-new');
  expect(wxStub.__loginCount).toBe(1);
});

test('余量内（>300s）不触发刷新', async () => {
  const now = 1_700_000_000_000;
  setNowSource(() => now);
  setJson('auth:token', {
    accessToken: 'tok-ok',
    userId: 'anon_old',
    expiresAtMs: now + 400_000,
  });
  respondAuth('tok-should-not-be-used');
  const { tokens } = createHttpStack(BASE);
  const token = await tokens.getAccessToken();
  expect(token).toBe('tok-ok');
  expect(wxStub.__loginCount).toBe(0);
});

test('登录接口失败（AUTH_CODE_INVALID）向调用方抛 ApiClientError', async () => {
  wxStub.__requestResponder = (o) => {
    o.success({
      statusCode: 401,
      header: {},
      data: {
        error: { code: 'AUTH_CODE_INVALID', message: 'bad code', retryable: false, details: {} },
        requestId: 'req_login',
      },
    });
  };
  const { tokens } = createHttpStack(BASE);
  await expect(tokens.getAccessToken()).rejects.toMatchObject({
    name: 'ApiClientError',
    code: 'AUTH_CODE_INVALID',
    requestId: 'req_login',
  });
});

test('invalidate 清除缓存与持久化', async () => {
  respondAuth('tok-x');
  const { tokens } = createHttpStack(BASE);
  await tokens.getAccessToken();
  tokens.invalidate();
  expect(tokens.currentUserId()).toBeNull();
  expect(getJson('auth:token')).toBeNull();
});
