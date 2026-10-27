# -*- coding: utf-8 -*-
"""地图管理 API：场所/版本浏览、草稿读写、校验、发布与回滚（技术设计 §5-§7）。

全部端点要求管理员会话且已完成首改密；写请求额外校验 CSRF 与同源 Origin。
"""
from __future__ import annotations

import copy

from fastapi import APIRouter, Request, Response

from pathai_backend.admin import deps, drafts, publish, service
from pathai_backend.admin.schemas import DraftPatchRequest
from pathai_backend.errors import ApiError
from pathai_backend.map_repository import MapRepository, PublishedMap

router = APIRouter(prefix="/api/v1/admin", tags=["admin-maps"])


def _repo(request: Request) -> MapRepository:
    repo = getattr(request.app.state, "map_repository", None)
    if repo is None:
        raise ApiError(
            503, "MAP_REPOSITORY_UNAVAILABLE", "地图仓库暂不可用，请联系管理员重启服务", retryable=True
        )
    return repo


def _latest(request: Request, repo: MapRepository, site_id: str) -> PublishedMap:
    if not repo.is_known_site(site_id):
        raise ApiError(404, "SITE_NOT_FOUND", f"场所不存在：{site_id}")
    latest = repo.latest(site_id)
    if latest is None:
        raise ApiError(404, "MAP_NOT_PUBLISHED", f"场所尚无已发布地图：{site_id}")
    return latest


def _iso(dt) -> str:
    text = dt.isoformat()
    return text.replace("+00:00", "Z")


_GEOMETRY_DECIMALS = 3


def _round_coord(value) -> float | None:
    try:
        return round(float(value), _GEOMETRY_DECIMALS)
    except (TypeError, ValueError):
        return None


def _polygon_points(feature: dict) -> list[list[float]]:
    coordinates = ((feature.get("geometry") or {}).get("coordinates")) or []
    if not coordinates:
        return []
    points: list[list[float]] = []
    for point in coordinates[0]:
        if not isinstance(point, (list, tuple)) or len(point) < 2:
            continue
        x = _round_coord(point[0])
        y = _round_coord(point[1])
        if x is None or y is None:
            continue
        points.append([x, y])
    return points


def _line_points(feature: dict) -> list[float] | None:
    coordinates = ((feature.get("geometry") or {}).get("coordinates")) or []
    if len(coordinates) < 2:
        return None
    x1 = _round_coord(coordinates[0][0])
    y1 = _round_coord(coordinates[0][1])
    x2 = _round_coord(coordinates[1][0])
    y2 = _round_coord(coordinates[1][1])
    if None in (x1, y1, x2, y2):
        return None
    return [x1, y1, x2, y2]


