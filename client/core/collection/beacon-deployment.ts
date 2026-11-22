/**
 * 蓝牙部署点位参考墙计算（纯几何，零 wx 依赖）：
 * - 公共空间取“连通的公共房间并集”（走廊/大厅/电梯厅等被门洞切断的多段多边形视为一个整体）；
 * - 安装墙：公共空间边界上距信标点最近、且垂足落在线段内的墙面；
 * - 参考墙：该公共空间内与安装墙垂直（即与信标到安装墙的垂线平行）的墙，
 *   要求垂足落在参考墙段内部，信标到垂足之间始终位于公共空间内，且不被其他墙体阻断；
 * - 距离口径：信标点到参考墙的垂直距离（最近点距离）。
 */
import type { MapRoomFeature, MapWallFeature } from '../../typings/api';

/** 垂直判定：两墙单位方向向量的点积绝对值上限。 */
export const PERPENDICULAR_DOT_MAX = 0.3;
/** 参考墙沿安装墙方向的最小偏移（米）：排除信标正对的墙角。 */
export const REFERENCE_MIN_OFFSET_M = 0.1;
/** 信标点落在公共空间多边形外时，就近吸附的最大距离（米）。 */
export const PUBLIC_SPACE_SNAP_M = 1;
/** 相邻公共房间边界间隙不超过该值（门洞/门槛建模误差）时，视为同一连通公共空间。 */
const PUBLIC_SPACE_CONNECT_GAP_M = 0.12;
/** 参考墙垂足距墙段端点的最小比例：端点处无法垂直测距。 */
const REFERENCE_EDGE_MARGIN = 0.02;
/** 有效墙面最小长度（米）：门垛、墙角补段等 0.2m 级碎段不作为安装墙或参考墙。 */
const MIN_WALL_LENGTH_M = 0.5;
/** 可见性检测允许贴近公共空间边界的距离（米，覆盖墙面到房间多边形的建模偏移）。 */
const SPACE_EDGE_TOLERANCE_M = 0.3;
/** 墙与公共空间边界关联时，沿法线采样的偏移（墙半厚 + 该值，覆盖双层墙/复合墙建模偏移）。 */
const WALL_ASSOCIATION_OFFSET_M = 0.3;
/** 可见性检测的采样间距（米）。 */
const VISIBILITY_SAMPLE_STEP_M = 0.5;
/** 阻挡判定：交点距测量线端点的最小比例/距墙段端点的最小距离。 */
const BLOCK_LINE_MARGIN = 0.02;
const BLOCK_WALL_ENDPOINT_MARGIN_M = 0.05;
/** 与测量线夹角小于该点积阈值（约 20°）的墙视为贴墙测量，不算阻挡。 */
const BLOCK_PARALLEL_DOT_MAX = 0.94;
/** 距离相等时的比较容差。 */
const DISTANCE_EPSILON = 1e-6;
/** 数据缺少 public 标记时，按房间类型兜底识别公共空间。 */
const PUBLIC_ROOM_TYPES = new Set([
  'corridor', 'lobby', 'hall', 'foyer', 'elevator_lobby', 'stair_lobby', 'staircase', 'entrance_hall',
]);

export interface DeploymentWallHit {
  x: number;
  y: number;
}

export interface DeploymentReference {
  wallId: string;
  side: 'left' | 'right';
  /** 信标点到参考墙的垂直距离（米，未取整）。 */
  distanceM: number;
  /** 参考墙上的测距点（垂足）。 */
  hit: DeploymentWallHit;
}

export interface BeaconDeployment {
  installationWallId: string | null;
  installationDistanceM: number | null;
  /** 按左、右排序；该侧无可测垂直墙时不出现。 */
  references: DeploymentReference[];
}

interface SegmentHit {
  x: number;
  y: number;
  distance: number;
  /** 最近点在线段上的参数（0/1 为端点）。 */
  t: number;
}

interface WallDirection {
  x: number;
  y: number;
}

