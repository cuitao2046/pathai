/**
 * 后端契约 TS 镜像 —— 逐字段对齐 backend/pathai_backend/schemas/*.py。
 *
 * 修改后端 schema 时必须同步本文件。约定：
 * - 后端 `X | None` 且响应中可能显式为 null 的字段 → `X | null`（可选性按响应实测标注）；
 * - datetime → ISO8601 字符串（IsoDateTime）；
 * - 客户端按稳定错误码分支，不依赖 message（docs/24 §7.2）。
 */

/** ISO8601 时间字符串（后端 pydantic datetime 序列化产物）。 */
export type IsoDateTime = string;

// ---------------------------------------------------------------------------
// 枚举（schemas/common.py）
// ---------------------------------------------------------------------------

export type IntentType =
  | 'start_navigation'
  | 'query_location'
  | 'search_facility'
  | 'cancel'
  | 'end_navigation'
  | 'repeat_instruction'
  | 'unknown';

export type LocationQuality = 'reliable' | 'degraded' | 'unreliable' | 'lost';

/** 可用于驱动转向/到达判断的质量集合；unreliable/lost 必须进入恢复流程。 */
export type TrustedQuality = 'reliable' | 'degraded';

export type SessionStatus = 'active' | 'recovering' | 'arrived' | 'ended';

export type InstructionAction =
  | 'continue'
  | 'turn_left'
  | 'turn_right'
  | 'enter_elevator'
  | 'exit_elevator'
  | 'arrive'
  | 'stop'
  | 'relocalize';

export type InstructionPriority = 'normal' | 'turn' | 'safety';

export type NavEvent = 'instruction' | 'relocalizing' | 'reroute' | 'arrived';

export type EndReason = 'user_cancelled' | 'user_finished' | 'arrived' | 'system_error';

export type RouteFallbackType = 'none' | 'best_door' | 'all_doors' | 'wall_fallback';

export type UserMode = 'blind' | 'normal' | 'wheelchair';

// ---------------------------------------------------------------------------
// 错误信封（backend/pathai_backend/errors.py + docs/24 §7.2）
// ---------------------------------------------------------------------------

export interface ApiErrorBody {
  code: ApiErrorCode;
  message: string;
  retryable: boolean;
  details: Record<string, unknown>;
}

export interface ApiErrorEnvelope {
  error: ApiErrorBody;
  requestId: string;
}

/**
 * 服务端稳定错误码全集（backend/pathai_backend 各 ApiError 抛点，2026-10 盘点）；
 * 另含客户端合成码（NETWORK_ERROR/REQUEST_TIMEOUT，本地生成不发往服务端）。
 */
export type ApiErrorCode =
  // 通用
  | 'INVALID_REQUEST'
  | 'NOT_FOUND'
  | 'METHOD_NOT_ALLOWED'
  | 'PAYLOAD_TOO_LARGE'
  | 'HTTP_ERROR'
  | 'INTERNAL_ERROR'
  | 'FORBIDDEN'
  // 认证（API-01）
  | 'AUTH_CODE_INVALID'
  | 'AUTH_PROVIDER_UNAVAILABLE'
  | 'UNAUTHENTICATED'
  | 'TOKEN_EXPIRED'
  // 地图（API-02/03）
  | 'SITE_NOT_FOUND'
  | 'MAP_NOT_PUBLISHED'
  | 'MAP_VERSION_CONFLICT'
  // 意图（API-04）
  | 'PLACE_NOT_FOUND'
  // 定位（API-05）
  | 'STALE_LOCATION_BATCH'
  // 路线（API-06）
  | 'ROUTE_NOT_FOUND'
  | 'ACCESSIBLE_ROUTE_NOT_FOUND'
  | 'LOCATION_UNRELIABLE'
  // 会话（API-06..09）
  | 'SESSION_NOT_FOUND'
  | 'SESSION_NOT_ACTIVE'
  | 'STALE_NAV_UPDATE'
  | 'IDEMPOTENCY_CONFLICT'
  // 客户端合成（本地生成，不发往服务端）
  | 'NETWORK_ERROR'
  | 'REQUEST_TIMEOUT'
  | 'MAP_UNAVAILABLE';

// ---------------------------------------------------------------------------
// 公共模型（schemas/common.py）
// ---------------------------------------------------------------------------

/** 局部米制坐标；floorId 在楼层不可靠时可为 null（docs/24 API-05 规则）。 */
export interface GeoPosition {
  x: number;
  y: number;
  floorId?: string | null;
}

