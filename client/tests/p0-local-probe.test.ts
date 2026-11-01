import { runP0LocalProbe } from '../services/p0-local-probe';
import { P0_DISCOVERY_PROTOCOL, P0_DISCOVERY_SERVICE } from '../core/discovery/p0-protocol';

const requestId = '01010101010101010101010101010101';

function offer(id: string): ArrayBuffer {
  const value = JSON.stringify({
    protocol: P0_DISCOVERY_PROTOCOL,
    version: 1,
    type: 'P0_OFFER',
    serviceName: P0_DISCOVERY_SERVICE,
    requestId: id,
    port: 39002,
    authenticated: false,
  });
  return Uint8Array.from(value, (character) => character.charCodeAt(0)).buffer;
}

describe('P0 探测适配器', () => {
  let onMessage: ((message: { message: ArrayBuffer; remoteInfo: { address: string } }) => void) | undefined;
  let onError: ((error: { errMsg: string }) => void) | undefined;
  let onListening: (() => void) | undefined;
  let send: jest.Mock;
  let request: jest.Mock;
  let close: jest.Mock;

  beforeEach(() => {
    jest.useFakeTimers();
    onMessage = undefined;
    onError = undefined;
    onListening = undefined;
    send = jest.fn();
    close = jest.fn();
    request = jest.fn(() => ({ abort: jest.fn() }));
    (globalThis as unknown as { wx: unknown }).wx = {
      getUserCryptoManager: () => ({
        getRandomValues: ({ success }: { success: (result: { randomValues: ArrayBuffer }) => void }) => {
          success({ randomValues: new Uint8Array(16).fill(1).buffer });
        },
      }),
      createUDPSocket: () => ({
        bind: jest.fn(),
        send,
        close,
        onMessage: (listener: typeof onMessage) => { onMessage = listener; },
        onError: (listener: typeof onError) => { onError = listener; },
        onListening: (listener: typeof onListening) => { onListening = listener; },
      }),
      request,
    };
  });

  afterEach(() => {
    jest.useRealTimers();
  });

  test('匹配轮次后才请求探测 HTTP，成功也不写业务地址', async () => {
    const result = runP0LocalProbe();
    await Promise.resolve();
    expect(send).not.toHaveBeenCalled();
    onListening?.();
    expect(send).toHaveBeenCalledWith(expect.objectContaining({ address: '255.255.255.255', setBroadcast: true }));
    onMessage?.({ message: offer('f'.repeat(32)), remoteInfo: { address: '192.168.1.12' } });
    expect(request).not.toHaveBeenCalled();
    onMessage?.({ message: offer(requestId), remoteInfo: { address: '192.168.1.12' } });
    expect(request).toHaveBeenCalledWith(expect.objectContaining({ url: 'http://192.168.1.12:39002/local/p0/probe' }));
    request.mock.calls[0][0].success({ statusCode: 200, data: { protocol: P0_DISCOVERY_PROTOCOL, ready: true } });
    expect((await result).result).toBe('udp-and-http');
    expect(close).toHaveBeenCalledTimes(1);
  });

  test('随机数未回调会有界失败，不发送 UDP', async () => {
    (globalThis as unknown as { wx: { getUserCryptoManager: () => unknown } }).wx.getUserCryptoManager =
      () => ({ getRandomValues: jest.fn() });
    const result = runP0LocalProbe();
    jest.advanceTimersByTime(1501);
    expect((await result).result).toBe('random-timeout');
    expect(send).not.toHaveBeenCalled();
  });

  test('安全随机数接口不存在时不发送 UDP', async () => {
    (globalThis as unknown as { wx: { getUserCryptoManager?: unknown } }).wx.getUserCryptoManager = undefined;
    expect((await runP0LocalProbe()).result).toBe('random-unavailable');
    expect(send).not.toHaveBeenCalled();
  });

  test('安全随机数调用失败时不发送 UDP', async () => {
    (globalThis as unknown as { wx: { getUserCryptoManager: () => unknown } }).wx.getUserCryptoManager =
      () => ({ getRandomValues: ({ fail }: { fail: () => void }) => fail() });
    expect((await runP0LocalProbe()).result).toBe('random-failed');
    expect(send).not.toHaveBeenCalled();
  });

  test('UDP 接口缺失时不请求安全随机数', async () => {
    const adapter = (globalThis as unknown as { wx: { createUDPSocket?: unknown; getUserCryptoManager: jest.Mock } }).wx;
    adapter.createUDPSocket = undefined;
    adapter.getUserCryptoManager = jest.fn();
    expect((await runP0LocalProbe()).result).toBe('udp-unavailable');
    expect(adapter.getUserCryptoManager).not.toHaveBeenCalled();
  });

  test('UDP socket 建立失败时区分失败阶段', async () => {
    (globalThis as unknown as { wx: { createUDPSocket: () => never } }).wx.createUDPSocket =
      () => { throw new Error('socket unavailable'); };
    expect((await runP0LocalProbe()).result).toBe('udp-create-failed');
  });

  test('监听后发送失败时关闭 socket', async () => {
    send.mockImplementation(() => { throw new Error('send failed'); });
    const result = runP0LocalProbe();
    await Promise.resolve();
    expect(send).not.toHaveBeenCalled();
    onListening?.();
    expect(await result).toEqual({ result: 'udp-send-failed', phase: 'listening', errorCode: null });
    expect(close).toHaveBeenCalledTimes(1);
  });

  test('重复监听只广播一次', async () => {
    const result = runP0LocalProbe();
    await Promise.resolve();
    onListening?.();
    onListening?.();
    expect(send).toHaveBeenCalledTimes(1);
    jest.advanceTimersByTime(3001);
    expect((await result).result).toBe('timeout');
  });

  test('监听未就绪时超时关闭 socket，迟到事件不得发送', async () => {
    const result = runP0LocalProbe();
    await Promise.resolve();
    expect(send).not.toHaveBeenCalled();
    jest.advanceTimersByTime(3001);
    expect(await result).toEqual({ result: 'timeout', phase: 'binding', errorCode: null });
    expect(close).toHaveBeenCalledTimes(1);
    onListening?.();
    expect(send).not.toHaveBeenCalled();
  });

  test('HTTP 无回调时有界结束，不被第二个应答重复发起', async () => {
    const result = runP0LocalProbe();
    await Promise.resolve();
    onListening?.();
    onMessage?.({ message: offer(requestId), remoteInfo: { address: '192.168.1.12' } });
    onMessage?.({ message: offer(requestId), remoteInfo: { address: '192.168.1.13' } });
    expect(request).toHaveBeenCalledTimes(1);
    jest.advanceTimersByTime(1601);
    expect((await result).result).toBe('udp-only');
    expect(close).toHaveBeenCalledTimes(1);
  });

  test('UDP 错误有界失败', async () => {
    const result = runP0LocalProbe();
    await Promise.resolve();
    onError?.({ errMsg: 'UDPSocket:fail errCode: -1024' });
    expect(await result).toEqual({ result: 'udp-error', phase: 'binding', errorCode: '-1024' });
    expect(request).not.toHaveBeenCalled();
    onListening?.();
    expect(send).not.toHaveBeenCalled();
  });

  test('监听后异步错误只显示数字码，不回显任意错误内容', async () => {
    const result = runP0LocalProbe();
    await Promise.resolve();
    onListening?.();
    expect(send).toHaveBeenCalledTimes(1);
    onError?.({ errMsg: 'UDPSocket:fail token=secret errorCode=42' });
    expect(await result).toEqual({ result: 'udp-error', phase: 'listening', errorCode: '42' });
    expect(request).not.toHaveBeenCalled();
  });
});
