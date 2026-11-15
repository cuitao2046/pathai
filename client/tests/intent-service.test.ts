/**
 * IntentService（API-04）测试：请求路径/体/Bearer、响应透出、
 * 地图未加载短路、服务端错误信封转 ApiClientError。
 */
import { createHttpStack } from '../core/http/api-client';
import { ApiClientError } from '../core/http/errors';
import { IntentService, resolveIntentTarget, type MapVersionProvider } from '../services/intent.service';
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

  it('发送 POST /api/v1/intents/parse，携带 siteId/mapVersion/locale（免鉴权）', async () => {
    const svc = makeService();
    const result = await svc.parse('带我去图书馆');
    expect(result).toEqual(OK_RESPONSE);
    const request = wxStub.__requestLog[wxStub.__requestLog.length - 1];
    expect(request.url).toBe('http://localhost:8000/api/v1/intents/parse');
    expect(request.method).toBe('POST');
    expect(request.header?.Authorization).toBeUndefined();
    expect(request.data).toEqual({
      siteId: 'school-building-01',
      mapVersion: '2026.10.1',
      text: '带我去图书馆',
      sessionId: null,
      locale: 'zh-CN',
      location: null,
    });
  });

  it('传入定位时随请求发送（直答类意图需要空间上下文）', async () => {
    const svc = makeService();
    await svc.parse('前面好走吗', undefined, {
      x: 1.5,
      y: 2.5,
      floorId: '1',
      confidence: 0.9,
      headingDeg: 45,
    });
    const request = wxStub.__requestLog[wxStub.__requestLog.length - 1];
    expect((request.data as { location?: unknown }).location).toEqual({
      x: 1.5,
      y: 2.5,
      floorId: '1',
      confidence: 0.9,
      headingDeg: 45,
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

describe('resolveIntentTarget（动作优先分发）', () => {
  it('新动作 query_current_location 即使 intent 为 unknown 也触发定位', () => {
    expect(
      resolveIntentTarget({
        intent: 'unknown',
        action: { name: 'query_current_location', params: {}, confirmationRequired: false },
      }),
    ).toBe('location');
  });

  it('查周边动作 describe_surroundings 分发到周边播报，查位置不混用', () => {
    expect(
      resolveIntentTarget({
        intent: 'query_location',
        action: { name: 'describe_surroundings', params: {}, confirmationRequired: false },
      }),
    ).toBe('surroundings');
    expect(
      resolveIntentTarget({
        intent: 'query_location',
        action: { name: 'query_current_location', params: {}, confirmationRequired: false },
      }),
    ).toBe('location');
  });

  it('无 action 的旧响应继续按 intent=query_location 触发定位', () => {
    expect(resolveIntentTarget({ intent: 'query_location' })).toBe('location');
  });

  it('未接入的动作不触发定位，回退旧 intent 判断', () => {
    expect(
      resolveIntentTarget({
        intent: 'unknown',
        action: { name: 'navigate_to_place', params: {}, confirmationRequired: true },
      }),
    ).toBeNull();
  });

  it('非位置意图保持原行为', () => {
    expect(resolveIntentTarget({ intent: 'start_navigation' })).toBeNull();
  });

  it('start_navigation 动作分发到开始导航（语音启动入口）', () => {
    expect(
      resolveIntentTarget({
        intent: 'start_navigation',
        action: { name: 'start_navigation', params: {}, confirmationRequired: false },
      }),
    ).toBe('start');
  });

  it('导航控制动作分发到取消/结束/重播', () => {
    expect(
      resolveIntentTarget({
        intent: 'cancel',
        action: { name: 'cancel_navigation', params: {}, confirmationRequired: false },
      }),
    ).toBe('cancel');
    expect(
      resolveIntentTarget({
        intent: 'end_navigation',
        action: { name: 'end_navigation', params: {}, confirmationRequired: false },
      }),
    ).toBe('end');
    expect(
      resolveIntentTarget({
        intent: 'repeat_instruction',
        action: { name: 'repeat_instruction', params: {}, confirmationRequired: false },
      }),
    ).toBe('repeat');
  });

  it('旧后端无 action 时按 intent 兼容分发控制意图', () => {
    expect(resolveIntentTarget({ intent: 'cancel' })).toBe('cancel');
    expect(resolveIntentTarget({ intent: 'end_navigation' })).toBe('end');
    expect(resolveIntentTarget({ intent: 'repeat_instruction' })).toBe('repeat');
  });

  it('search_facility 不再映射定位（由后端给出 action 或候选）', () => {
    expect(resolveIntentTarget({ intent: 'search_facility' })).toBeNull();
  });

  it('speak_answer / navigate_to_place 不触发额外执行', () => {
    expect(
      resolveIntentTarget({
        intent: 'unknown',
        action: { name: 'speak_answer', params: {}, confirmationRequired: false },
      }),
    ).toBeNull();
    expect(
      resolveIntentTarget({
        intent: 'start_navigation',
        action: {
          name: 'navigate_to_place',
          params: { placeId: 'F1-RM-0003' },
          confirmationRequired: true,
        },
      }),
    ).toBeNull();
  });
});
