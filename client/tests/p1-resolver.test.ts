import { P1Resolver } from '../core/discovery/p1-resolver';

const pairing = { deviceId: 'srv-0123456789abcdef', pairId: 'pair-0123456789abcdef',
  key: 'AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8', endpointHint: 'http://192.168.1.28:8000' };
const cached = { endpoint: pairing.endpointHint };
const found = { endpoint: 'http://10.1.2.3:8000', bootId: 'boot-0123456789abcdef' };

describe('P1 隔离解析器', () => {
  test('缓存成功时忽略先结束的发现，合并同设备任务', async () => {
    const resolver = new P1Resolver();
    const accept = jest.fn();
    const discover = jest.fn(() => Promise.resolve([found]));
    const verify = jest.fn(async (candidate) => candidate.endpoint === cached.endpoint ? 'boot-0123456789abcdef' : null);
    const first = resolver.resolve(pairing, cached, discover, verify, accept);
    const second = resolver.resolve(pairing, cached, discover, verify, accept);
    expect(first).toBe(second);
    expect(await first).toEqual({ source: 'cached', endpoint: cached.endpoint, bootId: 'boot-0123456789abcdef' });
    expect(discover).toHaveBeenCalledTimes(1);
    expect(verify).toHaveBeenCalledTimes(1);
    expect(accept).toHaveBeenCalledTimes(1);
  });

  test('缓存失败才挑战发现候选，双路失败不写缓存', async () => {
    const resolver = new P1Resolver();
    const accept = jest.fn();
    const result = await resolver.resolve(pairing, cached, () => Promise.resolve([found]),
      async (candidate) => candidate.endpoint === found.endpoint ? found.bootId : null, accept);
    expect(result).toEqual({ source: 'discovered', endpoint: found.endpoint, bootId: found.bootId });
    expect(accept).toHaveBeenCalledWith(result);
    expect(await resolver.resolve(pairing, cached, () => Promise.resolve([]), async () => null, accept)).toBeNull();
    expect(accept).toHaveBeenCalledTimes(1);
  });

  test('失效代次不能被旧请求逆转', async () => {
    const resolver = new P1Resolver();
    const accept = jest.fn();
    let finish!: (value: string) => void;
    const old = resolver.resolve(pairing, cached, async () => [found],
      () => new Promise((resolve) => { finish = resolve; }), accept);
    await Promise.resolve();
    resolver.invalidate();
    finish('boot-0123456789abcdef');
    expect(await old).toBeNull();
    expect(accept).not.toHaveBeenCalled();
  });

  test('首个已签名候选 HTTP 挑战失败则继续尝试第二候选', async () => {
    const resolver = new P1Resolver();
    const second = { endpoint: 'http://10.1.2.4:8000', bootId: found.bootId };
    const verified: string[] = [];
    const result = await resolver.resolve(pairing, cached, async () => [found, second],
      async ({ endpoint }) => {
        verified.push(endpoint);
        return endpoint === second.endpoint ? second.bootId : null;
      }, jest.fn());
    expect(result).toEqual({ source: 'discovered', endpoint: second.endpoint, bootId: second.bootId });
    expect(verified).toEqual([cached.endpoint, found.endpoint, second.endpoint]);
  });

  test('超过恢复预算后返回失败且迟到的身份结果不能写缓存', async () => {
    jest.useFakeTimers();
    try {
      const resolver = new P1Resolver();
      const accept = jest.fn();
      let finish!: (bootId: string) => void;
      const pending = resolver.resolve(pairing, cached, () => Promise.resolve([found]),
        () => new Promise((resolve) => { finish = resolve; }), accept, 100);
      await Promise.resolve();
      await Promise.resolve();
      jest.advanceTimersByTime(101);
      expect(await pending).toBeNull();
      finish(found.bootId);
      await Promise.resolve();
      expect(accept).not.toHaveBeenCalled();
    } finally {
      jest.useRealTimers();
    }
  });
});
