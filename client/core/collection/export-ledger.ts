/**
 * 指纹导出台账：记录哪些 (fpId, collectedAt) 已成功导出到文件。
 *
 * 入库按钮只允许上传「已导出」的数据：导出是可审计的显式动作，
 * 未导出的记录若被自动导出并入库，现场误操作将无法在本地留痕。
 * 因此这里只做记录，不触发导出。
 *
 * 纯逻辑 + kv，不依赖页面；记录 key 与后端合并键一致。
 */
import { getJson, setJson, remove } from '../../utils/kv';
import type { FpDeletion, FpRecord } from './types';

export const FP_EXPORT_LEDGER_KEY = 'fp:export:v1';

interface LedgerData {
  version: 1;
  /** 最近一次成功导出时间（ISO）。 */
  exportedAt: string;
  /** 已导出记录的 `fpId|collectedAt` 集合。 */
  keys: string[];
  /** 已导出墓碑的 `fpId|collectedAt` 集合（1.2.0 删除语义）。 */
  deletionKeys?: string[];
}

/** 导出/入库共用的记录唯一键（与后端 (fpId, collectedAt) 合并键一致）。 */
export function recordKey(record: Pick<FpRecord, 'fpId' | 'collectedAt'>): string {
  return `${record.fpId}|${record.collectedAt}`;
}

function load(): LedgerData {
  const stored = getJson<LedgerData>(FP_EXPORT_LEDGER_KEY);
  if (stored === null || stored.version !== 1 || !Array.isArray(stored.keys)) {
    return { version: 1, exportedAt: '', keys: [] };
  }
  return stored;
}

/** 已导出记录键集合。 */
export function exportedKeySet(): Set<string> {
  return new Set(load().keys);
}

/** 标记这批记录已成功导出；返回是否写入持久层失败（false=仅内存）。 */
export function markRecordsExported(records: FpRecord[]): boolean {
  const data = load();
  const keys = new Set(data.keys);
  for (const record of records) {
    keys.add(recordKey(record));
  }
  return setJson(FP_EXPORT_LEDGER_KEY, {
    version: 1,
    exportedAt: new Date().toISOString(),
    keys: [...keys],
    deletionKeys: data.deletionKeys ?? [],
  } satisfies LedgerData);
}

/** 已导出的墓碑键集合。 */
export function exportedDeletionKeySet(): Set<string> {
  return new Set(load().deletionKeys ?? []);
}

/** 标记墓碑已随导出文件落盘（入库只接受已导出的删除）。 */
export function markDeletionsExported(items: FpDeletion[]): boolean {
  const data = load();
  const keys = new Set(data.deletionKeys ?? []);
  for (const item of items) {
    keys.add(recordKey(item));
  }
  return setJson(FP_EXPORT_LEDGER_KEY, {
    version: 1,
    exportedAt: new Date().toISOString(),
    keys: data.keys,
    deletionKeys: [...keys],
  } satisfies LedgerData);
}

/** 尚未导出的墓碑（保持原始顺序）。 */
export function listUnexportedDeletions(items: FpDeletion[]): FpDeletion[] {
  const exported = exportedDeletionKeySet();
  return items.filter((item) => !exported.has(recordKey(item)));
}

/** 尚未导出的墓碑条数。 */
export function countUnexportedDeletions(items: FpDeletion[]): number {
  return listUnexportedDeletions(items).length;
}

/** 清空台账（联调/重置用；不影响已写入磁盘的导出文件）。 */
export function clearExportedLedger(): void {
  remove(FP_EXPORT_LEDGER_KEY);
}

/** 尚未导出的记录（保持原始顺序）。 */
export function listUnexported(records: FpRecord[]): FpRecord[] {
  const exported = exportedKeySet();
  return records.filter((record) => !exported.has(recordKey(record)));
}

/** 尚未导出的记录条数。 */
export function countUnexported(records: FpRecord[]): number {
  return listUnexported(records).length;
}