interface WallSegment {
  wallIndex: number;
  wallId: string;
  x1: number;
  y1: number;
  x2: number;
  y2: number;
  thickness: number;
  dir: WallDirection;
}

interface NearestWall {
  segment: WallSegment;
  hit: SegmentHit;
}

function roomRings(room: MapRoomFeature): Array<Array<[number, number]>> {
  return room.geometry?.coordinates ?? [];
}

function isPublicRoom(room: MapRoomFeature): boolean {
  const properties = room.properties;
  if (properties?.public === true) {
    return true;
  }
  if (properties?.public === false) {
    return false;
  }
  const type = properties?.roomType ?? properties?.type ?? '';
  return PUBLIC_ROOM_TYPES.has(type);
}

/** 射线法判断点是否在环内。 */
function inRing(x: number, y: number, ring: Array<[number, number]>): boolean {
  let inside = false;
  for (let i = 0, j = ring.length - 1; i < ring.length; j = i, i += 1) {
    const xi = ring[i][0];
    const yi = ring[i][1];
    const xj = ring[j][0];
    const yj = ring[j][1];
    if ((yi > y) !== (yj > y) && x < ((xj - xi) * (y - yi)) / (yj - yi) + xi) {
      inside = !inside;
    }
  }
  return inside;
}

function inRoom(x: number, y: number, room: MapRoomFeature): boolean {
  const prepared = prepareRoom(room);
  const bounds = prepared.bounds;
  if (bounds === null
    || x < bounds.minX || x > bounds.maxX || y < bounds.minY || y > bounds.maxY) {
    return false;
  }
  return prepared.rings.some((ring) => inRing(x, y, ring));
}

interface PreparedRoom {
  rings: Array<Array<[number, number]>>;
  bounds: RoomBounds | null;
}

/** 房间多边形与包围盒只解析一次：逐墙 × 逐房间的空间判定会重复调用。 */
const preparedRoomCache = new WeakMap<MapRoomFeature, PreparedRoom>();

function computeBounds(rings: ReadonlyArray<Array<[number, number]>>): RoomBounds | null {
  let minX = Number.POSITIVE_INFINITY;
  let minY = Number.POSITIVE_INFINITY;
  let maxX = Number.NEGATIVE_INFINITY;
  let maxY = Number.NEGATIVE_INFINITY;
  for (const ring of rings) {
    for (const [x, y] of ring) {
      if (x < minX) minX = x;
      if (x > maxX) maxX = x;
      if (y < minY) minY = y;
      if (y > maxY) maxY = y;
    }
  }
  return Number.isFinite(minX) ? { minX, minY, maxX, maxY } : null;
}

function prepareRoom(room: MapRoomFeature): PreparedRoom {
  const cached = preparedRoomCache.get(room);
  if (cached !== undefined) {
    return cached;
  }
  const rings = roomRings(room);
  const prepared: PreparedRoom = { rings, bounds: computeBounds(rings) };
  preparedRoomCache.set(room, prepared);
  return prepared;
}

/** 点到线段最近点与距离（参数 t 已夹紧到 [0, 1]）。 */
function closestOnSegment(
  px: number,
  py: number,
  x1: number,
  y1: number,
  x2: number,
  y2: number,
): SegmentHit {
  const dx = x2 - x1;
  const dy = y2 - y1;
  const length2 = dx * dx + dy * dy;
  const t = length2 <= 0
    ? 0
    : Math.min(1, Math.max(0, ((px - x1) * dx + (py - y1) * dy) / length2));
  const x = x1 + t * dx;
  const y = y1 + t * dy;
  return { x, y, distance: Math.hypot(px - x, py - y), t };
}

/** 点到房间多边形边界的最短距离。 */
function distanceToRoom(x: number, y: number, room: MapRoomFeature): number {
  let best = Number.POSITIVE_INFINITY;
  for (const ring of prepareRoom(room).rings) {
    for (let i = 0; i < ring.length; i += 1) {
      const a = ring[i];
      const b = ring[(i + 1) % ring.length];
      const hit = closestOnSegment(x, y, a[0], a[1], b[0], b[1]);
      if (hit.distance < best) {
        best = hit.distance;
      }
    }
  }
  return best;
}

