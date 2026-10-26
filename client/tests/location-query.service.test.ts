/**
 * LocationQueryService：「我在哪儿」单次定位查询（意图 query_location）。
 * 观测与注册表使用真实地图数据（REAL_MAP / REAL_F1_TRIO，项目 mock 真实数据约定）；
 * 断言 FE-01 质量策略：reliable 报区域、degraded 只报楼层、失败/超时播安全文案。
 */
import { BeaconRegistry } from '../core/ble/beacon-registry';
import type { BeaconSourceLike } from '../core/ble/beacon-source';
import { LiveBeaconScanner } from '../core/ble/beacon-scanner';
import { createBeaconSource } from '../core/ble/beacon-source-factory';
import { MockBeaconSource } from '../core/ble/mock-beacon-source';
import type { BeaconSample } from '../core/ble/scan-window';
import type { ApiClient } from '../core/http/api-client';
import { ApiClientError } from '../core/http/errors';
import type { LocationMapProvider } from '../core/position/location-pipeline';
import {
  LocationQueryService,
  UNRESOLVED_TEXT,
  describePositionText,
} from '../services/location-query.service';
import type { PositionEstimateResponse } from '../typings/api';
import { REAL_F1_TRIO, REAL_MAP } from './fixtures/real-map';

jest.useFakeTimers({ doNotFake: ['setImmediate'] });

const flush = async (): Promise<void> => {
  for (let i = 0; i < 30; i += 1) {
    await Promise.resolve();
  }
};

const FLOOR_1_NAME = REAL_MAP.floors['1'].displayName;

function makeMap(): LocationMapProvider {
  return {
    siteId: REAL_MAP.siteId,
    requireVersion: () => REAL_MAP.mapVersion,
    beaconRegistry: new BeaconRegistry(REAL_MAP.beacons),
  };
}

function estimateOf(over: Partial<PositionEstimateResponse>): PositionEstimateResponse {
  return {
    estimateId: 'est-1',
    sequence: 1,
    mapVersion: REAL_MAP.mapVersion,
    position: { floorId: '1', x: 10, y: 20 },
    headingDeg: null,
    confidence: 0.97,
    quality: 'reliable',
    matchedArea: null,
    algorithmVersion: 'tri-v1',
    observedAt: '2026-10-26T08:00:00.000Z',
    ignoredBeaconIds: [],
    ...over,
  };
}

/** 假客户端：handler 同步产响应或抛错（统一转为异步 rejection）。 */
function fakeClient(handler: () => PositionEstimateResponse): {
  client: ApiClient;
  request: jest.Mock;
} {
  const request = jest.fn(() =>
    Promise.resolve()
      .then(() => ({
        statusCode: 200,
        data: handler(),
        requestId: null,
        etag: null,
      })),
  );
  return { client: { request } as unknown as ApiClient, request };
}

class FakeSource implements BeaconSourceLike {
  startCount = 0;
  stopCount = 0;
  /** true = 持续供样（模拟真实源的周期发射）；false = 从不供样（超时用例）。 */
  emitOnStart = true;
  private handler: ((sample: BeaconSample) => void) | null = null;
  private timer: ReturnType<typeof setInterval> | null = null;

  async start(handler: (sample: BeaconSample) => void): Promise<void> {
    this.startCount += 1;
    this.handler = handler;
    if (this.emitOnStart) {
      this.emit();
      this.timer = setInterval(() => this.emit(), 10); // 持续供样，任意 tick 时刻窗口都有新鲜观测
    }
  }

  stop(): void {
    this.stopCount += 1;
    this.handler = null;
    if (this.timer !== null) {
      clearInterval(this.timer);
      this.timer = null;
    }
  }

  /** 用真实 1 层三信标合成观测（三元组可被真实注册表解析）。 */
  emit(): void {
    const at = Date.now();
    let rssi = -55;
    for (const beacon of REAL_F1_TRIO) {
      this.handler?.({
        uuid: beacon.uuid as string,
        major: beacon.major as number,
        minor: beacon.minor as number,
        rssi,
        observedAtMs: at,
      });
      rssi -= 5;
    }
  }
}

function makeService(opts: {
  client: ApiClient;
  source: FakeSource;
  timeoutMs?: number;
}): LocationQueryService {
  return new LocationQueryService({
    client: opts.client,
    map: makeMap(),
    makeSource: () => opts.source,
    floorName: (floorId) => (floorId ? REAL_MAP.floors[floorId]?.displayName : undefined),
    tickIntervalMs: 20,
    timeoutMs: opts.timeoutMs ?? 60_000,
    windowMs: 50,
  });
}

/** 推进 fake timers 直到 promise 收敛；返回值或抛出未收敛。 */
async function drive(p: Promise<string | null>): Promise<string | null> {
  let done: string | null | undefined;
  let failed: unknown;
  void p.then(
    (v) => (done = v),
    (e) => {
      failed = e;
      done = 'REJECTED';
    },
  );
  for (let i = 0; i < 60 && done === undefined; i += 1) {
    jest.advanceTimersByTime(25);
    await flush();
  }
  if (failed !== undefined) {
    throw failed;
  }
  if (done === undefined) {
    throw new Error('位置查询未在驱动窗口内收敛');
  }
  return done;
}

