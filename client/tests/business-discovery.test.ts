import { parseBusinessOffer } from '../core/discovery/business-protocol';
import { discoverBusinessCandidate } from '../services/business-discovery';

const requestId = '01010101010101010101010101010101';
const valid = { protocol: 'local-service-discovery', version: 1, type: 'BUSINESS_OFFER',
  serviceName: 'my-backend', requestId, port: 8000, authenticated: false };

function packet(value: unknown): ArrayBuffer {
  return Uint8Array.from(JSON.stringify(value), (character) => character.charCodeAt(0)).buffer;
}

describe('免配对业务发现', () => {
  let onMessage: ((message: { message: ArrayBuffer; remoteInfo: { address: string } }) => void) | undefined;
  let onError: (() => void) | undefined;
  let onListening: (() => void) | undefined;
  let send: jest.Mock;
  let close: jest.Mock;
  let request: jest.Mock;

  beforeEach(() => {
    jest.useFakeTimers();
    send = jest.fn();
    close = jest.fn();
    request = jest.fn(() => ({ abort: jest.fn() }));
    (globalThis as unknown as { wx: unknown }).wx = {
      getUserCryptoManager: () => ({ getRandomValues: ({ success }: { success: (r: { randomValues: ArrayBuffer }) => void }) =>
        success({ randomValues: new Uint8Array(16).fill(1).buffer }) }),
      createUDPSocket: () => ({ bind: jest.fn(), send, close,
        onMessage: (cb: typeof onMessage) => { onMessage = cb; },
        onError: (cb: typeof onError) => { onError = cb; },
        onListening: (cb: typeof onListening) => { onListening = cb; } }),
      request,
    };
  });

  afterEach(() => { jest.useRealTimers(); });

  test('拒绝旧轮次、公网、探测端口和配对 OFFER', () => {
    expect(parseBusinessOffer(valid, '192.168.1.44', requestId, 8000)).toBe('http://192.168.1.44:8000');
    expect(parseBusinessOffer(valid, '8.8.8.8', requestId, 8000)).toBeNull();
    expect(parseBusinessOffer(valid, '192.168.1.44', 'f'.repeat(32), 8000)).toBeNull();
    expect(parseBusinessOffer({ ...valid, port: 39002 }, '192.168.1.44', requestId, 8000)).toBeNull();
    expect(parseBusinessOffer({ ...valid, type: 'OFFER' }, '192.168.1.44', requestId, 8000)).toBeNull();
    expect(parseBusinessOffer({ ...valid, extra: 'unexpected' }, '192.168.1.44', requestId, 8000)).toBeNull();
  });

  test('只在监听后发送，匹配轮次再检查实际来源的业务端口', async () => {
    const pending = discoverBusinessCandidate();
    await Promise.resolve();
    expect(send).not.toHaveBeenCalled();
    onListening?.();
    expect(JSON.parse(send.mock.calls[0][0].message)).toEqual(expect.objectContaining({ type: 'BUSINESS_DISCOVER' }));
    onMessage?.({ message: packet({ ...valid, requestId: 'f'.repeat(32) }), remoteInfo: { address: '192.168.1.44' } });
    onMessage?.({ message: packet(valid), remoteInfo: { address: '192.168.1.44' } });
    expect(request).toHaveBeenCalledTimes(1);
    expect(request.mock.calls[0][0].url).toBe('http://192.168.1.44:8000/healthz');
    expect(request.mock.calls[0][0]).not.toHaveProperty('header');
    request.mock.calls[0][0].success({ statusCode: 200, data: { status: 'ok' } });
    expect(await pending).toBe('http://192.168.1.44:8000');
    expect(close).toHaveBeenCalledTimes(1);
  });

  test('错误健康响应、超时与迟到响应均不产生候选', async () => {
    const pending = discoverBusinessCandidate();
    await Promise.resolve();
    onListening?.();
    onMessage?.({ message: packet(valid), remoteInfo: { address: '192.168.1.44' } });
    request.mock.calls[0][0].success({ statusCode: 200, data: { status: 'not-ready' } });
    await Promise.resolve();
    jest.advanceTimersByTime(3001);
    expect(await pending).toBeNull();
    const retry = discoverBusinessCandidate();
    await Promise.resolve();
    jest.advanceTimersByTime(3001);
    expect(await retry).toBeNull();
    onListening?.();
    expect(send).toHaveBeenCalledTimes(1);
  });

  test('第一个候选失败后可以尝试第二个候选', async () => {
    const pending = discoverBusinessCandidate();
    await Promise.resolve();
    onListening?.();
    onMessage?.({ message: packet(valid), remoteInfo: { address: '192.168.1.44' } });
    request.mock.calls[0][0].fail();
    await Promise.resolve();
    onMessage?.({ message: packet(valid), remoteInfo: { address: '192.168.1.45' } });
    expect(request).toHaveBeenCalledTimes(2);
    request.mock.calls[1][0].success({ statusCode: 200, data: { status: 'ok' } });
    expect(await pending).toBe('http://192.168.1.45:8000');
  });

  test('异步 UDP 错误仅结束发现，不请求业务', async () => {
    const pending = discoverBusinessCandidate();
    await Promise.resolve();
    onError?.();
    expect(await pending).toBeNull();
    expect(request).not.toHaveBeenCalled();
  });
});
