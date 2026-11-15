/**
 * ApiClient：错误信封→ApiClientError、requestId 透出、304 成功分支、
 * 网络失败/超时映射、幂等键头透传。
 *
 * 2026-11-11 局域网阶段：小程序接口免鉴权，客户端不再注入 Authorization。
 */
import { ApiClient, createHttpStack } from '../core/http/api-client';
import { ApiClientError } from '../core/http/errors';
import { describeError } from '../core/http/error-messages';
import type { WxRequestOptionsStub } from './setup-wx';
import { wxStub } from './setup-wx';

const BASE = 'http://localhost:8000';

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
  wxStub.__requestLog.length = 0;
  wxStub.__requestResponder = null;
});

describe('错误信封与 requestId', () => {
  test('404 信封解析为 ApiClientError 并透出 requestId', async () => {
    respondError(404, 'PLACE_NOT_FOUND', 'req_abc');
    const { client } = createHttpStack(BASE);
    const err = await expectError(client.request({ method: 'GET', path: '/api/v1/none' }));
    expect(err).toBeInstanceOf(ApiClientError);
    expect(err.code).toBe('PLACE_NOT_FOUND');
    expect(err.requestId).toBe('req_abc');
    expect(err.statusCode).toBe(404);
  });

  test('信封损坏时退化为 HTTP_ERROR 且保留状态码', async () => {
    respondWith((o) => o.success({ statusCode: 500, header: {}, data: 'oops' }));
    const { client } = createHttpStack(BASE);
    const err = await expectError(client.request({ method: 'GET', path: '/x' }));
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

describe('传输层映射', () => {
  test('网络失败 → NETWORK_ERROR（retryable）', async () => {
    respondWith((o) => o.fail({ errMsg: 'request:fail ' }));
    const { client } = createHttpStack(BASE);
    const err = await expectError(client.request({ method: 'GET', path: '/x' }));
    expect(err.code).toBe('NETWORK_ERROR');
    expect(err.retryable).toBe(true);
  });

  test('超时 → REQUEST_TIMEOUT', async () => {
    respondWith((o) => o.fail({ errMsg: 'request:fail timeout' }));
    const { client } = createHttpStack(BASE);
    const err = await expectError(client.request({ method: 'GET', path: '/x' }));
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
      timeoutMs: 5_000,
    });
    expect(seenTimeout).toBe(5000);
  });
});

describe('请求装配', () => {
  test('动态地址来源：每次请求按当前解析结果构造 URL', async () => {
    let base = 'http://10.1.2.3:8000';
    respondWith((o) => o.success({ statusCode: 200, header: {}, data: {} }));
    const { client } = createHttpStack(() => base);
    await client.request({ method: 'GET', path: '/api/v1/ping' });
    const first = wxStub.__requestLog[wxStub.__requestLog.length - 1];
    expect(first?.url).toBe('http://10.1.2.3:8000/api/v1/ping');

    base = 'http://10.9.9.9:8000';
    await client.request({ method: 'GET', path: '/api/v1/ping' });
    const second = wxStub.__requestLog[wxStub.__requestLog.length - 1];
    expect(second?.url).toBe('http://10.9.9.9:8000/api/v1/ping');
  });

  test('304 视为成功分支返回（ETag 短路，C3 消费）', async () => {
    respondWith((o) => o.success({ statusCode: 304, header: {}, data: '' }));
    const { client } = createHttpStack(BASE);
    const resp = await client.request({ method: 'GET', path: '/api/v1/sites/s/map' });
    expect(resp.statusCode).toBe(304);
    expect(resp.data).toBeNull();
  });

  test('不携带 Authorization；幂等键头透传', async () => {
    let header: Record<string, string> | undefined;
    respondWith((o) => {
      header = o.header;
      o.success({ statusCode: 200, header: {}, data: {} });
    });
    const { client } = createHttpStack(BASE);
    await client.request({
      method: 'POST',
      path: '/x',
      idempotencyKey: 'idem-1',
    });
    expect(header?.Authorization).toBeUndefined();
    expect(header?.['Idempotency-Key']).toBe('idem-1');
  });

  test('stack 组装：client 可用', () => {
    const stack = createHttpStack(BASE);
    expect(stack.client).toBeInstanceOf(ApiClient);
  });
});