afterEach(() => {
  jest.clearAllTimers();
});

describe('LocationQueryService.query', () => {
  test('reliable + 匹配区域 → 播报楼层+地点附近，结束后停止观测源', async () => {
    const source = new FakeSource();
    const { client, request } = fakeClient(() =>
      estimateOf({
        quality: 'reliable',
        matchedArea: { placeId: 'P-LIB', name: '图书馆' },
      }),
    );
    const svc = makeService({ client, source });

    const text = await drive(svc.query());
    expect(text).toBe(`您当前在${FLOOR_1_NAME}图书馆附近。`);
    expect(source.startCount).toBe(1);
    expect(source.stopCount).toBe(1); // finally 必须收观测源
    expect(request).toHaveBeenCalledTimes(1);
    const body = request.mock.calls[0][0] as { path: string };
    expect(body.path).toBe('/api/v1/locations/estimate');
  });

  test('degraded → 仅播报楼层与精度提示，不报精确点位', async () => {
    const source = new FakeSource();
    const { client } = fakeClient(() =>
      estimateOf({
        quality: 'degraded',
        confidence: 0.45,
        matchedArea: { placeId: 'P-LIB', name: '图书馆' },
      }),
    );
    const svc = makeService({ client, source });

    const text = await drive(svc.query());
    expect(text).toBe(`您当前在${FLOOR_1_NAME}，定位精度一般。`);
    expect(text).not.toContain('图书馆');
    expect(source.stopCount).toBe(1);
  });

  test('API 返回 LOCATION_UNRELIABLE → 安全文案且不伪造位置', async () => {
    const source = new FakeSource();
    const err = new ApiClientError({
      code: 'LOCATION_UNRELIABLE',
      message: '暂时无法可靠确定当前位置',
      retryable: true,
    });
    const { client } = fakeClient(() => {
      throw err;
    });
    const svc = makeService({ client, source });

    const text = await drive(svc.query());
    expect(text).toBe(UNRESOLVED_TEXT);
    expect(source.stopCount).toBe(1);
  });

  test('观测一直不足 → 超时兜底返回安全文案并停止源', async () => {
    const source = new FakeSource();
    source.emitOnStart = false; // 窗口永远凑不齐 3 个可解析信标
    const { client, request } = fakeClient(() => estimateOf({}));
    const svc = makeService({ client, source, timeoutMs: 100 });

    const text = await drive(svc.query());
    expect(text).toBe(UNRESOLVED_TEXT);
    expect(request).not.toHaveBeenCalled(); // 从未凑齐观测，不发请求
    expect(source.stopCount).toBe(1);
  });

  test('查询进行中再次查询 → 返回 null（并发被忽略）', async () => {
    const source = new FakeSource();
    source.emitOnStart = false;
    const { client } = fakeClient(() => estimateOf({}));
    const svc = makeService({ client, source, timeoutMs: 60_000 });

    const first = svc.query();
    await flush();
    await expect(svc.query()).resolves.toBeNull();

    jest.advanceTimersByTime(60_000); // 让首个查询走超时收尾
    await flush();
    await expect(first).resolves.toBe(UNRESOLVED_TEXT);
    expect(source.stopCount).toBe(1);
  });
});

describe('describePositionText（FE-01 质量策略纯函数）', () => {
  const floorName = (id?: string | null) => (id === '1' ? '一楼' : undefined);

  test('reliable：区域优先，无区域退化到楼层，全无则确认文案', () => {
    expect(
      describePositionText(
        estimateOf({ matchedArea: { placeId: 'p', name: '生物工作室' } }),
        floorName,
      ),
    ).toBe('您当前在一楼生物工作室附近。');
    expect(describePositionText(estimateOf({}), floorName)).toBe('您当前在一楼。');
    expect(
      describePositionText(
        estimateOf({ position: null, matchedArea: null }),
        floorName,
      ),
    ).toBe('当前位置已确定。');
    expect(
      describePositionText(
        estimateOf({ position: null, matchedArea: { placeId: 'p', name: '实验室' } }),
        floorName,
      ),
    ).toBe('您当前在实验室附近。');
  });

  test('degraded：只报楼层；楼层未知则报区域级文案', () => {
    expect(
      describePositionText(
        estimateOf({ quality: 'degraded', matchedArea: { placeId: 'p', name: '实验室' } }),
        floorName,
      ),
    ).toBe('您当前在一楼，定位精度一般。');
    expect(
      describePositionText(estimateOf({ quality: 'degraded', position: null }), floorName),
    ).toBe('已确认所在区域，定位精度一般。');
  });
});

describe('createBeaconSource 工厂', () => {
  test('mock → 模拟轨迹源；live → 实扫源', () => {
    const map = { beacons: REAL_MAP.beacons, beaconRegistry: new BeaconRegistry(REAL_MAP.beacons) };
    expect(createBeaconSource('mock', map)).toBeInstanceOf(MockBeaconSource);
    expect(createBeaconSource('live', map)).toBeInstanceOf(LiveBeaconScanner);
  });
});