def _floor_geometry(published: PublishedMap, floor_id: str) -> dict:
    """按楼层返回紧凑几何（坐标已降精度），供管理端 SVG 地图渲染。"""
    floors = published.data.get("floors") or {}
    floor = floors.get(floor_id)
    if not isinstance(floor, dict):
        raise ApiError(404, "FLOOR_NOT_FOUND", f"楼层不存在：{floor_id}")
    geometry = floor.get("geometry") or {}

    rooms = []
    for feature in geometry.get("rooms") or []:
        props = feature.get("properties") or {}
        points = _polygon_points(feature)
        if not points:
            continue
        centroid = props.get("centroid")
        rooms.append(
            {
                "id": feature.get("id") or props.get("roomId") or "",
                "label": props.get("label") or "",
                "type": props.get("type") or "",
                "points": points,
                "centroid": (
                    [_round_coord(centroid[0]), _round_coord(centroid[1])]
                    if isinstance(centroid, (list, tuple)) and len(centroid) >= 2
                    else None
                ),
            }
        )

    walls = []
    for feature in geometry.get("walls") or []:
        segment = _line_points(feature)
        if segment is not None:
            walls.append(segment)

    def _shapes(key: str) -> list[dict]:
        items = []
        for feature in geometry.get(key) or []:
            points = _polygon_points(feature)
            if not points:
                continue
            props = feature.get("properties") or {}
            items.append(
                {
                    "id": feature.get("id") or "",
                    "label": props.get("label") or props.get("code") or "",
                    "points": points,
                }
            )
        return items

    columns = _shapes("columns")
    stairs = _shapes("stairs")
    elevators = _shapes("elevators")

    xs: list[float] = []
    ys: list[float] = []
    for points in [room["points"] for room in rooms] + [item["points"] for item in columns]:
        for x, y in points:
            xs.append(x)
            ys.append(y)
    for points in [item["points"] for item in stairs] + [item["points"] for item in elevators]:
        for x, y in points:
            xs.append(x)
            ys.append(y)
    for x1, y1, x2, y2 in walls:
        xs.extend((x1, x2))
        ys.extend((y1, y2))
    # 标记点也纳入范围，避免对象落在几何范围之外时无法定位
    views = drafts.effective_place_views(published.data, {})
    for view in views:
        if view["floorId"] == floor_id and view["x"] is not None and view["y"] is not None:
            xs.append(view["x"])
            ys.append(view["y"])
    for beacon in published.data.get("beacons") or []:
        if beacon.get("floorId") == floor_id:
            xs.append(float(beacon["x"]))
            ys.append(float(beacon["y"]))

    bounds = (
        [_round_coord(min(xs)), _round_coord(min(ys)), _round_coord(max(xs)), _round_coord(max(ys))]
        if xs and ys
        else None
    )
    return {
        "siteId": published.site_id,
        "mapVersion": published.map_version,
        "floorId": floor_id,
        "displayName": floor.get("displayName") or floor_id,
        "level": floor.get("level"),
        "bounds": bounds,
        "rooms": rooms,
        "walls": walls,
        "columns": columns,
        "stairs": stairs,
        "elevators": elevators,
    }


@router.get("/sites")
def list_sites(request: Request) -> dict:
    deps.require_admin_ready(request)
    repo = _repo(request)
    items = []
    for site_id in sorted(repo.known_sites):
        latest = repo.latest(site_id)
        if latest is None:
            continue
        items.append(
            {
                "siteId": site_id,
                "latestVersion": latest.map_version,
                "publishedAt": _iso(latest.pkg.publishedAt),
                "floorCount": len(latest.pkg.floors),
                "placeCount": len(latest.pkg.places),
                "beaconCount": len(latest.pkg.beacons),
            }
        )
    return {"items": items}


@router.get("/sites/{site_id}/versions")
def list_versions(request: Request, site_id: str) -> dict:
    deps.require_admin_ready(request)
    repo = _repo(request)
    latest = _latest(request, repo, site_id)
    items = []
    for version in repo.get_site_versions(site_id):
        published = repo.get(site_id, version)
        if published is None:
            continue
        items.append(
            {
                "mapVersion": published.map_version,
                "publishedAt": _iso(published.pkg.publishedAt),
                "isLatest": published.map_version == latest.map_version,
            }
        )
    items.sort(key=lambda item: (item["publishedAt"], item["mapVersion"]), reverse=True)
    return {"siteId": site_id, "latestVersion": latest.map_version, "items": items}


@router.get("/sites/{site_id}/versions/{version}")
def version_detail(request: Request, site_id: str, version: str) -> dict:
    deps.require_admin_ready(request)
    repo = _repo(request)
    latest = _latest(request, repo, site_id)
    published = repo.get(site_id, version)
    if published is None:
        raise ApiError(404, "MAP_NOT_PUBLISHED", f"地图版本未发布：{site_id}@{version}")
    floors = [
        {"floorId": floor_id, "displayName": floor.displayName, "level": floor.level}
        for floor_id, floor in sorted(published.pkg.floors.items(), key=lambda kv: kv[1].level)
    ]
    return {
        "siteId": site_id,
        "mapVersion": published.map_version,
        "publishedAt": _iso(published.pkg.publishedAt),
        "isLatest": published.map_version == latest.map_version,
        "floors": floors,
        "places": drafts.effective_place_views(published.data, {}),
        "beacons": drafts.effective_beacon_views(published.data, {}),
    }


