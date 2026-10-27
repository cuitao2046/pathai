# -*- coding: utf-8 -*-
"""草稿 patch 校验、overlay 视图与候选地图包构造（map-admin 技术设计 §6）。

规则（spec FR-03/FR-04，已确认值）：
- 地点 name/spokenName/type：trim 后非空，最多 128 个 Unicode 字符；
- aliases：最多 32 项，每项 trim 后非空且最多 128 字符，trim 后按 casefold 去重；
- 信标 locationDesc：可空，trim 后最多 200 字符。
"""
from __future__ import annotations

import copy
import json
from dataclasses import dataclass, field

from pathai_backend.errors import ApiError

PLACE_TEXT_MAX = 128
ALIAS_MAX_ITEMS = 32
BEACON_DESC_MAX = 200

_PLACE_FIELDS = ("name", "spokenName", "type")


def validate_place_patch(place_id: str, patch: dict) -> dict:
    """清洗并校验单个地点 patch；返回可入库的干净值（trim 后）。"""
    cleaned: dict = {}
    for field_name in _PLACE_FIELDS:
        value = patch.get(field_name)
        if value is None:
            continue
        if not isinstance(value, str):
            raise _field_error(place_id, field_name, "必须是字符串")
        trimmed = value.strip()
        if not trimmed:
            raise _field_error(place_id, field_name, "trim 后不能为空")
        if len(trimmed) > PLACE_TEXT_MAX:
            raise _field_error(place_id, field_name, f"最多 {PLACE_TEXT_MAX} 个字符")
        cleaned[field_name] = trimmed
    aliases = patch.get("aliases")
    if aliases is not None:
        if not isinstance(aliases, list):
            raise _field_error(place_id, "aliases", "必须是字符串数组")
        if len(aliases) > ALIAS_MAX_ITEMS:
            raise _field_error(place_id, "aliases", f"最多 {ALIAS_MAX_ITEMS} 项")
        seen: set[str] = set()
        deduped: list[str] = []
        for alias in aliases:
            if not isinstance(alias, str):
                raise _field_error(place_id, "aliases", "别名必须是字符串")
            trimmed = alias.strip()
            if not trimmed:
                raise _field_error(place_id, "aliases", "别名 trim 后不能为空")
            if len(trimmed) > PLACE_TEXT_MAX:
                raise _field_error(place_id, "aliases", f"别名最多 {PLACE_TEXT_MAX} 个字符")
            folded = trimmed.casefold()
            if folded in seen:
                continue
            seen.add(folded)
            deduped.append(trimmed)
        cleaned["aliases"] = deduped
    return cleaned


def validate_beacon_patch(beacon_id: str, patch: dict) -> dict:
    value = patch.get("locationDesc")
    if value is None:
        return {}
    if not isinstance(value, str):
        raise _field_error(beacon_id, "locationDesc", "必须是字符串")
    trimmed = value.strip()
    if len(trimmed) > BEACON_DESC_MAX:
        raise _field_error(beacon_id, "locationDesc", f"最多 {BEACON_DESC_MAX} 个字符")
    return {"locationDesc": trimmed}


def _field_error(object_id: str, field_name: str, reason: str) -> ApiError:
    return ApiError(
        422,
        "INVALID_FIELD_VALUE",
        f"字段取值不合法：{field_name}（{reason}）",
        details={"objectId": object_id, "field": field_name},
    )


@dataclass
class DraftState:
    site_id: str
    base_version: str
    patch: dict = field(default_factory=dict)


def normalize_patch(request_patch: dict) -> dict:
    """把请求 patch 清洗为数据库形态：{places: {id: {...}}, beacons: {id: {...}}}。"""
    return {
        "places": {
            place_id: validate_place_patch(place_id, dict(values))
            for place_id, values in (request_patch.get("places") or {}).items()
        },
        "beacons": {
            beacon_id: validate_beacon_patch(beacon_id, dict(values))
            for beacon_id, values in (request_patch.get("beacons") or {}).items()
        },
    }


def load_draft_state(site_id: str) -> DraftState | None:
    from pathai_backend.database import session_scope
    from pathai_backend.models import MapDraft

    with session_scope() as session:
        row = session.get(MapDraft, site_id)
        if row is None:
            return None
        return DraftState(
            site_id=row.site_id,
            base_version=row.base_version,
            patch=json.loads(row.patch_json),
        )


def save_draft_state(state: DraftState) -> None:
    import time

    from pathai_backend.database import session_scope
    from pathai_backend.models import MapDraft

    now = int(time.time())
    with session_scope() as session:
        row = session.get(MapDraft, state.site_id)
        if row is None:
            row = MapDraft(
                site_id=state.site_id,
                base_version=state.base_version,
                patch_json=json.dumps(state.patch, ensure_ascii=False),
                created_at_epoch=now,
                updated_at_epoch=now,
            )
            session.add(row)
        else:
            row.base_version = state.base_version
            row.patch_json = json.dumps(state.patch, ensure_ascii=False)
            row.updated_at_epoch = now
        session.commit()
        session.refresh(row)


