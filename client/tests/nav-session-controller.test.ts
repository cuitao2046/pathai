/**
 * NavSessionController 测试（C7，docs/24 FE-04/FE-05）：
 * - API-06 创建幂等（key+冻结 body 失败重试复用，2xx 清除）；
 * - API-07 更新编排：estimate→POST、事件映射（instruction/reroute/
 *   relocalizing/arrived）、STALE/SESSION_NOT_ACTIVE 恢复；
 * - FE-05 前台恢复次序：查询校验 → 重启采集 → 重播；
 * - 客户端从不发送 reason=arrived。
 * TTS 用记录型引擎：speak 同步登记文本、默认立即完成（hold 模式挂起）。
 */
import { createHttpStack } from '../core/http/api-client';
import { ApiClientError } from '../core/http/errors';
import {
  NavSessionController,
  type NavPipelineControl,
  type NavState,
} from '../core/nav/nav-session-controller';
import { SpeechQueue } from '../core/speech/speech-queue';
import type { TtsEngine } from '../core/speech/tts-engine';
import { remove } from '../utils/kv';
import type {
  ApiErrorCode,
  AuthResponse,
  CreateSessionResponse,
  EndReason,
  EndSessionResponse,
  NavigationInstruction,
  NavUpdateResponse,
  OriginPosition,
  PositionEstimateResponse,
  RouteInfo,
  SessionQueryResponse,
} from '../typings/api';
import { wxStub, type WxRequestOptionsStub } from './setup-wx';

const NOW_ISO = '2026-10-25T00:00:00Z';

const AUTH_RESPONSE: AuthResponse = {
  accessToken: 'token-1',
  tokenType: 'Bearer',
  expiresIn: 7200,
  userId: 'anon_1',
};

function makeInstruction(
  overrides: Partial<NavigationInstruction> & Pick<NavigationInstruction, 'instructionId'>,
): NavigationInstruction {
  return {
    instructionSequence: 1,
    action: 'continue',
    text: `指令 ${overrides.instructionId}`,
    distanceM: null,
    landmark: null,
    priority: 'normal',
    trigger: { type: 'immediate', valueM: null, nodeId: null },
    expiresAt: null,
    ...overrides,
  };
}

const INSTR_1 = makeInstruction({
  instructionId: 'ins-1',
  instructionSequence: 1,
  text: '沿走廊直行 5 米。',
  priority: 'normal',
});

const RELOC_INSTR = makeInstruction({
  instructionId: 'ins-re-1',
  instructionSequence: 3,
  action: 'relocalize',
  text: '正在重新定位，请原地稍候。',
  priority: 'safety',
});

const ARRIVE_INSTR = makeInstruction({
  instructionId: 'ins-arr',
  instructionSequence: 9,
  action: 'arrive',
  text: '您已到达目的地。',
});

function makeRoute(routeId: string, first: NavigationInstruction): RouteInfo {
  return {
    routeId,
    mapVersion: 'v1',
    routeRulesVersion: 'rules-0.1',
    distanceM: 12,
    estimatedDurationSec: null,
    polyline: [
      { x: 0, y: 0, floorId: '1' },
      { x: 8, y: 0, floorId: '1' },
      { x: 8, y: 4, floorId: '1' },
    ],
    segments: [first],
    fallbackType: 'none',
    safeForBlind: true,
    riskNotices: [],
  };
}

const ROUTE1 = makeRoute('route-1', INSTR_1);
const ROUTE2 = makeRoute(
  'route-2',
  makeInstruction({
    instructionId: 'ins-re-2',
    instructionSequence: 10,
    text: '已为您重新规划路线，沿走廊直行 10 米。',
  }),
);

const CREATE_RESPONSE: CreateSessionResponse = {
  sessionId: 'sess-1',
  status: 'active',
  mapVersion: 'v1',
  paramsVersion: 'unset',
  destination: { placeId: 'p1', name: '图书馆', floorId: '1' },
  route: ROUTE1,
  createdAt: NOW_ISO,
};

const ORIGIN: OriginPosition = {
  x: 1,
  y: 2,
  floorId: '1',
  confidence: 0.9,
  observedAt: NOW_ISO,
};

