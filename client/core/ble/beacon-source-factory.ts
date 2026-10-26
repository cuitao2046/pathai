/**
 * 信标源工厂：按数据源构造 mock 模拟轨迹 / live 实扫源。
 * 导航页与「我在哪儿」单次定位查询共用（锚点轨迹构造只此一份）。
 * 数据源一律取 appContext.dataSource（设置页 kv 覆盖 + 重启生效），
 * 不要用 config/env 的 DEFAULT_DATA_SOURCE——它读不到用户覆盖。
 */
import type { DataSource } from '../../config/env';
import type { MapBeaconEntry } from '../../typings/api';
import type { BeaconRegistry } from './beacon-registry';
import type { BeaconSourceLike } from './beacon-source';
import { LiveBeaconScanner } from './beacon-scanner';
import { MockBeaconSource } from './mock-beacon-source';

export interface BeaconSourceMap {
  beacons: MapBeaconEntry[];
  beaconRegistry: BeaconRegistry;
}

/** mock 轨迹：以地图包首个信标为锚点的短点列，保证解算可靠。 */
export function createBeaconSource(
  dataSource: DataSource,
  map: BeaconSourceMap,
): BeaconSourceLike {
  if (dataSource !== 'mock') {
    return new LiveBeaconScanner(map.beaconRegistry.scanUuids);
  }
  const beacons = map.beacons.filter((b) => typeof b.uuid === 'string');
  const anchor = beacons[0];
  const path = anchor
    ? [
        { x: anchor.x, y: anchor.y },
        { x: anchor.x + 1, y: anchor.y + 1 },
      ]
    : [
        { x: 0, y: 0 },
        { x: 1, y: 1 },
      ];
  return new MockBeaconSource({
    beacons: map.beacons,
    floorId: anchor?.floorId ?? '1',
    path,
  });
}
