import {
  FieldOutbox, checkpointCount, fieldSteps, fingerprintWindow, rotatedFieldMethod, type FieldSegment,
} from '../core/collection/field-experiment';
import { FieldNavigationRecorder, type ArmedNavigation } from '../services/field-navigation';

jest.mock('../services/app-context', () => ({ appContext: {} }));

const files = new Map<string, string>();
const kv = new Map<string, unknown>();
let failWrite: (path: string) => boolean;
let failDelete: (path: string) => boolean;
const directory = 'local/experiment-operator';
const read = (path: string): string => {
  const content = files.get(path);
  if (content === undefined) throw new Error('missing');
  return content;
};

beforeEach(() => {
  files.clear();
  kv.clear();
  failWrite = () => false;
  failDelete = () => false;
  Object.assign(wx, {
    env: { USER_DATA_PATH: 'local' },
    getFileSystemManager: () => ({
      accessSync: (path: string) => { if (path !== directory) read(path); },
      mkdirSync: jest.fn(),
      readdirSync: () => [...files.keys()].map((path) => path.split('/').pop()),
      readFileSync: read,
      writeFileSync: (path: string, content: string) => {
        if (failWrite(path)) throw new Error('no space');
        files.set(path, content);
      },
      renameSync: (from: string, to: string) => { files.set(to, read(from)); files.delete(from); },
      unlinkSync: (path: string) => {
        if (failDelete(path)) throw new Error('cannot delete draft');
        files.delete(path);
      },
    }),
    getStorageSync: (key: string) => kv.get(key),
    setStorageSync: (key: string, value: unknown) => kv.set(key, value),
  });
});

function segment(): FieldSegment {
  return {
    segmentId: 'batch-dy-001', runId: 'run-1', task: 'DY', batch: 'batch', part: 1,
    startedAtEpoch: 1000, endedAtEpoch: 481000, mapVersion: 'map-1', paramsVersion: 'params-1',
    deviceId: 'device-1', status: 'complete', rssi: [{ t: 1200, beacon: 'b-1', rssi: -61 }],
    checkpoints: [],
  };
}

test('an interrupted upload retains raw data and progress across page recreation', async () => {
  const data = segment();
  const first = new FieldOutbox('operator');
  first.saveDraft(data);
  expect(new FieldOutbox('operator').recoverDraft()).toEqual(data);
  first.enqueue('segments', data.segmentId, data);
  first.clearDraft();
  const offline = jest.fn().mockRejectedValue(new Error('offline'));
  await expect(first.flush(offline)).rejects.toThrow('offline');
  const recovered = new FieldOutbox('operator');
  expect(recovered.pendingCount()).toBe(1);
  expect(recovered.progress()).toMatchObject([{ segmentId: data.segmentId, status: 'complete' }]);
  const post = jest.fn().mockResolvedValue({ duplicate: true });
  await recovered.flush(post);
  expect(post).toHaveBeenCalledWith('segments', data);
  expect(new FieldOutbox('operator').pendingCount()).toBe(0);
  expect(new FieldOutbox('operator').progress()).toHaveLength(1);
});

test('does not replace an already queued segment with different content', () => {
  const store = new FieldOutbox('operator');
  const data = segment();
  store.enqueue('segments', data.segmentId, data);
  store.enqueue('segments', data.segmentId, data);
  expect(() => store.enqueue('segments', data.segmentId, { ...data, status: 'aborted' })).toThrow();
  expect(store.pendingCount()).toBe(1);
});

test('a full storage key does not block uploading or hide older progress', async () => {
  kv.set('field:experiment:v1:operator', { deviceId: 'old-device', summaries: [
    { ...segment(), segmentId: 'already-uploaded' },
  ] });
  const failed = jest.fn(() => { throw new Error('storage quota'); });
  Object.assign(wx, { setStorageSync: failed });
  const store = new FieldOutbox('operator');
  store.enqueue('segments', segment().segmentId, segment());
  await store.flush(jest.fn().mockResolvedValue({ duplicate: false }));
  const recovered = new FieldOutbox('operator');
  expect(recovered.deviceId).toBe('old-device');
  expect(recovered.progress().map((row) => row.segmentId)).toEqual(['already-uploaded', segment().segmentId]);
  expect(recovered.pendingCount()).toBe(0);
  expect(failed).not.toHaveBeenCalled();
});

test('low disk space preserves the last draft and does not block acknowledged uploads', async () => {
  const data = segment();
  const store = new FieldOutbox('operator');
  store.saveDraft(data);
  store.enqueue('segments', data.segmentId, data);
  const next = { ...data, segmentId: 'batch-dy-002' };
  store.enqueue('segments', next.segmentId, next);
  failWrite = () => true;
  expect(() => store.saveDraft({ ...data, endedAtEpoch: 481001 })).toThrow('no space');
  expect(store.recoverDraft()).toEqual(data);
  const post = jest.fn().mockResolvedValue({ duplicate: false });
  await store.flush(post);
  const recovered = new FieldOutbox('operator');
  expect(recovered.pendingCount()).toBe(0);
  expect(post).toHaveBeenCalledTimes(2);
  failWrite = () => false;
  expect(recovered.progress().map((row) => row.segmentId)).toEqual([data.segmentId, next.segmentId]);
  expect(JSON.parse(read(`${directory}/sent-segments-${data.segmentId}.json`))).not.toHaveProperty('body');
});

