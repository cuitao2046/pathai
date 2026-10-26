/**
 * 楼层地图几何预处理（纯函数，零 wx 依赖，供渲染与单测复用）。
 *
 * 墙体在地图包中是 Feature 对象数组（F1 2436 根 / F2 2006 根），逐帧遍历
 * 对象开销大：这里一次性扁平化为 Float32Array（每段 4 个 float：x1,y1,x2,y2），
 * 绘制时直接线性读取。
 */
import type { MapFloorData, MapWallFeature } from '../../typings/api';

export interface FloorBounds {
  minX: number;
  minY: number;
  maxX: number;
  maxY: number;
}

/** 墙体扁平化：每段 4 float（x1,y1,x2,y2），坐标为局部米制（y 北向）。 */
export function flattenWalls(floors: Record<string, MapFloorData>, floorId: string): Float32Array {
  const walls = floors[floorId]?.geometry.walls ?? [];
  let segCount = 0;
  for (const wall of walls) {
    segCount += Math.max(0, wall.geometry.coordinates.length - 1);
  }
  const out = new Float32Array(segCount * 4);
  let i = 0;
  for (const wall of walls) {
    const coords = wall.geometry.coordinates;
    for (let k = 0; k + 1 < coords.length; k += 1) {
      out[i++] = coords[k][0];
      out[i++] = coords[k][1];
      out[i++] = coords[k + 1][0];
      out[i++] = coords[k + 1][1];
    }
  }
  return out;
}

/** 扁平墙段的包围盒（供视口 fit）。 */
export function flatBounds(segments: Float32Array): FloorBounds {
  if (segments.length < 4) {
    return { minX: 0, minY: 0, maxX: 1, maxY: 1 };
  }
  let minX = Infinity;
  let minY = Infinity;
  let maxX = -Infinity;
  let maxY = -Infinity;
  for (let i = 0; i + 3 < segments.length; i += 4) {
    const x1 = segments[i];
    const y1 = segments[i + 1];
    const x2 = segments[i + 2];
    const y2 = segments[i + 3];
    if (x1 < minX) minX = x1;
    if (x2 < minX) minX = x2;
    if (y1 < minY) minY = y1;
    if (y2 < minY) minY = y2;
    if (x1 > maxX) maxX = x1;
    if (x2 > maxX) maxX = x2;
    if (y1 > maxY) maxY = y1;
    if (y2 > maxY) maxY = y2;
  }
  return { minX, minY, maxX, maxY };
}

/** 楼层列表按 level 升序（楼层药丸渲染顺序）。 */
export function floorsSortedByLevel(
  floors: Record<string, MapFloorData>,
): Array<{ floorId: string; displayName: string; level: number }> {
  return Object.entries(floors)
    .map(([floorId, data]) => ({
      floorId,
      displayName: data.displayName,
      level: data.level,
    }))
    .sort((a, b) => a.level - b.level);
}

/** 墙体要素厚度（米）取值，缺省 0.2。 */
export function wallThickness(wall: MapWallFeature): number {
  return typeof wall.properties?.thickness === 'number' && wall.properties.thickness > 0
    ? wall.properties.thickness
    : 0.2;
}

/** 地点名称标注项（世界坐标 + 显示文本）。 */
export interface PlaceLabel {
  x: number;
  y: number;
  text: string;
}

/**
 * 当前楼层的地点名称标注（空间语义：教室/办公室/卫生间等）：
 * place 经 nodeId 查拓扑节点坐标；查不到坐标的位置跳过（数据自洽性由
 * 后端 test_places_reference_existing_nodes 保证，此处防御）。
 */
export function floorPlaceLabels(
  floors: Record<string, MapFloorData>,
  places: readonly { placeId: string; name: string; floorId: string; nodeId: string }[],
  floorId: string,
): PlaceLabel[] {
  const nodes = floors[floorId]?.topology.nodes;
  if (nodes === undefined) {
    return [];
  }
  const coordById = new Map(nodes.map((n) => [n.id, n.coordinates] as const));
  const labels: PlaceLabel[] = [];
  for (const place of places) {
    if (place.floorId !== floorId) {
      continue;
    }
    const coord = coordById.get(place.nodeId);
    if (coord !== undefined) {
      labels.push({ x: coord[0], y: coord[1], text: place.name });
    }
  }
  return labels;
}
