# -*- coding: utf-8 -*-
"""API-06..09：导航会话创建/更新/查询/结束（docs/24 §8）。

状态机：active↔recovering→arrived/ended；终态不可逆；到达仅由服务端判定一次。
"""
from __future__ import annotations

import hashlib
import time
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Header, Request
from sqlalchemy.orm import Session

from pathai_backend import auth
from pathai_backend.config import Settings
from pathai_backend.errors import ApiError
from pathai_backend.map_repository import MapRepository, PublishedMap
from pathai_backend.models import NavIdempotency, NavSession
from pathai_backend.nav_text import rewrite_route_texts
from pathai_backend.nav_service import (
    ARRIVAL_RADIUS_M,
    DECISION_POINT_RADIUS_M,
    LOCATION_ALGORITHM_VERSION,
    OFF_ROUTE_M,
    ORIGIN_MIN_CONFIDENCE,
    PackageIndex,
    NavPoint,
    plan_route,
    snap_to_node,
)
from pathai_backend.routers.map import get_map_repository
from pathai_backend.schemas.common import (
    EndReason,
    InstructionAction,
    InstructionPriority,
    NavEvent,
    NavigationInstruction,
    PlaceCandidate,
    SessionStatus,
    TERMINAL_SESSION_STATUSES,
    TRUSTED_QUALITIES,
    Trigger,
    UserMode,
)
from pathai_backend.schemas.nav import (
    CreateSessionRequest,
    CreateSessionResponse,
    CurrentInstructionSummary,
    EndSessionRequest,
    EndSessionResponse,
    LocationUpdate,
    NavUpdateRequest,
    NavUpdateResponse,
    RouteInfo,
    SessionQueryResponse,
)
from math import hypot

router = APIRouter(prefix="/api/v1/nav", tags=["nav"])


def _now_epoch() -> int:
    return int(time.time())


def _epoch_to_dt(epoch: int) -> datetime:
    return datetime.fromtimestamp(epoch, tz=timezone.utc)


def _owned_session(db: Session, session_id: str, user: auth.AuthenticatedUser) -> NavSession:
    session = db.get(NavSession, session_id)
    if session is None:
        raise ApiError(404, "SESSION_NOT_FOUND", f"会话不存在：{session_id}")
    if session.subject_id != user.subject_id:
        raise ApiError(403, "FORBIDDEN", "无权访问该会话")
    return session


def _resolve_published(
    repo: MapRepository, site_id: str, map_version: str
) -> PublishedMap:
    if not repo.is_known_site(site_id):
        raise ApiError(404, "SITE_NOT_FOUND", f"场所不存在：{site_id}")
    latest = repo.latest(site_id)
    published = latest if map_version == "latest" else repo.get(site_id, map_version)
    if published is None:
        raise ApiError(404, "MAP_NOT_PUBLISHED", f"地图版本未发布：{site_id}@{map_version}")
    if latest is not None and published.map_version != latest.map_version:
        raise ApiError(
            409,
            "MAP_VERSION_CONFLICT",
            f"地图版本不一致：客户端 {published.map_version}，最新 {latest.map_version}",
        )
    return published