def delete_draft_state(site_id: str) -> None:
    from pathai_backend.database import session_scope
    from pathai_backend.models import MapDraft

    with session_scope() as session:
        row = session.get(MapDraft, site_id)
        if row is not None:
            session.delete(row)
            session.commit()


def draft_updated_at(site_id: str) -> int | None:
    from pathai_backend.database import session_scope
    from pathai_backend.models import MapDraft

    with session_scope() as session:
        row = session.get(MapDraft, site_id)
        return row.updated_at_epoch if row else None


def assert_ids_exist(base_raw: dict, patch: dict) -> None:
    """patch 引用的对象必须在基准地图中存在（不允许新增/删除对象）。"""
    place_ids = {p["placeId"] for p in base_raw.get("places", [])}
    beacon_ids = {b["beaconId"] for b in base_raw.get("beacons", [])}
    for place_id in patch.get("places", {}):
        if place_id not in place_ids:
            raise ApiError(
                422, "PLACE_NOT_FOUND", f"地点不存在：{place_id}", details={"objectId": place_id}
            )
    for beacon_id in patch.get("beacons", {}):
        if beacon_id not in beacon_ids:
            raise ApiError(
                422, "BEACON_NOT_FOUND", f"信标不存在：{beacon_id}", details={"objectId": beacon_id}
            )


def apply_patch(base_raw: dict, patch: dict) -> dict:
    """深拷贝基准包并只写入白名单可编辑字段，返回候选包 dict。"""
    candidate = copy.deepcopy(base_raw)
    place_patch = patch.get("places", {})
    beacon_patch = patch.get("beacons", {})
    for place in candidate.get("places", []):
        overrides = place_patch.get(place["placeId"], {})
        for field_name in ("name", "spokenName", "aliases", "type"):
            if field_name in overrides:
                place[field_name] = overrides[field_name]
    for beacon in candidate.get("beacons", []):
        overrides = beacon_patch.get(beacon["beaconId"], {})
        if "locationDesc" in overrides:
            beacon["locationDesc"] = overrides["locationDesc"]
    return candidate


def strip_editable_fields(raw: dict) -> dict:
    """去掉可编辑字段与版本元数据，用于只读字段一致性断言。"""
    stripped = copy.deepcopy(raw)
    stripped.pop("mapVersion", None)
    stripped.pop("publishedAt", None)
    for place in stripped.get("places", []):
        for field_name in ("name", "spokenName", "aliases", "type"):
            place.pop(field_name, None)
    for beacon in stripped.get("beacons", []):
        beacon.pop("locationDesc", None)
    return stripped


def _node_coordinates(base_raw: dict) -> dict[tuple[str, str], tuple[float, float]]:
    """floorId + nodeId → (x, y)，供地点在地图上定位。"""
    coords: dict[tuple[str, str], tuple[float, float]] = {}
    for floor_id, floor in (base_raw.get("floors") or {}).items():
        nodes = ((floor or {}).get("topology") or {}).get("nodes") or []
        for node in nodes:
            xy = node.get("coordinates")
            if isinstance(xy, (list, tuple)) and len(xy) >= 2:
                coords[(floor_id, node.get("id"))] = (float(xy[0]), float(xy[1]))
    return coords


def effective_place_views(base_raw: dict, patch: dict) -> list[dict]:
    """编辑视图：只读上下文 + 叠加 patch 后的可编辑值。"""
    place_patch = patch.get("places", {})
    node_coords = _node_coordinates(base_raw)
    views = []
    for place in base_raw.get("places", []):
        overrides = place_patch.get(place["placeId"], {})
        xy = node_coords.get((place["floorId"], place.get("nodeId")))
        views.append(
            {
                "placeId": place["placeId"],
                "floorId": place["floorId"],
                "nodeId": place["nodeId"],
                "x": xy[0] if xy else None,
                "y": xy[1] if xy else None,
                "accessible": place.get("accessible", True),
                "name": overrides.get("name", place.get("name", "")),
                "spokenName": overrides.get("spokenName", place.get("spokenName", "")),
                "aliases": list(overrides.get("aliases", place.get("aliases", []))),
                "type": overrides.get("type", place.get("type", "")),
            }
        )
    return views


def effective_beacon_views(base_raw: dict, patch: dict) -> list[dict]:
    beacon_patch = patch.get("beacons", {})
    views = []
    for beacon in base_raw.get("beacons", []):
        overrides = beacon_patch.get(beacon["beaconId"], {})
        views.append(
            {
                "beaconId": beacon["beaconId"],
                "floorId": beacon["floorId"],
                "x": beacon["x"],
                "y": beacon["y"],
                "txPower": beacon.get("txPower"),
                "installHeightM": beacon.get("installHeightM"),
                "uuid": beacon.get("uuid"),
                "major": beacon.get("major"),
                "minor": beacon.get("minor"),
                "locationDesc": overrides.get("locationDesc", beacon.get("locationDesc", "")),
                "mountType": beacon.get("mountType"),
                "semanticTag": beacon.get("semanticTag"),
            }
        )
    return views