/** 地标引用：只允许来自地图已发布地点，不得来自现场识别或模型生成。 */
export interface LandmarkRef {
  placeId: string;
  name: string;
  relativeDirection?: 'left' | 'right' | 'ahead' | 'behind' | null;
  distanceM?: number | null;
}

/** 指令触发条件：distance 按剩余距离、decision_point 按拓扑节点、immediate 立即。 */
export interface Trigger {
  type: 'distance' | 'decision_point' | 'immediate';
  valueM?: number | null;
  nodeId?: string | null;
}

/**
 * 结构化导航指令。instructionSequence 在会话内单调递增（去重/重播判定）；
 * expiresAt 当前恒为 null（视为不过期），但客户端保留判定逻辑。
 */
export interface NavigationInstruction {
  instructionId: string;
  instructionSequence: number;
  action: InstructionAction;
  text: string;
  distanceM?: number | null;
  landmark?: LandmarkRef | null;
  priority: InstructionPriority;
  trigger: Trigger;
  expiresAt?: IsoDateTime | null;
}

/** 意图候选/会话目的地共用的最小地点引用。 */
export interface PlaceCandidate {
  placeId: string;
  name: string;
  floorId: string;
}

// ---------------------------------------------------------------------------
// API-01 微信登录（schemas/auth.py）
// ---------------------------------------------------------------------------

export interface WechatLoginRequest {
  code: string;
}

export interface AuthResponse {
  accessToken: string;
  tokenType: 'Bearer';
  expiresIn: number;
  /** 匿名主体 ID：anon_<hex16>。 */
  userId: string;
}

// ---------------------------------------------------------------------------
// API-02 地图版本摘要 / API-03 地点查询（schemas/map.py）
// ---------------------------------------------------------------------------

export interface CoordinateSystem {
  unit: 'm';
  originDescription: string;
  headingReference: string;
}

export interface FloorSummary {
  floorId: string;
  displayName: string;
  level: number;
}

export interface MapVersionResponse {
  siteId: string;
  mapVersion: string;
  publishedAt: IsoDateTime;
  coordinateSystem: CoordinateSystem;
  floors: FloorSummary[];
  /** 契约要求 HTTPS；本地联调由客户端重写为 API 同源（sha256 校验不关闭）。 */
  dataUrl: string;
  /** 64 位小写 hex。 */
  dataSha256: string;
}

/** API-03 地点条目：只返回已发布且可导航的地点。 */
export interface Place {
  placeId: string;
  name: string;
  spokenName: string;
  aliases: string[];
  type: string;
  floorId: string;
  nodeId: string;
  accessible: boolean;
}

export interface PlacesResponse {
  mapVersion: string;
  items: Place[];
  nextCursor?: string | null;
}

// ---------------------------------------------------------------------------
// 地图包（dataUrl 下载内容，backend/map_package/schema.py 客户端消费子集）
// ---------------------------------------------------------------------------

/** 地图包信标条目：uuid/major/minor 同现同缺（C1 契约）。 */
export interface MapBeaconEntry {
  beaconId: string;
  floorId: string;
  x: number;
  y: number;
  txPower?: number | null;
  installHeightM?: number | null;
  uuid?: string | null;
  major?: number | null;
  minor?: number | null;
}

/** 地图包地点目录（字段与 API-03 Place 一致）。 */
export interface MapPlaceEntry {
  placeId: string;
  name: string;
  spokenName: string;
  aliases: string[];
  type: string;
  floorId: string;
  nodeId: string;
  accessible: boolean;
}

/** 地图包根结构：客户端只消费 siteId/mapVersion/places/beacons。 */
export interface MapPackageRoot {
  siteId: string;
  mapVersion: string;
  publishedAt: IsoDateTime;
  places: MapPlaceEntry[];
  beacons: MapBeaconEntry[];
}

// ---------------------------------------------------------------------------
// API-04 意图解析（schemas/intent.py）
// ---------------------------------------------------------------------------

export interface IntentParseRequest {
  siteId: string;
  mapVersion: string;
  text: string;
  sessionId?: string | null;
  locale?: string;
}

export interface IntentParseResponse {
  intentId: string;
  intent: IntentType;
  confidence: number;
  needsClarification: boolean;
  destination?: PlaceCandidate | null;
  candidates: PlaceCandidate[];
  responseText: string;
}

