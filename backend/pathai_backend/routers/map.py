# -*- coding: utf-8 -*-
"""API-02 地图版本摘要 / 地图数据下载 / API-03 地点查询（docs/24 §8）。

- 均要求 Bearer 令牌；
- ETag = 地图包 sha256；If-None-Match 命中返回 304；
- siteId 未知 → 404 SITE_NOT_FOUND；已知但版本未发布 → 404 MAP_NOT_PUBLISHED。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request, Response

from pathai_backend import auth
from pathai_backend.errors import ApiError
from pathai_backend.map_package.schema import PlaceEntry
from pathai_backend.map_repository import MapRepository, PublishedMap
from pathai_backend.schemas.map import (
    CoordinateSystem,
    FloorSummary,
    MapVersionResponse,
    Place,
    PlacesResponse,
)

router = APIRouter(
    prefix="/api/v1/sites",
    tags=["map"],
    dependencies=[Depends(auth.get_current_user)],
)


def get_map_repository(request: Request) -> MapRepository:
    return request.app.state.map_repository


def _resolve_published(
    repo: MapRepository, site_id: str, version: str
) -> PublishedMap:
    if not repo.is_known_site(site_id):
        raise ApiError(404, "SITE_NOT_FOUND", f"场所不存在：{site_id}")
    published = repo.latest(site_id) if version == "latest" else repo.get(site_id, version)
    if published is None:
        raise ApiError(404, "MAP_NOT_PUBLISHED", f"地图版本未发布：{site_id}@{version}")
    return published


def _etag_hit(request: Request, published: PublishedMap) -> bool:
    header = request.headers.get("if-none-match")
    if not header:
        return False
    for candidate in header.split(","):
        candidate = candidate.strip()
        if candidate.startswith("W/"):
            candidate = candidate[2:]
        if candidate == published.etag or candidate == published.sha256:
            return True
    return False


def _parse_range_header(value: str, total: int) -> tuple[int, int] | None:
    """解析单段 `Range: bytes=start-end`；非法/不可满足返回 None（调用方回 416）。

    仅支持单段（断点续传只需要 `bytes=N-`）；多段或非 bytes 单位视为不可满足。
    """
    unit, _, spec = value.partition("=")
    if unit.strip().lower() != "bytes" or not spec.strip():
        return None
    first = spec.split(",")[0].strip()
    start_text, sep, end_text = first.partition("-")
    if sep == "":
        return None
    try:
        if start_text == "":
            # 后缀范围：bytes=-N（末尾 N 字节）
            suffix = int(end_text)
            if suffix <= 0 or total == 0:
                return None
            start = max(0, total - suffix)
            end = total - 1
        else:
            start = int(start_text)
            end = int(end_text) if end_text else total - 1
    except ValueError:
        return None
    if start < 0 or start >= total or start > end:
        return None
    return start, min(end, total - 1)


def _range_response(request: Request, published: PublishedMap) -> Response:
    """地图数据下载响应：支持断点续传（Accept-Ranges/206/416），ETag 304 短路优先。"""
    if _etag_hit(request, published):
        return Response(
            status_code=304,
            headers={"ETag": published.etag, "Accept-Ranges": "bytes"},
        )
    raw = published.raw
    total = len(raw)
    headers = {"ETag": published.etag, "Accept-Ranges": "bytes"}
    range_header = request.headers.get("range")
    if not range_header:
        return Response(content=raw, media_type="application/json", headers=headers)
    parsed = _parse_range_header(range_header, total)
    if parsed is None:
        return Response(
            status_code=416,
            headers={**headers, "Content-Range": f"bytes */{total}"},
        )
    start, end = parsed
    return Response(
        content=raw[start : end + 1],
        status_code=206,
        media_type="application/json",
        headers={**headers, "Content-Range": f"bytes {start}-{end}/{total}"},
    )


def _floor_summaries(published: PublishedMap) -> list[FloorSummary]:
    return [
        FloorSummary(floorId=floor_id, displayName=floor.displayName, level=floor.level)
        for floor_id, floor in sorted(
            published.pkg.floors.items(), key=lambda kv: kv[1].level
        )
    ]


@router.get("/{site_id}/map", response_model=MapVersionResponse)
def get_map_version(
    site_id: str,
    request: Request,
    version: str = Query(default="latest", min_length=1, max_length=64),
    repo: MapRepository = Depends(get_map_repository),
) -> Response:
    published = _resolve_published(repo, site_id, version)
    if _etag_hit(request, published):
        return Response(status_code=304, headers={"ETag": published.etag})
    body = MapVersionResponse(
        siteId=published.site_id,
        mapVersion=published.map_version,
        publishedAt=published.pkg.publishedAt,
        coordinateSystem=CoordinateSystem(
            unit=published.pkg.coordinateSystem.unit,
            originDescription=published.pkg.coordinateSystem.originDescription,
            headingReference=published.pkg.coordinateSystem.headingReference,
        ),
        floors=_floor_summaries(published),
        dataUrl=published.data_url,
        dataSha256=published.sha256,
    )
    return Response(
        content=body.model_dump_json(),
        media_type="application/json",
        headers={"ETag": published.etag},
    )


@router.get("/{site_id}/map/data")
def get_map_data(
    site_id: str,
    request: Request,
    version: str = Query(default="latest", min_length=1, max_length=64),
    repo: MapRepository = Depends(get_map_repository),
) -> Response:
    published = _resolve_published(repo, site_id, version)
    return _range_response(request, published)


def _match_rank(place: PlaceEntry, q: str) -> int | None:
    """名称相关度：精确匹配 > 前缀 > 包含 > 别名精确 > 别名包含；不匹配返回 None。"""
    ql = q.lower()
    name = place.name.lower()
    spoken = place.spokenName.lower()
    if ql == name or ql == spoken:
        return 0
    if name.startswith(ql) or spoken.startswith(ql):
        return 1
    if ql in name or ql in spoken:
        return 2
    alias_exact = any(ql == a.lower() for a in place.aliases)
    if alias_exact:
        return 3
    if any(ql in a.lower() for a in place.aliases):
        return 4
    return None


@router.get("/{site_id}/places", response_model=PlacesResponse)
def search_places(
    site_id: str,
    q: str | None = Query(default=None, max_length=100),
    floor: str | None = Query(default=None, min_length=1, max_length=16),
    type: str | None = Query(default=None, min_length=1, max_length=64),
    limit: int = Query(default=10, ge=1, le=50),
    repo: MapRepository = Depends(get_map_repository),
) -> PlacesResponse:
    published = _resolve_published(repo, site_id, "latest")
    places = published.pkg.places
    if floor is not None:
        places = [p for p in places if p.floorId == floor]
    if type is not None:
        places = [p for p in places if p.type == type]
    if q:
        ranked: list[tuple[int, str, PlaceEntry]] = []
        for p in places:
            rank = _match_rank(p, q)
            if rank is not None:
                ranked.append((rank, p.placeId, p))
        ranked.sort(key=lambda item: (item[0], item[1]))
        places = [item[2] for item in ranked]
    else:
        places = sorted(places, key=lambda p: p.placeId)
    places = places[:limit]
    return PlacesResponse(
        mapVersion=published.map_version,
        items=[
            Place(
                placeId=p.placeId,
                name=p.name,
                spokenName=p.spokenName,
                aliases=list(p.aliases),
                type=p.type,
                floorId=p.floorId,
                nodeId=p.nodeId,
                accessible=p.accessible,
            )
            for p in places
        ],
        nextCursor=None,
    )
