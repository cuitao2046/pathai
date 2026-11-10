/**
 * 指纹删除墓碑：本地删除 → 持久化墓碑 → 导出 deletions[] → 服务端生成新版本。
 *
 * 原则：
 * - 删除必须显式发生（记录管理的「删除」「清空该点」），绝不按「本批未出现」推断；
 * - 墓碑内容哈希与服务端同口径（fpId|collectedAt|floor|x,y|major:minor:rssi），
 *   服务端据此确认删的就是客户端以为的那条记录，防止跨版本误删；
 * - 纯逻辑 + kv，不依赖页面，可 jest 直测。
 */
import { getJson, setJson } from '../../utils/kv';
import { sha256 } from '../../utils/sha256';
import type { FpDeletion, FpRecord } from './types';

export const FP_TOMBSTONE_KEY = 'fp:tombstones:v1';

interface TombstoneData {
  version: 1;
  items: FpDeletion[];
}

function load(): TombstoneData {
  const stored = getJson<TombstoneData>(FP_TOMBSTONE_KEY);
  if (stored === null || stored.version !== 1 || !Array.isArray(stored.items)) {
    return { version: 1, items: [] };
  }
  return stored;
}

/** 墓碑唯一键（与后端 (fpId, collectedAt) 合并键一致）。 */
export function tombstoneKey(item: Pick<FpDeletion, 'fpId' | 'collectedAt'>): string {
  return `${item.fpId}|${item.collectedAt}`;
}

/**
 * 记录内容哈希（与后端 fingerprint_library.record_sha 完全同口径）。
 * 只包含参与向量语义的字段，坐标 2 位小数、RSSI 1 位小数。
 */
export function recordContentHash(
  record: Pick<FpRecord, 'fpId' | 'collectedAt' | 'floor' | 'coordinates' | 'beacons'>,
): string {
  const beacons = [...record.beacons]
    .sort((a, b) => a.major - b.major || a.minor - b.minor)
    .map((b) => `${b.major}:${b.minor}:${b.rssi.toFixed(1)}`)
    .join(',');
  const [x, y] = record.coordinates;
  return sha256(
    [
      record.fpId,
      record.collectedAt,
      String(Math.trunc(record.floor)),
      `${x.toFixed(2)},${y.toFixed(2)}`,
      beacons,
    ].join('|'),
  );
}

/** 由本地记录构造墓碑（删除入口统一走这里）。 */
export function buildTombstone(record: FpRecord): FpDeletion {
  return {
    fpId: record.fpId,
    collectedAt: record.collectedAt,
    contentSha256: recordContentHash(record),
    floor: String(Math.trunc(record.floor)),
  };
}

/** 当前全部墓碑（按写入顺序）。 */
export function listTombstones(): FpDeletion[] {
  return [...load().items];
}

/** 追加墓碑；同键以最新一条为准。返回是否持久化成功。 */
export function addTombstones(items: FpDeletion[]): boolean {
  if (items.length === 0) {
    return true;
  }
  const data = load();
  const map = new Map(data.items.map((item) => [tombstoneKey(item), item]));
  for (const item of items) {
    map.set(tombstoneKey(item), item);
  }
  return setJson(FP_TOMBSTONE_KEY, {
    version: 1,
    items: [...map.values()],
  } satisfies TombstoneData);
}

/** 入库成功后移除已应用的墓碑（避免重复上传）。 */
export function removeTombstones(keys: string[]): void {
  if (keys.length === 0) {
    return;
  }
  const drop = new Set(keys);
  const data = load();
  const items = data.items.filter((item) => !drop.has(tombstoneKey(item)));
  if (items.length !== data.items.length) {
    setJson(FP_TOMBSTONE_KEY, { version: 1, items } satisfies TombstoneData);
  }
}

/** 清空墓碑（联调/重置用）。 */
export function clearTombstones(): void {
  setJson(FP_TOMBSTONE_KEY, { version: 1, items: [] } satisfies TombstoneData);
}
