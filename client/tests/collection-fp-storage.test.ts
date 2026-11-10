/**
 * 指纹记录分片持久化：突破单 key 1MB 限制、旧单 key 迁移、写失败不切换索引。
 */
import {
  FP_RECORDS_CHUNK,
  FP_RECORDS_INDEX_KEY,
  fpRecordsChunkKey,
  loadFpRecords,
  saveFpRecords,
} from '../core/collection/fp-records-storage';
import { FpRecordsPersistError, FpRecordsStore } from '../core/collection/points-store';
import type { FpRecord } from '../core/collection/types';
import { getJson, remove, setJson } from '../utils/kv';
import { wxStub } from './setup-wx';

const LEGACY = 'col:fp:test';

function makeRecord(i: number): FpRecord {
  const beacons = Array.from({ length: 25 }, (_, k) => ({
    uuid: 'B9407F30-F5F8-466E-AFF9-25556B57FE6D',
    major: 1,
    minor: 10000 + k,
    rssi: -80 + k,
    txPower: -71,
    accuracy: 3.16,
  }));
  return {
    fpId: `FP-1-${String(i).padStart(4, '0')}`,
    floor: 1,
    coordinates: [-36, -47],
    relCoordinates: null,
    zoneId: null,
    zoneName: null,
    anchor: null,
    regionType: 'normal',
    captureIndex: 1,
    collectedAt: new Date(1_700_000_000_000 + i * 1000).toISOString(),
    beaconCount: beacons.length,
    beacons,
  };
}

beforeEach(() => {
  // 索引/旧 key 是固定名，必须显式清掉（含 kv 的内存兜底）
  remove(FP_RECORDS_INDEX_KEY);
  remove(LEGACY);
  for (const key of [...wxStub.__storage.keys()]) {
    if (key.startsWith('pathai:col:fp')) {
      wxStub.__storage.delete(key);
    }
  }
});

test('单 key 超 1MB 的记录可分片写入并完整读回', () => {
  const records = Array.from({ length: 400 }, (_, i) => makeRecord(i));
  const original = wxStub.setStorageSync;
  // 模拟微信单 key 1MB 上限：超限抛异常（旧实现 400 条全量数组必然失败）
  wxStub.setStorageSync = (key, value) => {
    if (value.length > 1_000_000) {
      throw new Error('exceed storage max length');
    }
    original(key, value);
  };
  try {
    expect(saveFpRecords(LEGACY, records, 'gen-ok')).toBe(true);
  } finally {
    wxStub.setStorageSync = original;
  }

  const chunks = Math.ceil(records.length / FP_RECORDS_CHUNK);
  expect(getJson(FP_RECORDS_INDEX_KEY)).toEqual({ version: 2, gen: 'gen-ok', chunks });
  for (let i = 0; i < chunks; i += 1) {
    const key = fpRecordsChunkKey('gen-ok', i);
    expect(wxStub.__storage.has(`pathai:${key}`)).toBe(true);
    expect((wxStub.__storage.get(`pathai:${key}`) ?? '').length).toBeLessThan(1_000_000);
  }

  const restored = loadFpRecords(LEGACY);
  expect(restored).toHaveLength(400);
  expect(restored[0].fpId).toBe('FP-1-0000');
  expect(restored[399].fpId).toBe('FP-1-0399');
});

test('旧单 key 可读，成功写入新格式后旧 key 被清理', () => {
  setJson(LEGACY, [makeRecord(1)]);
  expect(loadFpRecords(LEGACY)).toHaveLength(1);

  expect(saveFpRecords(LEGACY, [makeRecord(1), makeRecord(2)], 'gen-2')).toBe(true);
  expect(getJson(LEGACY)).toBeNull();
  expect(loadFpRecords(LEGACY).map((r) => r.fpId)).toEqual(['FP-1-0001', 'FP-1-0002']);
});

test('分片写入失败返回 false，读取仍是上一份完整快照', () => {
  expect(saveFpRecords(LEGACY, [makeRecord(1), makeRecord(2), makeRecord(3)], 'gen-old')).toBe(true);

  const records = Array.from({ length: FP_RECORDS_CHUNK + 10 }, (_, i) => makeRecord(100 + i));
  const original = wxStub.setStorageSync;
  wxStub.setStorageSync = (key, value) => {
    if (key.includes(':gen-new:1')) {
      throw new Error('disk full');
    }
    original(key, value);
  };
  try {
    expect(saveFpRecords(LEGACY, records, 'gen-new')).toBe(false);
  } finally {
    wxStub.setStorageSync = original;
  }

  // 索引未切换、本次已写分片被清理
  expect(getJson(FP_RECORDS_INDEX_KEY)).toEqual({ version: 2, gen: 'gen-old', chunks: 1 });
  expect(loadFpRecords(LEGACY).map((r) => r.fpId)).toEqual([
    'FP-1-0001',
    'FP-1-0002',
    'FP-1-0003',
  ]);
});

test('落盘失败时 store 回滚该条并抛错，界面计数不会虚增', () => {
  const store = new FpRecordsStore(
    () => [],
    () => false,
  );
  expect(() => store.addRecord(makeRecord(1))).toThrow(FpRecordsPersistError);
  expect(store.allRecords()).toHaveLength(0);
});
