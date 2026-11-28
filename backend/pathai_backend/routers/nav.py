# -*- coding: utf-8 -*-
"""API-06..09：导航会话创建/更新/查询/结束（docs/24 §8）。

状态机：active↔recovering→arrived/ended；终态不可逆；到达仅由服务端判定一次。
"""
from __future__ import annotations

import hashlib
import json
import logging
import time
import uuid
from dataclasses import replace
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Header, Request
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from pathai_backend import auth
from pathai_backend.config import Settings
from pathai_backend.errors import ApiError
from pathai_backend.hazard_layer import HazardRepository
from pathai_backend.map_repository import MapRepository, PublishedMap
from pathai_backend.models import NavIdempotency, NavSession

from pathai_backend.observation_time import (
    ADVISORY_STALE_AGE_S,
    is_fresh_for_guidance,
    is_sane,
    observation_age_s,
)

from pathai_backend.nav_service import (
    DECISION_POINT_RADIUS_M,
    LOCATION_ALGORITHM_VERSION,
    OFF_ROUTE_M,
    ORIGIN_MIN_CONFIDENCE,
    PackageIndex,
    NavPoint,
    origin_anchor_node,
    plan_route,
    TURN_PROMPT_DISTANCE_M,
)
from pathai_backend.routers.map import get_map_repository
from pathai_backend.routers.field import SITE as FIELD_SITE, frozen_config, require_plan
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
    PreviewRouteRequest,
    PreviewRouteResponse,
    RouteInfo,
    SessionQueryResponse,
)
from math import hypot

router = APIRouter(prefix="/api/v1/nav", tags=["nav"])
logger = logging.getLogger(__name__)


def _now_epoch() -> int:
    return int(time.time())


def get_hazard_repository(request: Request) -> HazardRepository:
    """危险层仓库（启动时加载；缺失/损坏已在仓库内降级为空层）。"""
    return request.app.state.hazard_repository


def _epoch_to_dt(epoch: int) -> datetime:
    return datetime.fromtimestamp(epoch, tz=timezone.utc)


def _origin_age_s(observed: datetime) -> float | None:
    """起点观测龄（秒，1 位小数）；时区缺失返回 None（随后由 is_sane 拒绝）。"""
    age = observation_age_s(observed)
    return None if age is None else round(age, 1)


def _is_stale(age_s: float | None) -> bool:
    """仅作提示：起点是否已超过提示阈值，不参与拒绝判断。"""
    return age_s is not None and age_s > ADVISORY_STALE_AGE_S


def _owned_session(
    db: Session,
    session_id: str,
    user: auth.AuthenticatedUser,
    *,
    for_update: bool = False,
) -> NavSession:
    """读取归属会话；for_update 时对行加锁（SELECT ... FOR UPDATE）。

    写路径（nav_update）必须加行锁：两个并发更新都读到旧 last_sequence
    时会互相覆盖状态；PostgreSQL 下锁行串行化，SQLite 由库级写锁兜底。
    """
    session = db.get(NavSession, session_id, with_for_update=for_update)
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
    # Explicit retained versions remain valid; only "latest" follows publication.

    return published


def _resolve_destination(
    index: PackageIndex, place_id: str
) -> tuple[PlaceCandidate, NavPoint]:
    """地点与拓扑节点；与 plan_route 保持同一 404 语义。"""
    place = index.places.get(place_id)
    if place is None:
        raise ApiError(404, "PLACE_NOT_FOUND", f"地点不存在：{place_id}")
    point = index.nodes.get(place.nodeId)
    if point is None:
        raise ApiError(404, "PLACE_NOT_FOUND", f"地点节点不存在：{place.nodeId}")
    return place, point


