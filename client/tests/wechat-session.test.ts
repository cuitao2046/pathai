import { createHttpStack } from '../core/http/api-client';
import { remove } from '../utils/kv';
import { wxStub } from './setup-wx';

const BASE = 'http://localhost:8000';
const USER = 'anon_0123456789abcdef';
beforeEach(() => {
  remove(`auth:wechat:${BASE}`); remove('auth:wechat:http://localhost:8001');
  wxStub.__storage.clear(); wxStub.__requestLog.length = 0; wxStub.__loginCount = 0;
});
function responder(expired = false) {
  let logins = 0;
  wxStub.__requestResponder = o => {
    if (o.url.endsWith('/auth/wechat')) {
      logins++;
      o.success({ statusCode: 200, data: { accessToken: `token-${logins}`, userId: USER, tokenType: 'Bearer', expiresIn: 7200 } });
    } else if (expired && o.header?.Authorization === 'Bearer token-1') {
      o.success({ statusCode: 401, data: { error: { code: 'TOKEN_EXPIRED', message: 'expired', retryable: false, details: {} }, requestId: 'r' } });
    } else o.success({ statusCode: 200, data: { ok: true } });
  };
}
test('first request silently logs in; subsequent request reuses the cached identity', async () => {
  responder();
  const { client } = createHttpStack(BASE, { wechatLogin: true });
  await client.request({ method: 'GET', path: '/api/v1/sites/s/map' });
  await client.request({ method: 'GET', path: '/api/v1/voice/asr-url' });
  expect(wxStub.__loginCount).toBe(1);
  expect(wxStub.__requestLog.slice(1).every(o => o.header?.Authorization === 'Bearer token-1')).toBe(true);
});
test('concurrent expired requests share a refresh and retain POST body/idempotency key', async () => {
  responder(true);
  const { client } = createHttpStack(BASE, { wechatLogin: true });
  await Promise.all([1, 2].map(() => client.request({ method: 'POST', path: '/api/v1/nav/sessions', body: { value: 1 }, idempotencyKey: 'same' })));
  expect(wxStub.__loginCount).toBe(2);
  for (const req of wxStub.__requestLog.filter(o => o.url.endsWith('/nav/sessions'))) {
    expect(req.data).toEqual({ value: 1 });
    expect(req.header?.['Idempotency-Key']).toBe('same');
  }
});
test('foreign URLs are rejected before login without leaking a token', async () => {
  responder();
  const { client } = createHttpStack(BASE, { wechatLogin: true });
  await expect(client.request({ method: 'GET', url: 'https://foreign.test/map' })).rejects.toThrow();
  expect(wxStub.__requestLog).toHaveLength(0);
});
test('caller cannot override authorization header', async () => {
  responder();
  const { client } = createHttpStack(BASE, { wechatLogin: true });
  await expect(client.request({ method: 'GET', path: '/', headers: { authorization: 'other' } })).rejects.toThrow();
});
test('403 is not retried or turned into another login', async () => {
  responder();
  const { client } = createHttpStack(BASE, { wechatLogin: true });
  await client.request({ method: 'GET', path: '/' });
  wxStub.__requestResponder = o => o.success({ statusCode: 403, data: { error: { code: 'FORBIDDEN', message: 'no', details: {} } } });
  await expect(client.request({ method: 'POST', path: '/', body: {} })).rejects.toMatchObject({ statusCode: 403 });
  expect(wxStub.__loginCount).toBe(1);
});
test('malformed login is rejected before business request', async () => {
  wxStub.__requestResponder = o => o.success({ statusCode: 200, data: {} });
  const { client } = createHttpStack(BASE, { wechatLogin: true });
  await expect(client.request({ method: 'GET', path: '/' })).rejects.toThrow();
  expect(wxStub.__requestLog).toHaveLength(1);
});
test('credentials are isolated when the resolved backend changes', async () => {
  responder();
  let base = BASE;
  const { client } = createHttpStack(() => base, { wechatLogin: true });
  await client.request({ method: 'GET', path: '/' });
  base = 'http://localhost:8001';
  await client.request({ method: 'GET', path: '/' });
  expect(wxStub.__loginCount).toBe(2);
});
test('repeated 401 stops after one replay', async () => {
  responder();
  const { client } = createHttpStack(BASE, { wechatLogin: true });
  await client.request({ method: 'GET', path: '/' });
  const loginResponder = wxStub.__requestResponder!;
  wxStub.__requestResponder = o => o.url.endsWith('/auth/wechat') ? loginResponder(o) : o.success({ statusCode: 401, data: { error: { code: 'TOKEN_EXPIRED', message: 'expired', details: {} } } });
  await expect(client.request({ method: 'GET', path: '/' })).rejects.toMatchObject({ statusCode: 401 });
  expect(wxStub.__loginCount).toBe(2);
});
