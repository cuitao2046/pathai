/**
 * MockBeaconSource 模拟导航单测（真实地图信标 + fake timers）：
 * 起点=MOCK_POSITION、1.5m/s 步速、updatePath 沿路线行走、按路径点楼层动态换层、走完驻留。
 */
import type { BeaconSample } from '../core/ble/scan-window';
import { MockBeaconSource, type MockWalkPoint } from '../core/ble/mock-beacon-source';
import { MOCK_POSITION, MOCK_WALK_SPEED_MPS } from '../core/map/mock-position';
import { REAL_MAP } from './fixtures/real-map';

jest.useFakeTimers({ doNotFake: ['setImmediate'] });

/** 按三元组从真实地图反查所在楼层（不依赖「major=楼层」的假设）。 */
function floorOf(sample: BeaconSample): string | undefined {
  return REAL_MAP.beacons.find(
    (b) => b.uuid === sample.uuid && b.major === sample.major && b.minor === sample.minor,
  )?.floorId;
}

function makeSource(path: MockWalkPoint[]): {
  source: MockBeaconSource;
  received: BeaconSample[];
} {
  const source = new MockBeaconSource({
    beacons: REAL_MAP.beacons,
    floorId: MOCK_POSITION.floorId,
    path,
    speedMps: MOCK_WALK_SPEED_MPS,
    tickMs: 500,
  });
  const received: BeaconSample[] = [];
  void source.start((sample) => received.push(sample));
  return { source, received };
}

afterEach(() => {
  jest.clearAllTimers();
});

describe('MockBeaconSource 模拟导航', () => {
  test('起点统一为 mock 当前位置，按 1.5m/s 推进（500ms/步=0.75m）', async () => {
    const { source } = makeSource([
      { x: MOCK_POSITION.x, y: MOCK_POSITION.y },
      { x: MOCK_POSITION.x + 4, y: MOCK_POSITION.y },
    ]);
    expect(source.currentPosition).toMatchObject({
      x: MOCK_POSITION.x,
      y: MOCK_POSITION.y,
      floorId: MOCK_POSITION.floorId,
    });

    jest.advanceTimersByTime(500); // 第一拍
    expect(source.currentPosition.x).toBeCloseTo(MOCK_POSITION.x + 0.75, 5);
    expect(source.currentPosition.y).toBeCloseTo(MOCK_POSITION.y, 5);

    jest.advanceTimersByTime(1500); // 再走三拍（累计 4 步 ×0.75=3m）
    expect(source.currentPosition.x).toBeCloseTo(MOCK_POSITION.x + 3, 5);
    expect(MOCK_WALK_SPEED_MPS).toBe(1.5);
  });

  test('观测按真实地图 1 层信标发射（三元组反查楼层）', async () => {
    const { received } = makeSource([
      { x: MOCK_POSITION.x, y: MOCK_POSITION.y },
      { x: MOCK_POSITION.x + 2, y: MOCK_POSITION.y },
    ]);
    jest.advanceTimersByTime(500);
    expect(received.length).toBeGreaterThan(0);
    for (const sample of received) {
      expect(floorOf(sample)).toBe('1'); // 真实地图数据反查该三元组所在楼层
      expect(sample.rssi).toBeGreaterThanOrEqual(-100);
      expect(sample.rssi).toBeLessThanOrEqual(0); // 钳制到契约范围在 scan-window 层完成
    }
  });

  test('沿路线跨层行走：到边界后按新楼层信标组发射（模拟电梯换层）', async () => {
    const { source, received } = makeSource([
      { x: MOCK_POSITION.x, y: MOCK_POSITION.y, floorId: '1' },
      { x: MOCK_POSITION.x + 1, y: MOCK_POSITION.y, floorId: '2' },
    ]);

    jest.advanceTimersByTime(500); // 段内 0.75m，仍在 1 层
    expect(source.currentPosition.floorId).toBe('1');
    expect(received.every((s) => floorOf(s) === '1')).toBe(true);

    received.length = 0;
    jest.advanceTimersByTime(500); // 累计 1.5m > 段长 1m → 进入 2 层段
    expect(source.currentPosition.floorId).toBe('2');
    expect(received.length).toBeGreaterThan(0);
    expect(received.every((s) => floorOf(s) === '2')).toBe(true);
  });

  test('updatePath：路线就绪后切换行走轨迹并从新首点起步', async () => {
    const { source } = makeSource([
      { x: MOCK_POSITION.x, y: MOCK_POSITION.y },
      { x: MOCK_POSITION.x + 1, y: MOCK_POSITION.y },
    ]);
    jest.advanceTimersByTime(500); // 先走 0.75m

    source.updatePath([
      { x: 10, y: 20, floorId: '1' },
      { x: 10, y: 24, floorId: '1' },
    ]);
    expect(source.currentPosition).toMatchObject({ x: 10, y: 20, floorId: '1' });

    jest.advanceTimersByTime(500);
    expect(source.currentPosition.y).toBeCloseTo(20 + 0.75, 5); // 沿新路线继续走
    expect(source.currentPosition.x).toBeCloseTo(10, 5);
  });

  test('走完末点驻留不越界；空轨迹 updatePath 抛错', async () => {
    const { source } = makeSource([
      { x: 0, y: 0, floorId: '1' },
      { x: 0.5, y: 0, floorId: '1' },
    ]);
    jest.advanceTimersByTime(2000); // 远超走完所需时间
    expect(source.currentPosition).toMatchObject({ x: 0.5, y: 0, floorId: '1' });

    expect(() => source.updatePath([])).toThrow('updatePath');
  });
});