@router.post("/preview", response_model=PreviewRouteResponse)
def preview_route(
    body: PreviewRouteRequest,
    user: auth.AuthenticatedUser = Depends(auth.get_current_user),
    repo: MapRepository = Depends(get_map_repository),
    hazard_repo: HazardRepository = Depends(get_hazard_repository),
    settings: Settings = Depends(auth.get_settings),
) -> PreviewRouteResponse:
    """只做规划与概要预览：不建会话、不写库、不需要 Idempotency-Key。

    与建会话共用同一套地图解析、起点可信度校验与 plan_route（含危险层），
    保证「预览看到的路线」与「确认后建会话得到的路线」同源。
    """
    del user  # 鉴权仅要求登录态；预览不产生任何持久化副作用
    published = _resolve_published(repo, body.siteId, body.mapVersion)
    origin_age = _origin_age_s(body.origin.observedAt)
    if not is_sane(body.origin.observedAt):
        raise ApiError(
            422, 'LOCATION_UNRELIABLE', '起点观测时间戳不可用，请校准设备时间后重新定位',
            details={'reason': 'observation_time_invalid'},
        )
    if body.origin.confidence < ORIGIN_MIN_CONFIDENCE or body.origin.floorId is None:
        raise ApiError(422, "LOCATION_UNRELIABLE", "起点定位不可靠，请先重新定位")
    index = PackageIndex(published.pkg)
    place, dest_point = _resolve_destination(index, body.destinationId)
    origin_node = origin_anchor_node(
        index, body.origin.floorId, body.origin.x, body.origin.y,
        toward=dest_point,
    )
    route_info, _dest = plan_route(
        published.data, published.pkg, index, origin_node, body.destinationId, body.userMode,
        hazard_layer=hazard_repo.get(published.site_id, published.map_version),
        turn_prompt_distance_m=settings.nav_turn_prompt_distance_m,
        origin_position=(body.origin.floorId, body.origin.x, body.origin.y),
    )
    return PreviewRouteResponse(
        mapVersion=published.map_version,
        destination=PlaceCandidate(placeId=place.placeId, name=place.name, floorId=place.floorId),
        route=route_info,
        overviewText=None,
        originObservationAgeS=origin_age,
        originObservationStale=_is_stale(origin_age),
    )


