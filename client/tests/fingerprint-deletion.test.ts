/**
 * 删除语义（客户端侧）：墓碑持久化、导出载荷、对账差异。
 *
 * 覆盖：本地删了→墓碑→导出 deletions[]→入库前对账提示「库中有、本地无」。
 */
import {
  addTombstones,
  buildTombstone,
  clearTombstones,
  listTombstones,
  recordContentHash,
  removeTombstones,
  tombstoneKey,
} from '../core/collection/tombstones';
import {
  FP_EXPORT_LEDGER_KEY,
  countUnexportedDeletions,
  markDeletionsExported,
  markRecordsExported,
} from '../core/collection/export-ledger';
import { buildFpExport } from '../core/collection/export-builder';
import { FpRecordsStore } from '../core/collection/points-store';
import { buildFpRecord } from '../core/collection/fp-snapshot';
import { diffManifest } from '../core/collection/manifest-diff';
import type { FpRecord } from '../core/collection/types';
import { remove } from '../utils/kv';

const TOMBSTONE_KEY = 'fp:tombstones:v1';

function rec(fpId: string, collectedAt: string, rssi = -70): FpRecord {
  return {
    fpId,
    floor: 1,
    coordinates: [1, 2],
    relCoordinates: null,
    zoneId: null,
    zoneName: null,
    anchor: null,
    regionType: 'normal',
    captureIndex: 1,
    collectedAt,
    beaconCount: 1,
    beacons: [
      { uuid: 'uuid', major: 1, minor: 10101, rssi, txPower: -52, accuracy: 1 },
    ],
  };
}

describe('删除墓碑', () => {
  beforeEach(() => {
    remove(TOMBSTONE_KEY);
    remove(FP_EXPORT_LEDGER_KEY);
  });

  it('内容哈希稳定，且 RSSI/坐标变化会改变哈希', () => {
    const a = rec('G1-001', '2026-11-09T10:00:00Z', -70);
    const b = rec('G1-001', '2026-11-09T10:00:00Z', -71);
    expect(recordContentHash(a)).toHaveLength(64);
    // 与后端 fingerprint_library.record_sha 同一口径的固定值（跨语言契约）
    expect(recordContentHash(a)).toBe(
      '08eefa07ab02c5f6258eba92434b391f6ce9f9e19a5467ea741b83ddde3210ef',
    );
    expect(recordContentHash(a)).toBe(recordContentHash(rec('G1-001', '2026-11-09T10:00:00Z', -70)));
    expect(recordContentHash(a)).not.toBe(recordContentHash(b));
  });

  it('同一键重复删除只保留一条墓碑', () => {
    const r = rec('G1-001', '2026-11-09T10:00:00Z');
    addTombstones([buildTombstone(r)]);
    addTombstones([buildTombstone(r)]);
    expect(listTombstones()).toHaveLength(1);
    expect(tombstoneKey(listTombstones()[0])).toBe('G1-001|2026-11-09T10:00:00Z');
  });

  it('墓碑导出记账：未导出数随导出归零，入库后可移除', () => {
    const items = [buildTombstone(rec('G1-001', '2026-11-09T10:00:00Z'))];
    addTombstones(items);
    expect(countUnexportedDeletions(listTombstones())).toBe(1);
    expect(markDeletionsExported(items)).toBe(true);
    expect(countUnexportedDeletions(listTombstones())).toBe(0);
    removeTombstones(items.map(tombstoneKey));
    expect(listTombstones()).toHaveLength(0);
    clearTombstones();
  });

  it('导出载荷带 deletions 与显式 mode；换层替换写入 replaceFloors', () => {
    let saved: FpRecord[] | null = null;
    const store = new FpRecordsStore(
      () => saved,
      (d) => {
        saved = d;
      },
    );
    store.addRecord(
      buildFpRecord({
        point: { fpId: 'FP-1-001', floorId: '1', x: 1, y: 2, regionType: 'normal' },
        window: [],
        lookup: () => null,
        rssiAt1m: -71,
      }),
    );
    const deletion = buildTombstone(rec('FP-1-001', '2026-11-09T10:00:00Z'));
    const meta = {
      venueId: 'school-building-01',
      venueName: 'school-building-01',
      operator: '张三',
      device: null,
      appVersion: '1.0.0',
      mapVersion: '2026-11-03.1',
      ledgerSha256: null,
    };
    const merge = buildFpExport(store, meta, {
      beaconUuid: 'uuid',
      deletions: [deletion],
      mode: 'merge',
    });
    expect(merge.data.schemaVersion).toBe('1.2.0');
    expect(merge.data.deletions).toEqual([deletion]);
    expect(merge.data.mode).toBe('merge');
    expect(merge.data.replaceFloors).toBeUndefined();

    const replace = buildFpExport(store, meta, {
      beaconUuid: 'uuid',
      mode: 'replaceFloor',
      replaceFloors: ['1'],
    });
    expect(replace.data.mode).toBe('replaceFloor');
    expect(replace.data.replaceFloors).toEqual(['1']);
    markRecordsExported(merge.data.records);
  });

  it('对账：区分两侧都有/仅本地/仅服务端/同键内容变化', () => {
    const local = [
      rec('G1-001', '2026-11-09T10:00:00Z'),
      rec('G1-002', '2026-11-09T10:01:00Z'),
      rec('G1-003', '2026-11-09T10:02:00Z'),
    ];
    const items = [
      {
        fpId: 'G1-001',
        collectedAt: '2026-11-09T10:00:00Z',
        floorId: '1',
        contentSha256: recordContentHash(local[0]),
      },
      {
        fpId: 'G1-002',
        collectedAt: '2026-11-09T10:01:00Z',
        floorId: '1',
        contentSha256: 'f'.repeat(64),
      },
      {
        fpId: 'G1-009',
        collectedAt: '2026-11-08T09:00:00Z',
        floorId: '2',
        contentSha256: 'a'.repeat(64),
      },
    ];
    const diff = diffManifest(local, items);
    expect(diff.same).toBe(1);
    expect(diff.changed).toBe(1);
    expect(diff.changedItems[0].fpId).toBe('G1-002');
    expect(diff.localOnly).toBe(1);
    expect(diff.dbOnly).toBe(1);
    expect(diff.dbOnlyItems[0].fpId).toBe('G1-009');
    expect(diff.dbOnlyItems[0].floorId).toBe('2');
  });
});
