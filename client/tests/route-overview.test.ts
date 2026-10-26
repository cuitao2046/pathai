/**
 * buildRouteOverview：REQ-NAV 路径概要（总距离/楼层变化跨层显式提示/终点）。
 */
import { buildRouteOverview, routeFloorIds } from '../core/nav/route-overview';
import type { GeoPosition, RouteInfo } from '../typings/api';

function makeRoute(over: Partial<RouteInfo> = {}): RouteInfo {
  return {
    routeId: 'r-1',
    mapVersion: '2026-10-25.1',
    routeRulesVersion: '1.0.0',
    distanceM: 45.6,
    estimatedDurationSec: null,
    polyline: [],
    segments: [],
    fallbackType: 'none',
    safeForBlind: true,
    riskNotices: [],
    ...over,
  };
}

const p = (x: number, y: number, floorId?: string | null): GeoPosition => ({
  x,
  y,
  floorId,
});

const floorName = (id: string): string | undefined =>
  ({ 1: '一楼', 2: '二楼' })[id as '1' | '2'];

describe('routeFloorIds', () => {
  test('连续重复合并、空楼层跳过、集合去重', () => {
    const route = makeRoute({
      polyline: [p(0, 0, '1'), p(1, 0, '1'), p(2, 0, null), p(3, 0, '2'), p(4, 0, '2')],
    });
    expect(routeFloorIds(route)).toEqual(['1', '2']);
  });
});

describe('buildRouteOverview（REQ-NAV 路径概要）', () => {
  test('跨楼层：途经列表 + 显式电梯提示 + 起讫楼层 + 终点', () => {
    const route = makeRoute({
      distanceM: 60.4,
      polyline: [p(0, 0, '1'), p(5, 0, '1'), p(5, 5, '2')],
    });
    const text = buildRouteOverview(route, '资源教室', floorName);
    expect(text).toBe('路线概要：全程约 60 米，途经一楼、二楼，需要乘坐电梯从一楼到二楼，终点资源教室。');
    expect(text).toContain('电梯'); // 跨层必须显式提示
  });

  test('单楼层：楼层内走到终点，不提电梯', () => {
    const route = makeRoute({ distanceM: 45.6, polyline: [p(0, 0, '1'), p(9, 9, '1')] });
    const text = buildRouteOverview(route, '图书馆', floorName);
    expect(text).toBe('路线概要：全程约 45 米，在一楼内走到图书馆。');
    expect(text).not.toContain('电梯');
  });

  test('楼层信息缺失：退回距离+终点，且距离至少 1 米', () => {
    const route = makeRoute({ distanceM: 0.2, polyline: [p(0, 0), p(1, 0)] });
    expect(buildRouteOverview(route, '办公室', floorName)).toBe(
      '路线概要：全程约 1 米，终点办公室。',
    );
  });
});
