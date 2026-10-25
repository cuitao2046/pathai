/**
 * ApiClient：Bearer 注入、401 作废重登+同 body 重放一次（不重放第二次）、
 * requestId 透出、304 成功分支、网络失败/超时映射、幂等键头透传。
 */
import { ApiClient, createHttpStack } from '../core/http/api-client';
import { ApiClientError } from '../core/http/errors';
import { describeError } from '../core/http/error-messages';
import { remove } from '../utils/kv';
import type { WxRequestOptionsStub, WxStub } from './setup-wx';
import { wxStub } from './setup-wx';

const BASE = 'http://localhost:8000';
const LOGIN_URL = `${BASE}/api/v1/auth/wechat`;

function respondWith(handler: (options: WxRequestOptionsStub) => void): void {
  wxStub.__requestResponder = handler;
}

function respondError(statusCode: number, code: string, requestId: string): void {
  respondWith((o) => {
    o.success({
      statusCode,
      header: { 'X-Request-Id': requestId },
      data: {
        error: { code, message: 'stub', retryable: false, details: {} },
        requestId,
      },
    });
  });
}

function seedUsableToken(): void {
  wxStub.__storage.set(
    'pathai:auth:token',
    JSON.stringify({
      accessToken: 'tok-old',
      userId: 'anon_old',
      expiresAtMs: Date.now() + 3_600_000,
    }),
  );
}

function authCalls(): WxStub['__requestLog'] {
  return wxStub.__requestLog.filter((r) => r.url === LOGIN_URL);
}

async function expectError(promise: Promise<unknown>): Promise<ApiClientError> {
  try {
    await promise;
  } catch (err) {
    return err as ApiClientError;
  }
  throw new Error('expected ApiClientError but resolved');
}

beforeEach(() => {
  wxStub.__storage.clear();
  wxStub.__loginCount = 0;
  wxStub.__requestLog.length = 0;
  wxStub.__requestResponder = null;
  // kv 模块级内存降级缓存跨测试存活，需一并清除
  remove('auth:token');
});

describe('错误信封与 requestId', () => {
  test('404 信封解析为 ApiClientError 并透出 requestId', async () => {
    respondError(404, 'PLACE_NOT_FOUND', 'req_abc');
    const { client } = createHttpStack(BASE);
    const err = await expectError(
      client.request({ method: 'GET', path: '/api/v1/none', auth: false }),
    );
    expect(err).toBeInstanceOf(ApiClientError);
    expect(err.code).toBe('PLACE_NOT_FOUND');
    expect(err.requestId).toBe('req_abc');
    expect(err.statusCode).toBe(404);
  });

  test('信封损坏时退化为 HTTP_ERROR 且保留状态码', async () => {
    respondWith((o) => o.success({ statusCode: 500, header: {}, data: 'oops' }));
    const { client } = createHttpStack(BASE);
    const err = await expectError(client.request({ method: 'GET', path: '/x', auth: false }));
    expect(err.code).toBe('HTTP_ERROR');
    expect(err.statusCode).toBe(500);
    expect(err.retryable).toBe(true);
  });

  test('错误码→本地话术映射与兜底', () => {
    expect(describeError('NETWORK_ERROR')).toBe('网络连接不可用，请检查网络');
    expect(describeError('INTERNAL_ERROR' as never)).toBe('服务暂时不可用，请稍后再试');
    expect(typeof describeError('HTTP_ERROR')).toBe('string');
  });
});

