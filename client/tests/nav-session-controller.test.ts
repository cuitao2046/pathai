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
import type { NavCreateJournal } from '../core/nav/create-journal';
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
  /** 引擎无响应：stop 也不让挂起的 speak 结束（模拟播放器不回调）。 */
  ignoreStop = false;
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
    if (this.ignoreStop) {
      return;
    }
    for (const resolve of this.resolvers.splice(0)) {
      resolve();
    }
  }

  /** 释放全部挂起播报并恢复立即完成模式。 */
  settleAll(): void {
    this.hold = false;
    this.ignoreStop = false;
    this.stop();
  }
}

/** 记录型本地音频播放器：play 登记名称；hold=true 时挂起（观察串行语义）。 */
class RecordClipPlayer {
  readonly played: string[] = [];
  private resolvers: Array<() => void> = [];
  hold = false;

  play(name: string): Promise<void> {
    this.played.push(name);
    if (!this.hold) {
      return Promise.resolve();
    }
    return new Promise((resolve) => this.resolvers.push(resolve));
  }

  /** 模拟片段播完/被 stop 打断。 */
  stop(): void {
    for (const resolve of this.resolvers.splice(0)) {
      resolve();
    }
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
  clips: RecordClipPlayer;
  pipeline: FakePipeline;
  states: NavState[];
  routes: RouteInfo[];
  instructions: Array<NavigationInstruction | null>;
  ended: Array<{ reason: EndReason | null; byArrival: boolean }>;
}

describe('NavSessionController 会话编排', () => {
  const controllers: NavSessionController[] = [];
  let tts: RecordTts;
  let pipeline: FakePipeline;
  let states: NavState[];
  let routes: RouteInfo[];
  let instructions: Array<NavigationInstruction | null>;
  let ended: Array<{ reason: EndReason | null; byArrival: boolean }>;

  const createRequests = () =>
    wxStub.__requestLog.filter(
      (r) => r.method === 'POST' && r.url.endsWith('/api/v1/nav/sessions'),
    );
  const updateRequests = () =>
    wxStub.__requestLog.filter((r) => r.url.endsWith('/sessions/sess-1/updates'));

  function makeHarness(options: { overview?: string; overviewMaxWaitMs?: number; journal?: NavCreateJournal } = {}): Harness {
    const clips = new RecordClipPlayer();
    const speech = new SpeechQueue({
      engine: tts,
      onUnavailable: () => undefined,
      onTextFallback: () => undefined,
      stallTimeoutMs: () => 30, // 测试用短看门狗：引擎无响应时快速放手
      playClip: (clip) => clips.play(clip),
      stopClip: () => clips.stop(),
    });
    const controller = new NavSessionController({
      client: createHttpStack('http://localhost:8000').client,
      map: { siteId: 's1', requireVersion: () => 'v1' },
      speech,
      pipeline,
      overviewMaxWaitMs: options.overviewMaxWaitMs,
      createJournal: options.journal,
      onStateChange: (state) => states.push(state),
      onRoute: (route) => {
        routes.push(route);
        if (options.overview !== undefined) {
          speech.enqueue(options.overview, 'normal'); // 模拟 map 页把概要点播入队
        }
      },
      onInstruction: (instruction) => instructions.push(instruction),
      onEnded: (info) => ended.push(info),
    });
    controllers.push(controller);
    return { controller, speech, tts, clips, pipeline, states, routes, instructions, ended };
  }

  beforeEach(() => {
    tts = new RecordTts();
    pipeline = new FakePipeline();
    states = [];
    routes = [];
    instructions = [];
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

  afterEach(async () => {
    // 收尾：释放挂起播报，避免语音队列的看门狗定时器在测试结束后触发
    for (const controller of controllers.splice(0)) controller.dispose();
    tts.settleAll();
    await flush();
  });

  // ------------------------------------------------------------------
  // API-06 创建
  // ------------------------------------------------------------------

  it('页面重建后先用原键确认并结束未知会话，再按新目的地创建新会话', async () => {
    let saved: ReturnType<NavCreateJournal['load']> = null;
    const journal: NavCreateJournal = { load: () => saved, save: value => { saved = value; }, clear: () => { saved = null; } };
    const first = makeHarness({ journal });
    const normal = wxStub.__requestResponder!;
    wxStub.__requestResponder = options => options.fail({ errMsg: 'request:fail timeout' });
    await expect(first.controller.createSession(CREATE_RESPONSE.destination, ORIGIN)).rejects.toThrow();
    const before = createRequests()[0];
    const restored = makeHarness({ journal });
    wxStub.__requestResponder = normal;
    await restored.controller.createSession({ placeId: 'p2', name: '新目的地', floorId: '1' }, { ...ORIGIN, x: 9 });
    const requests = createRequests();
    expect(requests[1].header?.['Idempotency-Key']).toBe(before.header?.['Idempotency-Key']);
    expect(requests[1].data).toEqual(before.data);
    expect(requests[2].header?.['Idempotency-Key']).not.toBe(before.header?.['Idempotency-Key']);
    expect(requests[2].data).toMatchObject({ destinationId: 'p2', origin: { x: 9 } });
    const urls = wxStub.__requestLog.map(request => request.url);
    expect(urls[2]).toMatch(/\/end$/);
    expect(saved).toBeNull();
  });

  it('未知会话未确认结束时不得创建新会话或丢弃恢复记录', async () => {
    let saved: ReturnType<NavCreateJournal['load']> = { key: 'original-key', body: {
      siteId: 's1', mapVersion: 'v1', origin: ORIGIN, destinationId: 'p1', userMode: 'blind', locale: 'zh-CN',
    } };
    const journal: NavCreateJournal = { load: () => saved, save: value => { saved = value; }, clear: () => { saved = null; } };
    const h = makeHarness({ journal });
    const normal = wxStub.__requestResponder!;
    wxStub.__requestResponder = options => options.url.endsWith('/end') ? options.fail({ errMsg: 'request:fail timeout' }) : normal(options);
    await expect(h.controller.createSession(CREATE_RESPONSE.destination, ORIGIN)).rejects.toThrow();
    expect(createRequests()).toHaveLength(1);
    expect(saved?.key).toBe('original-key');
    expect(h.controller.sessionId).toBeNull();
  });

  it('连接长时间无有效响应时清除旧指令并暂停，销毁后释放计时器', async () => {
    jest.useFakeTimers();
    const h = makeHarness();
    try {
      await h.controller.createSession(CREATE_RESPONSE.destination, ORIGIN, 'blind', { overviewPlayed: true });
      await flush();
      jest.advanceTimersByTime(10_000);
      await flush();
      expect(h.controller.state).toBe('recovering');
      expect(h.instructions[h.instructions.length - 1]).toBeNull();
      expect(h.controller.currentInstructionSequence).toBeNull();
      expect(h.clips.played).toContain('nav-offline');
    } finally {
      h.controller.dispose();
      jest.clearAllTimers();
      jest.useRealTimers();
    }
  });

  it('断网后查询成功仍保持暂停，只有新鲜可信位置更新才能恢复', async () => {
    const h = makeHarness();
    await h.controller.createSession(CREATE_RESPONSE.destination, ORIGIN, 'blind', { overviewPlayed: true });
    const healthy = wxStub.__requestResponder!;
    wxStub.__requestResponder = options => options.fail({ errMsg: 'request:fail timeout' });
    h.controller.handleEstimate({ ...ESTIMATE_RELIABLE, observedAt: new Date().toISOString() });
    await flush();
    expect(h.controller.state).toBe('recovering');
    wxStub.__requestResponder = healthy;
    await h.controller.handleForeground();
    expect(h.controller.state).toBe('recovering');
    const count = updateRequests().length;
    h.controller.handleEstimate(ESTIMATE_RELIABLE); // Historical cached position cannot restore guidance.
    await flush();
    expect(updateRequests()).toHaveLength(count);
    h.controller.handleEstimate({ ...ESTIMATE_RELIABLE, observedAt: new Date().toISOString() });
    await flush();
    expect(h.controller.state).toBe('active');
  });

  it('进入后台后迟到的导航响应不能恢复状态或播报', async () => {
    const h = makeHarness();
    await h.controller.createSession(CREATE_RESPONSE.destination, ORIGIN, 'blind', { overviewPlayed: true });
    let pending!: WxRequestOptionsStub;
    wxStub.__requestResponder = options => { pending = options; };
    h.controller.handleEstimate(ESTIMATE_RELIABLE);
    await flush();
    h.controller.handleBackground();
    const count = h.instructions.length;
    pending.success({ statusCode: 200, data: updateResponse({ processedSequence: 1, instruction: INSTR_1 }) });
    await flush();
    expect(h.instructions).toHaveLength(count);
  });

  it('看门狗暂停后迟到的旧请求成功不能恢复引导', async () => {
    jest.useFakeTimers();
    const h = makeHarness();
    try {
      await h.controller.createSession(CREATE_RESPONSE.destination, ORIGIN, 'blind', { overviewPlayed: true });
      let pending!: WxRequestOptionsStub;
      wxStub.__requestResponder = options => { pending = options; };
      h.controller.handleEstimate({ ...ESTIMATE_RELIABLE, observedAt: new Date().toISOString() });
      await flush();
      jest.advanceTimersByTime(10_000);
      pending.success({ statusCode: 200, data: updateResponse({ processedSequence: 1, instruction: INSTR_1 }) });
      await flush();
      expect(h.controller.state).toBe('recovering');
      expect(h.instructions[h.instructions.length - 1]).toBeNull();
    } finally {
      h.controller.dispose();
      jest.clearAllTimers();
      jest.useRealTimers();
    }
  });

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

  it('overviewPlayed：概要已在预览阶段播完，建会话后直接播首条，不再等概要窗口', async () => {
    tts.hold = true; // 若仍走概要窗口，首条会被挂起不播
    const h = makeHarness();
    await h.controller.createSession(
      { placeId: 'p1', name: '图书馆', floorId: '1' },
      ORIGIN,
      'blind',
      { overviewPlayed: true },
    );

    expect(h.routes).toEqual([ROUTE1]);
    expect(h.tts.spoken).toEqual(['沿走廊直行 5 米。']); // 不重播概要
    expect(h.instructions.map((i) => i?.text)).toEqual(['沿走廊直行 5 米。']);
    expect(h.pipeline.calls).toEqual(['startNavigating']);

    // 没有概要窗口：定位立即可上报
    h.controller.handleEstimate(ESTIMATE_RELIABLE);
    await flush();
    expect(updateRequests()).toHaveLength(1);
  });

  it('overviewPlayed + transitionClip：本地音频播完才播首条指令（串行不重叠）', async () => {
    const h = makeHarness();
    h.clips.hold = true;
    await h.controller.createSession(
      { placeId: 'p1', name: '图书馆', floorId: '1' },
      ORIGIN,
      'blind',
      { overviewPlayed: true, transitionClip: 'nav-start' },
    );

    // 本地音频先播；首条指令排在它后面，等音频结束才播
    expect(h.clips.played).toEqual(['nav-start']);
    expect(h.tts.spoken).toEqual([]);
    h.clips.stop(); // 模拟 nav-start.mp3 播完
    await flush();
    expect(h.tts.spoken).toEqual(['沿走廊直行 5 米。']);
    expect(h.instructions.map((i) => i?.text)).toEqual(['沿走廊直行 5 米。']);
    expect(h.pipeline.calls).toEqual(['startNavigating']);
  });

  it('概要点播未结束：首条指令不播、HUD 不写、定位不上报；队列排空后同时开始', async () => {
    tts.hold = true; // 概要挂起，模拟正在播报
    const h = makeHarness({ overview: '全程约 5 米，直行后左转。' });
    await h.controller.createSession({ placeId: 'p1', name: '图书馆', floorId: '1' }, ORIGIN);

    expect(h.tts.spoken).toEqual(['全程约 5 米，直行后左转。']);
    expect(h.instructions).toEqual([]); // 屏幕上仍是「正在播报路线概要」占位
    expect(h.pipeline.calls).toEqual([]); // 概要未播完不开始逐步引导

    h.tts.stop(); // 语音队列排空
    await flush();

    expect(h.tts.spoken).toEqual(['全程约 5 米，直行后左转。', '沿走廊直行 5 米。']);
    expect(h.instructions.map((i) => i?.text)).toEqual(['沿走廊直行 5 米。']); // 与语音同时出现
    expect(h.pipeline.calls).toEqual(['startNavigating']);
  });

  it('概要播报期间冻结定位上报；放行后才恢复上报', async () => {
    tts.hold = true; // 概要挂起，模拟正在播报
    const h = makeHarness({ overview: '全程约 5 米，直行后左转。' });
    await h.controller.createSession({ placeId: 'p1', name: '图书馆', floorId: '1' }, ORIGIN);

    // 概要窗口内：standby 拍的估计不得上报（否则服务端 turn/safety 会打断概要）
    h.controller.handleEstimate(ESTIMATE_RELIABLE);
    h.controller.reportUnavailable();
    await flush();
    expect(updateRequests()).toHaveLength(0);

    h.tts.stop(); // 概要播完
    await flush();
    h.controller.handleEstimate(ESTIMATE_RELIABLE);
    await flush();
    expect(updateRequests()).toHaveLength(1);
  });

  it('概要被打断不计作播完：不重播首条，但仍开始逐步引导', async () => {
    tts.hold = true;
    const h = makeHarness({ overview: '全程约 5 米，直行后左转。' });
    await h.controller.createSession({ placeId: 'p1', name: '图书馆', floorId: '1' }, ORIGIN);

    h.speech.enqueue('前方路口请左转。', 'turn', null, 'test:turn'); // 抢占概要
    await flush();
    tts.settleAll(); // 抢占后的转向播完，队列安静
    await flush();

    expect(h.instructions).toEqual([]); // 打断不能当作「概要播完」→ 不写/不播首条
    expect(h.pipeline.calls).toEqual(['startNavigating']); // 但引导要继续
  });

  it('并发创建合并为一次 API-06（standby 多拍不产生双会话/双概要）', async () => {
    const h = makeHarness();
    const [a, b] = await Promise.all([
      h.controller.createSession({ placeId: 'p1', name: '图书馆', floorId: '1' }, ORIGIN),
      h.controller.createSession({ placeId: 'p1', name: '图书馆', floorId: '1' }, ORIGIN),
    ]);

    expect(createRequests()).toHaveLength(1);
    expect(a.sessionId).toBe(b.sessionId);
    expect(a).toBe(b);
  });

  it('引擎无响应：队列看门狗先放手，仍切首条指令并恢复上报（stalled）', async () => {
    tts.hold = true;
    tts.ignoreStop = true; // 播放器/引擎都不回应
    const h = makeHarness({ overview: '全程约 5 米，直行后左转。' });
    await h.controller.createSession({ placeId: 'p1', name: '图书馆', floorId: '1' }, ORIGIN);

    await new Promise((resolve) => setTimeout(resolve, 80)); // 队列看门狗 30ms 先放手

    expect(h.instructions.map((i) => i?.text)).toEqual(['沿走廊直行 5 米。']);
    expect(h.pipeline.calls).toEqual(['startNavigating']);

    h.controller.handleEstimate(ESTIMATE_RELIABLE);
    await flush();
    expect(updateRequests()).toHaveLength(1);

    h.tts.settleAll();
    await flush();
    expect(h.instructions.map((i) => i?.text)).toEqual(['沿走廊直行 5 米。']); // 不重复补播
  });

  it('概要窗口超时兜底：引擎无响应也切首条指令并恢复上报', async () => {
    tts.hold = true;
    tts.ignoreStop = true; // 引擎既不结束也不响应 stop
    const h = makeHarness({ overview: '全程约 5 米，直行后左转。', overviewMaxWaitMs: 10 });
    await h.controller.createSession({ placeId: 'p1', name: '图书馆', floorId: '1' }, ORIGIN);

    expect(h.instructions).toEqual([]);
    expect(h.pipeline.calls).toEqual([]);

    await new Promise((resolve) => setTimeout(resolve, 30)); // 等概要窗口超时

    expect(h.instructions.map((i) => i?.text)).toEqual(['沿走廊直行 5 米。']); // HUD 不再停占位
    expect(h.pipeline.calls).toEqual(['startNavigating']);

    h.controller.handleEstimate(ESTIMATE_RELIABLE);
    await flush();
    expect(updateRequests()).toHaveLength(1); // 上报恢复

    h.tts.settleAll(); // 收尾：队列排空后不得再重复补播（窗口只放行一次）
    await flush();
    expect(h.instructions.map((i) => i?.text)).toEqual(['沿走廊直行 5 米。']);
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

  it('创建收到永久 4xx：清空幂等暂存，重试携带新参数而非旧目的地', async () => {
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
          replyError(options, 422, 'ROUTE_NOT_FOUND');
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

    // 用户改选目的地后重试：必须使用新 body（p2）与新幂等键，不得复用旧目的地
    await h.controller.createSession({ placeId: 'p2', name: '食堂', floorId: '2' }, ORIGIN);

    const [firstReq, secondReq] = createRequests();
    expect(firstReq.header?.['Idempotency-Key']).not.toBe(secondReq.header?.['Idempotency-Key']);
    expect((secondReq.data as Record<string, unknown>).destinationId).toBe('p2');
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
    expect(h.clips.played).toContain('nav-relocalizing');

    // 第二拍仍 recovering：新 instructionId 但不重复播报
    h.controller.handleEstimate({ ...ESTIMATE_RELIABLE, estimateId: 'loc-2' });
    await flush();
    expect(h.clips.played.filter((c) => c === 'nav-relocalizing')).toHaveLength(1);
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

    expect(h.clips.played[h.clips.played.length - 1]).toBe('nav-arrived-near');
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
    expect(h.clips.played).toContain('nav-map-updated');
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
    expect(h.clips.played).toContain('nav-session-invalid');
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

    expect(h.controller.state).toBe('recovering'); // 本地暂停，不继续旧方向
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
    expect(h.clips.played.filter((c) => c === 'nav-relocalizing')).toHaveLength(2);
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