interface RoomBounds {
  minX: number;
  minY: number;
  maxX: number;
  maxY: number;
}

function roomBounds(room: MapRoomFeature): RoomBounds | null {
  return prepareRoom(room).bounds;
}

/** 两个包围盒之间的最短间隙（相交为 0）。 */
function boundsGap(a: RoomBounds, b: RoomBounds): number {
  const dx = Math.max(0, a.minX - b.maxX, b.minX - a.maxX);
  const dy = Math.max(0, a.minY - b.maxY, b.minY - a.maxY);
  return Math.hypot(dx, dy);
}

/** 点到包围盒的最短距离（盒内为 0），用于提前排除远处房间。 */
function boundsPointGap(bounds: RoomBounds, x: number, y: number): number {
  const dx = Math.max(bounds.minX - x, 0, x - bounds.maxX);
  const dy = Math.max(bounds.minY - y, 0, y - bounds.maxY);
  return Math.hypot(dx, dy);
}

function orientation(px: number, py: number, qx: number, qy: number, rx: number, ry: number): number {
  return (qy - py) * (rx - qx) - (qx - px) * (ry - qy);
}

function segmentsIntersect(
  ax1: number, ay1: number, ax2: number, ay2: number,
  bx1: number, by1: number, bx2: number, by2: number,
): boolean {
  const o1 = orientation(ax1, ay1, ax2, ay2, bx1, by1);
  const o2 = orientation(ax1, ay1, ax2, ay2, bx2, by2);
  const o3 = orientation(bx1, by1, bx2, by2, ax1, ay1);
  const o4 = orientation(bx1, by1, bx2, by2, ax2, ay2);
  return (o1 > 0) !== (o2 > 0) && (o3 > 0) !== (o4 > 0);
}

function segmentDistance(
  ax1: number, ay1: number, ax2: number, ay2: number,
  bx1: number, by1: number, bx2: number, by2: number,
): number {
  if (segmentsIntersect(ax1, ay1, ax2, ay2, bx1, by1, bx2, by2)) {
    return 0;
  }
  return Math.min(
    closestOnSegment(ax1, ay1, bx1, by1, bx2, by2).distance,
    closestOnSegment(ax2, ay2, bx1, by1, bx2, by2).distance,
    closestOnSegment(bx1, by1, ax1, ay1, ax2, ay2).distance,
    closestOnSegment(bx2, by2, ax1, ay1, ax2, ay2).distance,
  );
}

/** 两个房间多边形的边界间隙是否不超过 limit（包围盒预筛，避免全量边遍历）。 */
function roomsConnected(
  a: MapRoomFeature, aBounds: RoomBounds | null,
  b: MapRoomFeature, bBounds: RoomBounds | null,
  limit: number,
): boolean {
  if (aBounds === null || bBounds === null || boundsGap(aBounds, bBounds) > limit) {
    return false;
  }
  const ringsA = roomRings(a);
  const ringsB = roomRings(b);
  for (const ra of ringsA) {
    for (let i = 0; i < ra.length; i += 1) {
      const [ax1, ay1] = ra[i];
      const [ax2, ay2] = ra[(i + 1) % ra.length];
      for (const rb of ringsB) {
        for (let j = 0; j < rb.length; j += 1) {
          const [bx1, by1] = rb[j];
          const [bx2, by2] = rb[(j + 1) % rb.length];
          if (segmentDistance(ax1, ay1, ax2, ay2, bx1, by1, bx2, by2) <= limit) {
            return true;
          }
        }
      }
    }
  }
  return false;
}

/**
 * 把种子公共房间沿门洞/边界间隙扩展到整个连通的公共空间。
 * 走廊常被门洞切成多段多边形，只取单段会漏掉相邻段外侧的可测参考墙。
 */