const ESTIMATE_RELIABLE: PositionEstimateResponse = {
  estimateId: 'loc-1',
  sequence: 1,
  mapVersion: 'v1',
  position: { x: 3, y: 4, floorId: '1' },
  confidence: 0.9,
  quality: 'reliable',
  algorithmVersion: 'location-0.1',
  paramsVersion: 'unset',
  observedAt: NOW_ISO,
  ignoredBeaconIds: [],
};

const QUERY_RESPONSE: SessionQueryResponse = {
  sessionId: 'sess-1',
  status: 'active',
  siteId: 's1',
  mapVersion: 'v1',
  paramsVersion: 'unset',
  destination: { placeId: 'p1', name: '图书馆', floorId: '1' },
  lastProcessedSequence: 0,
  updatedAt: NOW_ISO,
};

const END_RESPONSE: EndSessionResponse = {
  sessionId: 'sess-1',
  status: 'ended',
  reason: 'user_finished',
  endedAt: NOW_ISO,
};

function updateResponse(
  overrides: Partial<NavUpdateResponse> & { processedSequence: number },
): NavUpdateResponse {
  return {
    sessionId: 'sess-1',
    status: 'active',
    event: 'instruction',
    instruction: null,
    routeChanged: false,
    ...overrides,
  };
}

/** 记录型 TTS：speak 同步登记文本；hold=true 时挂起（观察 pending 语义）。 */
class RecordTts implements TtsEngine {
  readonly spoken: string[] = [];
  private resolvers: Array<() => void> = [];
  hold = false;
  stopCount = 0;

  speak(text: string): Promise<void> {
    this.spoken.push(text);
    if (!this.hold) {
      return Promise.resolve();
    }
    return new Promise((resolve) => this.resolvers.push(resolve));
  }

  /** 引擎契约：stop 使当前 speak 以 resolve 结束。 */
  stop(): void {
    this.stopCount += 1;
    for (const resolve of this.resolvers.splice(0)) {
      resolve();
    }
  }

  /** 释放全部挂起播报并恢复立即完成模式。 */
  settleAll(): void {
    this.hold = false;
    this.stop();
  }
}

/** 记录调用的假定位管线（断言 stop/startNavigating 次序）。 */
class FakePipeline implements NavPipelineControl {
  readonly calls: string[] = [];

  stop(): void {
    this.calls.push('stop');
  }

  async startNavigating(): Promise<void> {
    this.calls.push('startNavigating');
  }
}

function replyError(options: WxRequestOptionsStub, statusCode: number, code: ApiErrorCode): void {
  options.success({
    statusCode,
    header: {},
    data: {
      error: { code, message: code, retryable: false, details: {} },
      requestId: 'req-x',
    },
  });
}

const flush = async (): Promise<void> => {
  for (let i = 0; i < 20; i += 1) {
    await Promise.resolve();
  }
};

interface Harness {
  controller: NavSessionController;
  speech: SpeechQueue;
  tts: RecordTts;
  pipeline: FakePipeline;
  states: NavState[];
  routes: RouteInfo[];
  ended: Array<{ reason: EndReason | null; byArrival: boolean }>;
}

