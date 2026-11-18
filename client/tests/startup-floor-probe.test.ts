/**
 * 启动楼层判层单测：无样本保持首层、多数楼层胜出、并列按 RSSI 决胜。
 * 扫描本身依赖 wx BLE，不在单测覆盖；这里只测纯投票逻辑。
 */
import { pickStartupFloor, type StartupFloorSample } from '../core/ble/startup-floor-probe';

const UUID = 'B9407F30-F5F8-466E-AFF9-25556B57FE6D';

/** minor 区间映射楼层：<10000 未登记，1xxxx→1F，2xxxx→2F。 */
function resolve(_uuid: string, _major: number, minor: number): string | null {
  if (minor >= 20000) return '2';
  if (minor >= 10000) return '1';
  return null;
}

function sample(minor: number, rssi: number): StartupFloorSample {
  return { uuid: UUID, major: 1, minor, rssi };
}

describe('pickStartupFloor', () => {
  it('无样本或全部未登记 → null（调用方保持首层）', () => {
    expect(pickStartupFloor([], resolve)).toBeNull();
    expect(pickStartupFloor([sample(10101, -60)], () => null)).toBeNull();
  });

  it('样帧数更多的楼层胜出', () => {
    const winner = pickStartupFloor(
      [sample(10101, -50), sample(10104, -55), sample(20101, -70)],
      resolve,
    );
    expect(winner).toEqual({ floorId: '1', count: 2, avgRssi: -52.5 });
  });

  it('帧数并列时取平均 RSSI 更强的楼层', () => {
    const winner = pickStartupFloor(
      [sample(10101, -80), sample(20101, -55)],
      resolve,
    );
    expect(winner).toEqual({ floorId: '2', count: 1, avgRssi: -55 });
  });
});
