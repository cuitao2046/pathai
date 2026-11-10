import {
  RUNTIME_PARAMS_KEY,
  cachedRuntimeParams,
  clearRuntimeParams,
  refreshRuntimeParams,
} from '../core/config/runtime-config';
import type { ApiClient } from '../core/http/api-client';

function clientReturning(data: unknown): ApiClient {
  return {
    request: jest.fn(async () => ({
      statusCode: 200,
      data,
      etag: null,
      requestId: null,
      header: {},
    })),
  } as unknown as ApiClient;
}

const response = {
  siteId: 'demo-site',
  paramsVersion: 'loc-20261109-abcd',
  gridVersion: 'g-20261109-abcdef',
  source: 'db',
  rssiAt1mDbm: -68.4,
  pathLossN: 2.31,
  residualReliableM: 3.5,
  residualDegradedM: 6,
  ttlS: 2,
};

beforeEach(() => {
  clearRuntimeParams();
});

describe('runtime-config', () => {
  it('成功后缓存并在有效期内不再请求', async () => {
    const client = clientReturning(response);
    const first = await refreshRuntimeParams(client);
    expect(first?.paramsVersion).toBe('loc-20261109-abcd');
    expect(cachedRuntimeParams()?.rssiAt1mDbm).toBe(-68.4);

    const second = await refreshRuntimeParams(client);
    expect(second?.paramsVersion).toBe('loc-20261109-abcd');
    expect((client.request as jest.Mock).mock.calls).toHaveLength(1);
  });

  it('force 跳过有效期，重新请求', async () => {
    const client = clientReturning(response);
    await refreshRuntimeParams(client);
    await refreshRuntimeParams(client, { force: true });
    expect((client.request as jest.Mock).mock.calls).toHaveLength(2);
  });

  it('请求失败回退旧缓存', async () => {
    writeCache();
    const client = {
      request: jest.fn(async () => {
        throw new Error('offline');
      }),
    } as unknown as ApiClient;
    const params = await refreshRuntimeParams(client, { force: true });
    expect(params?.paramsVersion).toBe('cached-version');
  });

  it('无缓存且失败返回 null', async () => {
    const client = {
      request: jest.fn(async () => {
        throw new Error('offline');
      }),
    } as unknown as ApiClient;
    expect(await refreshRuntimeParams(client)).toBeNull();
  });

  it('缓存键带 pathai: 前缀', async () => {
    await refreshRuntimeParams(clientReturning(response));
    expect(wx.getStorageSync(`pathai:${RUNTIME_PARAMS_KEY}`)).not.toBe('');
  });
});

function writeCache(): void {
  wx.setStorageSync(
    `pathai:${RUNTIME_PARAMS_KEY}`,
    JSON.stringify({
      siteId: 'demo-site',
      paramsVersion: 'cached-version',
      gridVersion: 'cached-grid',
      source: 'db',
      rssiAt1mDbm: -69,
      pathLossN: 2.2,
      residualReliableM: 3.5,
      residualDegradedM: 6,
      ttlS: 2,
      fetchedAt: Date.now(),
    }),
  );
}