function expandPublicSpaces(
  seeds: readonly MapRoomFeature[],
  publicRooms: readonly MapRoomFeature[],
): MapRoomFeature[] {
  const boundsByRoom = new Map<MapRoomFeature, RoomBounds | null>();
  for (const room of publicRooms) {
    boundsByRoom.set(room, roomBounds(room));
  }
  const selected = new Set<MapRoomFeature>(seeds);
  const queue: MapRoomFeature[] = [...seeds];
  for (let head = 0; head < queue.length; head += 1) {
    const current = queue[head];
    const currentBounds = boundsByRoom.get(current) ?? null;
    for (const room of publicRooms) {
      if (selected.has(room)) {
        continue;
      }
      if (!roomsConnected(current, currentBounds, room, boundsByRoom.get(room) ?? null, PUBLIC_SPACE_CONNECT_GAP_M)) {
        continue;
      }
      selected.add(room);
      queue.push(room);
    }
  }
  return queue;
}

/**
 * 信标所在公共空间：优先包含信标点的公共房间；点落在墙厚/多边形外时，
 * 吸附 PUBLIC_SPACE_SNAP_M 内所有相邻公共房间，再扩展为连通公共空间并集。
 */
function findPublicSpaces(
  point: { x: number; y: number },
  rooms: readonly MapRoomFeature[],
): MapRoomFeature[] {
  const publicRooms = rooms.filter(isPublicRoom);
  if (publicRooms.length === 0) {
    return [];
  }
  let seeds = publicRooms.filter((room) => inRoom(point.x, point.y, room));
  if (seeds.length === 0) {
    seeds = publicRooms.filter((room) => distanceToRoom(point.x, point.y, room) <= PUBLIC_SPACE_SNAP_M);
  }
  if (seeds.length === 0) {
    return [];
  }
  return expandPublicSpaces(seeds, publicRooms);
}

/** 统一墙段单位方向（优先 +x，其次 +y），保证左右判定不随坐标点顺序变化。 */
function unitDirection(x1: number, y1: number, x2: number, y2: number): WallDirection | null {
  let dx = x2 - x1;
  let dy = y2 - y1;
  const length = Math.hypot(dx, dy);
  if (length <= 1e-9) {
    return null;
  }
  dx /= length;
  dy /= length;
  if (dx < -1e-9 || (Math.abs(dx) <= 1e-9 && dy < 0)) {
    dx = -dx;
    dy = -dy;
  }
  return { x: dx, y: dy };
}

function collectSegments(walls: readonly MapWallFeature[]): WallSegment[] {
  const segments: WallSegment[] = [];
  for (let wallIndex = 0; wallIndex < walls.length; wallIndex += 1) {
    const wall = walls[wallIndex];
    const coords = wall.geometry?.coordinates ?? [];
    const thickness = wall.properties?.thickness ?? 0.2;
    for (let i = 0; i + 1 < coords.length; i += 1) {
      const [x1, y1] = coords[i];
      const [x2, y2] = coords[i + 1];
      if (Math.hypot(x2 - x1, y2 - y1) < MIN_WALL_LENGTH_M) {
        continue;
      }
      const dir = unitDirection(x1, y1, x2, y2);
      if (dir === null) {
        continue;
      }
      segments.push({ wallIndex, wallId: wall.id ?? '', x1, y1, x2, y2, thickness, dir });
    }
  }
  return segments;
}

/**
 * 墙段是否围合该公共空间：沿墙段多点向两侧法线偏移半个墙厚再取样，
 * 任一点落在公共空间内即视为边界墙。房间内隔墙/门洞侧墙会被排除。
 */
