import { encodeP1Bytes, p1Tag } from '../core/discovery/p1-auth';
import { activateP1Pairing, discoverP1Pairing, forgetP1Pairing, getP1Pairing, verifyP1Candidate, verifyP1ScannedAddress } from '../services/p1-identity';

const key = new Uint8Array(Array.from({ length: 32 }, (_, index) => index));
const pairing = { deviceId: 'srv-0123456789abcdef', pairId: 'pair-0123456789abcdef',
  key: encodeP1Bytes(key), endpointHint: 'http://192.168.1.28:8000' };
const raw = JSON.stringify({ kind: 'local-service-pairing', version: 1, serviceName: 'my-backend',
  deviceId: pairing.deviceId, pairId: pairing.pairId, pairingKey: pairing.key,
  endpointHint: { ipv4: '192.168.1.28', port: 8000 }, expiresAt: 4102444800 });
const bootId = 'boot-0123456789abcdef';
const nonce = '0123456789abcdef0123456789abcdef';
const storage = new Map<string, string>();
let request: jest.Mock;

beforeEach(() => {
  storage.clear();
  forgetP1Pairing();
  request = jest.fn(() => ({ abort: jest.fn() }));
  (globalThis as unknown as { wx: unknown }).wx = {
    getUserCryptoManager: () => ({ getRandomValues: ({ success }: { success: (value: { randomValues: ArrayBuffer }) => void }) =>
      success({ randomValues: new Uint8Array(16).fill(1).buffer }) }),
    getStorageSync: (key: string) => storage.get(key),
    setStorageSync: (key: string, value: string) => storage.set(key, value),
    removeStorageSync: (key: string) => storage.delete(key),
    request,
  };
});

function activationResponse(): Record<string, unknown> {
  const payload = { protocol: 'local-service-discovery', version: 1, type: 'ACTIVATED',
    serviceName: 'my-backend', deviceId: pairing.deviceId, pairId: pairing.pairId,
    nonce: '01010101010101010101010101010101', bootId };
  return { ...payload, auth: { alg: 'HS256', tag: p1Tag('ACTIVATE', payload, key) } };
}

describe('P1 微信能力适配', () => {
  test('激活仅向二维码私网地址发送配对证明，签名响应通过才持久化', async () => {
    const pending = activateP1Pairing(raw);
    await Promise.resolve();
    await Promise.resolve();
    expect(request).toHaveBeenCalledTimes(1);
    const options = request.mock.calls[0][0];
    expect(options.url).toBe('http://192.168.1.28:8000/local/v1/pair/activate');
    expect(options.data.auth.tag).toBeTruthy();
    options.success({ statusCode: 200, data: activationResponse() });
    expect(await pending).toBe(true);
    expect(getP1Pairing()?.pairId).toBe(pairing.pairId);
    expect(storage.get('pathai:discovery:p1-last-address')).toBeUndefined();
  });

  test('挑战请求无业务凭据，假身份响应不能成为已验证地址', async () => {
    const pending = verifyP1Candidate({ endpoint: pairing.endpointHint, bootId }, pairing);
    await Promise.resolve();
    await Promise.resolve();
    const options = request.mock.calls[0][0];
    expect(options.url).toContain('/local/v1/probe?pairId=');
    expect(options).not.toHaveProperty('header');
    options.success({ statusCode: 200, data: { challenge: nonce, bootId } });
    expect(await pending).toBeNull();
  });

  test('两个扫码操作并发时较早响应不得覆盖后启动的配对', async () => {
    const first = activateP1Pairing(raw);
    await Promise.resolve();
    await Promise.resolve();
    const second = activateP1Pairing(raw);
    await Promise.resolve();
    await Promise.resolve();
    expect(request).toHaveBeenCalledTimes(2);
    request.mock.calls[1][0].success({ statusCode: 200, data: activationResponse() });
    expect(await second).toBe(true);
    request.mock.calls[0][0].success({ statusCode: 200, data: activationResponse() });
    expect(await first).toBe(false);
    expect(getP1Pairing()?.pairId).toBe(pairing.pairId);
  });

  test('地址码只在同一设备身份挑战成功后缓存，失败时不缓存', async () => {
    const activated = activateP1Pairing(raw);
    await Promise.resolve();
    await Promise.resolve();
    request.mock.calls[0][0].success({ statusCode: 200, data: activationResponse() });
    expect(await activated).toBe(true);
    const address = JSON.stringify({ kind: 'local-service-address', version: 1, serviceName: 'my-backend',
      deviceId: pairing.deviceId, endpointHint: { ipv4: '10.1.2.3', port: 8000 } });
    const pending = verifyP1ScannedAddress(address);
    await Promise.resolve();
    await Promise.resolve();
    expect(request.mock.calls[1][0].url).toContain('http://10.1.2.3:8000/local/v1/probe');
    request.mock.calls[1][0].success({ statusCode: 200, data: { ready: true } });
    expect(await pending).toBe(false);
    expect(storage.has(`pathai:discovery:p1-last-address:${pairing.deviceId}:${pairing.pairId}`)).toBe(false);
  });

  test('配对持久化失败不留下新配对凭据', async () => {
    const current = (globalThis as unknown as { wx: { setStorageSync: (key: string, value: string) => void } }).wx;
    current.setStorageSync = () => { throw new Error('storage failed'); };
    const pending = activateP1Pairing(raw);
    await Promise.resolve();
    await Promise.resolve();
    request.mock.calls[0][0].success({ statusCode: 200, data: activationResponse() });
    expect(await pending).toBe(false);
    expect(getP1Pairing()).toBeNull();
  });

  test('P1 发现只接受有效 OFFER，最大集合有界', async () => {
    const socket = { onMessage: jest.fn(), onError: jest.fn(), onListening: jest.fn(), bind: jest.fn(),
      send: jest.fn(), close: jest.fn() };
    (globalThis as unknown as { wx: { createUDPSocket: () => unknown } }).wx.createUDPSocket = () => socket;
    jest.useFakeTimers();
    try {
      const pending = discoverP1Pairing(pairing);
      await Promise.resolve();
      await Promise.resolve();
      socket.onListening.mock.calls[0][0]();
      const query = JSON.parse(socket.send.mock.calls[0][0].message);
      const offer = { protocol: 'local-service-discovery', version: 1, type: 'OFFER',
        requestId: query.requestId, serviceName: 'my-backend', deviceId: pairing.deviceId,
        pairId: pairing.pairId, bootId, port: 8000, transport: 'http', apiVersion: 1, ttlSec: 30 };
      const emit = (payload: object) => socket.onMessage.mock.calls[0][0]({
        message: Uint8Array.from(JSON.stringify(payload), (character) => character.charCodeAt(0)).buffer,
        remoteInfo: { address: '10.1.2.3' },
      });
      emit({ ...offer, auth: { alg: 'HS256', tag: 'invalid' } });
      emit({ ...offer, auth: { alg: 'HS256', tag: p1Tag('OFFER', offer, key) } });
      jest.advanceTimersByTime(2501);
      expect(await pending).toEqual([{ endpoint: 'http://10.1.2.3:8000', bootId, port: 8000 }]);
      expect(socket.close).toHaveBeenCalledTimes(1);
    } finally { jest.useRealTimers(); }
  });
});
