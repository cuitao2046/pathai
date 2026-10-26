/**
 * utils/wss-url.ts：讯飞签名 URL provider 的缓存/过期/并发去重/失败重试语义。
 */
import type { ApiClient } from '../core/http/api-client';
import { createWssUrlProvider } from '../utils/wss-url';

type Handler = (opts: { method: string; path: string }) => Promise<unknown>;

function fakeClient(handler: Handler): { client: ApiClient; request: jest.Mock } {
  const request = jest.fn(handler);
  const client = { request } as unknown as ApiClient;
  return { client, request };
}

function ok(url: string, expiresIn = 240) {
  return { statusCode: 200, data: { url, expiresIn }, requestId: null, etag: null };
}

describe('createWssUrlProvider', () => {
  it('缓存命中：TTL 内重复取用不发第二次请求', async () => {
    let t = 0;
    const { client, request } = fakeClient(() => Promise.resolve(ok('wss://host/v1?sig=1')));
    const getUrl = createWssUrlProvider(client, '/api/v1/voice/asr-url', 240_000, () => t);

    await expect(getUrl()).resolves.toBe('wss://host/v1?sig=1');
    t = 239_000;
    await expect(getUrl()).resolves.toBe('wss://host/v1?sig=1');
    expect(request).toHaveBeenCalledTimes(1);
  });

  it('过期后重新签发并采用新 URL', async () => {
    let t = 0;
    let n = 0;
    const { client, request } = fakeClient(() => Promise.resolve(ok(`wss://host?sig=${(n += 1)}`)));
    const getUrl = createWssUrlProvider(client, '/p', 240_000, () => t);

    await expect(getUrl()).resolves.toBe('wss://host?sig=1');
    t = 240_000;
    await expect(getUrl()).resolves.toBe('wss://host?sig=2');
    expect(request).toHaveBeenCalledTimes(2);
  });

  it('采用服务端更短的 expiresIn', async () => {
    let t = 0;
    const { client, request } = fakeClient(() => Promise.resolve(ok('wss://host?sig=1', 10)));
    const getUrl = createWssUrlProvider(client, '/p', 240_000, () => t);

    await getUrl();
    t = 9_000;
    await getUrl();
    expect(request).toHaveBeenCalledTimes(1);
    t = 10_000;
    await getUrl();
    expect(request).toHaveBeenCalledTimes(2);
  });

  it('并发调用共享同一次请求', async () => {
    let resolveReq: (v: unknown) => void = () => undefined;
    const { client, request } = fakeClient(
      () => new Promise((res) => (resolveReq = res)),
    );
    const getUrl = createWssUrlProvider(client, '/p');

    const p1 = getUrl();
    const p2 = getUrl();
    resolveReq(ok('wss://host?sig=1'));
    await expect(p1).resolves.toBe('wss://host?sig=1');
    await expect(p2).resolves.toBe('wss://host?sig=1');
    expect(request).toHaveBeenCalledTimes(1);
  });

  it('请求失败向上传播，且下一次可重试', async () => {
    let fail = true;
    const { client } = fakeClient(() =>
      fail ? Promise.reject(new Error('network')) : Promise.resolve(ok('wss://host?sig=2')),
    );
    const getUrl = createWssUrlProvider(client, '/p');

    await expect(getUrl()).rejects.toThrow('network');
    fail = false;
    await expect(getUrl()).resolves.toBe('wss://host?sig=2');
  });

  it('响应缺少 url 字段时报错', async () => {
    const { client } = fakeClient(() =>
      Promise.resolve({ statusCode: 200, data: null, requestId: null, etag: null }),
    );
    const getUrl = createWssUrlProvider(client, '/p');
    await expect(getUrl()).rejects.toThrow('缺少 url');
  });
});