describe('NavSessionController 会话编排', () => {
  let tts: RecordTts;
  let pipeline: FakePipeline;
  let states: NavState[];
  let routes: RouteInfo[];
  let ended: Array<{ reason: EndReason | null; byArrival: boolean }>;

  const createRequests = () =>
    wxStub.__requestLog.filter(
      (r) => r.method === 'POST' && r.url.endsWith('/api/v1/nav/sessions'),
    );
  const updateRequests = () =>
    wxStub.__requestLog.filter((r) => r.url.endsWith('/sessions/sess-1/updates'));

  function makeHarness(): Harness {
    const speech = new SpeechQueue({
      engine: tts,
      onUnavailable: () => undefined,
      onTextFallback: () => undefined,
    });
    const controller = new NavSessionController({
      client: createHttpStack('http://localhost:8000').client,
      map: { siteId: 's1', requireVersion: () => 'v1' },
      speech,
      pipeline,
      onStateChange: (state) => states.push(state),
      onRoute: (route) => routes.push(route),
      onEnded: (info) => ended.push(info),
    });
    return { controller, speech, tts, pipeline, states, routes, ended };
  }

  beforeEach(() => {
    tts = new RecordTts();
    pipeline = new FakePipeline();
    states = [];
    routes = [];
    ended = [];
    wxStub.__requestLog.length = 0;
    wxStub.__requestResponder = (options) => {
      if (options.url.endsWith('/api/v1/auth/wechat')) {
        options.success({ statusCode: 200, header: {}, data: AUTH_RESPONSE });
        return;
      }
      if (options.url.endsWith('/api/v1/nav/sessions')) {
        options.success({ statusCode: 201, header: {}, data: CREATE_RESPONSE });
        return;
      }
      if (options.url.endsWith('/sessions/sess-1/updates')) {
        options.success({
          statusCode: 200,
          header: {},
          data: updateResponse({ processedSequence: 1, instruction: null }),
        });
        return;
      }
      if (options.url.endsWith('/sessions/sess-1')) {
        options.success({ statusCode: 200, header: {}, data: QUERY_RESPONSE });
        return;
      }
      if (options.url.endsWith('/sessions/sess-1/end')) {
        options.success({ statusCode: 200, header: {}, data: END_RESPONSE });
        return;
      }
      options.fail({ errMsg: 'request:fail unexpected url' });
    };
    remove('auth:token');
  });

  // ------------------------------------------------------------------
  // API-06 创建
  // ------------------------------------------------------------------

  it('创建成功：幂等键请求体正确，播首条指令，onRoute + 重启导航采集，状态 active', async () => {
    const h = makeHarness();
    await h.controller.createSession({ placeId: 'p1', name: '图书馆', floorId: '1' }, ORIGIN);

    const request = createRequests()[0];
    expect(request.url).toBe('http://localhost:8000/api/v1/nav/sessions');
    expect(request.header?.['Idempotency-Key']).toHaveLength(36);
    expect(request.data).toEqual({
      siteId: 's1',
      mapVersion: 'v1',
      origin: ORIGIN,
      destinationId: 'p1',
      userMode: 'blind',
      locale: 'zh-CN',
    });
    expect(h.routes).toEqual([ROUTE1]);
    expect(h.tts.spoken).toEqual(['沿走廊直行 5 米。']);
    expect(h.pipeline.calls).toEqual(['startNavigating']);
    expect(h.controller.state).toBe('active');
    expect(h.controller.sessionId).toBe('sess-1');
  });

  it('创建失败后重试：复用同一幂等键与冻结 body；成功后再次创建换新键', async () => {
    const h = makeHarness();
    let attempts = 0;
    wxStub.__requestResponder = (options) => {
      if (options.url.endsWith('/api/v1/auth/wechat')) {
        options.success({ statusCode: 200, header: {}, data: AUTH_RESPONSE });
        return;
      }
      if (options.url.endsWith('/api/v1/nav/sessions')) {
        attempts += 1;
        if (attempts === 1) {
          replyError(options, 500, 'INTERNAL_ERROR');
          return;
        }
        options.success({ statusCode: 201, header: {}, data: CREATE_RESPONSE });
        return;
      }
      options.fail({ errMsg: 'request:fail unexpected url' });
    };

    const firstFailure = await h.controller
      .createSession({ placeId: 'p1', name: '图书馆', floorId: '1' }, ORIGIN)
      .then(
        () => null,
        (err: unknown) => err,
      );
    expect(firstFailure).toBeInstanceOf(ApiClientError);

    // 传入不同参数：应被忽略，复用冻结 body 与原 key
    await h.controller.createSession({ placeId: 'p2', name: '食堂', floorId: '2' }, {
      ...ORIGIN,
      x: 99,
    });

    const [firstReq, secondReq] = createRequests();
    expect(firstReq.header?.['Idempotency-Key']).toBe(secondReq.header?.['Idempotency-Key']);
    expect(secondReq.data).toEqual(firstReq.data); // 冻结 body：destinationId 仍为 p1

    // 2xx 后暂存清除：再次创建生成新 key
    await h.controller.createSession({ placeId: 'p1', name: '图书馆', floorId: '1' }, ORIGIN);
    const [key1, , key3] = createRequests().map((r) => r.header?.['Idempotency-Key']);
    expect(key1).not.toBe(key3);
  });

  // ------------------------------------------------------------------
  // API-07 更新编排
  // ------------------------------------------------------------------

  it('handleEstimate 可信质量：POST sequence=1 并携带坐标与置信度', async () => {
    const h = makeHarness();
    await h.controller.createSession({ placeId: 'p1', name: '图书馆', floorId: '1' }, ORIGIN);
    wxStub.__requestLog.length = 0;

    h.controller.handleEstimate(ESTIMATE_RELIABLE);
    await flush();

    expect(updateRequests()).toHaveLength(1);
    expect(updateRequests()[0].data).toEqual({
      sequence: 1,
      location: {
        x: 3,
        y: 4,
        floorId: '1',
        confidence: 0.9,
        quality: 'reliable',
        observedAt: NOW_ISO,
      },
    });
  });

  it('handleEstimate 不可信质量：仅带 quality/confidence，坐标字段置空', async () => {
    const h = makeHarness();
    await h.controller.createSession({ placeId: 'p1', name: '图书馆', floorId: '1' }, ORIGIN);
    wxStub.__requestLog.length = 0;

    h.controller.handleEstimate({
      ...ESTIMATE_RELIABLE,
      quality: 'unreliable',
      position: null,
      confidence: 0.2,
    });
    await flush();

    expect(updateRequests()[0].data).toEqual({
      sequence: 1,
      location: {
        x: null,
        y: null,
        floorId: null,
        confidence: 0.2,
        quality: 'unreliable',
        observedAt: NOW_ISO,
      },
    });
  });

  it('同一 instructionId 重复下发不重复播报', async () => {
    const h = makeHarness();
    await h.controller.createSession({ placeId: 'p1', name: '图书馆', floorId: '1' }, ORIGIN);
    wxStub.__requestLog.length = 0;
    wxStub.__requestResponder = (options) => {
      if (options.url.endsWith('/api/v1/auth/wechat')) {
        options.success({ statusCode: 200, header: {}, data: AUTH_RESPONSE });
        return;
      }
      options.success({
        statusCode: 200,
        header: {},
        data: updateResponse({
          processedSequence: Number((options.data as { sequence: number }).sequence),
          instruction: INSTR_1, // 服务端重复下发同指令
        }),
      });
    };

    h.controller.handleEstimate(ESTIMATE_RELIABLE);
    await flush();
    h.controller.handleEstimate({ ...ESTIMATE_RELIABLE, estimateId: 'loc-2' });
    await flush();

    expect(updateRequests()).toHaveLength(2);
    expect(h.tts.spoken).toEqual(['沿走廊直行 5 米。']); // 创建播报一次，更新不再播
  });

  it('reroute：清空待播 normal、onRoute 更新为新路线、播报新首条', async () => {
    const h = makeHarness();
    h.tts.hold = true; // 观察队列语义：首条播报挂起
    await h.controller.createSession({ placeId: 'p1', name: '图书馆', floorId: '1' }, ORIGIN);
    // 注入一条待播 normal（旧路线提示）
    h.speech.enqueue('旧路线提示', 'normal');

    wxStub.__requestResponder = (options) => {
      if (options.url.endsWith('/api/v1/auth/wechat')) {
        options.success({ statusCode: 200, header: {}, data: AUTH_RESPONSE });
        return;
      }
      options.success({
        statusCode: 200,
        header: {},
        data: updateResponse({
          processedSequence: 1,
          status: 'active',
          event: 'reroute',
          routeChanged: true,
          route: ROUTE2,
          instruction: ROUTE2.segments[0],
        }),
      });
    };
    h.controller.handleEstimate(ESTIMATE_RELIABLE);
    await flush();
    h.tts.settleAll();
    await flush();

    expect(h.routes).toEqual([ROUTE1, ROUTE2]);
    expect(h.tts.spoken).toEqual(['沿走廊直行 5 米。', ROUTE2.segments[0].text]);
    expect(h.tts.spoken).not.toContain('旧路线提示'); // clearNormal 移除待播项
    expect(h.controller.state).toBe('active');
  });

  it('首次 relocalizing：safety 播报一次 + 保持采集 + 状态 recovering；再次进入不重播', async () => {
    const h = makeHarness();
    await h.controller.createSession({ placeId: 'p1', name: '图书馆', floorId: '1' }, ORIGIN);
    let updates = 0;
    wxStub.__requestResponder = (options) => {
      if (options.url.endsWith('/api/v1/auth/wechat')) {
        options.success({ statusCode: 200, header: {}, data: AUTH_RESPONSE });
        return;
      }
      updates += 1;
      options.success({
        statusCode: 200,
        header: {},
        data: updateResponse({
          processedSequence: updates,
          status: 'recovering',
          event: 'relocalizing',
          instruction: {
            ...RELOC_INSTR,
            instructionId: `ins-re-${updates}`, // 服务端每 sequence 新 instructionId
            instructionSequence: updates,
          },
        }),
      });
    };

    h.controller.handleEstimate(ESTIMATE_RELIABLE);
    await flush();
    expect(h.controller.state).toBe('recovering');
    expect(h.pipeline.calls).toEqual(['startNavigating']); // recovering 不停止采集
    expect(h.tts.spoken).toContain('正在重新定位，请原地稍候。');

    // 第二拍仍 recovering：新 instructionId 但不重复播报
    h.controller.handleEstimate({ ...ESTIMATE_RELIABLE, estimateId: 'loc-2' });
    await flush();
    expect(h.tts.spoken.filter((t) => t === '正在重新定位，请原地稍候。')).toHaveLength(1);
  });

  it('recovering → active：状态机重启采集（startPipeline 副作用）', async () => {
    const h = makeHarness();
    await h.controller.createSession({ placeId: 'p1', name: '图书馆', floorId: '1' }, ORIGIN);
    let updates = 0;
    wxStub.__requestResponder = (options) => {
      if (options.url.endsWith('/api/v1/auth/wechat')) {
        options.success({ statusCode: 200, header: {}, data: AUTH_RESPONSE });
        return;
      }
      updates += 1;
      if (updates === 1) {
        options.success({
          statusCode: 200,
          header: {},
          data: updateResponse({
            processedSequence: 1,
            status: 'recovering',
            event: 'relocalizing',
            instruction: RELOC_INSTR,
          }),
        });
        return;
      }
      options.success({
        statusCode: 200,
        header: {},
        data: updateResponse({ processedSequence: 2, status: 'active', event: 'instruction' }),
      });
    };

    h.controller.handleEstimate(ESTIMATE_RELIABLE);
    await flush();
    h.controller.handleEstimate({ ...ESTIMATE_RELIABLE, estimateId: 'loc-2' });
    await flush();

    expect(h.states).toEqual(['active', 'recovering', 'active']);
    // recovering 期间未停采集，恢复 active 时仅再切一次导航档（幂等）
    expect(h.pipeline.calls).toEqual(['startNavigating', 'startNavigating']);
  });

  it('reportUnavailable：只报 quality=unreliable（无坐标），服务端转 recovering', async () => {
    const h = makeHarness();
    await h.controller.createSession({ placeId: 'p1', name: '图书馆', floorId: '1' }, ORIGIN);
    wxStub.__requestResponder = (options) => {
      if (options.url.endsWith('/api/v1/auth/wechat')) {
        options.success({ statusCode: 200, header: {}, data: AUTH_RESPONSE });
        return;
      }
      options.success({
        statusCode: 200,
        header: {},
        data: updateResponse({
          processedSequence: 1,
          status: 'recovering',
          event: 'relocalizing',
          instruction: RELOC_INSTR,
        }),
      });
    };

    h.controller.reportUnavailable();
    await flush();

    const req = updateRequests()[0];
    expect(req.data).toMatchObject({
      sequence: 1,
      location: {
        x: null,
        y: null,
        floorId: null,
        confidence: 0,
        quality: 'unreliable',
      },
    });
    expect(h.controller.state).toBe('recovering');
    expect(h.pipeline.calls).toEqual(['startNavigating']); // 仍保持采集
  });

  it('arrived 高置信度：播「已到达目的地。」、停采集、onEnded(byArrival)', async () => {
    const h = makeHarness();
    await h.controller.createSession({ placeId: 'p1', name: '图书馆', floorId: '1' }, ORIGIN);
    wxStub.__requestResponder = (options) => {
      if (options.url.endsWith('/api/v1/auth/wechat')) {
        options.success({ statusCode: 200, header: {}, data: AUTH_RESPONSE });
        return;
      }
      options.success({
        statusCode: 200,
        header: {},
        data: updateResponse({
          processedSequence: 1,
          status: 'arrived',
          event: 'arrived',
          instruction: ARRIVE_INSTR,
        }),
      });
    };

    h.controller.handleEstimate(ESTIMATE_RELIABLE); // confidence 0.9 ≥ 0.75
    await flush();

    expect(h.controller.state).toBe('arrived');
    expect(h.pipeline.calls).toEqual(['startNavigating', 'stop']);
    expect(h.tts.spoken[h.tts.spoken.length - 1]).toBe('已到达目的地。');
    expect(h.tts.spoken).not.toContain(ARRIVE_INSTR.text); // arrived 分支播固定话术
    expect(h.ended).toEqual([{ reason: 'arrived', byArrival: true }]);
  });

  it('arrived 低置信度：播「已到达目的地附近…」', async () => {
    const h = makeHarness();
    await h.controller.createSession({ placeId: 'p1', name: '图书馆', floorId: '1' }, ORIGIN);
    wxStub.__requestResponder = (options) => {
      if (options.url.endsWith('/api/v1/auth/wechat')) {
        options.success({ statusCode: 200, header: {}, data: AUTH_RESPONSE });
        return;
      }
      options.success({
        statusCode: 200,
        header: {},
        data: updateResponse({
          processedSequence: 1,
          status: 'arrived',
          event: 'arrived',
          instruction: ARRIVE_INSTR,
        }),
      });
    };

    h.controller.handleEstimate({ ...ESTIMATE_RELIABLE, confidence: 0.4 });
    await flush();

    expect(h.tts.spoken[h.tts.spoken.length - 1]).toBe('已到达目的地附近，请留意周围环境。');
  });

  it('终态拒更新：arrived 后 handleEstimate 不再发送 updates', async () => {
    const h = makeHarness();
    await h.controller.createSession({ placeId: 'p1', name: '图书馆', floorId: '1' }, ORIGIN);
    wxStub.__requestResponder = (options) => {
      if (options.url.endsWith('/api/v1/auth/wechat')) {
        options.success({ statusCode: 200, header: {}, data: AUTH_RESPONSE });
        return;
      }
      options.success({
        statusCode: 200,
        header: {},
        data: updateResponse({
          processedSequence: 1,
          status: 'arrived',
          event: 'arrived',
          instruction: ARRIVE_INSTR,
        }),
      });
    };
    h.controller.handleEstimate(ESTIMATE_RELIABLE);
    await flush();
    expect(updateRequests()).toHaveLength(1);

    h.controller.handleEstimate({ ...ESTIMATE_RELIABLE, estimateId: 'loc-2' });
    await flush();
    expect(updateRequests()).toHaveLength(1); // 无新请求
    expect(h.controller.state).toBe('arrived');
  });

  it('409 STALE_NAV_UPDATE：GET 重对齐 lastProcessedSequence 后续拍递增', async () => {
    const h = makeHarness();
    await h.controller.createSession({ placeId: 'p1', name: '图书馆', floorId: '1' }, ORIGIN);
    let updateCount = 0;
    wxStub.__requestResponder = (options) => {
      if (options.url.endsWith('/api/v1/auth/wechat')) {
        options.success({ statusCode: 200, header: {}, data: AUTH_RESPONSE });
        return;
      }
      if (options.url.endsWith('/sessions/sess-1/updates')) {
        updateCount += 1;
        if (updateCount === 1) {
          replyError(options, 409, 'STALE_NAV_UPDATE');
          return;
        }
        options.success({
          statusCode: 200,
          header: {},
          data: updateResponse({ processedSequence: (options.data as { sequence: number }).sequence }),
        });
        return;
      }
      if (options.url.endsWith('/sessions/sess-1')) {
        options.success({
          statusCode: 200,
          header: {},
          data: { ...QUERY_RESPONSE, lastProcessedSequence: 5 },
        });
        return;
      }
      options.fail({ errMsg: 'request:fail unexpected url' });
    };

    h.controller.handleEstimate(ESTIMATE_RELIABLE); // 409 → resync → sessionSeq=5
    await flush();
    h.controller.handleEstimate({ ...ESTIMATE_RELIABLE, estimateId: 'loc-2' });
    await flush();

    const second = updateRequests()[1];
    expect((second.data as { sequence: number }).sequence).toBe(6); // serverSeq + 1
  });

  it('409 SESSION_NOT_ACTIVE：查询确认终态后收敛 onEnded', async () => {
    const h = makeHarness();
    await h.controller.createSession({ placeId: 'p1', name: '图书馆', floorId: '1' }, ORIGIN);
    wxStub.__requestResponder = (options) => {
      if (options.url.endsWith('/api/v1/auth/wechat')) {
        options.success({ statusCode: 200, header: {}, data: AUTH_RESPONSE });
        return;
      }
      if (options.url.endsWith('/sessions/sess-1/updates')) {
        replyError(options, 409, 'SESSION_NOT_ACTIVE');
        return;
      }
      if (options.url.endsWith('/sessions/sess-1')) {
        options.success({
          statusCode: 200,
          header: {},
          data: {
            ...QUERY_RESPONSE,
            status: 'ended',
            endReason: 'user_cancelled',
            lastProcessedSequence: 3,
          },
        });
        return;
      }
      options.fail({ errMsg: 'request:fail unexpected url' });
    };

    h.controller.handleEstimate(ESTIMATE_RELIABLE);
    await flush();

    expect(h.controller.state).toBe('ended');
    expect(h.pipeline.calls).toContain('stop');
    expect(h.ended).toEqual([{ reason: 'user_cancelled', byArrival: false }]);
  });

  // ------------------------------------------------------------------
  // FE-05 前台恢复
  // ------------------------------------------------------------------

  it('handleBackground → handleForeground：采集次序 停→查→启，并重播当前有效指令', async () => {
    const h = makeHarness();
    await h.controller.createSession({ placeId: 'p1', name: '图书馆', floorId: '1' }, ORIGIN);
    expect(h.pipeline.calls).toEqual(['startNavigating']);

    h.controller.handleBackground();
    expect(h.pipeline.calls).toEqual(['startNavigating', 'stop']);

    wxStub.__requestResponder = (options) => {
      if (options.url.endsWith('/api/v1/auth/wechat')) {
        options.success({ statusCode: 200, header: {}, data: AUTH_RESPONSE });
        return;
      }
      options.success({
        statusCode: 200,
        header: {},
        data: { ...QUERY_RESPONSE, status: 'active', lastProcessedSequence: 0 },
      });
    };
    await h.controller.handleForeground();

    expect(h.pipeline.calls).toEqual(['startNavigating', 'stop', 'startNavigating']);
    // 创建播报 + 回前台重播同一指令（tracker 去重后仍允许跨生命周期重播）
    expect(h.tts.spoken.filter((t) => t === '沿走廊直行 5 米。')).toHaveLength(2);
    expect(h.controller.state).toBe('active');
  });

  it('回前台地图版本不一致：结束会话并 safety 提示', async () => {
    const h = makeHarness();
    await h.controller.createSession({ placeId: 'p1', name: '图书馆', floorId: '1' }, ORIGIN);
    h.controller.handleBackground();
    wxStub.__requestResponder = (options) => {
      if (options.url.endsWith('/api/v1/auth/wechat')) {
        options.success({ statusCode: 200, header: {}, data: AUTH_RESPONSE });
        return;
      }
      options.success({
        statusCode: 200,
        header: {},
        data: { ...QUERY_RESPONSE, status: 'active', mapVersion: 'v2' },
      });
    };

    await h.controller.handleForeground();

    expect(h.controller.state).toBe('ended');
    expect(h.tts.spoken).toContain('地图已更新，请重新发起导航。');
    expect(h.ended).toEqual([{ reason: 'system_error', byArrival: false }]);
    expect(h.pipeline.calls).toEqual(['startNavigating', 'stop', 'stop']); // RESUME_FAILED 副作用再次停采集（幂等）
  });

  it('回前台会话已不存在（404）：safety 提示并按 system_error 收尾', async () => {
    const h = makeHarness();
    await h.controller.createSession({ placeId: 'p1', name: '图书馆', floorId: '1' }, ORIGIN);
    h.controller.handleBackground();
    wxStub.__requestResponder = (options) => {
      if (options.url.endsWith('/api/v1/auth/wechat')) {
        options.success({ statusCode: 200, header: {}, data: AUTH_RESPONSE });
        return;
      }
      replyError(options, 404, 'SESSION_NOT_FOUND');
    };

    await h.controller.handleForeground();

    expect(h.controller.state).toBe('ended');
    expect(h.tts.spoken).toContain('导航会话已失效，请重新发起导航。');
    expect(h.ended).toEqual([{ reason: 'system_error', byArrival: false }]);
  });

  it('回前台网络故障：保持会话并恢复采集继续', async () => {
    const h = makeHarness();
    await h.controller.createSession({ placeId: 'p1', name: '图书馆', floorId: '1' }, ORIGIN);
    h.controller.handleBackground();
    wxStub.__requestResponder = (options) => {
      if (options.url.endsWith('/api/v1/auth/wechat')) {
        options.success({ statusCode: 200, header: {}, data: AUTH_RESPONSE });
        return;
      }
      options.fail({ errMsg: 'request:fail timeout' });
    };

    await h.controller.handleForeground();

    expect(h.controller.state).toBe('active'); // 保持现状
    expect(h.pipeline.calls).toEqual(['startNavigating', 'stop', 'startNavigating']);
    expect(h.ended).toHaveLength(0);
  });

  it('recovering 态回前台：恢复采集并重播安全提示（不播旧方向指令）', async () => {
    const h = makeHarness();
    await h.controller.createSession({ placeId: 'p1', name: '图书馆', floorId: '1' }, ORIGIN);
    wxStub.__requestResponder = (options) => {
      if (options.url.endsWith('/api/v1/auth/wechat')) {
        options.success({ statusCode: 200, header: {}, data: AUTH_RESPONSE });
        return;
      }
      options.success({
        statusCode: 200,
        header: {},
        data: updateResponse({
          processedSequence: 1,
          status: 'recovering',
          event: 'relocalizing',
          instruction: RELOC_INSTR,
        }),
      });
    };
    h.controller.handleEstimate(ESTIMATE_RELIABLE);
    await flush();
    expect(h.controller.state).toBe('recovering');

    h.controller.handleBackground();
    wxStub.__requestResponder = (options) => {
      if (options.url.endsWith('/api/v1/auth/wechat')) {
        options.success({ statusCode: 200, header: {}, data: AUTH_RESPONSE });
        return;
      }
      options.success({
        statusCode: 200,
        header: {},
        data: { ...QUERY_RESPONSE, status: 'recovering' },
      });
    };

    await h.controller.handleForeground();

    expect(h.controller.state).toBe('recovering');
    expect(h.pipeline.calls).toEqual(['startNavigating', 'stop', 'startNavigating']);
    expect(h.tts.spoken.filter((t) => t === '正在重新定位，请原地稍候。')).toHaveLength(2);
    expect(h.tts.spoken.filter((t) => t === '沿走廊直行 5 米。')).toHaveLength(1); // 不回放旧方向
  });

  // ------------------------------------------------------------------
  // API-09 结束
  // ------------------------------------------------------------------

  it('endSession(user_finished)：POST 理由非 arrived，END_ACK 后清队收尾', async () => {
    const h = makeHarness();
    await h.controller.createSession({ placeId: 'p1', name: '图书馆', floorId: '1' }, ORIGIN);
    wxStub.__requestLog.length = 0;

    await h.controller.endSession('user_finished');

    const endRequests = wxStub.__requestLog.filter((r) => r.url.endsWith('/sessions/sess-1/end'));
    expect(endRequests).toHaveLength(1);
    expect(endRequests[0].data).toEqual({ reason: 'user_finished' });
    expect(h.controller.state).toBe('ended');
    expect(h.pipeline.calls).toContain('stop');
    expect(h.ended).toEqual([{ reason: 'user_finished', byArrival: false }]);
  });

  it('endSession 遇 409 SESSION_NOT_ACTIVE：视为已结束，正常收尾不抛错', async () => {
    const h = makeHarness();
    await h.controller.createSession({ placeId: 'p1', name: '图书馆', floorId: '1' }, ORIGIN);
    wxStub.__requestResponder = (options) => {
      if (options.url.endsWith('/api/v1/auth/wechat')) {
        options.success({ statusCode: 200, header: {}, data: AUTH_RESPONSE });
        return;
      }
      replyError(options, 409, 'SESSION_NOT_ACTIVE');
    };

    await expect(h.controller.endSession('user_cancelled')).resolves.toBeUndefined();
    expect(h.controller.state).toBe('ended');
    expect(h.ended).toEqual([{ reason: 'user_cancelled', byArrival: false }]);
  });
});
