/**
 * 导出 / 入库按钮状态机（本地 → 已导出 → 已入库）。
 *
 * 现场口径（2026-11-10 确认）：
 * - 一次入库 = 本地全部记录 + 全部墓碑作为一个整体提交，不按楼层分批；
 * - 本地内容与「上次成功导出」一致时，导出按钮置灰，避免同内容重复导出；
 * - 本地内容与「上次成功入库」一致时，导出与入库都置灰；
 * - 内容发生变化（新采集 / 删除记录 / 清理后重新采集）后，导出恢复；
 * - 只有导出成功之后入库才恢复。
 *
 * 摘要只取 (fpId, collectedAt) 这一对后端合并键：记录在本地是追加制、
 * 采集后不再改写内容，因此「键集合不变 = 内容不变」；墓碑同理再加上
 * 内容哈希，避免同键不同载荷被当成同一份数据。
 * 摘要与两个标记都落 kv，重启小程序后仍能正确推导，不会永久灰键：
 * 导出写文件失败 / 入库失败都不会写入标记。
 */
import { getJson, remove, setJson } from '../../utils/kv';
import { sha256 } from '../../utils/sha256';
import type { FpDeletion, FpRecord } from './types';

export const FP_SYNC_STATE_KEY = 'fp:sync-state:v1';

export interface FpSyncState {
  version: 1;
  /** 上次成功导出对应的本地内容摘要（null=从未成功导出）。 */
  lastExportSha: string | null;
  /** 上次成功入库对应的本地内容摘要（null=从未成功入库）。 */
  lastIngestSha: string | null;
}

export interface FpActionState {
  canExport: boolean;
  canIngest: boolean;
}

type ContentRef = Pick<FpRecord, 'fpId' | 'collectedAt'>;
type DeletionRef = Pick<FpDeletion, 'fpId' | 'collectedAt' | 'contentSha256'>;

export function loadSyncState(): FpSyncState {
  const stored = getJson<Partial<FpSyncState>>(FP_SYNC_STATE_KEY);
  if (stored === null || stored.version !== 1) {
    return { version: 1, lastExportSha: null, lastIngestSha: null };
  }
  return {
    version: 1,
    lastExportSha: typeof stored.lastExportSha === 'string' ? stored.lastExportSha : null,
    lastIngestSha: typeof stored.lastIngestSha === 'string' ? stored.lastIngestSha : null,
  };
}

/**
 * 当前本地内容摘要：记录键 + 墓碑键（含墓碑内容哈希），排序后取 SHA-256。
 * 只做一次大文本摘要（约 20KB），不逐条算哈希，现场低端机也可同步调用。
 */
export function contentSha(records: ContentRef[], deletions: DeletionRef[]): string {
  const lines: string[] = [];
  for (const r of records) {
    lines.push(`R|${r.fpId}|${r.collectedAt}`);
  }
  for (const d of deletions) {
    lines.push(`D|${d.fpId}|${d.collectedAt}|${d.contentSha256}`);
  }
  lines.sort();
  return sha256(lines.join('\n'));
}

/**
 * 按钮可用性推导。
 * @param sha 当前本地内容摘要
 * @param hasContent 本地是否存在待提交内容（记录或墓碑）
 * @param pendingExport 台账口径下仍有未导出的记录/墓碑（storage 写入失败时的兜底证据）
 */
export function deriveActionState(args: {
  sha: string;
  hasContent: boolean;
  pendingExport: boolean;
  state: FpSyncState;
}): FpActionState {
  const { sha, hasContent, pendingExport, state } = args;
  const sameAsIngest = state.lastIngestSha !== null && state.lastIngestSha === sha;
  const sameAsExport = state.lastExportSha !== null && state.lastExportSha === sha;
  return {
    // 已入库 / 已导出且内容未变：不重复导出；本地为空时也没有可导出的内容
    canExport: hasContent && !sameAsExport && !sameAsIngest,
    // 导出成功（或台账证明全部已导出）之后才允许入库；已入库则不再入库
    canIngest: hasContent && !sameAsIngest && (sameAsExport || !pendingExport),
  };
}

/**
 * 兼容旧版本：升级前用户已把全部内容导出过（台账齐全）但没有摘要标记。
 * 首次进入时按当前内容补一个「已导出」标记，避免要求无意义地再导出一次。
 * 只在从未记录过导出摘要、且台账无未导出项时生效。
 */
export function seedExportedState(args: {
  sha: string;
  hasContent: boolean;
  pendingExport: boolean;
}): FpSyncState {
  const state = loadSyncState();
  if (!args.hasContent || args.pendingExport || state.lastExportSha !== null) {
    return state;
  }
  const next: FpSyncState = { ...state, lastExportSha: args.sha };
  setJson(FP_SYNC_STATE_KEY, next);
  return next;
}

/** 记录「本次导出的内容摘要」；返回是否真正落盘（false=仅本次会话内存）。 */
export function markExported(sha: string): boolean {
  const state = loadSyncState();
  return setJson(FP_SYNC_STATE_KEY, { ...state, lastExportSha: sha } satisfies FpSyncState);
}

/** 记录「本次入库的内容摘要」；返回是否真正落盘。 */
export function markIngested(sha: string): boolean {
  const state = loadSyncState();
  return setJson(FP_SYNC_STATE_KEY, { ...state, lastIngestSha: sha } satisfies FpSyncState);
}

/** 重置状态（联调/测试用）。 */
export function clearSyncState(): void {
  remove(FP_SYNC_STATE_KEY);
}
