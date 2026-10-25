/**
 * BeaconRegistry：三元组解析、大小写不敏感、未命中丢弃、uuid 去重。
 */
import { BeaconRegistry } from '../core/ble/beacon-registry';
import type { MapBeaconEntry } from '../typings/api';

const UUID = 'FDA50693-A4E2-4FB1-AFCF-C6EB07647825';

function beacon(overrides: Partial<MapBeaconEntry>): MapBeaconEntry {
  return {
    beaconId: 'BK-X',
    floorId: '1',
    x: 0,
    y: 0,
    uuid: UUID,
    major: 1,
    minor: 1,
    ...overrides,
  };
}

describe('BeaconRegistry', () => {
  const entries: MapBeaconEntry[] = [
    beacon({ beaconId: 'BK-1', major: 1, minor: 1 }),
    beacon({ beaconId: 'BK-2', major: 1, minor: 2 }),
    beacon({ beaconId: 'BK-3', floorId: '2', major: 2, minor: 1 }),
    beacon({ beaconId: 'BK-NO-TRIPLET', uuid: null, major: null, minor: null }),
  ];
  const registry = new BeaconRegistry(entries);

  test('三元组命中解析 beaconId', () => {
    expect(registry.resolve(UUID, 1, 2)).toBe('BK-2');
    expect(registry.resolve(UUID, 2, 1)).toBe('BK-3');
  });

  test('uuid 大小写不敏感且容忍首尾空白', () => {
    expect(registry.resolve(UUID.toLowerCase(), 1, 1)).toBe('BK-1');
    expect(registry.resolve(`  ${UUID.toLowerCase()}  `, 1, 1)).toBe('BK-1');
  });

  test('未命中返回 null（调用方丢弃，不虚构）', () => {
    expect(registry.resolve(UUID, 9, 9)).toBeNull();
    expect(registry.resolve('other-uuid', 1, 1)).toBeNull();
  });

  test('scanUuids 去重且小写', () => {
    expect(registry.scanUuids).toEqual([UUID.toLowerCase()]);
  });

  test('缺三元组的信标不入解析表', () => {
    expect(registry.resolvableCount).toBe(3);
  });
});
