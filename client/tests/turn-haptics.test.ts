/**
 * 转弯逼近震动（REQ-A11Y-TURN-HAPTIC）单测。
 *
 * 用注入的假时钟 + 假定时器驱动：不依赖 jest fake timer，断言精确到毫秒。
 * 覆盖：分区边界、分区切换节拍、上报间外推、越过拐点停止、中断后定时器不残留、
 * 以及 trigger.nodeId → 沿线剩余距离的几何计算。
 */
import {
  TURN_HAPTIC_CONFIG,
  TurnHaptics,
  intervalForZone,
  remainingRouteDistanceM,
  resolveTurnNode,
  turnDistanceM,
  zoneForDistance,
  type TurnHapticConfig,
} from '../core/nav/turn-haptics';
import type { GeoPosition, MapFloorData } from '../typings/api';

interface FakeTimer {
  at: number;
  interval: number;
  fn: () => void;
}

function makeHarness(overrides: Partial<TurnHapticConfig> = {}) {
  const clock = { now: 0 };
  const timers = new Map<number, FakeTimer>();
  let nextId = 1;
  const shortAt: number[] = [];
  const longAt: number[] = [];

  const setTimer = (fn: () => void, ms: number): ReturnType<typeof setInterval> => {
    const id = nextId;
    nextId += 1;
    timers.set(id, { at: clock.now + ms, interval: Math.max(1, ms), fn });
    return id as unknown as ReturnType<typeof setInterval>;
  };
  const clearTimer = (handle: ReturnType<typeof setInterval>): void => {
    timers.delete(handle as unknown as number);
  };

  /** 推进假时钟，按到期顺序执行循环定时器（同一时刻可能多次触发）。 */
  const advance = (ms: number): void => {
    const end = clock.now + ms;
    for (;;) {
      let pickId = -1;
      let pickAt = Infinity;
      timers.forEach((t, id) => {
        if (t.at <= end && t.at < pickAt) {
          pickAt = t.at;
          pickId = id;
        }
      });
      if (pickId < 0) {
        break;
      }
      const due = timers.get(pickId);
      if (due === undefined) {
        break;
      }
      clock.now = due.at;
      due.at += due.interval; // setInterval 语义：先排下一次，再执行回调（回调内可 clear）
      due.fn();
    }
    clock.now = end;
  };

  const cfg: TurnHapticConfig = { ...TURN_HAPTIC_CONFIG, ...overrides };
  const haptics = new TurnHaptics(
    {
      vibrateShort: () => shortAt.push(clock.now),
      vibrateLong: () => longAt.push(clock.now),
      now: () => clock.now,
      setTimer,
      clearTimer,
    },
    cfg,
  );

  return {
    cfg,
    haptics,
    clock,
    shortAt,
    longAt,
    timerCount: () => timers.size,
    advance,
  };
}

function gaps(at: number[]): number[] {
  const out: number[] = [];
  for (let i = 1; i < at.length; i += 1) {
    out.push(at[i] - at[i - 1]);
  }
  return out;
}

describe('zoneForDistance / intervalForZone', () => {
  it('按 6/4/2/0.5 m 分区，边界归更近的一档', () => {
    expect(zoneForDistance(Number.POSITIVE_INFINITY)).toBe('passed');
    expect(zoneForDistance(Number.NaN)).toBe('passed');
    expect(zoneForDistance(6.01)).toBe('quiet');
    expect(zoneForDistance(6)).toBe('smooth');
    expect(zoneForDistance(4.01)).toBe('smooth');
    expect(zoneForDistance(4)).toBe('medium');
    expect(zoneForDistance(2.01)).toBe('medium');
    expect(zoneForDistance(2)).toBe('fast');
    expect(zoneForDistance(0.5)).toBe('fast');
    expect(zoneForDistance(0.49)).toBe('passed');
    expect(zoneForDistance(-1)).toBe('passed');
  });

  it('节拍间隔：0.8/0.4/0.2 s，静默与到达无节拍，且不低于下限', () => {
    expect(intervalForZone('quiet')).toBeNull();
    expect(intervalForZone('passed')).toBeNull();
    expect(intervalForZone('smooth')).toBe(800);
    expect(intervalForZone('medium')).toBe(400);
    expect(intervalForZone('fast')).toBe(200);
    expect(
      intervalForZone('fast', { ...TURN_HAPTIC_CONFIG, fastIntervalMs: 50, minIntervalMs: 200 }),
    ).toBe(200);
  });
});

