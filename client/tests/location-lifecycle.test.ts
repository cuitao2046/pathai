/** Regression tests for cancelled requests and stale starts. */
import { LocationPipeline } from '../core/position/location-pipeline';
import { ScanWindowAggregator } from '../core/ble/scan-window';
import { BeaconRegistry } from '../core/ble/beacon-registry';
import type { ApiClient } from '../core/http/api-client';

beforeEach(() => jest.useFakeTimers());
afterEach(() => { jest.restoreAllMocks(); jest.clearAllTimers(); jest.useRealTimers(); });

test('a request completed after stop cannot deliver a position', async () => {
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
  expect(onEstimate).not.toHaveBeenCalled();
});

test('an old start completion cannot schedule a second loop after restart', async () => {
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
  expect(jest.getTimerCount()).toBe(1);
});

test('concurrent manual ticks share one request and leave one timer', async () => {
  jest.spyOn(ScanWindowAggregator.prototype, 'snapshot').mockReturnValue([{ beaconId: 'b', rssi: -60, observedAtMs: Date.now(), n: 1 }]);
  let complete!: (response: unknown) => void;
  const request = jest.fn(() => new Promise(resolve => { complete = resolve; }));
  const pipeline = new LocationPipeline({ client: { request } as unknown as ApiClient,
    map: { siteId: 's', requireVersion: () => 'v', beaconRegistry: new BeaconRegistry([]) },
    source: { start: async () => {}, stop: () => {} }, sequence: { next: () => 1 }, onEstimate: jest.fn(), onUnusable: jest.fn() });
  await pipeline.start();
  const a = pipeline.tick(), b = pipeline.tick();
  expect(request).toHaveBeenCalledTimes(1);
  complete({ data: { quality: 'unreliable' } });
  await Promise.all([a,b]);
  expect(jest.getTimerCount()).toBe(1);
});
