/**
 * 蓝牙部署参考墙几何单测：
 * - 合成“走廊 + 邻接教室”覆盖公共空间边界墙筛选、安装墙识别、
 *   左右最近垂直墙、垂足在端点的墙排除、方向顺序无关；
 * - 真实地图包 2026-11-20.3 校验 BK-Q-F1-012 只在走道公共空间内选墙。
 */
import { analyzeBeaconDeployment } from '../core/collection/beacon-deployment';
import type { MapRoomFeature, MapWallFeature } from '../typings/api';
import { PUBLIC_MAP } from './fixtures/real-map';

function wall(id: string, x1: number, y1: number, x2: number, y2: number): MapWallFeature {
  return { id, geometry: { type: 'LineString', coordinates: [[x1, y1], [x2, y2]] } };
}

function room(
  id: string,
  ring: Array<[number, number]>,
  properties: MapRoomFeature['properties'],
): MapRoomFeature {
  return { id, geometry: { type: 'Polygon', coordinates: [ring] }, properties };
}

/** 公共走廊：x ∈ [-5, 4]，y ∈ [0, 6]，南边界即安装墙 y=0。 */
const corridor = room(
  'R-CORRIDOR',
  [[-5, 0], [4, 0], [4, 6], [-5, 6], [-5, 0]],
  { type: 'corridor', roomType: 'corridor', public: true },
);
/** 东侧邻接教室（非公共空间），内部隔墙不得参与参考墙。 */
const classroom = room(
  'R-CLASSROOM',
  [[4, 0], [12, 0], [12, 6], [4, 6], [4, 0]],
  { type: 'classroom', roomType: 'classroom', public: false },
);

describe('analyzeBeaconDeployment（合成走廊）', () => {
  const install = wall('W-INSTALL', -10, 0, 10, 0);
  const left = wall('W-LEFT', -5, 0, -5, 6);
  const right = wall('W-RIGHT', 4, 0, 4, 6);
  const rooms = [corridor, classroom];

  test('识别安装墙并左右各取最近垂直墙', () => {
    const plan = analyzeBeaconDeployment({ x: 0, y: 0.2 }, [install, left, right], rooms);
    expect(plan.installationWallId).toBe('W-INSTALL');
    expect(plan.installationDistanceM).toBeCloseTo(0.2, 6);
    expect(plan.references.map((r) => [r.wallId, r.side, Number(r.distanceM.toFixed(2))])).toEqual([
      ['W-LEFT', 'left', 5],
      ['W-RIGHT', 'right', 4],
    ]);
    expect(plan.references[0].hit.x).toBeCloseTo(-5, 9);
    expect(plan.references[0].hit.y).toBeCloseTo(0.2, 9);
    expect(plan.references[1].hit.x).toBeCloseTo(4, 9);
    expect(plan.references[1].hit.y).toBeCloseTo(0.2, 9);
  });

  test('平行墙不参与参考墙（即使更近）', () => {
    const parallel = wall('W-PARALLEL', -10, 0.8, 10, 0.8);
    const plan = analyzeBeaconDeployment({ x: 0, y: 0.2 }, [install, left, right, parallel], rooms);
    expect(plan.references.map((r) => r.wallId)).toEqual(['W-LEFT', 'W-RIGHT']);
  });

  test('单侧无垂直墙时只返回找到的一面', () => {
    const plan = analyzeBeaconDeployment({ x: 0, y: 0.2 }, [install, right], rooms);
    expect(plan.references.map((r) => [r.wallId, r.side])).toEqual([['W-RIGHT', 'right']]);
  });

  test('垂足落在墙段端点的墙面不可作为参考墙', () => {
    const stub = wall('W-STUB', 2, 0, 2, 0.2);
    const plan = analyzeBeaconDeployment({ x: 0, y: 0.2 }, [install, stub], rooms);
    expect(plan.references).toEqual([]);
  });

  test('邻接教室内部隔墙不参与参考墙', () => {
    const partition = wall('W-ROOM-PARTITION', 6, 0, 6, 6);
    const plan = analyzeBeaconDeployment({ x: 0, y: 0.2 }, [install, left, right, partition], rooms);
    expect(plan.references.map((r) => r.wallId)).toEqual(['W-LEFT', 'W-RIGHT']);
  });

  test('同侧多面垂直墙取垂直距离最近者', () => {
    const wideCorridor = room(
      'R-WIDE',
      [[-5, 0], [8, 0], [8, 3], [-5, 3], [-5, 0]],
      { type: 'corridor', roomType: 'corridor', public: true },
    );
    const nearer = wall('W-NEAR', 2, 0, 2, 3);
    const farther = wall('W-FAR', 8, 0, 8, 3);
    const plan = analyzeBeaconDeployment({ x: 0, y: 0.2 }, [install, nearer, farther], [wideCorridor]);
    expect(plan.references).toEqual([
      expect.objectContaining({ wallId: 'W-NEAR', side: 'right', distanceM: expect.closeTo(2, 6) }),
    ]);
  });

  test('安装墙坐标点顺序反转不影响左右判定', () => {
    const reversed = wall('W-INSTALL', 10, 0, -10, 0);
    const plan = analyzeBeaconDeployment({ x: 0, y: 0.2 }, [reversed, left, right], rooms);
    expect(plan.references.map((r) => [r.wallId, r.side])).toEqual([
      ['W-LEFT', 'left'],
      ['W-RIGHT', 'right'],
    ]);
  });

  test('信标不贴墙（2m 外）仍能匹配安装墙与参考墙', () => {
    const plan = analyzeBeaconDeployment({ x: 0, y: 2.5 }, [install, left, right], rooms);
    expect(plan.installationWallId).toBe('W-INSTALL');
    expect(plan.installationDistanceM).toBeCloseTo(2.5, 6);
    expect(plan.references.map((r) => r.wallId)).toEqual(['W-LEFT', 'W-RIGHT']);
  });

  test('信标落在公共空间多边形外时就近吸附', () => {
    const plan = analyzeBeaconDeployment({ x: 0, y: -0.4 }, [install, left, right], rooms);
    expect(plan.installationWallId).toBe('W-INSTALL');
    expect(plan.installationDistanceM).toBeCloseTo(0.4, 6);
  });

  test('信标不在任何公共空间附近时返回空结果', () => {
    const plan = analyzeBeaconDeployment({ x: 0, y: -5 }, [install, left, right], rooms);
    expect(plan).toEqual({
      installationWallId: null,
      installationDistanceM: null,
      references: [],
    });
  });
});