@router.get("/sites/{site_id}/versions/{version}/floors/{floor_id}/geometry")
def floor_geometry(request: Request, site_id: str, version: str, floor_id: str) -> dict:
    deps.require_admin_ready(request)
    repo = _repo(request)
    published = repo.get(site_id, version)
    if published is None:
        raise ApiError(404, "MAP_NOT_PUBLISHED", f"地图版本未发布：{site_id}@{version}")
    return _floor_geometry(published, floor_id)


@router.get("/sites/{site_id}/draft")
def get_draft(request: Request, site_id: str) -> dict:
    deps.require_admin_ready(request)
    repo = _repo(request)
    latest = _latest(request, repo, site_id)
    state = drafts.load_draft_state(site_id)
    if state is None:
        return {"exists": False, "siteId": site_id, "latestVersion": latest.map_version}
    base = repo.get(site_id, state.base_version)
    if base is None:
        raise ApiError(
            409, "DRAFT_BASE_MISSING", f"草稿基准版本不存在：{site_id}@{state.base_version}"
        )
    return {
        "exists": True,
        "siteId": site_id,
        "baseVersion": state.base_version,
        "latestVersion": latest.map_version,
        "stale": state.base_version != latest.map_version,
        "updatedAtEpoch": drafts.draft_updated_at(site_id),
        "patch": state.patch,
        "places": drafts.effective_place_views(base.data, state.patch),
        "beacons": drafts.effective_beacon_views(base.data, state.patch),
    }


@router.put("/sites/{site_id}/draft")
def save_draft(request: Request, site_id: str, body: DraftPatchRequest) -> dict:
    deps.check_same_origin(request)
    deps.require_admin_ready(request)
    repo = _repo(request)
    latest = _latest(request, repo, site_id)
    cleaned = drafts.normalize_patch(body.model_dump())
    drafts.assert_ids_exist(latest.data, cleaned)
    state = drafts.load_draft_state(site_id)
    if state is None:
        state = drafts.DraftState(site_id=site_id, base_version=latest.map_version, patch=cleaned)
    else:
        state.patch = cleaned
    drafts.save_draft_state(state)
    base = repo.get(site_id, state.base_version)
    assert base is not None
    return {
        "exists": True,
        "siteId": site_id,
        "baseVersion": state.base_version,
        "latestVersion": latest.map_version,
        "stale": state.base_version != latest.map_version,
        "updatedAtEpoch": drafts.draft_updated_at(site_id),
        "patch": state.patch,
        "places": drafts.effective_place_views(base.data, state.patch),
        "beacons": drafts.effective_beacon_views(base.data, state.patch),
    }


@router.post("/sites/{site_id}/draft/reset")
def reset_draft(request: Request, site_id: str, response: Response) -> dict:
    deps.check_same_origin(request)
    context = deps.require_admin_ready(request)
    repo = _repo(request)
    latest = _latest(request, repo, site_id)
    drafts.delete_draft_state(site_id)
    service.record_audit(
        "draft_reset",
        True,
        {"username": context.account.username, "siteId": site_id},
    )
    return {"exists": False, "siteId": site_id, "latestVersion": latest.map_version}


@router.post("/sites/{site_id}/draft/validate")
def validate_draft(request: Request, site_id: str) -> dict:
    deps.check_same_origin(request)
    deps.require_admin_ready(request)
    repo = _repo(request)
    latest = _latest(request, repo, site_id)
    state = drafts.load_draft_state(site_id)
    if state is None:
        raise ApiError(404, "DRAFT_NOT_FOUND", "尚未创建草稿")
    stale = state.base_version != latest.map_version
    try:
        publish.build_candidate(repo, site_id, state.base_version, state.patch)
    except ApiError as exc:
        if exc.code == "VALIDATION_FAILED":
            return {"ok": False, "stale": stale, "issues": exc.details.get("issues", [])}
        raise
    return {"ok": True, "stale": stale, "issues": []}


