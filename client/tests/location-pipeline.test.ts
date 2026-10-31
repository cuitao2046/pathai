/**
 * LocationPipeline 测试（C6）：聚合→API-05 上报、待命 8s/导航 2.5s 节流、
 * 待命信标不足跳过、导航信标不足 vs 请求失败 → onUnusable、
 * 任意 quality → onEstimate、floorHint 续接、stop 清理。
 * fake timers（保留 setImmediate 用于微任务冲刷）+ 假时钟联动推进。
 */
import { createHttpStack } from '../core/http/api-client';
import { BeaconRegistry } from '../core/ble/beacon-registry';
import type { BeaconSourceLike } from '../core/ble/beacon-source';
import type { BeaconSample } from '../core/ble/scan-window';
import {
  LocationPipeline,
  type LocationMapProvider,
} from '../core/position/location-pipeline';
import { ApiClientError } from '../core/http/errors';
import { resetNowSource, setNowSource } from '../utils/clock';
import { remove } from '../utils/kv';
import type { AuthResponse, MapBeaconEntry, PositionEstimateResponse } from '../typings/api';
import { wxStub } from './setup-wx';
import { REAL_F1_TRIO, REAL_MAP, REAL_UUID } from './fixtures/real-map';

jest.useFakeTimers({ doNotFake: ['setImmediate'] });

const UUID = REAL_UUID;
const AUTH_RESPONSE: AuthResponse = {
  accessToken: 'token-1',
  tokenType: 'Bearer',
  expiresIn: 7200,
  userId: 'anon_1',
};

const CATALOG: MapBeaconEntry[] = REAL_F1_TRIO;

const ESTIMATE: PositionEstimateResponse = {
  estimateId: 'loc_1',
  sequence: 1,
  mapVersion: REAL_MAP.mapVersion,
  position: { x: -25.7, y: -67.9, floorId: '1' },
  confidence: 0.9,
  quality: 'reliable',
  algorithmVersion: 'location-0.1',
  paramsVersion: 'unset',
  observedAt: '2026-10-25T00:00:00Z',
  ignoredBeaconIds: [],
};

/** 手动注入样本的桩信标源。 */
class StubSource implements BeaconSourceLike {
  handler: ((sample: BeaconSample) => void) | null = null;
  startCount = 0;
  stopCount = 0;

  start(handler: (sample: BeaconSample) => void): Promise<void> {
    this.startCount += 1;
    this.handler = handler;
    return Promise.resolve();
  }

  stop(): void {
    this.stopCount += 1;
    this.handler = null;
  }

  /** 注入一轮三个已登记信标样本。 */
  emitAll(atMs: number, rssi = -60): void {
    if (this.handler === null) {
      throw new Error('信标源未启动');
    }
    for (const beacon of CATALOG) {
      this.handler({
        uuid: beacon.uuid as string,
        major: beacon.major as number,
        minor: beacon.minor as number,
        rssi,
        observedAtMs: atMs,
      });
    }
  }
}

const flush = async (): Promise<void> => {
  // 排空微任务队列（fake timers 不介入 Promise）
  for (let i = 0; i < 20; i += 1) {
    await Promise.resolve();
  }
};

