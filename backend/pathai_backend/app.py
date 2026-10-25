from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import hmac
import heapq
import json
import math
import os
import secrets
import statistics
import uuid
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import delete, select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from pathai_backend.database import get_engine, get_session_factory
from pathai_backend.maps import map_package
from pathai_backend.models import AccessToken, AnalyticsAggregate, Base, IdempotencyRecord, LocationSequence, NavigationSession, User


ANALYTICS_RETENTION = timedelta(days=360)
ANALYTICS_CLEANUP_INTERVAL_SECONDS = 24 * 60 * 60

_validate_package = map_package._validate_package


def purge_expired_analytics(now: datetime | None = None) -> int:
    cutoff = utc_datetime(now or utcnow()) - ANALYTICS_RETENTION
    factory = getattr(app.state, "session_factory", None) or get_session_factory()
    with factory() as db:
        result = db.execute(delete(AnalyticsAggregate).where(AnalyticsAggregate.anonymized_at <= cutoff))
        db.commit()
        return result.rowcount or 0


async def _analytics_retention_worker() -> None:
    while True:
        await asyncio.sleep(ANALYTICS_CLEANUP_INTERVAL_SECONDS)
        await asyncio.to_thread(purge_expired_analytics)


@asynccontextmanager
async def lifespan(application: FastAPI):
    ensure_schema()
    purge_expired_analytics()
    cleanup_task = asyncio.create_task(_analytics_retention_worker())
    try:
        yield
    finally:
        cleanup_task.cancel()
        try:
            await cleanup_task
        except asyncio.CancelledError:
            pass


app = FastAPI(title="PathAI Backend", version="0.1.0", lifespan=lifespan)


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class WechatLogin(StrictModel):
    code: str = Field(min_length=1, max_length=512)


class IntentRequest(StrictModel):
    siteId: str
    mapVersion: str
    text: str = Field(min_length=1, max_length=500)
    sessionId: str | None = None
    locale: str = "zh-CN"


class BeaconObservation(StrictModel):
    beaconId: str
    rssi: int = Field(ge=-100, le=-20)
    observedAt: datetime


class LocationRequest(StrictModel):
    siteId: str
    mapVersion: str
    sequence: int = Field(ge=0)
    collectedAt: datetime
    floorHint: str | None = None
    beacons: list[BeaconObservation] = Field(max_length=100)
    imu: dict[str, Any] | None = None


class Position(StrictModel):
    x: float
    y: float
    floorId: str
    confidence: float = Field(ge=0, le=1)
    observedAt: datetime


class CreateSession(StrictModel):
    siteId: str
    mapVersion: str
    origin: Position
    destinationId: str
    userMode: str = "blind"
    locale: str = "zh-CN"


class UpdateLocation(StrictModel):
    x: float | None = None
    y: float | None = None
    floorId: str | None = None
    confidence: float = Field(ge=0, le=1)
    quality: str = "unreliable"
    observedAt: datetime


class SessionUpdate(StrictModel):
    sequence: int = Field(ge=0)
    location: UpdateLocation


