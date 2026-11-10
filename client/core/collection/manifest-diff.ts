/**
 * 入库前对账：本地记录 vs 服务端 manifest（fpId + collectedAt + 内容哈希）。
 *
 * 只做差异计算，不弹窗、不改数据；页面据结果显式提示，
 * 保证「库中有、本地无」这类差异永远可见、不被静默忽略。
 */
import type { FpRecord } from './types';
import { recordContentHash } from './tombstones';

export interface ManifestItem {
  fpId: string;
  collectedAt: string;
  floorId: string;
  contentSha256: string;
}

export interface ManifestDiff {
  /** 两侧都有且内容一致。 */
  same: number;
  /** 两侧都有但内容哈希不同（同键被改写）。 */
  changed: number;
  /** 仅本地有（普通增量，入库后新增）。 */
  localOnly: number;
  /** 仅服务端有：本地已不存在，必须显式确认后才生成墓碑。 */
  dbOnly: number;
  dbOnlyItems: ManifestItem[];
  changedItems: ManifestItem[];
}

function key(fpId: string, collectedAt: string): string {
  return `${fpId}|${collectedAt}`;
}

export function diffManifest(
  local: Pick<FpRecord, 'fpId' | 'collectedAt' | 'floor' | 'coordinates' | 'beacons'>[],
  items: ManifestItem[],
): ManifestDiff {
  const localByKey = new Map(
    local.map((record) => [key(record.fpId, record.collectedAt), record]),
  );
  const serverKeys = new Set<string>();
  const diff: ManifestDiff = {
    same: 0,
    changed: 0,
    localOnly: 0,
    dbOnly: 0,
    dbOnlyItems: [],
    changedItems: [],
  };
  for (const item of items) {
    const k = key(item.fpId, item.collectedAt);
    serverKeys.add(k);
    const record = localByKey.get(k);
    if (record === undefined) {
      diff.dbOnly += 1;
      diff.dbOnlyItems.push(item);
    } else if (recordContentHash(record) === item.contentSha256) {
      diff.same += 1;
    } else {
      diff.changed += 1;
      diff.changedItems.push(item);
    }
  }
  for (const k of localByKey.keys()) {
    if (!serverKeys.has(k)) {
      diff.localOnly += 1;
    }
  }
  return diff;
}
