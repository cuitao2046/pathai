/**
 * 导出/入库按钮状态机：本地 → 已导出 → 已入库。
 * 覆盖：重启后推导、入库失败、导出中断、清理、同内容重复导出、台账兜底。
 */
import {
  clearSyncState,
  contentSha,
  deriveActionState,
  loadSyncState,
  markExported,
  markIngested,
  seedExportedState,
} from '../core/collection/export-state';
import type { FpDeletion, FpRecord } from '../core/collection/types';

function rec(fpId: string, collectedAt: string): FpRecord {
  return {
    fpId,
    floor: 1,
    coordinates: [0, 0],
    relCoordinates: null,
    zoneId: null,
    zoneName: null,
    anchor: null,
    regionType: 'normal',
    captureIndex: 1,
    collectedAt,
    beaconCount: 1,
    beacons: [
      { uuid: 'uuid', major: 1, minor: 10101, rssi: -70, txPower: -52, accuracy: 1 },
    ],
  };
}

function del(fpId: string, collectedAt: string): FpDeletion {
  return { fpId, collectedAt, contentSha256: 'a'.repeat(64) };
}

const a = rec('FP-1-A', '2026-11-10T10:00:00.000Z');
const b = rec('FP-1-B', '2026-11-10T10:01:00.000Z');

/**
 * 与页面同样的一次推导：读取持久化状态后计算按钮。
 * pendingExport 对应台账里是否还有未导出的记录/墓碑。
 */
function actions(records: FpRecord[], deletions: FpDeletion[], pendingExport: boolean) {
  const sha = contentSha(records, deletions);
  const hasContent = records.length > 0 || deletions.length > 0;
  const state = seedExportedState({ sha, hasContent, pendingExport });
  return deriveActionState({ sha, hasContent, pendingExport, state });
}

describe('导出/入库按钮状态机', () => {
  beforeEach(() => {
    clearSyncState();
  });

  it('本地为空时两个按钮都不可用', () => {
    expect(actions([], [], false)).toEqual({ canExport: false, canIngest: false });
  });

  it('新采集后只有导出可用，导出成功后只有入库可用', () => {
    expect(actions([a], [], true)).toEqual({ canExport: true, canIngest: false });
    markExported(contentSha([a], []));
    expect(actions([a], [], false)).toEqual({ canExport: false, canIngest: true });
  });

  it('入库成功后两个按钮都不可用，重启后仍保持（不重复导出/入库）', () => {
    markExported(contentSha([a], []));
    markIngested(contentSha([a], []));
    expect(actions([a], [], false)).toEqual({ canExport: false, canIngest: false });
    // loadSyncState 每次从 storage 读：等价于重启小程序
    expect(loadSyncState().lastIngestSha).toBe(contentSha([a], []));
    expect(actions([a], [], false)).toEqual({ canExport: false, canIngest: false });
  });

  it('同内容重复导出被挡下（不会让用户导出两次）', () => {
    markExported(contentSha([a, b], []));
    expect(actions([a, b], [], false).canExport).toBe(false);
  });

  it('入库失败不写标记，入库按钮保持可用以便重试', () => {
    markExported(contentSha([a], []));
    // 入库抛错：不调用 markIngested
    expect(actions([a], [], false).canIngest).toBe(true);
  });

  it('导出中断（写文件失败）不写标记，导出按钮保持可用', () => {
    expect(actions([a], [], true)).toEqual({ canExport: true, canIngest: false });
  });

  it('新增记录后导出恢复可用，导出后入库重新可用', () => {
    markExported(contentSha([a], []));
    markIngested(contentSha([a], []));
    const next = [a, b];
    expect(actions(next, [], true)).toEqual({ canExport: true, canIngest: false });
    markExported(contentSha(next, []));
    expect(actions(next, [], false)).toEqual({ canExport: false, canIngest: true });
  });

  it('删除记录生成墓碑后需重新导出；入库后墓碑移除也不回到「可导出」', () => {
    markExported(contentSha([a, b], []));
    markIngested(contentSha([a, b], []));
    const tomb = [del('FP-1-B', b.collectedAt)];
    expect(actions([a], tomb, true)).toEqual({ canExport: true, canIngest: false });
    markExported(contentSha([a], tomb));
    expect(actions([a], tomb, false)).toEqual({ canExport: false, canIngest: true });
    // 入库成功：服务端库里是记录集合（墓碑已应用），本地也移除了墓碑
    markIngested(contentSha([a], []));
    expect(actions([a], [], false)).toEqual({ canExport: false, canIngest: false });
  });

  it('清理到空后没有可提交内容，两键不可用；重新采集后导出恢复', () => {
    markIngested(contentSha([a], []));
    expect(actions([], [], false)).toEqual({ canExport: false, canIngest: false });
    expect(actions([b], [], true)).toEqual({ canExport: true, canIngest: false });
  });

  it('摘要落盘失败时用台账兜底：仍需允许入库，避免永久灰键', () => {
    // lastExportSha 缺失（例如状态写入失败），但台账证明全部已导出
    const sha = contentSha([a], []);
    const state = loadSyncState();
    expect(
      deriveActionState({ sha, hasContent: true, pendingExport: false, state }).canIngest,
    ).toBe(true);
    // 已有未导出项时不允许入库
    expect(
      deriveActionState({ sha, hasContent: true, pendingExport: true, state }).canIngest,
    ).toBe(false);
  });

  it('升级兼容：旧版本已全部导出过时自动补「已导出」摘要', () => {
    const sha = contentSha([a], []);
    const seeded = seedExportedState({ sha, hasContent: true, pendingExport: false });
    expect(seeded.lastExportSha).toBe(sha);
    expect(
      deriveActionState({ sha, hasContent: true, pendingExport: false, state: seeded }),
    ).toEqual({ canExport: false, canIngest: true });
    // 有未导出项时不补，仍要求导出
    clearSyncState();
    const notSeeded = seedExportedState({ sha, hasContent: true, pendingExport: true });
    expect(notSeeded.lastExportSha).toBeNull();
  });

  it('摘要对顺序不敏感，对内容（键/墓碑哈希）敏感', () => {
    expect(contentSha([a, b], [])).toBe(contentSha([b, a], []));
    expect(contentSha([a], [])).not.toBe(contentSha([b], []));
    expect(contentSha([a], [del('FP-1-A', a.collectedAt)])).not.toBe(
      contentSha([a], [{ ...del('FP-1-A', a.collectedAt), contentSha256: 'b'.repeat(64) }]),
    );
  });
});
