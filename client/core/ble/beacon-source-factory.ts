/**
 * 信标源工厂：按数据源构造 mock 模拟轨迹 / live 实扫源。
 * 导航、「我在哪儿」单次定位查询共用（mock 起点与轨迹构造只此一份）。
 * 数据源一律取 appContext.dataSource（设置页 kv 覆盖 + 重启生效），
 * 不要用 config/env 的 DEFAULT_DATA_SOURCE——它读不到用户覆盖。
 */
import type { DataSource } from '../../config/env';
import { MOCK_POSITION, MOCK_WALK_SPEED_MPS } from '../map/mock-position';
import type { MapBeaconEntry } from '../../typings/api';
import type { BeaconRegistry } from './beacon-registry';
import type { BeaconSourceLike } from './beacon-source';
import { LiveBeaconScanner } from './beacon-scanner';
import { MockBeaconSource, type MockWalkPoint } from './mock-beacon-source';

export interface BeaconSourceMap {
  beacons: MapBeaconEntry[];
  beaconRegistry: BeaconRegistry;
}

/**
 * mock 源：**起点统一为 mock 当前位置**（MOCK_POSITION，与地图待命点同一事实源），
 * 首拍前走 1.5m 短斜线保证窗口内可解析信标 ≥3；进入导航后由地图页
 * `updatePath(route.polyline)` 切换为沿规划路线以 MOCK_WALK_SPEED_MPS 行走。
 */
export function createBeaconSource(
  dataSource: DataSource,
  map: BeaconSourceMap,
): BeaconSourceLike {
  if (dataSource !== 'mock') {
    return new LiveBeaconScanner(map.beaconRegistry.scanUuids);
  }
  const path: MockWalkPoint[] = [
    { x: MOCK_POSITION.x, y: MOCK_POSITION.y },
    { x: MOCK_POSITION.x + 1, y: MOCK_POSITION.y + 1 },
  ];
  return new MockBeaconSource({
    beacons: map.beacons,
    floorId: MOCK_POSITION.floorId,
    path,
    speedMps: MOCK_WALK_SPEED_MPS,
  });
}