describe('analyzeBeaconDeployment（真实地图包 2026-11-21.1）', () => {
  test('BK-Q-F1-012 只在走道公共空间内选墙，不选教室内墙', () => {
    const floor = PUBLIC_MAP.floors['1'];
    const walls = floor.geometry.walls;
    const rooms = floor.geometry.rooms ?? [];
    const beacon = PUBLIC_MAP.beacons.find((b) => b.beaconId === 'BK-Q-F1-012');
    expect(beacon).toBeDefined();

    const plan = analyzeBeaconDeployment({ x: beacon!.x, y: beacon!.y }, walls, rooms);

    // 安装墙为走道西墙，信标距墙约 0.30m。
    expect(plan.installationWallId).toBe('F1-W-0908');
    expect(plan.installationDistanceM).toBeCloseTo(0.3, 3);
    // 参考墙取走道北墙（与垂线平行），不是教室内隔墙。
    expect(plan.references.map((r) => [r.wallId, r.side])).toEqual([['F1-W-0476', 'right']]);
    expect(plan.references.map((r) => r.wallId)).not.toContain('F1-W-0323');
    expect(plan.references.map((r) => r.wallId)).not.toContain('F1-W-0334');
    for (const reference of plan.references) {
      expect(reference.distanceM).toBeGreaterThan(0);
      expect(Number.isFinite(reference.hit.x)).toBe(true);
      expect(Number.isFinite(reference.hit.y)).toBe(true);
    }
  });

  test('BK-01-006 连通公共空间并集内左右各取到参考墙', () => {
    const floor = PUBLIC_MAP.floors['1'];
    const walls = floor.geometry.walls;
    const rooms = floor.geometry.rooms ?? [];
    const beacon = PUBLIC_MAP.beacons.find((b) => b.beaconId === 'BK-01-006');
    expect(beacon).toBeDefined();

    const plan = analyzeBeaconDeployment({ x: beacon!.x, y: beacon!.y }, walls, rooms);

    // 信标点在竖向砖墙上；墙角的 0.2m 斜向补段不作为安装墙。
    expect(plan.installationWallId).toBe('F1-W-1268');
    expect(plan.installationDistanceM).toBeCloseTo(0, 2);
    // 南侧取 F1-W-0221；北侧取双层墙里信标一侧的 F1-W-0476（其外侧为 F1-W-0538）。
    expect(plan.references.map((r) => [r.wallId, r.side])).toEqual([
      ['F1-W-0221', 'left'],
      ['F1-W-0476', 'right'],
    ]);
    expect(plan.references[0].distanceM).toBeCloseTo(5.065, 2);
    expect(plan.references[1].distanceM).toBeCloseTo(18.479, 2);
  });

  test('地图包预计算 deployment 与本地算法同口径（选中即展示的字段来源）', () => {
    const floor = PUBLIC_MAP.floors['1'];
    for (const beacon of PUBLIC_MAP.beacons) {
      expect(beacon.deployment).toBeTruthy();
      expect(beacon.deployment?.references.length).toBeGreaterThan(0);
    }
    for (const beaconId of ['BK-Q-F1-012', 'BK-01-006']) {
      const beacon = PUBLIC_MAP.beacons.find((b) => b.beaconId === beaconId);
      expect(beacon?.deployment).toBeTruthy();
      const local = analyzeBeaconDeployment(
        { x: beacon!.x, y: beacon!.y },
        floor.geometry.walls,
        floor.geometry.rooms ?? [],
      );
      const precomputed = beacon!.deployment!;
      expect(precomputed.installationWallId).toBe(local.installationWallId);
      expect(precomputed.installationDistanceM).toBeCloseTo(local.installationDistanceM ?? NaN, 6);
      expect(precomputed.references.map((r) => [r.wallId, r.side])).toEqual(
        local.references.map((r) => [r.wallId, r.side]),
      );
      local.references.forEach((reference, index) => {
        expect(precomputed.references[index].distanceM).toBeCloseTo(reference.distanceM, 6);
        expect(precomputed.references[index].hit.x).toBeCloseTo(reference.hit.x, 6);
        expect(precomputed.references[index].hit.y).toBeCloseTo(reference.hit.y, 6);
      });
    }
  });
});