describe('401 重放', () => {
  test('作废重登并以同一 body 重放一次，新令牌生效', async () => {
    seedUsableToken();
    let navCalls = 0;
    const sentBodies: unknown[] = [];
    const authHeaders: string[] = [];
    respondWith((o) => {
      if (o.url === LOGIN_URL) {
        o.success({
          statusCode: 200,
          header: {},
          data: { accessToken: 'tok-new', tokenType: 'Bearer', expiresIn: 7200, userId: 'anon_old' },
        });
        return;
      }
      navCalls += 1;
      authHeaders.push(o.header?.Authorization ?? '');
      sentBodies.push(o.data);
      if (navCalls === 1) {
        o.success({
          statusCode: 401,
          header: {},
          data: {
            error: { code: 'TOKEN_EXPIRED', message: 'expired', retryable: false, details: {} },
            requestId: 'req_1',
          },
        });
      } else {
        o.success({ statusCode: 200, header: {}, data: { ok: true } });
      }
    });
    const { client } = createHttpStack(BASE);
    const resp = await client.request<{ ok: boolean }>({
      method: 'POST',
      path: '/api/v1/nav/sessions',
      body: { n: 1 },
    });
    expect(resp.statusCode).toBe(200);
    expect(resp.data).toEqual({ ok: true });
    expect(navCalls).toBe(2);
    expect(authHeaders[0]).toBe('Bearer tok-old');
    expect(authHeaders[1]).toBe('Bearer tok-new');
    expect(sentBodies[0]).toEqual(sentBodies[1]);
    expect(wxStub.__loginCount).toBe(1);
    expect(authCalls().length).toBe(1);
  });

  test('重放后仍 401：抛错且不发第三次', async () => {
    seedUsableToken();
    let navCalls = 0;
    respondWith((o) => {
      if (o.url === LOGIN_URL) {
        o.success({
          statusCode: 200,
          header: {},
          data: { accessToken: 'tok-n', tokenType: 'Bearer', expiresIn: 7200, userId: 'u' },
        });
        return;
      }
      navCalls += 1;
      o.success({
        statusCode: 401,
        header: {},
        data: {
          error: { code: 'UNAUTHENTICATED', message: 'bad', retryable: false, details: {} },
          requestId: 'req_x',
        },
      });
    });
    const { client } = createHttpStack(BASE);
    const err = await expectError(client.request({ method: 'GET', path: '/api/v1/nav/x' }));
    expect(err).toBeInstanceOf(ApiClientError);
    expect(err.code).toBe('UNAUTHENTICATED');
    expect(navCalls).toBe(2);
  });

  test('非认证类 401（如 AUTH_CODE_INVALID）不触发重放', async () => {
    seedUsableToken();
    respondError(401, 'AUTH_CODE_INVALID', 'req_2');
    const { client } = createHttpStack(BASE);
    const err = await expectError(client.request({ method: 'POST', path: '/x' }));
    expect(err.code).toBe('AUTH_CODE_INVALID');
    expect(authCalls().length).toBe(0);
  });
});

describe('传输层映射', () => {
  test('网络失败 → NETWORK_ERROR（retryable）', async () => {
    respondWith((o) => o.fail({ errMsg: 'request:fail ' }));
    const { client } = createHttpStack(BASE);
    const err = await expectError(client.request({ method: 'GET', path: '/x', auth: false }));
    expect(err.code).toBe('NETWORK_ERROR');
    expect(err.retryable).toBe(true);
  });

  test('超时 → REQUEST_TIMEOUT', async () => {
    respondWith((o) => o.fail({ errMsg: 'request:fail timeout' }));
    const { client } = createHttpStack(BASE);
    const err = await expectError(client.request({ method: 'GET', path: '/x', auth: false }));
    expect(err.code).toBe('REQUEST_TIMEOUT');
  });

  test('超时可按请求覆盖（导航更新 5s）', async () => {
    let seenTimeout: number | undefined;
    respondWith((o) => {
      seenTimeout = o.timeout;
      o.success({ statusCode: 200, header: {}, data: {} });
    });
    const { client } = createHttpStack(BASE);
    await client.request({
      method: 'POST',
      path: '/x',
      auth: false,
      timeoutMs: 5_000,
    });
    expect(seenTimeout).toBe(5000);
  });
});

describe('请求装配', () => {
  test('304 视为成功分支返回（ETag 短路，C3 消费）', async () => {
    respondWith((o) => o.success({ statusCode: 304, header: {}, data: '' }));
    const { client } = createHttpStack(BASE);
    const resp = await client.request({ method: 'GET', path: '/api/v1/sites/s/map', auth: false });
    expect(resp.statusCode).toBe(304);
    expect(resp.data).toBeNull();
  });

  test('auth:false 不携带 Authorization；幂等键头透传', async () => {
    let header: Record<string, string> | undefined;
    respondWith((o) => {
      header = o.header;
      o.success({ statusCode: 200, header: {}, data: {} });
    });
    const { client } = createHttpStack(BASE);
    await client.request({
      method: 'POST',
      path: '/x',
      auth: false,
      idempotencyKey: 'idem-1',
    });
    expect(header?.Authorization).toBeUndefined();
    expect(header?.['Idempotency-Key']).toBe('idem-1');
  });

  test('auth:true 携带 Bearer', async () => {
    seedUsableToken();
    let header: Record<string, string> | undefined;
    respondWith((o) => {
      header = o.header;
      o.success({ statusCode: 200, header: {}, data: {} });
    });
    const { client } = createHttpStack(BASE);
    await client.request({ method: 'GET', path: '/x' });
    expect(header?.Authorization).toBe('Bearer tok-old');
  });

  test('stack 组装：client 实例与 tokens 可用', () => {
    const stack = createHttpStack(BASE);
    expect(stack.client).toBeInstanceOf(ApiClient);
    expect(stack.tokens.currentUserId()).toBeNull();
  });
});