@router.post("/sessions", response_model=CreateSessionResponse, status_code=201)
def create_session(
    body: CreateSessionRequest,
    request: Request,
    user: auth.AuthenticatedUser = Depends(auth.get_current_user),
    db: Session = Depends(auth.get_db_session),
    repo: MapRepository = Depends(get_map_repository),
    settings: Settings = Depends(auth.get_settings),
) -> CreateSessionResponse:
    idem_key = (request.headers.get("Idempotency-Key") or "").strip()
    if not idem_key or len(idem_key) > 128:
        raise ApiError(400, "INVALID_REQUEST", "缺少 Idempotency-Key")
    request_hash = hashlib.sha256(body.model_dump_json().encode()).hexdigest()

    existing = db.get(
        NavIdempotency, {"subject_id": user.subject_id, "idempotency_key": idem_key}
    )
    if existing is not None:
        if existing.request_hash != request_hash:
            raise ApiError(409, "IDEMPOTENCY_CONFLICT", "幂等键已用于不同请求")
        return CreateSessionResponse.model_validate_json(existing.response_json)

    published = _resolve_published(repo, body.siteId, body.mapVersion)
    if body.origin.confidence < ORIGIN_MIN_CONFIDENCE or body.origin.floorId is None:
        raise ApiError(422, "LOCATION_UNRELIABLE", "起点定位不可靠，请先重新定位")
    index = PackageIndex(published.pkg)
    origin_node = snap_to_node(index, body.origin.floorId, body.origin.x, body.origin.y)
    route_info, _dest = plan_route(
        published.data, published.pkg, index, origin_node, body.destinationId, body.userMode
    )
    place = index.places[body.destinationId]
    # 第一阶段：创建会话时一次性改写播报文案（docs/29 §3）；失败自动回退原文，
    # 动作/距离/触发节点与播报时机保持不变。
    route_info = rewrite_route_texts(route_info, published, settings, place.name)

    session_id = f"nav_{uuid.uuid4().hex[:20]}"
    now = _now_epoch()
    session = NavSession(
        session_id=session_id,
        subject_id=user.subject_id,
        site_id=body.siteId,
        map_version=published.map_version,
        destination_place_id=place.placeId,
        destination_name=place.name,
        destination_floor_id=place.floorId,
        user_mode=body.userMode.value,
        status=SessionStatus.ACTIVE.value,
        route_json=route_info.model_dump_json(),
        current_index=0,
        last_sequence=0,
        location_algorithm_version=LOCATION_ALGORITHM_VERSION,
        location_params_version=settings.location_params_version,
        route_rules_version=route_info.routeRulesVersion,
        created_at_epoch=now,
        updated_at_epoch=now,
    )
    response = CreateSessionResponse(
        sessionId=session_id,
        status=SessionStatus.ACTIVE,
        mapVersion=published.map_version,
        paramsVersion=settings.location_params_version,
        destination=PlaceCandidate(placeId=place.placeId, name=place.name, floorId=place.floorId),
        route=route_info,
        createdAt=_epoch_to_dt(now),
    )
    db.add(session)
    db.add(
        NavIdempotency(
            subject_id=user.subject_id,
            idempotency_key=idem_key,
            request_hash=request_hash,
            session_id=session_id,
            response_json=response.model_dump_json(),
        )
    )
    db.commit()
    return response


def _relocalize_instruction(sequence: int) -> NavigationInstruction:
    return NavigationInstruction(
        instructionId=f"ins_re_{uuid.uuid4().hex[:8]}",
        instructionSequence=sequence,
        action=InstructionAction.RELOCALIZE,
        text="正在重新确定位置，请暂缓前行。",
        distanceM=None,
        landmark=None,
        priority=InstructionPriority.SAFETY,
        trigger=Trigger(type="immediate"),
        expiresAt=None,
    )


def _polyline_points(route: RouteInfo) -> list[NavPoint]:
    return [
        NavPoint(node_id=f"poly_{i}", floor_id=p.floorId or "", x=p.x, y=p.y)
        for i, p in enumerate(route.polyline)
    ]


def _polyline_suffix(route: RouteInfo) -> list[float]:
    """后缀距离：沿 polyline 折线（欧氏近似）到终点的距离。"""
    pts = route.polyline
    suffix = [0.0] * len(pts)
    for i in range(len(pts) - 2, -1, -1):
        d = hypot(pts[i + 1].x - pts[i].x, pts[i + 1].y - pts[i].y)
        suffix[i] = suffix[i + 1] + d
    return suffix


def _nearest_on_route(
    route: RouteInfo, floor_id: str | None, x: float, y: float
) -> tuple[int, float] | None:
    best_i, best_d = None, float("inf")
    for i, p in enumerate(route.polyline):
        if p.floorId != floor_id:
            continue
        d = hypot(p.x - x, p.y - y)
        if d < best_d:
            best_i, best_d = i, d
    return None if best_i is None else (best_i, best_d)


