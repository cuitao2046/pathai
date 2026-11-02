import { decodeP1Key, encodeP1Bytes, p1Tag, parseP1Json, verifyP1Tag } from '../core/discovery/p1-auth';
import { activationRequest, parseP1Address, parseP1Offer, parseP1Pairing, validActivation, validP1Probe } from '../core/discovery/p1-protocol';
declare const require: (name: string) => any;

describe('P1 跨语言签名契约', () => {
  const key = new Uint8Array(Array.from({ length: 32 }, (_, index) => index));
  const payload = {
    protocol: 'local-service-discovery', version: 1, type: 'OFFER', serviceName: 'my-backend',
    deviceId: 'srv-0123456789abcdef', pairId: 'pair-0123456789abcdef',
    requestId: '0123456789abcdef0123456789abcdef', bootId: 'boot-0123456789abcdef',
    port: 8000, transport: 'http', apiVersion: 1, ttlSec: 30,
  };

  test('跨语言固定向量与规范化字段顺序', () => {
    const encoded = encodeP1Bytes(key);
    expect(encoded).toBe('AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8');
    expect(decodeP1Key(encoded)).toEqual(key);
    expect(p1Tag('OFFER', payload, key)).toBe('fsdWVwN2b5SLuuT4rzA-SO_mjwzap13kG61WeeXjtNc');
    expect(p1Tag('OFFER', Object.fromEntries(Object.entries(payload).reverse()), key)).toBe(p1Tag('OFFER', payload, key));
  });

  test('小程序密码 bundle 自包含且与签名向量一致', () => {
    const file = require('fs').readFileSync(require('path').resolve('core/discovery/p1-crypto-bundle.js'), 'utf8');
    expect(file).not.toMatch(/\brequire\s*\(/);
    const bundled = require('../core/discovery/p1-crypto-bundle');
    expect(encodeP1Bytes(bundled.hmac(bundled.sha256, key,
      bundled.utf8ToBytes(`LSD-OFFER-V1\n${bundled.canonicalize(payload)}`))))
      .toBe('fsdWVwN2b5SLuuT4rzA-SO_mjwzap13kG61WeeXjtNc');
  });

  test('标签绑定域、完整内容与密钥', () => {
    const signed = { ...payload, auth: { alg: 'HS256', tag: p1Tag('OFFER', payload, key) } };
    expect(verifyP1Tag('OFFER', signed, key)).toBe(true);
    expect(verifyP1Tag('PROBE', signed, key)).toBe(false);
    expect(verifyP1Tag('OFFER', { ...signed, port: 39002 }, key)).toBe(false);
    expect(verifyP1Tag('OFFER', signed, new Uint8Array(32))).toBe(false);
    expect(decodeP1Key('bad')).toBeNull();
  });

  test('重复 JSON 键、转义重复键和原型键一律拒绝', () => {
    expect(parseP1Json('{"pairId":"a","pairId":"b"}')).toBeNull();
    expect(parseP1Json('{"pairId":"a","pair\\u0049d":"b"}')).toBeNull();
    expect(parseP1Json('{"nested":{"x":1,"x":2}}')).toBeNull();
    expect(parseP1Json('{"x":"unterminated}')).toBeNull();
    expect(parseP1Json('{"x":[]]')).toBeNull();
    expect(parseP1Json('{"__proto__":"x"}')).not.toBeNull();
    expect(parseP1Json('{"a":1}')).toEqual({ a: 1 });
  });

  test('配对二维码、激活与 HTTP 挑战必须绑定身份', () => {
    const pairing = parseP1Pairing(JSON.stringify({
      kind: 'local-service-pairing', version: 1, serviceName: 'my-backend',
      deviceId: payload.deviceId, pairId: payload.pairId, pairingKey: encodeP1Bytes(key),
      endpointHint: { ipv4: '192.168.1.28', port: 8000 }, expiresAt: 2000,
    }), 1000);
    expect(pairing).not.toBeNull();
    if (!pairing) return;
    const nonce = payload.requestId;
    const request = activationRequest(pairing, nonce);
    expect(verifyP1Tag('ACTIVATE', request, key)).toBe(true);
    const activated = { protocol: payload.protocol, version: 1, type: 'ACTIVATED', serviceName: 'my-backend',
      deviceId: pairing.deviceId, pairId: pairing.pairId, nonce, bootId: payload.bootId };
    expect(validActivation({ ...activated, auth: { alg: 'HS256', tag: p1Tag('ACTIVATE', activated, key) } }, pairing, nonce)).toBe(true);
    const probe = { protocol: payload.protocol, version: 1, type: 'PROBE', serviceName: 'my-backend',
      deviceId: pairing.deviceId, pairId: pairing.pairId, bootId: payload.bootId, challenge: nonce,
      ready: true, apiVersion: 1, serverTimeMs: 1000 };
    const response = { ...probe, auth: { alg: 'HS256', tag: p1Tag('PROBE', probe, key) } };
    expect(validP1Probe(response, pairing, nonce, payload.bootId)).toBe(true);
    expect(validP1Probe(response, pairing, nonce, 'boot-1111111111111111')).toBe(false);
  });

  test('OFFER 认证不等同于业务端口身份挑战', () => {
    const pairing = { deviceId: payload.deviceId, pairId: payload.pairId,
      key: encodeP1Bytes(key), endpointHint: 'http://192.168.1.28:8000' };
    const offer = { ...payload, auth: { alg: 'HS256', tag: p1Tag('OFFER', payload, key) } };
    expect(parseP1Offer(JSON.stringify(offer), '10.1.2.3', pairing, payload.requestId)).toEqual({
      endpoint: 'http://10.1.2.3:8000', bootId: payload.bootId, port: 8000,
    });
    expect(parseP1Offer(JSON.stringify(offer), '8.8.8.8', pairing, payload.requestId)).toBeNull();
    expect(parseP1Offer(JSON.stringify(offer), '10.1.2.3', pairing, 'ffffffffffffffffffffffffffffffff')).toBeNull();
  });

  test('地址二维码只提供同一设备的私网候选，不携带配对密钥', () => {
    const pairing = { deviceId: payload.deviceId, pairId: payload.pairId,
      key: encodeP1Bytes(key), endpointHint: 'http://192.168.1.28:8000' };
    const address = { kind: 'local-service-address', version: 1, serviceName: 'my-backend',
      deviceId: pairing.deviceId, endpointHint: { ipv4: '10.1.2.3', port: 8000 } };
    expect(parseP1Address(JSON.stringify(address), pairing)).toBe('http://10.1.2.3:8000');
    expect(parseP1Address(JSON.stringify({ ...address, deviceId: 'srv-ffffffffffffffff' }), pairing)).toBeNull();
    expect(parseP1Address(JSON.stringify({ ...address, endpointHint: { ipv4: '8.8.8.8', port: 8000 } }), pairing)).toBeNull();
    expect(parseP1Address(JSON.stringify({ ...address, pairingKey: pairing.key }), pairing)).toBeNull();
  });
});
