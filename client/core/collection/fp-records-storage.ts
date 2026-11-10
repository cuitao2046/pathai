/**
 * 网格指纹记录分片持久化。
 *
 * 背景：微信 storage 单个 key 上限 1MB。一条 FpRecord 约 3KB（25 枚信标），
 * 单层可采 572 点约 1.8MB；若全量数组写在一个 key 里，约 340 条后
 * `wx.setStorageSync` 就抛异常，kv 只把数据留在本次会话内存，
 * 重启后记录回退到最后一次成功写入的条数（界面「已采集」突然变少）。
 *
 * 方案：按固定条数切片写多个 key（每片约 370KB），最后原子写索引；
 * 任何一片失败都不切换索引，并清理本次已写分片，保证读到的始终是完整快照。
 * 旧版本的单 key（col:fp:v1）继续可读，成功写入新格式后删除。
 */
import { getJson, remove, setJson } from '../../utils/kv';
import type { FpRecord } from './types';

/** 每片记录数：120 × ~3.1KB ≈ 370KB，远低于单 key 1MB 上限。 */
export const FP_RECORDS_CHUNK = 120;

const INDEX_VERSION = 2;
export const FP_RECORDS_INDEX_KEY = 'col:fp:v1:index';

interface FpRecordsIndex {
  version: number;
  gen: string;
  chunks: number;
}

let generationSeq = 0;

function nextGeneration(): string {
  generationSeq += 1;
  return `${Date.now().toString(36)}${generationSeq.toString(36)}`;
}

export function fpRecordsChunkKey(gen: string, index: number): string {
  return `${FP_RECORDS_INDEX_KEY}:${gen}:${index}`;
}

function isValidIndex(value: unknown): value is FpRecordsIndex {
  if (typeof value !== 'object' || value === null) {
    return false;
  }
  const idx = value as Partial<FpRecordsIndex>;
  return (
    idx.version === INDEX_VERSION &&
    typeof idx.gen === 'string' &&
    idx.gen.length > 0 &&
    typeof idx.chunks === 'number' &&
    Number.isInteger(idx.chunks) &&
    idx.chunks >= 0
  );
}

/** 读取：优先分片索引；索引缺失/分片损坏时回退旧单 key。 */
export function loadFpRecords(legacyKey: string): FpRecord[] {
  const index = getJson<unknown>(FP_RECORDS_INDEX_KEY);
  if (isValidIndex(index)) {
    const out: FpRecord[] = [];
    for (let i = 0; i < index.chunks; i += 1) {
      const part = getJson<unknown>(fpRecordsChunkKey(index.gen, i));
      if (!Array.isArray(part)) {
        // 分片缺失视为损坏：读数不能拼半份，回退旧单 key（若有）
        return readLegacy(legacyKey);
      }
      out.push(...(part as FpRecord[]));
    }
    return out;
  }
  return readLegacy(legacyKey);
}

function readLegacy(legacyKey: string): FpRecord[] {
  const legacy = getJson<unknown>(legacyKey);
  return Array.isArray(legacy) ? (legacy as FpRecord[]) : [];
}

/**
 * 全量写入（分片 + 原子索引）。返回 false 表示持久层不可用，
 * 调用方必须回滚本次新增并提示现场先导出/清理，不能当作已保存。
 */
export function saveFpRecords(
  legacyKey: string,
  records: FpRecord[],
  gen: string = nextGeneration(),
): boolean {
  const chunks = Math.ceil(records.length / FP_RECORDS_CHUNK);
  const previous = getJson<unknown>(FP_RECORDS_INDEX_KEY);
  const written: string[] = [];

  for (let i = 0; i < chunks; i += 1) {
    const key = fpRecordsChunkKey(gen, i);
    const slice = records.slice(i * FP_RECORDS_CHUNK, (i + 1) * FP_RECORDS_CHUNK);
    if (!setJson(key, slice)) {
      written.forEach((k) => remove(k));
      return false;
    }
    written.push(key);
  }

  const index: FpRecordsIndex = { version: INDEX_VERSION, gen, chunks };
  if (!setJson(FP_RECORDS_INDEX_KEY, index)) {
    written.forEach((k) => remove(k));
    return false;
  }

  // 索引已切到新代：清理旧分片与旧单 key（失败只占空间，不影响读取）
  if (isValidIndex(previous) && previous.gen !== gen) {
    for (let i = 0; i < previous.chunks; i += 1) {
      remove(fpRecordsChunkKey(previous.gen, i));
    }
  }
  remove(legacyKey);
  return true;
}