describe('LocationPipeline 定位管线', () => {
  let source: StubSource;
  let now: number;
  let estimates: PositionEstimateResponse[];
  let unusable: ApiClientError[];
  let seq: number;

  const map: LocationMapProvider = {
    siteId: 'school-building-01',
    requireVersion: () => REAL_MAP.mapVersion,
    beaconRegistry: new BeaconRegistry(CATALOG),
  };

  const estimateRequests = () =>
    wxStub.__requestLog.filter((r) => r.url.endsWith('/api/v1/locations/estimate'));

  const advance = async (ms: number): Promise<void> => {
    now += ms;
    jest.advanceTimersByTime(ms);
    await flush();
  };

  beforeEach(() => {
    now = 1_700_000_000_000;
    seq = 0;
    source = new StubSource();
    estimates = [];
    unusable = [];
    wxStub.__requestLog.length = 0;
    wxStub.__requestResponder = (options) => {
      if (options.url.endsWith('/api/v1/auth/wechat')) {
        options.success({ statusCode: 200, header: {}, data: AUTH_RESPONSE });
        return;
      }
      if (options.url.endsWith('/api/v1/locations/estimate')) {
        options.success({ statusCode: 200, header: {}, data: ESTIMATE });
        return;
      }
      options.fail({ errMsg: 'request:fail unexpected url' });
    };
    remove('auth:token');
    setNowSource(() => now);
  });

  afterEach(() => {
    resetNowSource();
    jest.clearAllTimers();
  });

  function makePipeline(windowMs = 60_000): LocationPipeline {
    return new LocationPipeline({
      client: createHttpStack('http://localhost:8000').client,
      map,
      source,
      sequence: { next: () => (seq += 1) },
      onEstimate: (e) => estimates.push(e),
      onUnusable: (e) => unusable.push(e),
      windowMs,
    });
  }

  it('启动信标源，按待命节流 8s 首拍上报 reliable', async () => {
    const pipeline = makePipeline();
    await pipeline.start();
    await flush();
    expect(source.startCount).toBe(1);

    source.emitAll(now);
    await advance(2500);
    expect(estimateRequests()).toHaveLength(0); // 待命档未到点

    await advance(5500); // 累计 8s，触发首拍
    expect(estimateRequests()).toHaveLength(1);
    const request = estimateRequests()[0];
    expect(request.method).toBe('POST');
    expect(request.header?.Authorization).toBe('Bearer token-1');
    expect(request.data).toEqual({
      siteId: 'school-building-01',
      mapVersion: REAL_MAP.mapVersion,
      sequence: 1,
      collectedAt: new Date(now).toISOString(),
      floorHint: null,
      beacons: expect.arrayContaining([
        expect.objectContaining({ beaconId: 'BK-01-001', rssi: -60 }),
        expect.objectContaining({ beaconId: 'BK-01-004', rssi: -60 }),
        expect.objectContaining({ beaconId: 'BK-01-005', rssi: -60 }),
      ]),
    });
    expect(estimates).toHaveLength(1);
    expect(estimates[0]).toEqual(ESTIMATE);
    expect(unusable).toHaveLength(0);
  });

  it('setMode(navigating) 后按 2.5s 节流', async () => {
    const pipeline = makePipeline();
    await pipeline.start();
    await flush();

    source.emitAll(now);
    await advance(8000);
    expect(estimateRequests()).toHaveLength(1);

    pipeline.setMode('navigating');
    source.emitAll(now);
    await advance(2500);
    expect(estimateRequests()).toHaveLength(2);
    source.emitAll(now);
    await advance(2500);
    expect(estimateRequests()).toHaveLength(3);
  });

  it('可解析信标不足 3 个：跳过本拍且不消费序号', async () => {
    const pipeline = makePipeline();
    await pipeline.start();
    await flush();

    // 只注入 2 个信标
    source.handler?.({ uuid: UUID, major: 1, minor: 10101, rssi: -60, observedAtMs: now });
    source.handler?.({ uuid: UUID, major: 1, minor: 10104, rssi: -60, observedAtMs: now });
    await advance(8000);
    expect(estimateRequests()).toHaveLength(0);
    expect(seq).toBe(0);
    expect(estimates).toHaveLength(0);
  });

  it('floorHint 取上一拍 position 楼层并随请求续传', async () => {
    const pipeline = makePipeline();
    await pipeline.start();
    await flush();

    source.emitAll(now);
    await advance(8000);
    expect(estimateRequests()).toHaveLength(1);

    source.emitAll(now);
    await advance(8000); // 仍为待命档：2.5s 处不触发，8s 处才第二拍
    const second = estimateRequests()[1];
    expect((second.data as { floorHint: string | null }).floorHint).toBe('1');
  });

  it('quality=unreliable → onEstimate（position 为空，坐标不上报）', async () => {
    wxStub.__requestResponder = (options) => {
      if (options.url.endsWith('/api/v1/auth/wechat')) {
        options.success({ statusCode: 200, header: {}, data: AUTH_RESPONSE });
        return;
      }
      options.success({
        statusCode: 200,
        header: {},
        data: { ...ESTIMATE, quality: 'unreliable', position: null },
      });
    };
    const pipeline = makePipeline();
    await pipeline.start();
    await flush();
    source.emitAll(now);
    await advance(8000);
    expect(unusable).toHaveLength(0);
    expect(estimates).toHaveLength(1);
    expect(estimates[0].quality).toBe('unreliable');
    expect(estimates[0].position).toBeNull();
  });

  it('导航档可解析信标不足 3 个 → onUnusable（LOCATION_UNRELIABLE），不发请求', async () => {
    const pipeline = makePipeline();
    await pipeline.start();
    pipeline.setMode('navigating');
    await flush();

    source.handler?.({ uuid: UUID, major: 1, minor: 10101, rssi: -60, observedAtMs: now });
    source.handler?.({ uuid: UUID, major: 1, minor: 10104, rssi: -60, observedAtMs: now });
    await advance(2500);

    expect(estimateRequests()).toHaveLength(0);
    expect(unusable).toHaveLength(1);
    expect(unusable[0].code).toBe('LOCATION_UNRELIABLE');
    expect(seq).toBe(0); // 未发请求：不消费序号
  });

  it('服务端 409 STALE_LOCATION_BATCH → onUnusable 透出稳定错误码', async () => {
    wxStub.__requestResponder = (options) => {
      if (options.url.endsWith('/api/v1/auth/wechat')) {
        options.success({ statusCode: 200, header: {}, data: AUTH_RESPONSE });
        return;
      }
      options.success({
        statusCode: 409,
        header: {},
        data: {
          error: { code: 'STALE_LOCATION_BATCH', message: '批次过期', retryable: false, details: {} },
          requestId: 'req-9',
        },
      });
    };
    const pipeline = makePipeline();
    await pipeline.start();
    await flush();
    source.emitAll(now);
    await advance(8000);
    expect(unusable).toHaveLength(1);
    expect(unusable[0].code).toBe('STALE_LOCATION_BATCH');
    expect(unusable[0].statusCode).toBe(409);
  });

  it('stop 停止后不再触发拍点，信标源同步停止', async () => {
    const pipeline = makePipeline();
    await pipeline.start();
    await flush();
    pipeline.stop();
    expect(source.stopCount).toBe(1);
    expect(source.handler).toBeNull();
    await advance(8000);
    expect(estimateRequests()).toHaveLength(0);
  });
});
