import {
  FP_EXPORT_LEDGER_KEY,
  clearExportedLedger,
  countUnexported,
  exportedKeySet,
  listUnexported,
  markRecordsExported,
  recordKey,
} from '../core/collection/export-ledger';
import type { FpRecord } from '../core/collection/types';
import { getJson, remove, setJson } from '../utils/kv';

function rec(fpId: string, collectedAt: string, captureIndex = 1): FpRecord {
  return {
    fpId,
    floor: 1,
    coordinates: [0, 0],
    relCoordinates: null,
    zoneId: null,
    zoneName: null,
    anchor: null,
    regionType: 'normal',
    captureIndex,
    collectedAt,
    beaconCount: 1,
    beacons: [
      { uuid: 'uuid', major: 1, minor: 10101, rssi: -70, txPower: -52, accuracy: 1 },
    ],
  };
}

describe('指纹导出台账', () => {
  beforeEach(() => {
    remove(FP_EXPORT_LEDGER_KEY);
  });

  it('未导出时全部计入未导出', () => {
    const records = [rec('G1-001', '2026-11-09T10:00:00Z'), rec('G1-002', '2026-11-09T10:01:00Z')];
    expect(countUnexported(records)).toBe(2);
    expect(listUnexported(records)).toHaveLength(2);
    expect(exportedKeySet().size).toBe(0);
  });

  it('导出成功后仅剩余新增记录未导出，并持久化到本地存储', () => {
    const first = [rec('G1-001', '2026-11-09T10:00:00Z'), rec('G1-002', '2026-11-09T10:01:00Z')];
    expect(markRecordsExported(first)).toBe(true);
    expect(getJson<{ version: number }>(FP_EXPORT_LEDGER_KEY)?.version).toBe(1);

    const later = [...first, rec('G1-001', '2026-11-09T10:05:00Z')];
    expect(countUnexported(later)).toBe(1);
    expect(listUnexported(later)[0].collectedAt).toBe('2026-11-09T10:05:00Z');
  });

  it('同一点多次采集按 (fpId, collectedAt) 区分，重复导出幂等', () => {
    const a = rec('G1-001', '2026-11-09T10:00:00Z', 1);
    const b = rec('G1-001', '2026-11-09T10:00:30Z', 2);
    expect(recordKey(a)).not.toBe(recordKey(b));
    markRecordsExported([a]);
    markRecordsExported([a, b]);
    expect(countUnexported([a, b])).toBe(0);
    expect(exportedKeySet().size).toBe(2);
  });

  it('台账损坏时按空台账处理，不误判为已导出', () => {
    setJson(FP_EXPORT_LEDGER_KEY, { version: 99, keys: ['x'] });
    expect(countUnexported([rec('G1-001', '2026-11-09T10:00:00Z')])).toBe(1);
  });

  it('清空台账后重新计入未导出', () => {
    const records = [rec('G1-001', '2026-11-09T10:00:00Z')];
    markRecordsExported(records);
    clearExportedLedger();
    expect(countUnexported(records)).toBe(1);
  });
});