// ---------------------------------------------------------------------------
// API-05 位置估计（schemas/location.py）
// ---------------------------------------------------------------------------

export interface BeaconObservation {
  beaconId: string;
  /** 接受范围 -100..-20 dBm（工程默认，现场标定后冻结）。 */
  rssi: number;
  observedAt: IsoDateTime;
}

export interface ImuSample {
  timestampMs: number;
  gyroRadS: [number, number, number];
  accelMS2: [number, number, number];
}

export interface ImuPayload {
  samples: ImuSample[];
  headingDeg?: number | null;
}

export interface LocationEstimateRequest {
  siteId: string;
  mapVersion: string;
  /** 按 (subject, site) 全局单调递增，服务端拒收 <。 */
  sequence: number;
  collectedAt: IsoDateTime;
  floorHint?: string | null;
  beacons: BeaconObservation[];
  imu?: ImuPayload | null;
}

export interface MatchedArea {
  placeId: string;
  name: string;
}

export interface PositionEstimateResponse {
  estimateId: string;
  sequence: number;
  mapVersion: string;
  position?: GeoPosition | null;
  headingDeg?: number | null;
  confidence: number;
  quality: LocationQuality;
  matchedArea?: MatchedArea | null;
  algorithmVersion: string;
  observedAt: IsoDateTime;
  ignoredBeaconIds: string[];
}

// ---------------------------------------------------------------------------
// API-06..09 导航会话（schemas/nav.py）
// ---------------------------------------------------------------------------

/** 路线及按决策点组织的指令序列；首条指令即 segments[0]。 */
export interface RouteInfo {
  routeId: string;
  mapVersion: string;
  routeRulesVersion: string;
  distanceM: number;
  /** 后端当前恒为 null。 */
  estimatedDurationSec?: number | null;
  polyline: GeoPosition[];
  segments: NavigationInstruction[];
  fallbackType: RouteFallbackType;
  safeForBlind: boolean;
  riskNotices: string[];
}

export interface OriginPosition {
  x: number;
  y: number;
  floorId?: string | null;
  confidence: number;
  observedAt: IsoDateTime;
}

/** API-06 请求：仅在用户确认目的地后调用，须带 Idempotency-Key 请求头。 */
export interface CreateSessionRequest {
  siteId: string;
  mapVersion: string;
  origin: OriginPosition;
  destinationId: string;
  /** 缺省按最安全的 blind 模式（跨层强制电梯）。 */
  userMode?: UserMode;
  locale?: string;
}

export interface CreateSessionResponse {
  sessionId: string;
  status: SessionStatus;
  mapVersion: string;
  destination: PlaceCandidate;
  route: RouteInfo;
  createdAt: IsoDateTime;
}

/** 导航更新中的位置；不可信质量允许缺坐标，可信质量必须携带。 */
export interface LocationUpdate {
  x?: number | null;
  y?: number | null;
  floorId?: string | null;
  confidence: number;
  quality: LocationQuality;
  observedAt: IsoDateTime;
}

/** API-07 请求：每次提交一个已完成位置估计，sequence 按会话单调递增。 */
export interface NavUpdateRequest {
  sequence: number;
  location: LocationUpdate;
}

/** API-07 响应：instruction 可为 null，客户端必须处理。 */
export interface NavUpdateResponse {
  sessionId: string;
  status: SessionStatus;
  event: NavEvent;
  instruction: NavigationInstruction | null;
  routeChanged: boolean;
  processedSequence: number;
  route?: RouteInfo | null;
}

/** API-08 currentInstruction 的只读摘要。 */
export interface CurrentInstructionSummary {
  instructionId: string;
  text: string;
  action: InstructionAction;
}

export interface SessionQueryResponse {
  sessionId: string;
  status: SessionStatus;
  siteId: string;
  mapVersion: string;
  destination: PlaceCandidate;
  currentInstruction?: CurrentInstructionSummary | null;
  lastProcessedSequence?: number | null;
  updatedAt: IsoDateTime;
  endedAt?: IsoDateTime | null;
  endReason?: EndReason | null;
}

/** API-09 请求；reason=arrived 仅保留枚举兼容，到达判定权在服务端（客户端从不发送）。 */
export interface EndSessionRequest {
  reason: EndReason;
}

export interface EndSessionResponse {
  sessionId: string;
  status: 'ended';
  reason: EndReason;
  endedAt: IsoDateTime;
}