describe('TurnHaptics 分区节拍', () => {
  it('6~4 m 约 0.8 s、4~2 m 约 0.4 s、≤2 m 约 0.2 s，切换立即生效且间隔 ≥ 200 ms', () => {
    const h = makeHarness({ walkSpeedMps: 0 }); // 关外推，单独验证分区节拍

    h.haptics.beginTurn(5);
    h.advance(2000);
    expect(h.shortAt).toEqual([0, 800, 1600]);
    expect(gaps(h.shortAt)).toEqual([800, 800]);

    h.haptics.update(3); // smooth → medium
    h.advance(800);
    expect(gaps(h.shortAt.slice(3))).toEqual([400, 400]);

    h.haptics.update(1.5); // medium → fast
    h.advance(600);
    const fastGaps = gaps(h.shortAt.slice(5));
    expect(fastGaps).toEqual([200, 200, 200]);
    expect(Math.min(...gaps(h.shortAt))).toBeGreaterThanOrEqual(200);
    expect(h.longAt).toHaveLength(0);
  });

  it('> 6 m 不振，只在进入 6 m 后开始短振', () => {
    const h = makeHarness({ walkSpeedMps: 0 });
    h.haptics.beginTurn(8);
    h.advance(3000);
    expect(h.shortAt).toHaveLength(0);
    expect(h.timerCount()).toBe(1); // 静默期仍保留调度，等进区

    h.haptics.update(5.9);
    expect(h.shortAt).toEqual([3000]);
  });

  it('两次上报之间按 1.5 m/s 外推驱动分区切换（6.4 m 静默 → 约 0.3 s 后进 6~4 m 区）', () => {
    const h = makeHarness();
    h.haptics.beginTurn(6.4);
    expect(h.shortAt).toHaveLength(0);

    h.advance(1000); // 6.4 - 1.5 * 0.3 = 5.95 m，越过 6 m 静默边界
    expect(h.shortAt).toEqual([300]);
  });

  it('外推有上限：停止上报后不加速、不误触长振', () => {
    const h = makeHarness();
    h.haptics.beginTurn(6.5);
    h.advance(10_000); // 外推封顶 1.5 s → 冻结在 4.25 m
    expect(h.longAt).toHaveLength(0);
    expect(h.shortAt.length).toBeGreaterThan(0);
    expect(Math.min(...gaps(h.shortAt))).toBeGreaterThanOrEqual(400);
  });
});

describe('TurnHaptics 越过拐点', () => {
  it('越过（<0.5 m）只长振一次并立即停表，之后 update 不再触发', () => {
    const h = makeHarness();
    h.haptics.beginTurn(0.6); // fast 区，立即短振一次
    expect(h.shortAt).toEqual([0]);

    h.advance(100); // 0.6 - 0.15 = 0.45 < 0.5
    expect(h.longAt).toEqual([100]);
    expect(h.timerCount()).toBe(0);

    h.advance(2000);
    expect(h.shortAt).toEqual([0]);
    expect(h.longAt).toEqual([100]);

    h.haptics.update(0.3); // 已到达的转弯内不再重启
    h.advance(2000);
    expect(h.shortAt).toEqual([0]);
    expect(h.timerCount()).toBe(0);
  });

  it('beginTurn 复位“已到达”，下一个转弯重新开始节拍', () => {
    const h = makeHarness();
    h.haptics.beginTurn(0.4);
    expect(h.longAt).toHaveLength(1);
    expect(h.timerCount()).toBe(0);

    h.haptics.beginTurn(3);
    expect(h.shortAt).toEqual([0]);
    expect(h.timerCount()).toBe(1);
    expect(h.longAt).toHaveLength(1);
  });
});

describe('TurnHaptics 中断与定时器清理', () => {
  it.each(['导航结束', '偏航重规划', '页面 onHide', '语音听写'])(
    '%s：stop() 后 2 s 内 0 次震动且定时器数 = 0',
    (_label) => {
      const h = makeHarness();
      h.haptics.beginTurn(3);
      h.advance(300);
      expect(h.shortAt.length).toBeGreaterThan(0);

      const before = h.shortAt.length;
      h.haptics.stop();
      expect(h.timerCount()).toBe(0);

      h.advance(2000);
      expect(h.shortAt).toHaveLength(before);
      expect(h.longAt).toHaveLength(0);
      expect(h.timerCount()).toBe(0);
    },
  );

  it('distanceM 为 null（可信定位丢失）时立即停表', () => {
    const h = makeHarness();
    h.haptics.beginTurn(3);
    h.advance(100);
    h.haptics.update(null);
    expect(h.timerCount()).toBe(0);

    const before = h.shortAt.length;
    h.advance(2000);
    expect(h.shortAt).toHaveLength(before);
  });

  it('重复 stop() 幂等，不抛错也不留定时器', () => {
    const h = makeHarness();
    h.haptics.beginTurn(3);
    h.haptics.stop();
    h.haptics.stop();
    expect(h.timerCount()).toBe(0);
  });
});