@router.post("/sessions/{session_id}/updates", response_model=NavUpdateResponse)
def nav_update(
    session_id: str,
    body: NavUpdateRequest,
    user: auth.AuthenticatedUser = Depends(auth.get_current_user),
    db: Session = Depends(auth.get_db_session),
    repo: MapRepository = Depends(get_map_repository),
) -> NavUpdateResponse:
    session = _owned_session(db, session_id, user)
    if session.status in (s.value for s in TERMINAL_SESSION_STATUSES):
        raise ApiError(409, "SESSION_NOT_ACTIVE", "会话已进入终态")
    if body.sequence < session.last_sequence:
        raise ApiError(
            409, "STALE_NAV_UPDATE", f"更新序号过期：{body.sequence} < {session.last_sequence}"
        )
    if body.sequence == session.last_sequence and session.last_response_json:
        return NavUpdateResponse.model_validate_json(session.last_response_json)

    published = repo.get(session.site_id, session.map_version)
    if published is None:
        raise ApiError(404, "MAP_NOT_PUBLISHED", "会话地图版本已不可用")
    index = PackageIndex(published.pkg)
    route = RouteInfo.model_validate_json(session.route_json)
    loc: LocationUpdate = body.location
    dest_node_id = route.segments[-1].trigger.nodeId
    dest = index.nodes[dest_node_id]

    trusted = loc.quality in TRUSTED_QUALITIES and loc.floorId is not None
    if trusted:
        arrival = loc.floorId == dest.floor_id and hypot(loc.x - dest.x, loc.y - dest.y) <= ARRIVAL_RADIUS_M
    else:
        arrival = False

    if not trusted:
        session.status = SessionStatus.RECOVERING.value
        instruction = _relocalize_instruction(body.sequence)
        response = NavUpdateResponse(
            sessionId=session.session_id,
            status=SessionStatus.RECOVERING,
            event=NavEvent.RELOCALIZING,
            instruction=instruction,
            routeChanged=False,
            processedSequence=body.sequence,
        )
    elif arrival:
        session.status = SessionStatus.ARRIVED.value
        session.end_reason = EndReason.ARRIVED.value
        now = _now_epoch()
        session.ended_at_epoch = now
        response = NavUpdateResponse(
            sessionId=session.session_id,
            status=SessionStatus.ARRIVED,
            event=NavEvent.ARRIVED,
            instruction=route.segments[-1],
            routeChanged=False,
            processedSequence=body.sequence,
        )
    else:
        near = _nearest_on_route(route, loc.floorId, loc.x, loc.y)
        off_route = near is None or near[1] > OFF_ROUTE_M
        if off_route:
            # 重规划：从当前位置同层最近节点出发（保持目的地不变）
            floor_id = loc.floorId
            origin2 = snap_to_node(index, floor_id, loc.x, loc.y)
            new_route, _ = plan_route(
                published.data, published.pkg, index, origin2,
                session.destination_place_id, UserMode(session.user_mode),
            )
            session.route_json = new_route.model_dump_json()
            session.current_index = 0
            session.status = SessionStatus.ACTIVE.value
            response = NavUpdateResponse(
                sessionId=session.session_id,
                status=SessionStatus.ACTIVE,
                event=NavEvent.REROUTE,
                instruction=new_route.segments[0],
                routeChanged=True,
                processedSequence=body.sequence,
                route=new_route,
            )
        else:
            cur = session.current_index
            suffix = _polyline_suffix(route)
            i, d_near = near
            remaining = d_near + suffix[i]
            # 推进当前指令：immediate/决策点进入半径/剩余距离达标
            while cur < len(route.segments) - 1:
                seg = route.segments[cur]
                if seg.action is InstructionAction.ARRIVE:
                    break  # 到达由接近判定触发
                if seg.trigger.type == "immediate":
                    cur += 1
                    continue
                if (
                    seg.trigger.type == "decision_point"
                    and seg.trigger.nodeId in index.nodes
                    and loc.floorId == index.nodes[seg.trigger.nodeId].floor_id
                    and hypot(loc.x - index.nodes[seg.trigger.nodeId].x,
                              loc.y - index.nodes[seg.trigger.nodeId].y)
                    <= DECISION_POINT_RADIUS_M
                ):
                    cur += 1
                    continue
                if seg.trigger.type == "distance" and seg.trigger.valueM is not None and remaining <= seg.trigger.valueM:
                    cur += 1
                    continue
                break
            session.current_index = cur
            session.status = SessionStatus.ACTIVE.value
            response = NavUpdateResponse(
                sessionId=session.session_id,
                status=SessionStatus.ACTIVE,
                event=NavEvent.INSTRUCTION,
                instruction=route.segments[cur],
                routeChanged=False,
                processedSequence=body.sequence,
            )

    session.last_sequence = body.sequence
    session.last_response_json = response.model_dump_json()
    session.updated_at_epoch = _now_epoch()
    db.commit()
    return response


