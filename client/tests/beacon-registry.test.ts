/**
 * BeaconRegistry：三元组解析、大小写不敏感、未命中丢弃、uuid 去重。
 * mock 数据使用真实信标（uuid B9407F30-...，major=楼层，minor=1010x 格式）。
 */
import { BeaconRegistry } from '../core/ble/beacon-registry';
import type { MapBeaconEntry } from '../typings/api';
import { REAL_UUID, realBeacon } from './fixtures/real-map';

const UUID = REAL_UUID;

describe('BeaconRegistry', () => {
  const entries: MapBeaconEntry[] = [
    realBeacon('BK-01-001'),
    realBeacon('BK-01-004'),
    // 2 层信标（major=2）
    realBeacon('BK-02-001'),
    // 真实数据无缺三元组信标：保留一条手工样本覆盖健壮性分支
    { beaconId: 'BK-NO-TRIPLET', floorId: '1', x: 0, y: 0, uuid: null, major: null, minor: null },
  ];
  const registry = new BeaconRegistry(entries);

  test('三元组命中解析 beaconId', () => {
    expect(registry.resolve(UUID, 1, 10101)).toBe('BK-01-001');
    expect(registry.resolve(UUID, 1, 10104)).toBe('BK-01-004');
    expect(registry.resolve(UUID, 2, 10145)).toBe('BK-02-001');
  });

  test('uuid 大小写不敏感且容忍首尾空白', () => {
    expect(registry.resolve(UUID.toLowerCase(), 1, 10101)).toBe('BK-01-001');
    expect(registry.resolve(`  ${UUID.toLowerCase()}  `, 1, 10104)).toBe('BK-01-004');
  });

  test('未命中返回 null（调用方丢弃，不虚构）', () => {
    expect(registry.resolve(UUID, 1, 99999)).toBeNull();
    expect(registry.resolve('other-uuid', 1, 10101)).toBeNull();
  });

  test('scanUuids 去重且小写', () => {
    expect(registry.scanUuids).toEqual([UUID.toLowerCase()]);
  });

  test('缺三元组的信标不入解析表', () => {
    expect(registry.resolvableCount).toBe(3);
  });
});
