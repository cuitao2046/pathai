import { isP0CandidateUrl, resolveBusinessCandidate, resolveP0Candidate } from '../core/discovery/p0-candidate';
import { checkP0Candidate, getP0Candidate, saveP0Candidate } from '../services/p0-candidate';

describe('P0 候选缓存与并发预检', () => {
  const storage = new Map<string, string>();
  let request: jest.Mock;

  beforeEach(() => {
    storage.clear();
    request = jest.fn(() => ({ abort: jest.fn() }));
    (globalThis as unknown as { wx: unknown }).wx = {
      getStorageSync: (key: string) => storage.get(key),
      setStorageSync: (key: string, value: string) => storage.set(key, value),
      request,
    };
  });

  test('仅首次从 hardcode 地址缓存候选，不覆盖已有私网地址', () => {
    expect(getP0Candidate()).toBe('http://192.168.1.28:8000');
    expect(storage.get('pathai:discovery:p0-candidate-url')).toBe('http://192.168.1.28:8000');
    storage.set('pathai:discovery:p0-candidate-url', 'http://10.1.2.3:8000');
    expect(getP0Candidate()).toBe('http://10.1.2.3:8000');
  });

  test('非法候选不可用于网络请求', async () => {
    for (const value of ['https://evil.example:443', 'http://127.0.0.1:8000', 'http://192.168.1.2:8000/path', 'http://10.1.2.3:0']) {
      expect(isP0CandidateUrl(value)).toBe(false);
      expect(await checkP0Candidate(value)).toBe(false);
    }
    expect(await checkP0Candidate('http://10.1.2.3:39002')).toBe(false);
    expect(request).not.toHaveBeenCalled();
  });

  test('缓存预检只发无凭据 healthz 请求', async () => {
    const pending = checkP0Candidate('http://10.1.2.3:8000');
    expect(request).toHaveBeenCalledWith(expect.objectContaining({ url: 'http://10.1.2.3:8000/healthz', method: 'GET' }));
    expect(request.mock.calls[0][0]).not.toHaveProperty('header');
    request.mock.calls[0][0].success({ statusCode: 200, data: { status: 'ok' } });
    expect(await pending).toBe(true);
  });

  test('仅有效业务端口地址可作为未认证候选缓存', () => {
    expect(saveP0Candidate('http://192.168.1.44:39002')).toBe(false);
    expect(saveP0Candidate('http://192.168.1.44:8000')).toBe(true);
    expect(getP0Candidate()).toBe('http://192.168.1.44:8000');
  });

  test('损坏的候选缓存不发请求，仅重新写入 hardcode 初始候选', () => {
    storage.set('pathai:discovery:p0-candidate-url', 'http://evil.example:8000');
    expect(getP0Candidate()).toBe('http://192.168.1.28:8000');
    expect(storage.get('pathai:discovery:p0-candidate-url')).toBe('http://192.168.1.28:8000');
    expect(request).not.toHaveBeenCalled();
  });

  test('缓存预检超时会结束，之后迟到成功也不能逆转结果', async () => {
    jest.useFakeTimers();
    const pending = checkP0Candidate('http://10.1.2.3:8000');
    jest.advanceTimersByTime(1601);
    expect(await pending).toBe(false);
    request.mock.calls[0][0].success({ statusCode: 200, data: { status: 'ok' } });
    jest.useRealTimers();
  });

  test('发现先失败、缓存后成功时只采用缓存', async () => {
    let finish!: (value: boolean) => void;
    const cached = new Promise<boolean>((resolve) => { finish = resolve; });
    const result = resolveP0Candidate(() => cached, () => Promise.resolve(false));
    await Promise.resolve();
    finish(true);
    expect(await result).toBe('cached-reachable');
  });

  test('缓存失败才等待发现；两路失败有界返回失败', async () => {
    let finish!: (value: boolean) => void;
    const discovery = new Promise<boolean>((resolve) => { finish = resolve; });
    const result = resolveP0Candidate(() => Promise.resolve(false), () => discovery);
    await Promise.resolve();
    finish(true);
    expect(await result).toBe('discovery-reachable');
    expect(await resolveP0Candidate(() => Promise.reject(new Error()), () => Promise.reject(new Error()))).toBe('unavailable');
  });

  test('发现先完成也不抢占可达缓存；缓存失败才写入新候选', async () => {
    const persist = jest.fn();
    expect(await resolveBusinessCandidate(() => Promise.resolve(true),
      () => Promise.resolve('http://192.168.1.44:8000'), () => true, persist)).toBe('cached-reachable');
    expect(persist).not.toHaveBeenCalled();
    expect(await resolveBusinessCandidate(() => Promise.resolve(false),
      () => Promise.resolve('http://192.168.1.44:8000'), () => true, persist)).toBe('discovery-reachable');
    expect(persist).toHaveBeenCalledWith('http://192.168.1.44:8000');
  });

  test('旧轮次和双路失败均不写入候选', async () => {
    const persist = jest.fn();
    expect(await resolveBusinessCandidate(() => Promise.resolve(false),
      () => Promise.resolve('http://192.168.1.44:8000'), () => false, persist)).toBe('unavailable');
    expect(await resolveBusinessCandidate(() => Promise.resolve(false),
      () => Promise.resolve(null), () => true, persist)).toBe('unavailable');
    expect(persist).not.toHaveBeenCalled();
  });
});
