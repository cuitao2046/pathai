/**
 * 定位集成冒烟（C6）：Mock 信标轨迹 → 本地 uvicorn 真进程 API-05 → reliable。
 * 运行方式（需后端已启动且数据库为干净状态）：
 *   PowerShell:  $env:PATHAI_SMOKE='1'; npm test -- tests/smoke-location.int.test.ts
 * 未设置 PATHAI_SMOKE=1 时整体跳过，不影响常规 CI。
 */
import { createHttpStack } from '../core/http/api-client';
import { MockBeaconSource } from '../core/ble/mock-beacon-source';
import { ScanWindowAggregator } from '../core/ble/scan-window';
import { MapService } from '../core/map/map-service';
import { SequenceCounter } from '../core/position/sequence-counter';
import { remove } from '../utils/kv';
import type { PositionEstimateResponse } from '../typings/api';

/** 测试环境进程变量（避免引入 @types/node）。 */
declare const process: { env: Record<string, string | undefined> };

const maybe = process.env.PATHAI_SMOKE === '1' ? it : it.skip;
const API_URL = process.env.PATHAI_API_URL ?? 'http://localhost:8000';

const sleep = (ms: number): Promise<void> =>
  new Promise((resolve) => setTimeout(resolve, ms));

describe('定位集成冒烟（PATHAI_SMOKE=1 且本地后端运行时执行）', () => {
  maybe(
    'mock 轨迹 → API-05 返回 reliable',
    async () => {
      remove('auth:token');
      const stack = createHttpStack(API_URL);
      const map = new MapService({
        client: stack.client,
        siteId: 'school-building-01',
        rewriteDataUrlToLocalApi: true,
      });
      await map.ensureLoaded();

      const aggregator = new ScanWindowAggregator();
      const source = new MockBeaconSource({
        beacons: map.beacons,
        floorId: '1',
        // 真实 1 层信标区（BK-01-001 (-30.25,-75.08) 附近）内走动 2.8m
        path: [
          { x: -31, y: -76 },
          { x: -29, y: -74 },
        ],
        tickMs: 500,
        speedMps: 1.0,
      });
      source.start((sample) => aggregator.push(sample));
      await sleep(2300); // 覆盖一个 2s 窗口
      source.stop();

      const snapshot = aggregator.snapshot(map.beaconRegistry, Date.now());
      expect(snapshot).not.toBeNull();

      const resp = await stack.client.request<PositionEstimateResponse>({
        method: 'POST',
        path: '/api/v1/locations/estimate',
        body: {
          siteId: map.siteId,
          mapVersion: map.requireVersion(),
          sequence: new SequenceCounter().next(),
          collectedAt: new Date().toISOString(),
          floorHint: null,
          beacons: snapshot!.map((o) => ({
            beaconId: o.beaconId,
            rssi: o.rssi,
            observedAt: new Date(o.observedAtMs).toISOString(),
          })),
        },
      });
      expect(resp.statusCode).toBe(200);
      expect(resp.data?.quality).toBe('reliable');
      expect(resp.data?.position?.floorId).toBe('1');
    },
    30_000,
  );
});
