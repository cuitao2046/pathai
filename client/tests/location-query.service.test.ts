/**
 * LocationQueryService：「我在哪儿」单次定位查询（意图 query_location）。
 * 观测与注册表使用真实地图数据（REAL_MAP / REAL_F1_TRIO，项目 mock 真实数据约定）；
 * 断言 FE-01 质量策略：reliable 报区域、degraded 只报楼层、失败/超时播安全文案。
 */
import { BeaconRegistry } from '../core/ble/beacon-registry';
import { LiveBeaconScanner } from '../core/ble/beacon-scanner';
import type { BeaconSourceLike } from '../core/ble/beacon-source';
import { SharedLiveBeaconSource, createBeaconSource } from '../core/ble/beacon-source-factory';
import { MockBeaconSource } from '../core/ble/mock-beacon-source';
import type { BeaconSample } from '../core/ble/scan-window';
import type { ApiClient } from '../core/http/api-client';
import { ApiClientError } from '../core/http/errors';
import type { LocationMapProvider } from '../core/position/location-pipeline';
import {
  LocationQueryService,
  SURROUNDINGS_EMPTY_TEXT,
  SURROUNDINGS_UNAVAILABLE_TEXT,
  SURROUNDINGS_UNCERTAIN_TEXT,
  SURROUNDINGS_UNRESOLVED_TEXT,
  UNRESOLVED_TEXT,
  describePositionText,
  describeSurroundingsText,
  mergeSurroundings,
} from '../services/location-query.service';
import type { PositionEstimateResponse, SurroundingElement } from '../typings/api';
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
    paramsVersion: 'unset',
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
  headingDeg?: () => number | null;
}): LocationQueryService {
  return new LocationQueryService({
    client: opts.client,
    map: makeMap(),
    makeSource: () => opts.source,
    floorName: (floorId) => (floorId ? REAL_MAP.floors[floorId]?.displayName : undefined),
    tickIntervalMs: 20,
    timeoutMs: opts.timeoutMs ?? 60_000,
    windowMs: 50,
    headingDeg: opts.headingDeg,
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

  test('quality=unreliable（API 返回成功但无坐标）→ 安全文案且不伪造位置', async () => {
    const source = new FakeSource();
    const { client } = fakeClient(() =>
      estimateOf({ quality: 'unreliable', position: null, confidence: 0 }),
    );
    const svc = makeService({ client, source });

    const text = await drive(svc.query());
    expect(text).toBe(UNRESOLVED_TEXT);
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

  test('查周边：请求带 includeSurroundings 与朝向，播报周边要素而非当前位置', async () => {
    const source = new FakeSource();
    const { client, request } = fakeClient(() =>
      estimateOf({
        quality: 'reliable',
        surroundings: [
          {
            name: '档案室门',
            elementType: 'doorway',
            clockDirection: 11,
            distanceMeters: 3,
            accessibilityNote: '常闭防火门，可能需要推开',
          },
          {
            name: '楼梯口',
            elementType: 'stairs',
            clockDirection: 1,
            distanceMeters: 5,
            accessibilityNote: null,
          },
        ],
      }),
    );
    const svc = makeService({ client, source, headingDeg: () => 90 });

    const text = await drive(svc.query('surroundings'));

    expect(text).toBe('左前方3米有档案室门（常闭防火门，可能需要推开）；右前方5米有楼梯口。');
    expect(text).not.toContain('您当前在'); // 查周边不得播成"当前位置附近"
    const req = request.mock.calls[0][0] as {
      body: { includeSurroundings?: boolean; imu?: { headingDeg?: number } };
    };
    expect(req.body.includeSurroundings).toBe(true);
    expect(req.body.imu?.headingDeg).toBe(90); // 朝向随同一次请求上报
    expect(request).toHaveBeenCalledTimes(1); // 不串第二次请求
  });

  test('查周边：观测一直不足 → 定位失败降级话术（不静默）', async () => {
    const source = new FakeSource();
    source.emitOnStart = false;
    const { client, request } = fakeClient(() => estimateOf({}));
    const svc = makeService({ client, source, timeoutMs: 100 });

    const text = await drive(svc.query('surroundings'));

    expect(text).toBe(SURROUNDINGS_UNRESOLVED_TEXT);
    expect(request).not.toHaveBeenCalled();
    expect(source.stopCount).toBe(1);
  });

  test('查周边：定位接口报错 → 定位失败降级话术', async () => {
    const source = new FakeSource();
    const { client } = fakeClient(() => {
      throw new ApiClientError({
        code: 'LOCATION_UNRELIABLE',
        message: '暂时无法可靠确定当前位置',
        retryable: true,
      });
    });
    const svc = makeService({ client, source });

    const text = await drive(svc.query('surroundings'));

    expect(text).toBe(SURROUNDINGS_UNRESOLVED_TEXT);
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

describe('describeSurroundingsText（查周边话术）', () => {
  const surrounding = (over: Partial<SurroundingElement>) => ({
    name: '档案室门',
    elementType: 'doorway',
    clockDirection: 11,
    distanceMeters: 3,
    accessibilityNote: null,
    ...over,
  });

  test('reliable：按 8 扇区左右方位列周边，最多 3 条，不出现时钟方位', () => {
    const text = describeSurroundingsText(
      estimateOf({
        surroundings: [
          surrounding({}),
          surrounding({ name: '楼梯口', clockDirection: 1, distanceMeters: 5 }),
          surrounding({ name: '饮水机', clockDirection: 12, distanceMeters: 2 }),
          surrounding({ name: '柱子', clockDirection: 7, distanceMeters: 6 }),
        ],
      }),
    );
    expect(text).toBe('左前方3米有档案室门；右前方5米有楼梯口；前方2米有饮水机。');
    expect(text).not.toContain('点钟');
    expect(
      describeSurroundingsText(
        estimateOf({ surroundings: [surrounding({ clockDirection: 7 })] }),
      ),
    ).toBe('左后方3米有档案室门。');
  });

  test('12 个时钟方位统一映射到 8 个左右扇区', () => {
    const label = (clockDirection: number) =>
      describeSurroundingsText(
        estimateOf({ surroundings: [surrounding({ clockDirection })] }),
      );
    expect(label(12)).toBe('前方3米有档案室门。');
    expect(label(1)).toBe('右前方3米有档案室门。');
    expect(label(2)).toBe('右前方3米有档案室门。');
    expect(label(3)).toBe('右方3米有档案室门。');
    expect(label(4)).toBe('右后方3米有档案室门。');
    expect(label(5)).toBe('右后方3米有档案室门。');
    expect(label(6)).toBe('后方3米有档案室门。');
    expect(label(7)).toBe('左后方3米有档案室门。');
    expect(label(8)).toBe('左后方3米有档案室门。');
    expect(label(9)).toBe('左方3米有档案室门。');
    expect(label(10)).toBe('左前方3米有档案室门。');
    expect(label(11)).toBe('左前方3米有档案室门。');
  });

  test('朝向不可用：明确提示无法判断方向，只给距离不猜左右', () => {
    const text = describeSurroundingsText(
      estimateOf({
        surroundings: [
          surrounding({}),
          surrounding({ name: '楼梯口', clockDirection: 1, distanceMeters: 5 }),
        ],
      }),
      { headingAvailable: false },
    );
    expect(text).toBe('暂时无法判断方向，3米有档案室门；5米有楼梯口。');
    expect(text).not.toMatch(/左|右|前方|后方/);
    expect(text).not.toContain('点钟');
  });

  test('reliable 但附近无要素 / 服务端未返回清单 → 分别明确提示', () => {
    expect(describeSurroundingsText(estimateOf({ surroundings: [] }))).toBe(
      SURROUNDINGS_EMPTY_TEXT,
    );
    expect(describeSurroundingsText(estimateOf({ surroundings: null }))).toBe(
      SURROUNDINGS_UNAVAILABLE_TEXT,
    );
    expect(describeSurroundingsText(estimateOf({}))).toBe(SURROUNDINGS_UNAVAILABLE_TEXT);
  });

  test('degraded → 精度不足话术；unreliable/lost → 定位失败话术', () => {
    expect(
      describeSurroundingsText(
        estimateOf({ quality: 'degraded', surroundings: [surrounding({})] }),
      ),
    ).toBe(SURROUNDINGS_UNCERTAIN_TEXT);
    expect(
      describeSurroundingsText(estimateOf({ quality: 'unreliable', position: null })),
    ).toBe(SURROUNDINGS_UNRESOLVED_TEXT);
    expect(
      describeSurroundingsText(estimateOf({ quality: 'lost', position: null })),
    ).toBe(SURROUNDINGS_UNRESOLVED_TEXT);
  });

  test('同名要素同位置合并：保留设施名与风险提示，合并先于截断', () => {
    const text = describeSurroundingsText(
      estimateOf({
        surroundings: [
          surrounding({ name: '电梯', elementType: 'elevator', clockDirection: 4, distanceMeters: 4.0 }),
          surrounding({
            name: '电梯口风险点',
            elementType: 'hazard',
            clockDirection: 4,
            distanceMeters: 4.2,
            accessibilityNote: '电梯口，注意轿厢位置',
          }),
          surrounding({ name: '打印', clockDirection: 10, distanceMeters: 4.8 }),
          surrounding({
            name: '独立柱子',
            elementType: 'pillar',
            clockDirection: 3,
            distanceMeters: 6.0,
            accessibilityNote: '注意绕行',
          }),
        ],
      }),
    );
    expect(text).toBe(
      '右后方4米有电梯（电梯口，注意轿厢位置）；左前方5米有打印；右方6米有独立柱子（注意绕行）。',
    );
    // 未合并时 4 条会截断丢掉“独立柱子”；合并后 3 条名额都能用于不同位置
    expect(text).toContain('独立柱子');
  });

  test('同房间门与门口风险点：同位置同前缀合并，保留门名与风险提示', () => {
    expect(
      describeSurroundingsText(
        estimateOf({
          surroundings: [
            surrounding({ name: '档案室门', clockDirection: 11, distanceMeters: 4.0 }),
            surrounding({
              name: '档案室门口风险点',
              elementType: 'hazard',
              clockDirection: 11,
              distanceMeters: 4.2,
              accessibilityNote: '门口地面湿滑',
            }),
          ],
        }),
      ),
    ).toBe('左前方4米有档案室门（门口地面湿滑）。');
  });

  test('同名但不同扇区或距离差 >1m 不合并（不同实体）', () => {
    const items = [
      surrounding({ name: '电梯', clockDirection: 3, distanceMeters: 4.0 }),
      surrounding({ name: '电梯', clockDirection: 9, distanceMeters: 4.2 }),
      surrounding({ name: '电梯', clockDirection: 3, distanceMeters: 7.0 }),
    ];
    expect(mergeSurroundings(items)).toHaveLength(3);
  });
});

describe('createBeaconSource 工厂', () => {
  test('mock → 模拟轨迹源；live → 实扫源', () => {
    const map = { beacons: REAL_MAP.beacons, beaconRegistry: new BeaconRegistry(REAL_MAP.beacons) };
    expect(createBeaconSource('mock', map)).toBeInstanceOf(MockBeaconSource);
    expect(createBeaconSource('live', map)).toBeInstanceOf(SharedLiveBeaconSource);
  });

  test('共享扫描器缓存键含地图版本：换版即使 UUID 相同也重建', () => {
    const registry = new BeaconRegistry(REAL_MAP.beacons);
    const makeMap = (mapVersion: string) => ({
      beacons: REAL_MAP.beacons,
      beaconRegistry: registry,
      mapVersion,
    });
    // 预热：清掉其他用例在模块级共享槽里留下的扫描器，槽内即为 v-1
    createBeaconSource('live', makeMap('v-1'));
    const stopSpy = jest.spyOn(LiveBeaconScanner.prototype, 'stop');
    try {
      createBeaconSource('live', makeMap('v-1'));
      createBeaconSource('live', makeMap('v-1'));
      expect(stopSpy).not.toHaveBeenCalled(); // 同版本复用同一扫描器
      createBeaconSource('live', makeMap('v-2'));
      expect(stopSpy).toHaveBeenCalledTimes(1); // 换版重建，旧扫描器硬停止
    } finally {
      stopSpy.mockRestore();
    }
  });
});