describe('resolveTurnNode / turnDistanceM', () => {
  const floors: Record<string, MapFloorData> = {
    '1': {
      displayName: '1F',
      level: 1,
      geometry: { walls: [] },
      topology: { nodes: [{ id: 'F1-TI-0034', coordinates: [-37, -48] }] },
    },
  };

  it('trigger.nodeId 能在对应楼层解析出坐标，找不到返回 null', () => {
    expect(resolveTurnNode(floors, 'F1-TI-0034')).toEqual({ x: -37, y: -48, floorId: '1' });
    expect(resolveTurnNode(floors, 'NOPE')).toBeNull();
    expect(resolveTurnNode(floors, null)).toBeNull();
    expect(resolveTurnNode(undefined, 'F1-TI-0034')).toBeNull();
  });

  it('返回沿路线到拐点的剩余里程（L 形折线，拐点前后连续）', () => {
    const polyline: GeoPosition[] = [
      { x: 0, y: 0, floorId: '1' },
      { x: 10, y: 0, floorId: '1' },
      { x: 10, y: 10, floorId: '1' },
    ];
    const turn = { x: 10, y: 0, floorId: '1' };

    expect(turnDistanceM(polyline, { x: 5, y: 0 }, turn)).toBeCloseTo(5);
    expect(turnDistanceM(polyline, { x: 8, y: 0.3 }, turn)).toBeCloseTo(2);
    expect(turnDistanceM(polyline, { x: 10, y: 3 }, turn)).toBeCloseTo(-3); // 已越过
  });

  it('跨楼层或缺数据时返回 null（调用方停表）', () => {
    const polyline: GeoPosition[] = [
      { x: 0, y: 0, floorId: '1' },
      { x: 10, y: 0, floorId: '1' },
    ];
    expect(
      turnDistanceM(polyline, { x: 5, y: 0, floorId: '1' }, { x: 10, y: 0, floorId: '2' }),
    ).toBeNull();
    expect(turnDistanceM(polyline, null, { x: 10, y: 0, floorId: '1' })).toBeNull();
    expect(turnDistanceM([], { x: 0, y: 0, floorId: '1' }, { x: 10, y: 0, floorId: '1' })).toBeNull();
    expect(
      turnDistanceM(polyline, { x: 5, y: 0, floorId: '1' }, { x: 30, y: 0, floorId: '1' }),
    ).toBeNull();
  });
});

describe('remainingRouteDistanceM（HUD 剩余距离）', () => {
  const polyline: GeoPosition[] = [
    { x: 0, y: 0, floorId: '1' },
    { x: 10, y: 0, floorId: '1' },
    { x: 10, y: 0, floorId: '2' }, // 电梯换层：两点重合，距离 0
    { x: 10, y: 10, floorId: '2' },
  ];

  it('按当前位置沿折线投影，返回剩余里程（含跨层后的路段）', () => {
    expect(remainingRouteDistanceM(polyline, { x: 5, y: 0, floorId: '1' })).toBeCloseTo(15);
    expect(remainingRouteDistanceM(polyline, { x: 10, y: 0, floorId: '1' })).toBeCloseTo(10);
    expect(remainingRouteDistanceM(polyline, { x: 10, y: 5, floorId: '2' })).toBeCloseTo(5);
  });

  it('缺楼层/缺数据时返回 null（HUD 不展示过期距离）', () => {
    expect(remainingRouteDistanceM(polyline, null)).toBeNull();
    expect(remainingRouteDistanceM(polyline, { x: 5, y: 0, floorId: null })).toBeNull();
    expect(remainingRouteDistanceM([], { x: 0, y: 0, floorId: '1' })).toBeNull();
    expect(
      remainingRouteDistanceM(polyline, { x: 5, y: 0, floorId: '9' }),
    ).toBeNull();
  });
});