class EndSession(StrictModel):
    reason: str = "user_cancelled"


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def utc_datetime(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def is_fresh(value: datetime, now: datetime | None = None, max_age: int = 10) -> bool:
    age = ((now or utcnow()) - utc_datetime(value)).total_seconds()
    return 0 <= age <= max_age


def fail(status: int, code: str, message: str, retryable: bool = False, details: dict[str, Any] | None = None):
    raise HTTPException(status_code=status, detail={"code": code, "message": message, "retryable": retryable, "details": details or {}})


@app.middleware("http")
async def request_id_middleware(request: Request, call_next):
    request.state.request_id = request.headers.get("X-Request-ID") or f"req_{uuid.uuid4().hex}"
    response = await call_next(request)
    response.headers["X-Request-ID"] = request.state.request_id
    return response


def error_body(request: Request, code: str, message: str, retryable: bool = False, details: dict[str, Any] | None = None):
    return {"error": {"code": code, "message": message, "retryable": retryable, "details": details or {}}, "requestId": getattr(request.state, "request_id", "req_unknown")}


@app.exception_handler(HTTPException)
async def http_error_handler(request: Request, exc: HTTPException):
    detail = exc.detail if isinstance(exc.detail, dict) else {"code": "HTTP_ERROR", "message": str(exc.detail), "retryable": False, "details": {}}
    return JSONResponse(status_code=exc.status_code, content={"error": detail, "requestId": getattr(request.state, "request_id", "req_unknown")}, headers=exc.headers)


@app.exception_handler(RequestValidationError)
async def validation_error_handler(request: Request, exc: RequestValidationError):
    return JSONResponse(status_code=400, content=error_body(request, "INVALID_REQUEST", "请求格式或字段范围无效", details={"fields": [".".join(str(x) for x in e["loc"]) for e in exc.errors()]}))


@app.exception_handler(Exception)
async def unexpected_error_handler(request: Request, exc: Exception):
    return JSONResponse(status_code=500, content=error_body(request, "INTERNAL_ERROR", "服务暂时不可用", retryable=True))


def db_session():
    factory = getattr(app.state, "session_factory", None) or get_session_factory()
    with factory() as session:
        yield session


def get_user(authorization: str | None = Header(default=None), db = Depends(db_session)) -> User:
    if not authorization or not authorization.startswith("Bearer "):
        fail(401, "UNAUTHENTICATED", "请先登录")
    token = authorization.removeprefix("Bearer ").strip()
    record = db.get(AccessToken, sha256(token.encode()).hexdigest())
    if record is None or utc_datetime(record.expires_at) <= utcnow():
        fail(401, "TOKEN_EXPIRED", "登录已过期，请重新登录")
    user = db.get(User, record.user_id)
    if user is None:
        fail(401, "UNAUTHENTICATED", "登录状态无效")
    return user


def active_package(site_id: str, version: str) -> dict[str, Any]:
    package = map_package.load()
    if site_id != package["siteId"]:
        fail(404, "SITE_NOT_FOUND", "场所不存在")
    if version != package["mapVersion"]:
        fail(409, "MAP_VERSION_CONFLICT", "地图版本已更新", details={"currentMapVersion": package["mapVersion"]})
    return package


def ensure_schema() -> None:
    engine = getattr(app.state, "engine", None) or get_engine()
    with engine.connect() as connection:
        connection.execute(text("SELECT 1"))


@app.get("/health/live")
def liveness() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/health/ready")
def readiness(response: Response):
    engine = getattr(app.state, "engine", None) or get_engine()
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        map_package.load()
    except (SQLAlchemyError, RuntimeError, OSError, ValueError):
        response.status_code = 503
        return {"status": "unavailable"}
    return {"status": "ok"}


@app.delete("/api/v1/account", status_code=204)
def delete_account(response: Response, db: Session = Depends(db_session), user: User = Depends(get_user)):
    db.execute(delete(AccessToken).where(AccessToken.user_id == user.id))
    db.execute(delete(NavigationSession).where(NavigationSession.user_id == user.id))
    db.execute(delete(IdempotencyRecord).where(IdempotencyRecord.user_id == user.id))
    db.execute(delete(LocationSequence).where(LocationSequence.user_id == user.id))
    db.delete(user)
    db.commit()
    response.status_code = 204


def wechat_openid(code: str) -> str:
    mode = os.getenv("PATHAI_AUTH_MODE", "wechat").lower()
    if mode == "development" and os.getenv("PATHAI_ENV", "local").lower() in {"local", "development", "test"}:
        return "local:" + hmac.new(os.getenv("PATHAI_DEV_AUTH_SECRET", "local-only").encode(), code.encode(), "sha256").hexdigest()
    app_id, secret = os.getenv("WECHAT_APP_ID"), os.getenv("WECHAT_APP_SECRET")
    if not app_id or not secret:
        fail(503, "AUTH_PROVIDER_UNAVAILABLE", "登录服务尚未配置", retryable=True)
    import httpx
    try:
        response = httpx.get("https://api.weixin.qq.com/sns/jscode2session", params={"appid": app_id, "secret": secret, "js_code": code, "grant_type": "authorization_code"}, timeout=5.0)
        data = response.json()
    except (httpx.HTTPError, ValueError):
        fail(503, "AUTH_PROVIDER_UNAVAILABLE", "微信登录服务暂时不可用", retryable=True)
    if response.status_code != 200 or not data.get("openid"):
        fail(401, "AUTH_CODE_INVALID", "微信登录凭证无效")
    return data["openid"]


@app.post("/api/v1/auth/wechat")
def login(payload: WechatLogin, db: Session = Depends(db_session)):
    openid = wechat_openid(payload.code)
    user_hash = hmac.new(os.getenv("PATHAI_OPENID_HASH_SECRET", "local-subject-key").encode(), openid.encode(), "sha256").hexdigest()
    user = db.scalar(select(User).where(User.openid_hash == user_hash))
    if user is None:
        user = User(id=f"usr_{uuid.uuid4().hex}", openid_hash=user_hash, created_at=utcnow())
        db.add(user)
        db.flush()
    token = secrets.token_urlsafe(32)
    db.add(AccessToken(token_hash=sha256(token.encode()).hexdigest(), user_id=user.id, expires_at=utcnow() + timedelta(hours=2)))
    db.commit()
    return {"accessToken": token, "tokenType": "Bearer", "expiresIn": 7200, "userId": user.id}


@app.get("/api/v1/sites/{site_id}/map")
def get_map(site_id: str, response: Response, version: str = "latest", if_none_match: str | None = Header(default=None, alias="If-None-Match"), user: User = Depends(get_user)):
    package = map_package.load()
    if site_id != package["siteId"]:
        fail(404, "SITE_NOT_FOUND", "场所不存在")
    if version not in {"latest", package["mapVersion"]}:
        fail(404, "MAP_NOT_PUBLISHED", "地图版本不存在")
    manifest = map_package.manifest()
    etag = f'"{package["sha256"]}"'
    response.headers["ETag"] = etag
    if if_none_match == etag:
        response.status_code = 304
        return Response(status_code=304, headers={"ETag": etag})
    return {**manifest, "dataUrl": f"/api/v1/sites/{site_id}/map-data?version={package['mapVersion']}", "dataSha256": package["sha256"]}


@app.get("/api/v1/sites/{site_id}/map-data")
def get_map_data(site_id: str, version: str, user: User = Depends(get_user)):
    package = active_package(site_id, version)
    return package


@app.get("/api/v1/sites/{site_id}/places")
def get_places(site_id: str, q: str = "", floor: str | None = None, type: str | None = None, limit: int = 10, user: User = Depends(get_user)):
    package = map_package.load()
    if site_id != package["siteId"]:
        fail(404, "SITE_NOT_FOUND", "场所不存在")
    if limit < 1 or limit > 50:
        fail(400, "INVALID_REQUEST", "limit 必须在 1 到 50 之间")
    query = q.strip().casefold()
    items = [p for p in package["places"] if (not query or query in p["name"].casefold() or any(query in a.casefold() for a in p["aliases"])) and (floor is None or p["floorId"] == floor) and (type is None or p["type"] == type)]
    items.sort(key=lambda p: (0 if query and p["name"].casefold() == query else 1, p["name"], p["placeId"]))
    return {"mapVersion": package["mapVersion"], "items": items[:limit], "nextCursor": None}


def parse_intent(text_value: str, package: dict[str, Any]) -> tuple[str, list[dict[str, Any]], float]:
    text_value = text_value.strip().casefold()
    if not text_value:
        return "unknown", [], 0.0
    exact = [p for p in package["places"] if text_value in {p["name"].casefold(), *(a.casefold() for a in p["aliases"])}]
    if len(exact) == 1:
        return "start_navigation", exact, 0.93
    if len(exact) > 1:
        return "start_navigation", exact[:10], 0.61
    matches = [p for p in package["places"] if text_value in p["name"].casefold() or any(text_value in a.casefold() or a.casefold() in text_value for a in p["aliases"] if len(a) >= 2)]
    if matches:
        return "start_navigation", matches[:10], 0.61 if len(matches) > 1 else 0.8
    if any(word in text_value for word in ("结束", "取消导航", "停止导航")):
        return "end_navigation", [], 0.95
    if "重复" in text_value:
        return "repeat_instruction", [], 0.95
    if any(word in text_value for word in ("我在哪", "当前位置", "现在在哪")):
        return "query_location", [], 0.9
    if any(word in text_value for word in ("附近", "找", "搜索")):
        return "search_facility", [], 0.7
    if any(word in text_value for word in ("不用了", "算了", "取消")):
        return "cancel", [], 0.9
    return "unknown", [], 0.0


@app.post("/api/v1/intents/parse")
def intents(payload: IntentRequest, user: User = Depends(get_user)):
    package = active_package(payload.siteId, payload.mapVersion)
    intent, candidates, confidence = parse_intent(payload.text, package)
    destination = candidates[0] if len(candidates) == 1 and confidence >= 0.9 else None
    needs = intent == "unknown" or (intent == "start_navigation" and (len(candidates) != 1 or confidence < 0.9))
    if intent == "start_navigation" and destination:
        response_text = f"准备导航到{destination['spokenName']}，{destination['floorId']}层，请确认。"
    elif candidates:
        response_text = "找到多个地点，请选择地点名称或楼层。"
    elif intent == "unknown":
        response_text = "没有听清目的地，请再说一次地点名称。"
    else:
        response_text = "已识别请求，请使用相应的导航操作。"
    return {"intentId": f"intent_{uuid.uuid4().hex}", "intent": intent, "confidence": confidence, "needsClarification": needs,
            "destination": {k: destination[k] for k in ("placeId", "name", "floorId")} if destination else None,
            "candidates": [{k: p[k] for k in ("placeId", "name", "floorId")} for p in candidates], "responseText": response_text}


def estimate_position(payload: LocationRequest, package: dict[str, Any]) -> dict[str, Any]:
    now = utcnow()
    collected = utc_datetime(payload.collectedAt)
    if not is_fresh(collected, now):
        fail(422, "LOCATION_UNRELIABLE", "定位数据已过期或时间无效", retryable=True)
    beacons_by_id = {b["beaconId"]: b for b in package["beacons"]}
    by_beacon: dict[str, list[BeaconObservation]] = {}
    ignored: list[str] = []
    window_start = now - timedelta(seconds=5)
    for observation in payload.beacons:
        observed = utc_datetime(observation.observedAt)
        beacon = beacons_by_id.get(observation.beaconId)
        if observed < window_start or observed > now or beacon is None or beacon.get("deploymentStatus") != "deployed":
            ignored.append(observation.beaconId)
            continue
        by_beacon.setdefault(observation.beaconId, []).append(observation)
    filtered = [(beacons_by_id[key], statistics.median(item.rssi for item in values)) for key, values in by_beacon.items()]
    selected = [(beacon, rssi) for beacon, rssi in filtered if not payload.floorHint or beacon["floorId"] == payload.floorHint]
    selected.sort(key=lambda pair: pair[1], reverse=True)
    if len(selected) < 3:
        return {"estimateId": f"loc_{uuid.uuid4().hex}", "sequence": payload.sequence, "mapVersion": package["mapVersion"], "position": None,
                "headingDeg": None, "confidence": 0.0, "quality": "lost", "matchedArea": None,
                "algorithmVersion": "ble-trilat-0.2.0", "observedAt": collected.isoformat(), "ignoredBeaconIds": ignored}
    selected = selected[:8]
    distances = [max(1.0, min(30.0, 10 ** ((beacon["txPower"] - rssi) / 20))) for beacon, rssi in selected]
    x0, y0 = selected[0][0]["x"], selected[0][0]["y"]
    r0 = distances[0]
    rows, vals = [], []
    for (beacon, _obs), distance in zip(selected[1:], distances[1:]):
        x, y = beacon["x"], beacon["y"]
        rows.append([2 * (x - x0), 2 * (y - y0)])
        vals.append(r0 * r0 - distance * distance + x * x - x0 * x0 + y * y - y0 * y0)
    a = sum(r[0] * r[0] for r in rows)
    b = sum(r[0] * r[1] for r in rows)
    c = sum(r[1] * r[1] for r in rows)
    d = sum(r[0] * v for r, v in zip(rows, vals))
    e = sum(r[1] * v for r, v in zip(rows, vals))
    determinant = a * c - b * b
    if abs(determinant) < 1e-6:
        quality, confidence = "unreliable", 0.2
        x, y = None, None
    else:
        x, y = (d * c - b * e) / determinant, (a * e - b * d) / determinant
        residual = sum(abs(math.hypot(x - beacon["x"], y - beacon["y"]) - distance) for (beacon, _), distance in zip(selected, distances)) / len(selected)
        confidence = max(0.0, min(0.75, 0.75 - residual / 20))
        quality = "degraded" if residual < 8 else "unreliable"
    floor_counts: dict[str, int] = {}
    for beacon, _ in selected:
        floor_counts[beacon["floorId"]] = floor_counts.get(beacon["floorId"], 0) + 1
    floor_id = max(floor_counts, key=floor_counts.get) if len(floor_counts) == 1 or max(floor_counts.values()) > len(selected) / 2 else None
    geometry_score = determinant / (a * c) if a * c > 0 else 0.0
    if x is not None and residual <= 2.0 and confidence >= 0.5 and geometry_score >= 0.1 and floor_id is not None:
        quality = "reliable"
    return {"estimateId": f"loc_{uuid.uuid4().hex}", "sequence": payload.sequence, "mapVersion": package["mapVersion"],
            "position": {"x": round(x, 2), "y": round(y, 2), "floorId": floor_id} if x is not None and floor_id else None,
            "headingDeg": None, "confidence": confidence, "quality": quality, "matchedArea": None,
            "algorithmVersion": "ble-trilat-0.2.0", "observedAt": collected.isoformat(), "ignoredBeaconIds": ignored}


@app.post("/api/v1/locations/estimate")
def locations(payload: LocationRequest, db: Session = Depends(db_session), user: User = Depends(get_user)):
    package = active_package(payload.siteId, payload.mapVersion)
    current = db.scalar(select(LocationSequence).where(LocationSequence.user_id == user.id, LocationSequence.site_id == payload.siteId))
    if current and payload.sequence <= current.sequence:
        fail(409, "STALE_LOCATION_BATCH", "位置数据序号已处理")
    result = estimate_position(payload, package)
    if current:
        current.sequence = payload.sequence
    else:
        db.add(LocationSequence(user_id=user.id, site_id=payload.siteId, sequence=payload.sequence))
    db.commit()
    return result


def _segment_crosses_wall(a: dict[str, Any], b: dict[str, Any], walls: list[dict[str, Any]]) -> bool:
    p, q = (a["x"], a["y"]), (b["x"], b["y"])

    def orientation(u, v, w):
        return (v[0] - u[0]) * (w[1] - u[1]) - (v[1] - u[1]) * (w[0] - u[0])

    def point_segment_distance(point, start, end):
        dx, dy = end[0] - start[0], end[1] - start[1]
        length_squared = dx * dx + dy * dy
        if length_squared == 0:
            return math.dist(point, start)
        fraction = max(0.0, min(1.0, ((point[0] - start[0]) * dx + (point[1] - start[1]) * dy) / length_squared))
        closest = (start[0] + fraction * dx, start[1] + fraction * dy)
        return math.dist(point, closest)

    for wall in walls:
        if wall["floorId"] != a["floorId"]:
            continue
        points = wall["coordinates"]
        radius = float(wall.get("thickness") or 0.2) / 2 + 0.02
        for start, end in zip(points, points[1:]):
            o1, o2 = orientation(p, q, start), orientation(p, q, end)
            o3, o4 = orientation(start, end, p), orientation(start, end, q)
            if o1 * o2 < 0 and o3 * o4 < 0:
                return True
            if min(point_segment_distance(p, start, end), point_segment_distance(q, start, end),
                   point_segment_distance(start, p, q), point_segment_distance(end, p, q)) <= radius:
                return True
    return False


def route_between(package: dict[str, Any], origin: dict[str, Any], destination: dict[str, Any], blind: bool = True, mode: str | None = None) -> dict[str, Any] | None:
    nodes = package["nodes"]
    start, end = origin.get("nodeId"), destination["nodeId"]
    if not start:
        same_floor = [n for n in nodes.values() if n["floorId"] == str(origin.get("floorId")) and n["public"] and n["accessible"]]
        if not same_floor:
            return None
        start = min(same_floor, key=lambda n: (n["x"] - origin["x"]) ** 2 + (n["y"] - origin["y"]) ** 2)["id"]
    if start not in nodes or end not in nodes:
        return None
    selected_mode = mode or ("blind" if blind else "standard")
    same_floor = nodes[start]["floorId"] == nodes[end]["floorId"]
    same_floor_mid = {"intersection", "doorway", "facility_entrance"}
    cross_floor_mid = same_floor_mid | {"facility"}
    room_types = {node.get("roomId") or node["id"]: node.get("roomType") for node in nodes.values() if node["type"] == "room"}
    infra_doors = {
        node_id for node_id, node in nodes.items()
        if node["type"] == "doorway" and node.get("rooms")
        and all(room_types.get(room_id) == "infrastructure" for room_id in node["rooms"])
    }
    door_penalties = {"swing": 0.0, "fire": 0.5, "opening": 1.0}
    best_door: dict[str, float] = {}
    for node in nodes.values():
        if node["type"] != "doorway":
            continue
        penalty = 0.0 if node.get("doorType") == "fire" and node.get("isNormallyOpen") else door_penalties.get(node.get("doorType"), 9.0)
        for room_id in node.get("rooms", []):
            best_door[room_id] = min(best_door.get(room_id, math.inf), penalty)
    best_room_door = {room_id: value for room_id, value in best_door.items()}

    def search(door_filter: bool, allow_wall: bool, allow_facility_mid: bool) -> dict[str, Any] | None:
        adjacency: dict[str, list[tuple[str, float, dict[str, Any]]]] = {key: [] for key in nodes}
        for edge in package["edges"]:
            a, b = edge.get("from"), edge.get("to")
            if a not in nodes or b not in nodes or not edge.get("walkable", True):
                continue
            if selected_mode == "blind" and (edge.get("blindAccessible") is False or int(edge.get("accessibilityLevel", 0)) == 999):
                continue
            if selected_mode == "wheelchair" and (edge.get("wheelchairAccessible") is False or int(edge.get("accessibilityLevel", 0)) == 999):
                continue
            cross = nodes[a]["floorId"] != nodes[b]["floorId"] or edge.get("crossFloor")
            edge_type = edge.get("type")
            if selected_mode == "blind" and cross and edge_type != "elevator":
                continue
            if (a in infra_doors or b in infra_doors):
                continue
            door = nodes[a] if nodes[a]["type"] == "doorway" else nodes[b] if nodes[b]["type"] == "doorway" else None
            if door and door.get("doorType") == "fire" and door.get("isNormallyOpen") is False:
                continue
            if door_filter and door:
                room = nodes[a] if nodes[a]["type"] == "room" else nodes[b] if nodes[b]["type"] == "room" else None
                room_id = (room or {}).get("roomId")
                if room_id and room_id in best_room_door:
                    penalty = 0.0 if door.get("doorType") == "fire" and door.get("isNormallyOpen") else door_penalties.get(door.get("doorType"), 9.0)
                    if penalty > best_room_door[room_id]:
                        continue
            if not allow_wall and nodes[a]["type"] == nodes[b]["type"] == "intersection" and _segment_crosses_wall(nodes[a], nodes[b], package.get("walls", [])):
                continue
            weight = float(edge.get("distance") or math.dist((nodes[a]["x"], nodes[a]["y"]), (nodes[b]["x"], nodes[b]["y"])))
            if door and ((nodes[a]["type"] == "room") or (nodes[b]["type"] == "room")):
                weight += 0.0 if door.get("doorType") == "fire" and door.get("isNormallyOpen") else door_penalties.get(door.get("doorType"), 9.0)
            adjacency[a].append((b, weight, edge))
            adjacency[b].append((a, weight, edge))
        distances: dict[tuple[str, str | None], float] = {(start, None): 0.0}
        previous: dict[tuple[str, str | None], tuple[tuple[str, str | None], dict[str, Any]]] = {}
        queue = [(0.0, start, None)]
        final_state = None
        while queue:
            distance, current, prior = heapq.heappop(queue)
            state = (current, prior)
            if distance != distances.get(state):
                continue
            if current == end:
                final_state = state
                break
            if current not in {start, end}:
                node = nodes[current]
                if node.get("roomType") == "toilet":
                    continue
                allowed_mid = cross_floor_mid if allow_facility_mid else same_floor_mid
                if node["type"] not in allowed_mid:
                    continue
            for neighbor, cost, edge in adjacency[current]:
                if neighbor not in {start, end}:
                    node = nodes[neighbor]
                    if node.get("roomType") == "toilet":
                        continue
                    allowed_mid = cross_floor_mid if allow_facility_mid else same_floor_mid
                    if node["type"] not in allowed_mid:
                        continue
                if nodes[current]["type"] == "doorway" and prior and nodes[prior]["type"] == nodes[neighbor]["type"] == "room":
                    continue
                next_state = (neighbor, current)
                candidate = distance + cost
                if candidate < distances.get(next_state, math.inf):
                    distances[next_state] = candidate
                    previous[next_state] = (state, edge)
                    heapq.heappush(queue, (candidate, neighbor, current))
        if final_state is None:
            return None
        path, path_edges, cursor = [end], [], final_state
        while cursor != (start, None):
            parent, edge = previous[cursor]
            path_edges.append(edge)
            path.append(parent[0])
            cursor = parent
        path.reverse()
        path_edges.reverse()
        return {"nodes": path, "edges": path_edges, "distance": round(distances[final_state], 2), "wallFallback": allow_wall}

    result = search(door_filter=True, allow_wall=False, allow_facility_mid=not same_floor)
    fallback_type = "none"
    if result is None:
        result = search(door_filter=False, allow_wall=False, allow_facility_mid=not same_floor)
        if result:
            fallback_type = "all_doors"
    if result is None and same_floor:
        result = search(door_filter=True, allow_wall=False, allow_facility_mid=True)
        if result:
            fallback_type = "best_door"
    if result is None:
        return None
    fallback_type = {"cross_floor_roundtrip": "best_door"}.get(fallback_type, fallback_type)
    if fallback_type not in {"none", "best_door", "all_doors", "wall_fallback"}:
        fail(500, "ROUTE_INVALID", "路线回退类型无效")
    result["fallbackType"] = fallback_type
    return result


def polyline_distance(x: float, y: float, floor_id: str, polyline: list[dict[str, Any]]) -> float:
    distances = []
    for start, end in zip(polyline, polyline[1:]):
        if start["floorId"] != floor_id or end["floorId"] != floor_id:
            continue
        dx, dy = end["x"] - start["x"], end["y"] - start["y"]
        length_squared = dx * dx + dy * dy
        fraction = 0.0 if length_squared == 0 else max(0.0, min(1.0, ((x - start["x"]) * dx + (y - start["y"]) * dy) / length_squared))
        distances.append(math.hypot(x - (start["x"] + fraction * dx), y - (start["y"] + fraction * dy)))
    return min(distances, default=math.inf)


def route_risk(fallback_type: str) -> dict[str, Any]:
    if fallback_type == "wall_fallback":
        return {"safeForBlind": False, "reason": "route_crosses_wall", "message": "此路线不可用于行走，请停止导航并重新规划或联系工作人员。"}
    if fallback_type not in {"none", "best_door", "all_doors"}:
        fail(500, "ROUTE_INVALID", "路线回退类型无效")
    return {"safeForBlind": True, "reason": None, "message": None}


def instructions_for_path(package: dict[str, Any], route: dict[str, Any]) -> list[dict[str, Any]]:
    nodes = package["nodes"]
    result = []
    for index, (node_id, edge) in enumerate(zip(route["nodes"][1:], route["edges"]), start=1):
        node = nodes[node_id]
        previous = nodes[route["nodes"][index - 1]]
        next_floor = node["floorId"]
        cross = previous["floorId"] != next_floor
        if cross and edge.get("type") == "elevator":
            action, text_value = "enter_elevator", f"前往{next_floor}层，请使用电梯。"
        else:
            action, text_value = "continue", f"继续前往{node['label']}。" if node["type"] in {"facility", "facility_entrance"} else "请沿可通行通道继续前行。"
        result.append({"instructionId": f"ins_{index}", "action": action, "text": text_value, "distanceM": round(float(edge.get("distance") or 0), 1), "landmark": None, "priority": "turn" if action == "enter_elevator" else "normal", "trigger": {"type": "decision_point", "nodeId": node_id}})
    if not result:
        result = [{"instructionId": "ins_1", "action": "continue", "text": "目的地就在当前位置附近，请按场地实际标识确认。", "distanceM": 0, "landmark": None, "priority": "safety", "trigger": {"type": "immediate"}}]
    return result


def idempotent_response(db: Session, user_id: str, operation: str, key: str, payload: dict[str, Any]):
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    request_hash = sha256(encoded.encode()).hexdigest()
    record = db.scalar(select(IdempotencyRecord).where(IdempotencyRecord.user_id == user_id, IdempotencyRecord.operation == operation, IdempotencyRecord.key == key))
    if record:
        if record.request_hash != request_hash:
            fail(409, "IDEMPOTENCY_CONFLICT", "幂等键已用于不同请求")
        return record.response_json, True
    return request_hash, False


def remember_idempotent(db: Session, user_id: str, operation: str, key: str, request_hash: str, response: dict[str, Any]):
    db.add(IdempotencyRecord(user_id=user_id, operation=operation, key=key, request_hash=request_hash, response_json=response, created_at=utcnow()))


@app.post("/api/v1/nav/sessions", status_code=201)
def create_session(payload: CreateSession, idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"), db: Session = Depends(db_session), user: User = Depends(get_user)):
    if not idempotency_key:
        fail(400, "INVALID_REQUEST", "必须提供 Idempotency-Key")
    replay, exists = idempotent_response(db, user.id, "create-session", idempotency_key, payload.model_dump(mode="json"))
    if exists:
        return JSONResponse(status_code=200, content=replay)
    package = active_package(payload.siteId, payload.mapVersion)
    if (
        payload.origin.confidence < 0.5
        or not is_fresh(payload.origin.observedAt)
        or not math.isfinite(payload.origin.x)
        or not math.isfinite(payload.origin.y)
    ):
        fail(422, "LOCATION_UNRELIABLE", "当前位置不可信，请重新定位", retryable=True)
    if payload.userMode not in {"blind", "standard", "wheelchair"}:
        fail(400, "INVALID_REQUEST", "导航模式无效")
    destination = next((place for place in package["places"] if place["placeId"] == payload.destinationId), None)
    if destination is None:
        fail(404, "PLACE_NOT_FOUND", "目的地不存在")
    origin = payload.origin.model_dump(mode="json")
    route_path = route_between(package, origin, destination, blind=payload.userMode == "blind", mode=payload.userMode)
    if route_path is None:
        fail(422, "ROUTE_NOT_FOUND", "当前地图中没有安全可行的路线", retryable=False)
    route_id = f"route_{uuid.uuid4().hex}"
    segments = instructions_for_path(package, route_path)
    fallback_type = route_path.get("fallbackType", "none")
    risk = route_risk(fallback_type)
    if not risk["safeForBlind"]:
        fail(422, "UNSAFE_ROUTE", risk["message"])
    route = {"routeId": route_id, "distanceM": route_path["distance"], "estimatedDurationSec": None,
             "polyline": [{"x": package["nodes"][n]["x"], "y": package["nodes"][n]["y"], "floorId": package["nodes"][n]["floorId"]} for n in route_path["nodes"]], "segments": segments,
             "fallbackType": fallback_type, "safeForBlind": risk["safeForBlind"] and payload.userMode == "blind", "risk": risk,
             "routeRulesVersion": "1.0.0", "routeMode": payload.userMode}
    session_id = f"nav_{uuid.uuid4().hex}"
    now = utcnow()
    session = NavigationSession(id=session_id, user_id=user.id, site_id=payload.siteId, map_version=payload.mapVersion,
                                route_rules_version="1.0.0", status="active", destination_id=payload.destinationId,
                                destination={k: destination[k] for k in ("placeId", "name", "floorId")}, route=route, origin=origin,
                                last_sequence=-1, instruction_sequence=1, last_instruction=segments[0], started_at=now, updated_at=now)
    response = {"sessionId": session_id, "status": "active", "mapVersion": payload.mapVersion, "routeRulesVersion": "1.0.0",
                "destination": session.destination, "route": route, "instructionSequence": 1,
                "instructionExpiresAt": (now + timedelta(seconds=10)).isoformat(), "instruction": segments[0], "createdAt": now.isoformat()}
    db.add(session)
    remember_idempotent(db, user.id, "create-session", idempotency_key, replay, response)
    db.commit()
    return response


@app.post("/api/v1/nav/sessions/{session_id}/updates")
def update_session(session_id: str, payload: SessionUpdate, db: Session = Depends(db_session), user: User = Depends(get_user)):
    session = db.get(NavigationSession, session_id)
    if session is None or session.user_id != user.id:
        fail(404, "SESSION_NOT_FOUND", "导航会话不存在")
    if session.status not in {"active", "recovering"}:
        fail(409, "SESSION_NOT_ACTIVE", "导航会话已结束")
    if payload.sequence <= session.last_sequence:
        fail(409, "STALE_NAV_UPDATE", "导航更新序号已处理")
    if session.route.get("fallbackType", "none") == "wall_fallback":
        fail(409, "UNSAFE_ROUTE", "穿墙回退路线不可用于行走")
    now = utcnow()
    observed = utc_datetime(payload.location.observedAt)
    if payload.location.quality != "reliable" or payload.location.confidence < 0.5 or not is_fresh(observed, now) or payload.location.x is None or payload.location.y is None or payload.location.floorId is None:
        session.status = "recovering"; session.arrival_started_at = None; session.arrival_last_sample_at = None; session.off_route_count = 0
        session.last_sequence = payload.sequence; session.updated_at = now
        instruction = {"instructionId": f"ins_reloc_{payload.sequence}", "action": "relocalize", "text": "暂时无法可靠确定位置，请停下并重新定位。", "distanceM": None, "landmark": None, "priority": "safety", "trigger": {"type": "immediate"}}
        session.last_instruction = instruction; db.commit()
        return {"sessionId": session.id, "status": session.status, "event": "relocalizing", "instruction": instruction, "routeChanged": False, "processedSequence": payload.sequence}
    session.status = "active"; session.last_sequence = payload.sequence; session.updated_at = now
    origin = {"x": payload.location.x, "y": payload.location.y, "floorId": payload.location.floorId}
    session.origin = origin
    package = active_package(session.site_id, session.map_version)
    destination = next(place for place in package["places"] if place["placeId"] == session.destination_id)
    old_polyline = session.route.get("polyline", [])
    distance_from_route = polyline_distance(payload.location.x, payload.location.y, payload.location.floorId, old_polyline)
    session.off_route_count = session.off_route_count + 1 if distance_from_route > 3 else 0
    reroute_candidate = session.off_route_count >= 2
    route_path = route_between(package, origin, destination, mode=session.route.get("routeMode", "blind"))
    if route_path is None:
        session.status = "recovering"
        session.arrival_started_at = None
        session.arrival_last_sample_at = None
        instruction = {"instructionId": f"ins_reloc_{payload.sequence}", "action": "relocalize", "text": "暂时无法确认安全路线，请停下并重新定位。", "distanceM": None, "landmark": None, "priority": "safety", "trigger": {"type": "immediate"}}
        session.last_instruction = instruction; db.commit()
        return {"sessionId": session.id, "status": session.status, "event": "relocalizing", "instruction": instruction, "routeChanged": False, "processedSequence": payload.sequence}
    path_polyline = [{"x": package["nodes"][n]["x"], "y": package["nodes"][n]["y"], "floorId": package["nodes"][n]["floorId"]} for n in route_path["nodes"]]
    route_changed = False
    if reroute_candidate:
        route_changed = path_polyline != old_polyline
        segments = instructions_for_path(package, route_path)
        fallback = route_path.get("fallbackType", "none")
        risk = route_risk(fallback)
        session.route = {**session.route, "distanceM": route_path["distance"], "polyline": path_polyline, "segments": segments,
                         "fallbackType": fallback, "safeForBlind": risk["safeForBlind"], "risk": risk}
        session.off_route_count = 0
        if not risk["safeForBlind"]:
            session.status = "recovering"
            instruction = {"instructionId": f"ins_unsafe_{payload.sequence}", "action": "relocalize", "text": risk["message"], "distanceM": None, "landmark": None, "priority": "safety", "trigger": {"type": "immediate"}}
            session.last_instruction = instruction
            db.commit()
            return {"sessionId": session.id, "status": session.status, "event": "reroute_candidate", "instruction": instruction,
                    "routeChanged": route_changed, "rerouteCandidate": True, "distanceFromRouteM": round(distance_from_route, 2), "processedSequence": payload.sequence}
    polyline = session.route.get("polyline", old_polyline)
    target = package["nodes"].get(destination.get("nodeId"))
    final_distance = math.dist((payload.location.x, payload.location.y), (target["x"], target["y"])) if target and target["floorId"] == payload.location.floorId else math.inf
    final_segment_complete = bool(polyline) and polyline[-1]["floorId"] == payload.location.floorId and math.dist((payload.location.x, payload.location.y), (polyline[-1]["x"], polyline[-1]["y"])) <= 2
    if target and final_distance <= 2 and final_segment_complete and session.route.get("safeForBlind", True):
        previous_sample = utc_datetime(session.arrival_last_sample_at) if session.arrival_last_sample_at else None
        if session.arrival_started_at is None or previous_sample is None or observed <= previous_sample or (observed - previous_sample).total_seconds() > 10:
            session.arrival_started_at = observed
        elif (observed - utc_datetime(session.arrival_started_at)).total_seconds() >= 5:
            session.status = "arrived"; session.end_reason = "arrived"; session.ended_at = now
            session.arrival_last_sample_at = observed; session.last_instruction = None
            db.commit()
            return {"sessionId": session.id, "status": "arrived", "event": "arrived", "routeChanged": route_changed, "processedSequence": payload.sequence}
        session.arrival_last_sample_at = observed
    else:
        session.arrival_started_at = None; session.arrival_last_sample_at = None
    segments = session.route.get("segments", [])
    if not segments:
        segments = instructions_for_path(package, route_path)
        session.route = {**session.route, "segments": segments}
    nearest_node_index = min(
        range(len(route_path["nodes"])),
        key=lambda index: math.dist(
            (payload.location.x, payload.location.y),
            (package["nodes"][route_path["nodes"][index]]["x"], package["nodes"][route_path["nodes"][index]]["y"]),
        ) if package["nodes"][route_path["nodes"][index]]["floorId"] == payload.location.floorId else math.inf,
    )
    next_index = min(max(nearest_node_index - 1, 0), len(segments) - 1)
    previous_instruction = session.last_instruction
    previous_node = (previous_instruction or {}).get("trigger", {}).get("nodeId")
    previous_index = next((index for index, item in enumerate(segments) if item.get("trigger", {}).get("nodeId") == previous_node), None)
    if previous_index is not None:
        next_index = max(next_index, previous_index)
    instruction = segments[next_index]
    previous_key = ((previous_instruction or {}).get("trigger", {}).get("nodeId"), (previous_instruction or {}).get("action"))
    instruction_key = (instruction.get("trigger", {}).get("nodeId"), instruction.get("action"))
    if instruction_key == previous_key:
        instruction = previous_instruction
    else:
        session.instruction_sequence += 1
        session.last_instruction = instruction
    session.route["currentInstructionIndex"] = next_index
    db.commit()
    event = "reroute_candidate" if reroute_candidate else "instruction"
    return {"sessionId": session.id, "status": session.status, "event": event, "instructionSequence": session.instruction_sequence,
            "instructionExpiresAt": (now + timedelta(seconds=10)).isoformat(), "instruction": instruction,
            "routeChanged": route_changed, "rerouteCandidate": reroute_candidate, "distanceFromRouteM": round(distance_from_route, 2) if math.isfinite(distance_from_route) else None,
            "processedSequence": payload.sequence}


@app.get("/api/v1/nav/sessions/{session_id}")
def get_session(session_id: str, db: Session = Depends(db_session), user: User = Depends(get_user)):
    session = db.get(NavigationSession, session_id)
    if session is None or session.user_id != user.id:
        fail(404, "SESSION_NOT_FOUND", "导航会话不存在")
    return {"sessionId": session.id, "status": session.status, "siteId": session.site_id, "mapVersion": session.map_version,
            "routeRulesVersion": session.route_rules_version, "destination": session.destination,
            "currentInstruction": session.last_instruction if session.status in {"active", "recovering"} else None,
            "lastProcessedSequence": session.last_sequence, "updatedAt": session.updated_at.isoformat(), "endReason": session.end_reason}


@app.post("/api/v1/nav/sessions/{session_id}/end")
def end_session(session_id: str, payload: EndSession, idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"), db: Session = Depends(db_session), user: User = Depends(get_user)):
    if not idempotency_key or len(idempotency_key) > 200:
        fail(400, "INVALID_REQUEST", "必须提供有效的 Idempotency-Key")
    if payload.reason not in {"user_cancelled", "user_finished", "arrived", "system_error"}:
        fail(400, "INVALID_REQUEST", "结束原因无效")
    if payload.reason == "arrived":
        fail(400, "ARRIVAL_SERVER_DECIDED", "到达状态只能由服务端导航判定")
    operation_payload = {"sessionId": session_id, **payload.model_dump(mode="json")}
    request_hash, exists = idempotent_response(db, user.id, "end-session", idempotency_key, operation_payload)
    if exists:
        return JSONResponse(status_code=200, content=request_hash)
    session = db.get(NavigationSession, session_id)
    if session is None or session.user_id != user.id:
        fail(404, "SESSION_NOT_FOUND", "导航会话不存在")
    if session.status not in {"active", "recovering"}:
        fail(409, "SESSION_NOT_ACTIVE", "导航会话已结束")
    session.status = "ended"
    session.end_reason = payload.reason
    session.ended_at = utcnow()
    session.updated_at = session.ended_at
    response = {"sessionId": session.id, "status": "ended", "reason": payload.reason, "endedAt": session.ended_at.isoformat()}
    remember_idempotent(db, user.id, "end-session", idempotency_key, request_hash, response)
    db.commit()
    return response