@router.get("/sessions/{session_id}", response_model=SessionQueryResponse)
def query_session(
    session_id: str,
    user: auth.AuthenticatedUser = Depends(auth.get_current_user),
    db: Session = Depends(auth.get_db_session),
) -> SessionQueryResponse:
    session = _owned_session(db, session_id, user)
    status = SessionStatus(session.status)
    current: CurrentInstructionSummary | None = None
    if status not in TERMINAL_SESSION_STATUSES:
        route = RouteInfo.model_validate_json(session.route_json)
        seg = route.segments[min(session.current_index, len(route.segments) - 1)]
        current = CurrentInstructionSummary(
            instructionId=seg.instructionId, text=seg.text, action=seg.action
        )
    return SessionQueryResponse(
        sessionId=session.session_id,
        status=status,
        siteId=session.site_id,
        mapVersion=session.map_version,
        paramsVersion=session.location_params_version,
        destination=PlaceCandidate(
            placeId=session.destination_place_id,
            name=session.destination_name,
            floorId=session.destination_floor_id,
        ),
        currentInstruction=current,
        lastProcessedSequence=session.last_sequence or None,
        updatedAt=_epoch_to_dt(session.updated_at_epoch),
        endedAt=_epoch_to_dt(session.ended_at_epoch) if session.ended_at_epoch else None,
        endReason=EndReason(session.end_reason) if session.end_reason else None,
    )


@router.post("/sessions/{session_id}/end", response_model=EndSessionResponse)
def end_session(
    session_id: str,
    body: EndSessionRequest,
    user: auth.AuthenticatedUser = Depends(auth.get_current_user),
    db: Session = Depends(auth.get_db_session),
) -> EndSessionResponse:
    session = _owned_session(db, session_id, user)
    if session.status == SessionStatus.ENDED.value:
        # 结束接口幂等：重复结束返回当前终态
        return EndSessionResponse(
            sessionId=session.session_id,
            status=SessionStatus.ENDED,
            reason=EndReason(session.end_reason),
            endedAt=_epoch_to_dt(session.ended_at_epoch),
        )
    if session.status == SessionStatus.ARRIVED.value:
        raise ApiError(409, "SESSION_NOT_ACTIVE", "会话已到达终态")
    if body.reason is EndReason.ARRIVED:
        # 到达判定权在服务端，客户端不得伪报到达
        raise ApiError(409, "SESSION_NOT_ACTIVE", "到达状态只能由服务端判定")
    now = _now_epoch()
    session.status = SessionStatus.ENDED.value
    session.end_reason = body.reason.value
    session.ended_at_epoch = now
    session.updated_at_epoch = now
    db.commit()
    return EndSessionResponse(
        sessionId=session.session_id,
        status=SessionStatus.ENDED,
        reason=body.reason,
        endedAt=_epoch_to_dt(now),
    )
