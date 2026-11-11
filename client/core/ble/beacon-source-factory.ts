/**
 * 信标源工厂：按数据源构造 mock 模拟轨迹 / live 实扫源。
 * 导航、「我在哪儿」单次定位、采集页共用（mock 起点与轨迹构造只此一份）。
 * 数据源一律取 appContext.dataSource（设置页 kv 覆盖 + 重启生效），
 * 不要用 config/env 的 DEFAULT_DATA_SOURCE——它读不到用户覆盖。
 */
import type { DataSource } from '../../config/env';
import { MOCK_POSITION, MOCK_WALK_SPEED_MPS } from '../map/mock-position';
import type { MapBeaconEntry } from '../../typings/api';
import type { BeaconRegistry } from './beacon-registry';
import type { BeaconSourceLike, BleScanStatus } from './beacon-source';
import type { BeaconSample } from './scan-window';
import { LiveBeaconScanner } from './beacon-scanner';
import { MockBeaconSource, type MockWalkPoint } from './mock-beacon-source';

export interface BeaconSourceMap {
  beacons: MapBeaconEntry[];
  beaconRegistry: BeaconRegistry;
  /** 地图包版本（指纹用途）；换版时共享扫描器强制重建，避免沿用旧台账闭包。 */
  mapVersion?: string | null;
}

type SampleHandler = (sample: BeaconSample) => void;

/**
 * live 扫描是全局资源（一个蓝牙适配器、一份系统监听），多页面必须共享：
 * 每页拿到的是门面，start/release 只增删自己的 handler；
 * 最后一个 handler 释放时共享扫描器才真正停止。
 * 直接给每页 new 一个扫描器会导致后开的页面 startBeaconDiscovery 失败，
 * 或先关的页面把另一页的监听一起 off 掉。
 */
export class SharedLiveBeaconSource implements BeaconSourceLike {
  private handler: SampleHandler | null = null;

  constructor(private readonly core: LiveBeaconScanner) {}

  async start(handler: SampleHandler): Promise<void> {
    if (this.handler !== null && this.handler !== handler) {
      this.core.release(this.handler);
    }
    this.handler = handler;
    try {
      await this.core.start(handler);
    } catch (err) {
      if (this.handler === handler) {
        this.handler = null;
      }
      this.core.release(handler);
      throw err;
    }
  }

  stop(): void {
    const handler = this.handler;
    if (handler === null) {
      return;
    }
    this.handler = null;
    this.core.release(handler);
  }

  subscribeStatus(listener: (status: BleScanStatus) => void): () => void {
    return this.core.subscribeStatus(listener);
  }
}

let sharedLive: { key: string; core: LiveBeaconScanner } | null = null;

/**
 * 同一份台账复用同一个扫描器：缓存键 = 地图版本 + UUID 集合。
 * 地图换版（即使 UUID 集合不变）也重建，杜绝 resolve 闭包沿用旧地图。
 */
function getSharedLiveScanner(map: BeaconSourceMap): LiveBeaconScanner {
  const uuids = map.beaconRegistry.scanUuids;
  const key = `${map.mapVersion ?? '-'}|${[...uuids].sort().join(',')}`;
  if (sharedLive === null || sharedLive.key !== key) {
    sharedLive?.core.stop();
    sharedLive = {
      key,
      // resolve 读取 appContext.map 的当前台账（闭包引用 map 对象，换版后仍取最新 registry）
      core: new LiveBeaconScanner(uuids, (uuid, major, minor) =>
        map.beaconRegistry.resolve(uuid, major, minor),
      ),
    };
  }
  return sharedLive.core;
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
    return new SharedLiveBeaconSource(getSharedLiveScanner(map));
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