@router.post("/sessions", response_model=CreateSessionResponse, status_code=201)
def create_session(
    body: CreateSessionRequest,
    request: Request,
    user: auth.AuthenticatedUser = Depends(auth.get_current_user),
    db: Session = Depends(auth.get_db_session),
    repo: MapRepository = Depends(get_map_repository),
    hazard_repo: HazardRepository = Depends(get_hazard_repository),
    settings: Settings = Depends(auth.get_settings),
) -> CreateSessionResponse:
    idem_key = (request.headers.get("Idempotency-Key") or "").strip()
    if not idem_key or len(idem_key) > 128:
        raise ApiError(400, "INVALID_REQUEST", "缺少 Idempotency-Key")
    excluded = {"experiment"} if body.experiment is None else set()
    request_hash = hashlib.sha256(body.model_dump_json(exclude=excluded).encode()).hexdigest()

    existing = db.get(
        NavIdempotency, {"subject_id": user.subject_id, "idempotency_key": idem_key}
    )
    if existing is not None:
        if existing.request_hash != request_hash:
            raise ApiError(409, "IDEMPOTENCY_CONFLICT", "幂等键已用于不同请求")
        return CreateSessionResponse.model_validate_json(existing.response_json)

    published = _resolve_published(repo, body.siteId, body.mapVersion)
    experiment_json = None
    if body.experiment is not None:
        if body.siteId != FIELD_SITE:
            raise ApiError(422, "INVALID_FIELD_PLAN", "该场所未配置实验导航")
        experiment = body.experiment
        require_plan(db, experiment.planId, "NV", published.map_version)
        config = frozen_config(db, experiment.configId, published.map_version)
        profile = config["A" if experiment.method == "A" else "B"]
        settings = replace(settings, nav_arrival_radius_m=config["arrivalRadiusM"],
                           nav_turn_prompt_distance_m=config["turnPromptDistanceM"],
                           location_params_version=profile["paramsVersion"])
        experiment_json = json.dumps(experiment.model_dump())
        place = next((p for p in published.pkg.places if p.placeId == body.destinationId), None)
        if not place or place.nodeId != "F1-TR-0003":
            raise ApiError(422, "INVALID_FIELD_PLAN", "实验路线终点必须为资源教室")
    origin_age = _origin_age_s(body.origin.observedAt)
    if not is_sane(body.origin.observedAt):
        raise ApiError(
            422, 'LOCATION_UNRELIABLE', '起点观测时间戳不可用，请校准设备时间后重新定位',
            details={'reason': 'observation_time_invalid'},
        )
    if body.origin.confidence < ORIGIN_MIN_CONFIDENCE or body.origin.floorId is None:
        raise ApiError(422, "LOCATION_UNRELIABLE", "起点定位不可靠，请先重新定位")
    index = PackageIndex(published.pkg)
    place, dest_point = _resolve_destination(index, body.destinationId)
    origin_node = origin_anchor_node(
        index, body.origin.floorId, body.origin.x, body.origin.y,
        toward=dest_point,
    )
    route_info, _dest = plan_route(
        published.data, published.pkg, index, origin_node, body.destinationId, body.userMode,
        hazard_layer=hazard_repo.get(published.site_id, published.map_version),
        turn_prompt_distance_m=settings.nav_turn_prompt_distance_m,
        origin_position=(body.origin.floorId, body.origin.x, body.origin.y),
    )
    # 导航行动文案由路线模板确定，禁止模型改写。
    # 动作/距离/触发节点与播报时机保持不变。
    # Actionable text stays exactly as produced by deterministic route templates.

    session_id = f"nav_{uuid.uuid4().hex[:20]}"
    now = _now_epoch()
    session = NavSession(
        session_id=session_id,
        subject_id=user.subject_id,
        site_id=body.siteId,
        map_version=published.map_version,
        experiment_json=experiment_json,
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
        originObservationAgeS=origin_age,
        originObservationStale=_is_stale(origin_age),
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
    try:
        db.commit()
    except IntegrityError:
        # 并发同 Idempotency-Key：两请求都读到「无记录」，先提交者成功，后者触发
        # (subject_id, idempotency_key) 唯一约束。回滚后按幂等语义返回先提交者的响应，
        # 而不是把 500 抛给客户端（重复建会话同时也会被回滚，不留孤儿数据）。
        db.rollback()
        existing = db.get(
            NavIdempotency, {"subject_id": user.subject_id, "idempotency_key": idem_key}
        )
        if existing is None:
            raise
        if existing.request_hash != request_hash:
            raise ApiError(409, "IDEMPOTENCY_CONFLICT", "幂等键已用于不同请求")
        return CreateSessionResponse.model_validate_json(existing.response_json)
    return response


def _relocalize_instruction(sequence: int) -> NavigationInstruction:
    # spec 0.3.1 REQ-NAV：恢复提示必须说明原因与用户可采取的措施，
    # 不得只播报笼统的"正在重新定位"。
    return NavigationInstruction(
        instructionId=f"ins_re_{uuid.uuid4().hex[:8]}",
        instructionSequence=sequence,
        action=InstructionAction.RELOCALIZE,
        text=(
            "附近定位信标不足或信号不稳定，导航已暂停。"
            "请原地稍候或缓步移至信号覆盖较好处；"
            "持续未恢复时请寻求周围人员协助或结束导航。"
        ),
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
) -> tuple[int, float, float] | None:
    """Return endpoint index, lateral distance and remaining along-route distance.

    Project only onto same-floor segments; never connect unrelated floors.
    Endpoints cover isolated floor landings and zero-length segments.
    """
    if floor_id is None:
        return None
    suffix = _polyline_suffix(route)
    best = None
    for i, point in enumerate(route.polyline):
        if point.floorId == floor_id:
            distance = hypot(point.x - x, point.y - y)
            if best is None or distance < best[1]:
                best = (i, distance, distance + suffix[i])
    for i in range(len(route.polyline) - 1):
        a, b = route.polyline[i], route.polyline[i + 1]
        if a.floorId != floor_id or b.floorId != floor_id:
            continue
        dx, dy = b.x - a.x, b.y - a.y
        length_sq = dx * dx + dy * dy
        if length_sq == 0:
            continue
        fraction = max(0.0, min(1.0, ((x - a.x) * dx + (y - a.y) * dy) / length_sq))
        distance = hypot(x - (a.x + fraction * dx), y - (a.y + fraction * dy))
        remaining = distance + (1.0 - fraction) * hypot(dx, dy) + suffix[i + 1]
        if best is None or distance < best[1]:
            best = (i + 1, distance, remaining)
    return best


#: 决策点判定为“已通过”的沿线余量。转弯指令必须保持到用户真正越过路口，
#: 否则客户端会在路口前停止转向震动，甚至漏掉转向播报。
DECISION_POINT_PASSED_MARGIN_M = 1.0
#: recovering 恢复引导所需的连续可信更新次数：质量在阈值附近抖动时，
#: 单拍可信就恢复会造成「恢复→再暂停」反复切换（表现为安全音频连播）。
RECOVER_RESUME_HITS = 2


def _route_progress(
    route: RouteInfo, floor_id: str | None, x: float, y: float
) -> tuple[float, float, list[float], float] | None:
    """返回 (沿线进度, 到终点剩余, 折线后缀和, 总长)。"""
    near = _nearest_on_route(route, floor_id, x, y)
    if near is None:
        return None
    suffix = _polyline_suffix(route)
    total = suffix[0]
    _, lateral, remaining = near
    return total - (remaining - lateral), remaining, suffix, total


def _node_route_progress(
    route: RouteInfo,
    index: PackageIndex,
    node_id: str | None,
    floor_id: str | None,
    suffix: list[float],
    total: float,
    current_progress: float,
) -> float | None:
    """决策节点在路线折线上的沿线里程；取离当前进度最近的同坐标顶点。

    不能只找“当前进度之后”的顶点：用户一旦越过节点，节点进度就落在后面，
    旧实现会找不到节点并退回 3m 近距判定，速度稍快就会卡在转向指令上。
    """
    if node_id is None or floor_id is None:
        return None
    node = index.nodes.get(node_id)
    if node is None or node.floor_id != floor_id:
        return None
    best = None
    best_delta = None
    for i, point in enumerate(route.polyline):
        if point.floorId != floor_id:
            continue
        if hypot(point.x - node.x, point.y - node.y) > 1e-6:
            continue
        progress = total - suffix[i]
        delta = abs(progress - current_progress)
        if best_delta is None or delta < best_delta:
            best = progress
            best_delta = delta
    return best


def _advance_current_index(
    route: RouteInfo,
    index: PackageIndex,
    cur: int,
    floor_id: str | None,
    x: float,
    y: float,
    turn_prompt_distance_m: float = TURN_PROMPT_DISTANCE_M,
) -> tuple[int, float | None]:
    """按沿线进度推进当前指令；转向指令保持到用户真正越过路口。

    - immediate：进入更新即切换；
    - decision_point：沿线进度超过节点 + 余量才切换（电梯保持原有近距判定）；
    - distance：剩余距离降到 valueM 时切换，valueM 由 build_route_info 设为
      “下一个决策点剩余距离 + 提前播报距离”，因此转向会在路口前切换。
    """
    geometry = _route_progress(route, floor_id, x, y)
    if geometry is None:
        return cur, None
    progress, remaining, suffix, total = geometry
    start_cur = cur

    def _step(index_value: int) -> tuple[int, bool]:
        """前进一步；新到的直行指令至少保留一拍，避免短直行被转向吞掉。

        例外：如果这个直行段后面紧跟一个已进入提前播报窗口的转向，则不要停在
        直行上，让同一拍继续推进到转向——短直行（2~5m）否则会被低频率位置更新
        直接跨过，转向播报和震动都会丢。
        """
        index_value += 1
        if route.segments[index_value].action is not InstructionAction.CONTINUE:
            return index_value, False
        next_index = index_value + 1
        if next_index < len(route.segments):
            next_seg = route.segments[next_index]
            if (
                next_seg.action in (InstructionAction.TURN_LEFT, InstructionAction.TURN_RIGHT)
                and next_seg.trigger.type == "decision_point"
            ):
                next_progress = _node_route_progress(
                    route, index, next_seg.trigger.nodeId, floor_id,
                    suffix, total, progress,
                )
                if (
                    next_progress is not None
                    and progress >= next_progress - turn_prompt_distance_m
                ):
                    return index_value, False
        stop = index_value != start_cur
        return index_value, stop

    while cur < len(route.segments) - 1:
        seg = route.segments[cur]
        if seg.action is InstructionAction.ARRIVE:
            break
        if seg.trigger.type == "immediate":
            cur, stop = _step(cur)
            if stop:
                break
            continue
        if seg.trigger.type == "decision_point" and seg.trigger.nodeId:
            node = index.nodes.get(seg.trigger.nodeId)
            if seg.action in (
                InstructionAction.ENTER_ELEVATOR,
                InstructionAction.EXIT_ELEVATOR,
            ):
                # 电梯没有同层“越过节点”的折线进度，保持原近距判定。
                # 例外：当前段是「进电梯」，而上报楼层已不是该节点所在楼层——
                # 说明用户已乘电梯到达另一层（客户端在电梯口暂停、目标层恢复上报），
                # 直接越过进出电梯两条指令，避免永久卡在「请进入电梯」。
                if (
                    seg.action is InstructionAction.ENTER_ELEVATOR
                    and node is not None
                    and node.floor_id != floor_id
                ):
                    cur, stop = _step(cur)
                    if stop:
                        break
                    continue
                if (
                    node is not None
                    and node.floor_id == floor_id
                    and hypot(x - node.x, y - node.y) <= DECISION_POINT_RADIUS_M
                ):
                    cur, stop = _step(cur)
                    if stop:
                        break
                    continue
                break
            node_progress = _node_route_progress(
                route, index, seg.trigger.nodeId, floor_id, suffix, total, progress
            )
            if node_progress is not None:
                if progress >= node_progress + DECISION_POINT_PASSED_MARGIN_M:
                    cur, stop = _step(cur)
                    if stop:
                        break
                    continue
                break
            # 节点不在折线上时退回近距判定（数据退化兜底）。
            if (
                node is not None
                and node.floor_id == floor_id
                and hypot(x - node.x, y - node.y) <= DECISION_POINT_RADIUS_M
            ):
                cur, stop = _step(cur)
                if stop:
                    break
                continue
            break
        if (
            seg.trigger.type == "distance"
            and seg.trigger.valueM is not None
            and remaining <= seg.trigger.valueM
        ):
            cur, stop = _step(cur)
            if stop:
                break
            continue
        break
    return cur, remaining


@router.post("/sessions/{session_id}/updates", response_model=NavUpdateResponse)
def nav_update(
    session_id: str,
    body: NavUpdateRequest,
    request: Request,
    user: auth.AuthenticatedUser = Depends(auth.get_current_user),
    db: Session = Depends(auth.get_db_session),
    repo: MapRepository = Depends(get_map_repository),
    hazard_repo: HazardRepository = Depends(get_hazard_repository),
    settings: Settings = Depends(auth.get_settings),
) -> NavUpdateResponse:
    session = _owned_session(db, session_id, user, for_update=True)
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
    if session.experiment_json:
        config = frozen_config(db, json.loads(session.experiment_json)["configId"], session.map_version)
        settings = replace(settings, nav_arrival_radius_m=config["arrivalRadiusM"],
                           nav_turn_prompt_distance_m=config["turnPromptDistanceM"])
    index = PackageIndex(published.pkg)
    route = RouteInfo.model_validate_json(session.route_json)
    loc: LocationUpdate = body.location
    # 到达锚点：房间类目的地取进入房间的门洞（房间中心定位退化，2m 半径内永远到不了），
    # 旧会话快照没有 arrivalNodeId，回退到终点指令的触发节点。
    dest_node_id = route.arrivalNodeId or route.segments[-1].trigger.nodeId
    dest = index.nodes.get(dest_node_id) or index.nodes[route.segments[-1].trigger.nodeId]
    dest_detail = index.node_details.get(dest.node_id)
    arrival_radius_m = settings.nav_arrival_radius_m
    if dest_detail is not None and getattr(dest_detail, "type", None) == "doorway":
        # 门洞锚点允许略宽半径：用户停在门外（与门中心有几米）也应判定到达。
        arrival_radius_m = max(arrival_radius_m, 2.5)

    previous_status = session.status
    evaluated_at = datetime.now(timezone.utc)
    pause_reason = None
    if loc.quality not in TRUSTED_QUALITIES:
        pause_reason = "untrusted_quality"
    elif loc.floorId is None:
        pause_reason = "missing_floor"
    elif not is_fresh_for_guidance(loc.observedAt, evaluated_at):
        pause_reason = "observation_not_fresh"
    elif loc.observedAt.timestamp() < session.updated_at_epoch - 5:
        # A recently delivered older batch must not roll guidance back across requests.
        pause_reason = "observation_precedes_session_update"
    # recovering 恢复需要连续可信：单拍可信就恢复会与暂停反复交替
    if pause_reason is None:
        session.trusted_streak = (session.trusted_streak or 0) + 1
        effective = (
            session.trusted_streak >= RECOVER_RESUME_HITS
            or session.status != SessionStatus.RECOVERING.value
        )
        if not effective:
            pause_reason = "awaiting_consecutive_trusted_updates"
    else:
        session.trusted_streak = 0
        effective = False

    if effective:
        arrival = (
            loc.floorId == dest.floor_id
            and hypot(loc.x - dest.x, loc.y - dest.y) <= arrival_radius_m
        )
    else:
        arrival = False

    if not effective:
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
            dest_place = index.places.get(session.destination_place_id)
            origin2 = origin_anchor_node(
                index, floor_id, loc.x, loc.y,
                toward=index.nodes.get(dest_place.nodeId) if dest_place is not None else None,
            )
            new_route, _ = plan_route(
                published.data, published.pkg, index, origin2,
                session.destination_place_id, UserMode(session.user_mode),
                hazard_layer=hazard_repo.get(session.site_id, published.map_version),
                turn_prompt_distance_m=settings.nav_turn_prompt_distance_m,
                origin_position=(loc.floorId, loc.x, loc.y),
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
            cur, remaining = _advance_current_index(
                route, index, cur, loc.floorId, loc.x, loc.y,
                settings.nav_turn_prompt_distance_m,
            )
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
    trusted_streak = session.trusted_streak
    db.commit()
    state_changed = previous_status != response.status
    waiting_for_confirmation = pause_reason == "awaiting_consecutive_trusted_updates"
    log_update = logger.info if state_changed or waiting_for_confirmation else logger.debug
    observed_age_s = observation_age_s(loc.observedAt, evaluated_at)
    log_update(
        "nav_update request_id=%s session_id=%s sequence=%s previous_status=%s status=%s event=%s "
        "reason=%s quality=%s floor=%s observation_age_s=%s trusted_streak=%s",
        request.state.request_id, session_id, body.sequence, previous_status, response.status, response.event,
        pause_reason, loc.quality, loc.floorId, observed_age_s, trusted_streak,
    )
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
    session = _owned_session(db, session_id, user, for_update=True)
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
