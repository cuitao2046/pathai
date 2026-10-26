/**
 * utils/wss-url.ts：讯飞签名凭据 provider 的缓存/过期/并发去重/失败重试语义。
 */
import type { ApiClient } from '../core/http/api-client';
import { createWssUrlProvider, type VoiceCredential } from '../utils/wss-url';

type Handler = (opts: { method: string; path: string }) => Promise<unknown>;

function fakeClient(handler: Handler): { client: ApiClient; request: jest.Mock } {
  const request = jest.fn(handler);
  const client = { request } as unknown as ApiClient;
  return { client, request };
}

function ok(url: string, expiresIn = 240, appId = 'app-1') {
  return { statusCode: 200, data: { url, appId, expiresIn }, requestId: null, etag: null };
}

function cred(url: string, appId = 'app-1'): VoiceCredential {
  return { url, appId };
}

describe('createWssUrlProvider', () => {
  it('缓存命中：TTL 内重复取用不发第二次请求', async () => {
    let t = 0;
    const { client, request } = fakeClient(() => Promise.resolve(ok('wss://host/v1?sig=1')));
    const getUrl = createWssUrlProvider(client, '/api/v1/voice/asr-url', 240_000, () => t);

    await expect(getUrl()).resolves.toEqual(cred('wss://host/v1?sig=1'));
    t = 239_000;
    await expect(getUrl()).resolves.toEqual(cred('wss://host/v1?sig=1'));
    expect(request).toHaveBeenCalledTimes(1);
  });

  it('过期后重新签发并采用新 URL', async () => {
    let t = 0;
    let n = 0;
    const { client, request } = fakeClient(() => Promise.resolve(ok(`wss://host?sig=${(n += 1)}`)));
    const getUrl = createWssUrlProvider(client, '/p', 240_000, () => t);

    await expect(getUrl()).resolves.toEqual(cred('wss://host?sig=1'));
    t = 240_000;
    await expect(getUrl()).resolves.toEqual(cred('wss://host?sig=2'));
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
    await expect(p1).resolves.toEqual(cred('wss://host?sig=1'));
    await expect(p2).resolves.toEqual(cred('wss://host?sig=1'));
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
    await expect(getUrl()).resolves.toEqual(cred('wss://host?sig=2'));
  });

  it('响应缺少 url 或 appId 字段时报错', async () => {
    const noUrl = fakeClient(() =>
      Promise.resolve({ statusCode: 200, data: null, requestId: null, etag: null }),
    );
    await expect(createWssUrlProvider(noUrl.client, '/p')()).rejects.toThrow('缺少 url');

    const noAppId = fakeClient(() =>
      Promise.resolve(ok('wss://host?sig=1', 240, '')),
    );
    await expect(createWssUrlProvider(noAppId.client, '/p')()).rejects.toThrow('缺少 appId');
  });
});