function boundsPublicSpace(segment: WallSegment, spaces: readonly MapRoomFeature[]): boolean {
  const offset = segment.thickness / 2 + WALL_ASSOCIATION_OFFSET_M;
  const nx = -segment.dir.y;
  const ny = segment.dir.x;
  // 先用“墙段包围盒外扩 offset”排除远处的房间，避免逐点做多边形判定。
  const minX = Math.min(segment.x1, segment.x2) - offset;
  const maxX = Math.max(segment.x1, segment.x2) + offset;
  const minY = Math.min(segment.y1, segment.y2) - offset;
  const maxY = Math.max(segment.y1, segment.y2) + offset;
  for (const space of spaces) {
    const bounds = roomBounds(space);
    if (bounds === null
      || bounds.maxX < minX || bounds.minX > maxX
      || bounds.maxY < minY || bounds.minY > maxY) {
      continue;
    }
    for (let step = 1; step < 10; step += 1) {
      const ratio = step / 10;
      const x = segment.x1 + (segment.x2 - segment.x1) * ratio;
      const y = segment.y1 + (segment.y2 - segment.y1) * ratio;
      if (inRoom(x + nx * offset, y + ny * offset, space)
        || inRoom(x - nx * offset, y - ny * offset, space)) {
        return true;
      }
    }
  }
  return false;
}

/** 信标点到参考墙垂足之间是否始终位于公共空间内（允许贴边建模误差）。 */
function lineStaysInPublicSpace(
  point: { x: number; y: number },
  hit: DeploymentWallHit,
  spaces: readonly MapRoomFeature[],
): boolean {
  const length = Math.hypot(hit.x - point.x, hit.y - point.y);
  const steps = Math.max(10, Math.ceil(length / VISIBILITY_SAMPLE_STEP_M));
  for (let step = 1; step < steps; step += 1) {
    const ratio = step / steps;
    const x = point.x + (hit.x - point.x) * ratio;
    const y = point.y + (hit.y - point.y) * ratio;
    const inSpace = spaces.some((space) => {
      const bounds = roomBounds(space);
      if (bounds === null || boundsPointGap(bounds, x, y) > SPACE_EDGE_TOLERANCE_M) {
        return false;
      }
      return inRoom(x, y, space)
        || distanceToRoom(x, y, space) <= SPACE_EDGE_TOLERANCE_M;
    });
    if (!inSpace) {
      return false;
    }
  }
  return true;
}

/**
 * 测量线是否被其他墙体阻断：两墙端点相接（T 型墙角/门垛）不算阻挡。
 * 安装墙与目标墙自身、近平行（贴墙测量）的墙都跳过。
 */
function lineBlockedByWalls(
  point: { x: number; y: number },
  hit: DeploymentWallHit,
  install: WallSegment,
  target: WallSegment,
  segments: readonly WallSegment[],
): boolean {
  const dx = hit.x - point.x;
  const dy = hit.y - point.y;
  if (Math.hypot(dx, dy) <= 1e-9) {
    return false;
  }
  const lineDirX = dx;
  const lineDirY = dy;
  const lineLength = Math.hypot(lineDirX, lineDirY);
  const unitX = lineDirX / lineLength;
  const unitY = lineDirY / lineLength;
  for (const segment of segments) {
    if (segment === target || segment === install) {
      continue;
    }
    if (segment.wallId !== '' && segment.wallId === install.wallId) {
      continue;
    }
    if (Math.abs(unitX * segment.dir.x + unitY * segment.dir.y) > BLOCK_PARALLEL_DOT_MAX) {
      continue;
    }
    const wallDx = segment.x2 - segment.x1;
    const wallDy = segment.y2 - segment.y1;
    const denominator = lineDirX * wallDy - lineDirY * wallDx;
    if (Math.abs(denominator) <= 1e-12) {
      continue;
    }
    const offsetX = segment.x1 - point.x;
    const offsetY = segment.y1 - point.y;
    const t = (offsetX * wallDy - offsetY * wallDx) / denominator;
    const u = (offsetX * lineDirY - offsetY * lineDirX) / denominator;
    if (t <= BLOCK_LINE_MARGIN || t >= 1 - BLOCK_LINE_MARGIN) {
      continue;
    }
    if (u <= 0 || u >= 1) {
      continue;
    }
    const wallLength = Math.hypot(wallDx, wallDy);
    const endpointDist = Math.min(u, 1 - u) * wallLength;
    if (endpointDist <= BLOCK_WALL_ENDPOINT_MARGIN_M) {
      continue;
    }
    return true;
  }
  return false;
}

