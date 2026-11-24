/** Review reproductions: these assertions document current defects, not desired behavior. */
import { LocationPipeline } from '../core/position/location-pipeline';
import { ScanWindowAggregator } from '../core/ble/scan-window';
import { BeaconRegistry } from '../core/ble/beacon-registry';
import type { ApiClient } from '../core/http/api-client';

beforeEach(() => jest.useFakeTimers());
afterEach(() => { jest.restoreAllMocks(); jest.clearAllTimers(); jest.useRealTimers(); });

test('review: a request completed after stop still delivers a position', async () => {
  jest.spyOn(ScanWindowAggregator.prototype, 'snapshot').mockReturnValue([
    { beaconId: 'b', rssi: -60, observedAtMs: Date.now(), n: 1 },
  ]);
  let complete!: (response: unknown) => void;
  const onEstimate = jest.fn();
  const pipeline = new LocationPipeline({
    client: { request: () => new Promise(resolve => { complete = resolve; }) } as unknown as ApiClient,
    map: { siteId: 's', requireVersion: () => 'v', beaconRegistry: new BeaconRegistry([]) },
    source: { start: async () => {}, stop: () => {} },
    sequence: { next: () => 1 }, onEstimate, onUnusable: jest.fn(),
  });
  await pipeline.start();
  const pending = pipeline.tick();
  pipeline.stop();
  complete({ data: { quality: 'reliable', confidence: 0.9, position: { floorId: '1', x: 1, y: 2 } } });
  await pending;
  expect(onEstimate).toHaveBeenCalledTimes(1);
});

test('review: an old start completion schedules a second loop after restart', async () => {
  let release!: () => void;
  const starting = new Promise<void>(resolve => { release = resolve; });
  const start = jest.fn().mockReturnValueOnce(starting).mockResolvedValue(undefined);
  const pipeline = new LocationPipeline({
    client: {} as ApiClient,
    map: { siteId: 's', requireVersion: () => 'v', beaconRegistry: new BeaconRegistry([]) },
    source: { start, stop: () => {} }, sequence: { next: () => 1 },
    onEstimate: jest.fn(), onUnusable: jest.fn(),
  });
  const oldStart = pipeline.start();
  pipeline.stop();
  await pipeline.start();
  release();
  await oldStart;
  expect(jest.getTimerCount()).toBe(2);
});
