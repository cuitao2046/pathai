/**
 * 标定模式轨迹记录器单测（REQ-CALIB）：只记录、不改算法。
 */
import {
  TrajectoryRecorder,
  type CalibrationRecord,
} from '../core/calibration/trajectory-recorder';
import type { PositionEstimateResponse } from '../typings/api';

function estimate(over: Partial<PositionEstimateResponse> = {}): PositionEstimateResponse {
  return {
    estimateId: 'loc_1',
    sequence: 1,
    mapVersion: '2026-10-25.1',
    paramsVersion: 'loc-test.1',
    position: { x: 3.5, y: -2.25, floorId: '1' },
    confidence: 0.9,
    quality: 'reliable',
    algorithmVersion: 'location-0.1',
    observedAt: '2026-10-28T08:00:00Z',
    ignoredBeaconIds: [],
    ...over,
  };
}

function memoryStorage() {
  let stored: unknown = null;
  return {
    load: () => stored,
    save: (records: CalibrationRecord[]) => {
      stored = JSON.parse(JSON.stringify(records));
    },
    read: () => stored,
  };
}

describe('TrajectoryRecorder', () => {
  it('记录可信估计的坐标、版本、可见信标数与会话状态', () => {
    const storage = memoryStorage();
    const recorder = new TrajectoryRecorder(storage);
    expect(recorder.record({
      estimate: estimate(),
      visibleBeaconCount: 7,
      instructionSequence: 3,
      sessionStatus: 'active',
    })).toBe(true);

    expect(recorder.count).toBe(1);
    const rows = storage.read() as CalibrationRecord[];
    expect(rows[0]).toMatchObject({
      mapVersion: '2026-10-25.1',
      paramsVersion: 'loc-test.1',
      floorId: '1',
      x: 3.5,
      y: -2.25,
      quality: 'reliable',
      confidence: 0.9,
      visibleBeaconCount: 7,
      instructionSequence: 3,
      sessionStatus: 'active',
    });
    expect(typeof rows[0].ts).toBe('string');
  });

  it('无坐标的估计不记录（不可信位置不进入标定样本）', () => {
    const recorder = new TrajectoryRecorder(memoryStorage());
    expect(recorder.record({
      estimate: estimate({ position: null, quality: 'unreliable' }),
      visibleBeaconCount: 2,
      instructionSequence: null,
      sessionStatus: null,
    })).toBe(false);
    expect(recorder.count).toBe(0);
  });

  it('超过上限时丢弃最旧记录', () => {
    const recorder = new TrajectoryRecorder(memoryStorage(), 2);
    for (let i = 0; i < 3; i++) {
      recorder.record({
        estimate: estimate({ estimateId: `loc_${i}` }),
        visibleBeaconCount: i,
        instructionSequence: null,
        sessionStatus: 'idle',
      });
    }
    expect(recorder.count).toBe(2);
    const rows = recorder.buildExport({
      venueId: 'school-building-01',
      operator: '张三',
      device: null,
    }).records as CalibrationRecord[];
    expect(rows.map((r) => r.visibleBeaconCount)).toEqual([1, 2]);
  });

  it('导出结构符合动态标定契约，清空后持久化同步', () => {
    const storage = memoryStorage();
    const recorder = new TrajectoryRecorder(storage);
    recorder.record({
      estimate: estimate(),
      visibleBeaconCount: 5,
      instructionSequence: null,
      sessionStatus: null,
    });
    const payload = recorder.buildExport({
      venueId: 'school-building-01',
      operator: '李四',
      device: { model: 'test' },
      appVersion: '1.0.0',
      exportedAt: '2026-10-28T09:00:00Z',
    });
    expect(payload).toMatchObject({
      schemaVersion: '1.0',
      type: 'positioning_calibration_dynamic',
      venueId: 'school-building-01',
      operator: '李四',
      appVersion: '1.0.0',
      exportedAt: '2026-10-28T09:00:00Z',
      recordCount: 1,
    });

    recorder.clear();
    expect(recorder.count).toBe(0);
    expect(storage.read()).toEqual([]);
  });

  it('加载时丢弃结构不完整的缓存记录', () => {
    const recorder = new TrajectoryRecorder({
      load: () => [
        { ts: '2026-10-28T08:00:00Z', x: 1, y: 2, mapVersion: 'v1', paramsVersion: 'p' },
        { ts: 'bad', x: 'not-a-number', y: 2 },
        null,
        'oops',
      ],
      save: () => undefined,
    });
    expect(recorder.count).toBe(1);
  });
});