function pickInstallationWall(point: { x: number; y: number }, segments: readonly WallSegment[]): NearestWall | null {
  let best: NearestWall | null = null;
  for (const segment of segments) {
    const hit = closestOnSegment(point.x, point.y, segment.x1, segment.y1, segment.x2, segment.y2);
    if (best === null || hit.distance < best.hit.distance - DISTANCE_EPSILON) {
      best = { segment, hit };
      continue;
    }
    if (Math.abs(hit.distance - best.hit.distance) > DISTANCE_EPSILON) {
      continue;
    }
    // 墙角等距时优先取垂足在墙段内部的墙面，避免选中与安装墙垂直的墙角墙。
    const interior = hit.t > 1e-9 && hit.t < 1 - 1e-9;
    const bestInterior = best.hit.t > 1e-9 && best.hit.t < 1 - 1e-9;
    if (interior && !bestInterior) {
      best = { segment, hit };
    }
  }
  return best;
}

/**
 * 计算信标点位的安装墙与左右参考测距墙。
 * 仅在信标所在公共空间内取墙；该侧没有可垂直测距的墙面时不返回参考墙。
 */
export function analyzeBeaconDeployment(
  point: { x: number; y: number },
  walls: readonly MapWallFeature[],
  rooms: readonly MapRoomFeature[],
): BeaconDeployment {
  const empty: BeaconDeployment = {
    installationWallId: null,
    installationDistanceM: null,
    references: [],
  };
  const spaces = findPublicSpaces(point, rooms);
  if (spaces.length === 0) {
    return empty;
  }
  const allSegments = collectSegments(walls);
  const boundarySegments = allSegments.filter((segment) => boundsPublicSpace(segment, spaces));
  const install = pickInstallationWall(point, boundarySegments);
  if (install === null) {
    return empty;
  }

  const installDir = install.segment.dir;
  const bySide: Record<'left' | 'right', DeploymentReference | null> = {
    left: null,
    right: null,
  };

  for (const segment of boundarySegments) {
    if (segment.wallIndex === install.segment.wallIndex) {
      continue;
    }
    if (segment.wallId !== '' && segment.wallId === install.segment.wallId) {
      continue;
    }
    if (Math.abs(installDir.x * segment.dir.x + installDir.y * segment.dir.y) > PERPENDICULAR_DOT_MAX) {
      continue;
    }
    const hit = closestOnSegment(point.x, point.y, segment.x1, segment.y1, segment.x2, segment.y2);
    // 垂足落在端点说明信标垂线并没有打到该墙的墙面，无法垂直测距。
    if (hit.t <= REFERENCE_EDGE_MARGIN || hit.t >= 1 - REFERENCE_EDGE_MARGIN) {
      continue;
    }
    const along = (hit.x - point.x) * installDir.x + (hit.y - point.y) * installDir.y;
    if (Math.abs(along) < REFERENCE_MIN_OFFSET_M) {
      continue;
    }
    const side: 'left' | 'right' = along > 0 ? 'right' : 'left';
    const current = bySide[side];
    if (current !== null && hit.distance >= current.distanceM - DISTANCE_EPSILON) {
      continue;
    }
    if (!lineStaysInPublicSpace(point, hit, spaces)) {
      continue;
    }
    if (lineBlockedByWalls(point, hit, install.segment, segment, allSegments)) {
      continue;
    }
    bySide[side] = {
      wallId: segment.wallId,
      side,
      distanceM: hit.distance,
      hit: { x: hit.x, y: hit.y },
    };
  }

  const references: DeploymentReference[] = [];
  if (bySide.left !== null) {
    references.push(bySide.left);
  }
  if (bySide.right !== null) {
    references.push(bySide.right);
  }
  return {
    installationWallId: install.segment.wallId === '' ? null : install.segment.wallId,
    installationDistanceM: install.hit.distance,
    references,
  };
}