@router.post("/sites/{site_id}/draft/publish")
def publish_draft(request: Request, site_id: str) -> dict:
    deps.check_same_origin(request)
    context = deps.require_admin_ready(request)
    repo = _repo(request)
    latest = _latest(request, repo, site_id)
    state = drafts.load_draft_state(site_id)
    if state is None:
        raise ApiError(404, "DRAFT_NOT_FOUND", "尚未创建草稿")
    if state.base_version != latest.map_version:
        raise ApiError(
            409, "DRAFT_BASE_STALE", "草稿基准已不是最新版本，请先重置草稿再发布"
        )
    candidate, base_version = publish.build_candidate(
        repo, site_id, state.base_version, state.patch
    )
    settings = request.app.state.settings
    try:
        result = publish.publish_candidate(repo, settings.map_data_dir, site_id, candidate)
    except OSError:
        raise ApiError(500, "PUBLISH_WRITE_FAILED", "版本文件写入失败，当前 latest 未改变")
    try:
        publish.refresh_repository(request.app, settings.map_data_dir, settings.public_base_url)
    except Exception:
        # 版本文件已写入（提交点已过）：结果不确定，要求管理端重读版本状态
        service.record_audit(
            "publish",
            True,
            {
                "username": context.account.username,
                "siteId": site_id,
                "baseVersion": base_version,
                "mapVersion": result.map_version,
                "stateUnknown": True,
            },
        )
        raise ApiError(
            500,
            "PUBLISH_STATE_UNKNOWN",
            "版本文件已写入但服务刷新失败，请重新读取版本状态确认结果",
        )
    # 发布成功即消费草稿：后续编辑重新从新 latest 复制为草稿，避免刚发布就提示基准过期
    try:
        drafts.delete_draft_state(site_id)
    except Exception:  # noqa: BLE001 - 草稿清理失败不影响已完成的发布结果
        pass
    service.record_audit(
        "publish",
        True,
        {
            "username": context.account.username,
            "siteId": site_id,
            "baseVersion": base_version,
            "mapVersion": result.map_version,
        },
    )
    return {
        "siteId": site_id,
        "baseVersion": base_version,
        "mapVersion": result.map_version,
        "publishedAt": result.published_at,
    }


@router.post("/sites/{site_id}/versions/{version}/rollback")
def rollback_version(request: Request, site_id: str, version: str) -> dict:
    deps.check_same_origin(request)
    context = deps.require_admin_ready(request)
    repo = _repo(request)
    _latest(request, repo, site_id)
    published = repo.get(site_id, version)
    if published is None:
        raise ApiError(404, "MAP_NOT_PUBLISHED", f"地图版本未发布：{site_id}@{version}")
    candidate = copy.deepcopy(published.data)
    settings = request.app.state.settings
    try:
        result = publish.publish_candidate(repo, settings.map_data_dir, site_id, candidate)
    except OSError:
        raise ApiError(500, "PUBLISH_WRITE_FAILED", "版本文件写入失败，当前 latest 未改变")
    try:
        publish.refresh_repository(request.app, settings.map_data_dir, settings.public_base_url)
    except Exception:
        service.record_audit(
            "rollback",
            True,
            {
                "username": context.account.username,
                "siteId": site_id,
                "fromVersion": version,
                "mapVersion": result.map_version,
                "stateUnknown": True,
            },
        )
        raise ApiError(
            500,
            "PUBLISH_STATE_UNKNOWN",
            "版本文件已写入但服务刷新失败，请重新读取版本状态确认结果",
        )
    service.record_audit(
        "rollback",
        True,
        {
            "username": context.account.username,
            "siteId": site_id,
            "fromVersion": version,
            "mapVersion": result.map_version,
        },
    )
    return {
        "siteId": site_id,
        "rolledBackFrom": version,
        "mapVersion": result.map_version,
        "publishedAt": result.published_at,
    }
