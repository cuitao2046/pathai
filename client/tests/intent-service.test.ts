/**
 * IntentService（API-04）测试：请求路径/体/Bearer、响应透出、
 * 地图未加载短路、服务端错误信封转 ApiClientError。
 */
import { createHttpStack } from '../core/http/api-client';
import { ApiClientError } from '../core/http/errors';
import { IntentService, type MapVersionProvider } from '../services/intent.service';
import { remove } from '../utils/kv';
import type { AuthResponse, IntentParseResponse } from '../typings/api';
import { wxStub, type WxRequestOptionsStub } from './setup-wx';

const OK_RESPONSE: IntentParseResponse = {
  intentId: 'intent_1',
  intent: 'start_navigation',
  confidence: 0.9,
  needsClarification: false,
  destination: { placeId: 'p-library', name: '图书馆', floorId: 'F1' },
  candidates: [],
  responseText: '好的，开始导航到图书馆。',
};

const AUTH_RESPONSE: AuthResponse = {
  accessToken: 'token-1',
  tokenType: 'Bearer',
  expiresIn: 7200,
  userId: 'anon_1',
};

function defaultResponder(options: WxRequestOptionsStub): void {
  if (options.url.endsWith('/api/v1/auth/wechat')) {
    options.success({ statusCode: 200, header: {}, data: AUTH_RESPONSE });
    return;
  }
  if (options.url.endsWith('/api/v1/intents/parse')) {
    options.success({ statusCode: 200, header: {}, data: OK_RESPONSE });
    return;
  }
  options.fail({ errMsg: 'request:fail unexpected url' });
}

async function expectError(promise: Promise<unknown>): Promise<ApiClientError> {
  try {
    await promise;
  } catch (err) {
    if (err instanceof ApiClientError) {
      return err;
    }
    throw err;
  }
  throw new Error('期望请求失败，但成功了');
}

describe('IntentService（API-04）', () => {
  beforeEach(() => {
    wxStub.__requestLog.length = 0;
    wxStub.__requestResponder = defaultResponder;
    remove('auth:token');
  });

  const map: MapVersionProvider = {
    siteId: 'school-building-01',
    requireVersion: () => '2026.10.1',
  };

  function makeService(): IntentService {
    return new IntentService(createHttpStack('http://localhost:8000').client, map);
  }

  it('发送 POST /api/v1/intents/parse，携带 siteId/mapVersion/locale 与 Bearer', async () => {
    const svc = makeService();
    const result = await svc.parse('带我去图书馆');
    expect(result).toEqual(OK_RESPONSE);
    const request = wxStub.__requestLog[wxStub.__requestLog.length - 1];
    expect(request.url).toBe('http://localhost:8000/api/v1/intents/parse');
    expect(request.method).toBe('POST');
    expect(request.header?.Authorization).toBe('Bearer token-1');
    expect(request.data).toEqual({
      siteId: 'school-building-01',
      mapVersion: '2026.10.1',
      text: '带我去图书馆',
      sessionId: null,
      locale: 'zh-CN',
    });
  });

  it('sessionId 透传给服务端', async () => {
    const svc = makeService();
    await svc.parse('文本', 'sess_1');
    const request = wxStub.__requestLog[wxStub.__requestLog.length - 1];
    expect((request.data as { sessionId: string }).sessionId).toBe('sess_1');
  });

  it('地图未加载（requireVersion 抛 MAP_UNAVAILABLE）时短路且不发请求', async () => {
    const brokenMap: MapVersionProvider = {
      siteId: 's',
      requireVersion: () => {
        throw new ApiClientError({ code: 'MAP_UNAVAILABLE', message: '地图尚未加载' });
      },
    };
    const svc = new IntentService(createHttpStack('http://localhost:8000').client, brokenMap);
    const err = await expectError(svc.parse('任意'));
    expect(err.code).toBe('MAP_UNAVAILABLE');
    expect(wxStub.__requestLog).toHaveLength(0);
  });

  it('服务端错误信封转为 ApiClientError（MAP_NOT_PUBLISHED）', async () => {
    wxStub.__requestResponder = (options) => {
      if (options.url.endsWith('/api/v1/auth/wechat')) {
        options.success({ statusCode: 200, header: {}, data: AUTH_RESPONSE });
        return;
      }
      options.success({
        statusCode: 404,
        header: {},
        data: {
          error: { code: 'MAP_NOT_PUBLISHED', message: '版本未发布', retryable: false, details: {} },
          requestId: 'req-1',
        },
      });
    };
    const svc = makeService();
    const err = await expectError(svc.parse('带我去图书馆'));
    expect(err.code).toBe('MAP_NOT_PUBLISHED');
    expect(err.statusCode).toBe(404);
    expect(err.requestId).toBe('req-1');
  });
});
