/**
 * 路径概要播报文本（REQ-NAV「路径概要先行」）：
 * 总距离 + 楼层变化（跨楼层必须显式提示电梯）+ 终点。
 * 仅基于服务端已返回的路线数据生成，不新增服务端契约；
 * 楼层序列取自 polyline 的 floorId（盲模式跨层恒走电梯，与路线规则一致）。
 */
import type { RouteInfo } from '../../typings/api';

/** 按 polyline 顺序提取去重楼层（连续重复合并，A→B→A 保留首次出现顺序）。 */
export function routeFloorIds(route: RouteInfo): string[] {
  const ordered: string[] = [];
  for (const point of route.polyline) {
    const id = point.floorId;
    if (id === undefined || id === null || id === '') {
      continue;
    }
    if (ordered[ordered.length - 1] !== id) {
      ordered.push(id);
    }
  }
  return [...new Set(ordered)];
}

export function buildRouteOverview(
  route: RouteInfo,
  destinationName: string,
  floorName: (floorId: string) => string | undefined,
): string {
  const meters = Math.max(1, Math.round(route.distanceM));
  const floors = routeFloorIds(route);

  if (floors.length >= 2) {
    const names = floors.map((id) => floorName(id) ?? id).join('、');
    const first = floorName(floors[0]) ?? floors[0];
    const last = floorName(floors[floors.length - 1]) ?? floors[floors.length - 1];
    // 跨楼层必须显式提示（REQ-NAV）：电梯 + 起讫楼层
    return `路线概要：全程约 ${meters} 米，途经${names}，需要乘坐电梯从${first}到${last}，终点${destinationName}。`;
  }
  if (floors.length === 1) {
    const name = floorName(floors[0]) ?? floors[0];
    return `路线概要：全程约 ${meters} 米，在${name}内走到${destinationName}。`;
  }
  return `路线概要：全程约 ${meters} 米，终点${destinationName}。`;
}
