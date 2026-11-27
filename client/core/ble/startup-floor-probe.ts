/**
 * 启动楼层探测：用上次已知的信标 UUID 清单在后台实扫一小段时间（默认 2s），
 * 与地图加载并行，只收集原始三元组/RSSI；地图就绪后再由调用方解析成楼层。
 *
 * 为什么需要缓存 UUID：地图包未加载时拿不到 beaconRegistry；把上次成功加载的
 * UUID 清单落到 kv，下一次启动即可在地图下载/解密的同时开始扫描。
 * 首次启动没有缓存、蓝牙未授权或扫描失败时返回空数组，调用方按首层显示。
 */
import { LiveBeaconScanner, type BeaconResolver } from './beacon-scanner';
import type { BeaconSample } from './scan-window';

export interface StartupFloorSample {
  uuid: string;
  major: number;
  minor: number;
  rssi: number;
}

/** 缓存到 kv 的信标三元组目录条目（地图未加载时也能判定命中）。 */
export interface CachedBeaconTriplet {
  uuid: string;
  major: number;
  minor: number;
  beaconId: string;
  floorId: string;
}

/** 用缓存目录构造解析器：uuid 大小写不敏感，未登记返回 null。 */
export function createTripletResolver(
  entries: readonly CachedBeaconTriplet[],
): BeaconResolver {
  const byTriplet = new Map<string, string>();
  for (const entry of entries) {
    byTriplet.set(
      `${entry.uuid.trim().toLowerCase()}|${entry.major}|${entry.minor}`,
      entry.beaconId,
    );
  }
  return (uuid, major, minor) =>
    byTriplet.get(`${uuid.trim().toLowerCase()}|${major}|${minor}`) ?? null;
}

export interface StartupFloorVote {
  floorId: string;
  /** 命中该楼层的样本帧数。 */
  count: number;
  /** 该楼层样本的平均 RSSI（越大越近）。 */
  avgRssi: number;
}

/**
 * 按样本所属楼层投票：帧数最多者胜；并列取平均 RSSI 更强者。
 * 未登记（resolve 返回 null）的样本不参与判层。
 * 注意：`resolve` 必须返回楼层 ID；BeaconRegistry.resolve 返回的是 beaconId，
 * 调用方需先映射到所在楼层（否则会把信标编号当楼层显示）。
 */
export function pickStartupFloor(
  samples: StartupFloorSample[],
  resolve: (uuid: string, major: number, minor: number) => string | null,
): StartupFloorVote | null {
  const votes = new Map<string, { count: number; rssi: number }>();
  for (const sample of samples) {
    const floorId = resolve(sample.uuid, sample.major, sample.minor);
    if (floorId === null) {
      continue;
    }
    const vote = votes.get(floorId) ?? { count: 0, rssi: 0 };
    vote.count += 1;
    vote.rssi += sample.rssi;
    votes.set(floorId, vote);
  }
  let best: StartupFloorVote | null = null;
  for (const [floorId, vote] of votes) {
    const avgRssi = vote.rssi / vote.count;
    if (best === null || vote.count > best.count || (vote.count === best.count && avgRssi > best.avgRssi)) {
      best = { floorId, count: vote.count, avgRssi };
    }
  }
  return best;
}

/**
 * 扫描 `durationMs` 并返回原始样本（不去重、不解析楼层）。
 * 扫描失败（蓝牙关闭/未授权/开发者工具无 BLE）按「无信号」返回空数组。
 */
export async function probeStartupFloorSamples(
  uuids: string[],
  durationMs: number,
  resolveBeacon?: BeaconResolver,
): Promise<StartupFloorSample[]> {
  if (uuids.length === 0) {
    return [];
  }
  const scanner = new LiveBeaconScanner(uuids, resolveBeacon);
  const samples: StartupFloorSample[] = [];
  try {
    await scanner.start((sample: BeaconSample) => {
      samples.push({
        uuid: sample.uuid,
        major: sample.major,
        minor: sample.minor,
        rssi: sample.rssi,
      });
    });
  } catch {
    return [];
  }
  await new Promise<void>((resolve) => {
    setTimeout(resolve, durationMs);
  });
  scanner.stop();
  return samples;
}