test('a T8 save retry retains the completed payload when draft cleanup failed', () => {
  const run: ArmedNavigation = {
    owner: 'operator',
    plan: { planId: 'plan-1', batch: '20261120-010', mapVersion: 'map-1', day: '2026-11-20', task: 'NV', points: [] },
    config: { mapVersion: 'map-1', configId: 'config-1', frozenAt: 1000,
      A: { paramsVersion: 'a' }, B: { paramsVersion: 'b' }, C: { libraryVersion: 'lib-1', fallback: 'B' } },
    selection: { planId: 'plan-1', configId: 'config-1', runId: 'test-run', method: 'A' },
  };
  const report = jest.fn();
  const recorder = new FieldNavigationRecorder(run, report);
  recorder.start();
  failDelete = (path) => path.endsWith('/draft.json');
  expect(recorder.finish('complete', 'arrived')).toBe(false);
  expect(recorder.needsSaveRetry).toBe(true);
  const pending = [...files.keys()].find((path) => path.includes('/pending-segments-'))!;
  const payload = read(pending);
  failDelete = () => false;
  expect(recorder.finish('aborted', 'retry')).toBe(true);
  expect(read(pending)).toBe(payload);
  expect(JSON.parse(payload).body.status).toBe('complete');
  expect(new FieldOutbox('operator').recoverDraft()).toBeNull();
});

test('calibration and grid durations are distinct and undo keeps the checkpoint event', () => {
  expect(fieldSteps('CAL')).toEqual([{ part: 1, duration: 120 }]);
  expect(fieldSteps('FP').map((s) => [s.orientation, s.duration])).toEqual([
    ['N', 25], ['E', 25], ['S', 25], ['W', 25],
  ]);
  expect(fieldSteps('ST').map((s) => s.duration)).toEqual([30, 30, 30, 30]);
  expect(fieldSteps('CAL', true)).toEqual([{ part: 1, duration: 10 }]);
  for (const task of ['FP', 'ST'] as const) {
    expect(fieldSteps(task, true).map((s) => s.duration)).toEqual([10, 10, 10, 10]);
  }
  expect(fieldSteps('DY', true)).toEqual(fieldSteps('DY'));
  expect(checkpointCount([
    { pointId: 'CP1', t: 1, undo: false },
    { pointId: 'CP1', t: 2, undo: true },
    { pointId: 'CP1', t: 3, undo: false },
  ])).toBe(1);
});

test('fingerprint windows stop as soon as criteria are met from 12 seconds, else extend to 30', () => {
  const startedAtEpoch = 1000;
  const earlyStopAt = startedAtEpoch + 12000;
  const firstDeadline = startedAtEpoch + 25000;
  const maximum = startedAtEpoch + 30000;
  const rssi = Array.from({ length: 60 }, (_, i) => ({
    t: startedAtEpoch + i * 400, beacon: `b-${i % 3}`, rssi: -65,
  }));
  // 60 条达标样本在第 24.6 s 齐备：12 s 起逐 tick 校验，达标即停在 24.6 s（旧逻辑固定采满 25 s）。
  expect(fingerprintWindow({ startedAtEpoch, rssi }, firstDeadline, earlyStopAt)).toBeNull();
  expect(fingerprintWindow({ startedAtEpoch, rssi }, firstDeadline, earlyStopAt + 100)).toBeNull();
  const readyAt = startedAtEpoch + 24600;
  expect(fingerprintWindow({ startedAtEpoch, rssi }, firstDeadline, readyAt)).toEqual({
    deadline: readyAt, reason: '',
  });
  // 基准窗口到点达标：25 s 正常结束。
  expect(fingerprintWindow({ startedAtEpoch, rssi }, firstDeadline, firstDeadline)).toEqual({
    deadline: firstDeadline, reason: '',
  });
  const withLateReading = { startedAtEpoch, rssi: [
    ...rssi.slice(0, 59), { ...rssi[59], t: firstDeadline + 1 },
  ] };
  // 25 s 差一条：延长到 30 s 继续采，期间达标仍即停。
  expect(fingerprintWindow(withLateReading, firstDeadline, firstDeadline)).toEqual({
    deadline: maximum, reason: 'RSSI 样本不足 60 条',
  });
  expect(fingerprintWindow(withLateReading, maximum, firstDeadline + 2000)).toEqual({
    deadline: firstDeadline + 2000, reason: '',
  });
  expect(fingerprintWindow(withLateReading, maximum, maximum)).toEqual({ deadline: maximum, reason: '' });
  expect(fingerprintWindow(withLateReading, firstDeadline, maximum + 100)).toEqual({ deadline: maximum, reason: '' });
  // 30 s 仍不达标：疑似异常。
  const weak = { startedAtEpoch, rssi: rssi.map((f) => f.beacon === 'b-2' ? { ...f, rssi: -110 } : f) };
  expect(fingerprintWindow(weak, maximum, maximum)).toEqual({ deadline: maximum, reason: '有效信标不足 3 枚' });
  // 12 s 前即使样本齐备也不提前结束（不足 6 个 2 s 扫描窗口）。
  const dense = Array.from({ length: 60 }, (_, i) => ({
    t: startedAtEpoch + i * 100, beacon: `b-${i % 3}`, rssi: -65,
  }));
  expect(fingerprintWindow({ startedAtEpoch, rssi: dense }, firstDeadline, startedAtEpoch + 8000)).toBeNull();
  expect(fingerprintWindow({ startedAtEpoch, rssi: dense }, firstDeadline, earlyStopAt)).toEqual({
    deadline: earlyStopAt, reason: '',
  });
});

test('nine official navigation runs are balanced and rotate by day', () => {
  const sequence = (day: number) => Array.from({ length: 9 }, (_, i) => rotatedFieldMethod(day, i)).join('');
  expect(sequence(0)).toBe('ABCBCACAB');
  expect(sequence(1)).toBe('BCACABABC');
  expect(sequence(2)).toBe('CABABCBCA');
  expect(rotatedFieldMethod(0, 9)).toBeUndefined();
});
