// 管理后台 API 类型定义（与 backend/pathai_backend/routers/admin_*.py 响应结构一一对应）。

export interface MePayload {
  username: string;
  mustChangePassword: boolean;
  csrfToken: string;
}

export interface SiteSummary {
  siteId: string;
  latestVersion: string;
  publishedAt: string;
  floorCount: number;
  placeCount: number;
  beaconCount: number;
}

export interface VersionItem {
  mapVersion: string;
  publishedAt: string;
  isLatest: boolean;
}

export interface VersionsPayload {
  siteId: string;
  latestVersion: string;
  items: VersionItem[];
}

export interface FloorInfo {
  floorId: string;
  displayName: string;
  level: number;
}

export interface PlaceView {
  placeId: string;
  floorId: string;
  nodeId: string;
  /** 由节点坐标解析；缺失时为 null。 */
  x: number | null;
  y: number | null;
  accessible: boolean;
  name: string;
  spokenName: string;
  aliases: string[];
  type: string;
}

export interface BeaconView {
  beaconId: string;
  floorId: string;
  x: number;
  y: number;
  txPower: number | null;
  installHeightM: number | null;
  uuid: string | null;
  major: number | null;
  minor: number | null;
  locationDesc: string;
  mountType: string | null;
  semanticTag: string | null;
}

export interface FloorRoom {
  id: string;
  label: string;
  type: string;
  points: [number, number][];
  centroid: [number, number] | null;
}

export interface FloorShape {
  id: string;
  label: string;
  points: [number, number][];
}

/** 单层紧凑几何：[minX, minY, maxX, maxY]（local_meters，原始 CAD 坐标系）。 */
export type FloorBounds = [number, number, number, number];

export interface FloorGeometryPayload {
  siteId: string;
  mapVersion: string;
  floorId: string;
  displayName: string;
  level: number | null;
  bounds: FloorBounds | null;
  rooms: FloorRoom[];
  /** 墙线段 [x1, y1, x2, y2]。 */
  walls: [number, number, number, number][];
  columns: FloorShape[];
  stairs: FloorShape[];
  elevators: FloorShape[];
}

export interface VersionDetailPayload {
  siteId: string;
  mapVersion: string;
  publishedAt: string;
  isLatest: boolean;
  floors: FloorInfo[];
  places: PlaceView[];
  beacons: BeaconView[];
}

/** 地点白名单覆盖字段（仅这些字段允许写入草稿）。 */
export interface PlacePatchValues {
  name?: string;
  spokenName?: string;
  aliases?: string[];
  type?: string;
}

/** 信标白名单覆盖字段：首版仅位置描述。 */
export interface BeaconPatchValues {
  locationDesc?: string;
}

/** 服务端草稿 patch：{places: {id: 覆盖字段}, beacons: {id: 覆盖字段}} */
export interface DraftPatch {
  places: Record<string, PlacePatchValues>;
  beacons: Record<string, BeaconPatchValues>;
}

export interface DraftPayload {
  exists: true;
  siteId: string;
  baseVersion: string;
  latestVersion: string;
  stale: boolean;
  updatedAtEpoch: number;
  patch: DraftPatch;
  places: PlaceView[];
  beacons: BeaconView[];
}

export type DraftResponse =
  | { exists: false; siteId: string; latestVersion: string }
  | DraftPayload;

/** PUT draft 请求体：顶层 {places, beacons}（与 GET draft 返回的 patch 同形）。 */
export type DraftPatchBody = DraftPatch;

export interface ValidateIssue {
  code: string;
  message: string;
}

export interface ValidatePayload {
  ok: boolean;
  stale: boolean;
  issues: ValidateIssue[];
}

export interface PublishPayload {
  siteId: string;
  baseVersion: string;
  mapVersion: string;
  publishedAt: string;
}

export interface RollbackPayload {
  siteId: string;
  rolledBackFrom: string;
  mapVersion: string;
  publishedAt: string;
}

export type DraftResetPayload = {
  exists: false;
  siteId: string;
  latestVersion: string;
};
