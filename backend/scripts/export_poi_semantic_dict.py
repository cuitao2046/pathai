# -*- coding: utf-8 -*-
"""从已发布地图包导出 POI 语义词典（specs/voice-agent-semantic-nav REQ-VOICE-01）。

词典是派生数据：唯一事实源是 `backend/maps_data/<site>/<version>.json`；
人工标注（别名/分类/公共设施/区域提示/类别别名/弱相关推荐）维护在
`src/voice_agent/config/poi_alias_overrides.json`，本脚本按 standard_name 合并。

用法（仓库根目录）：
    python backend/scripts/export_poi_semantic_dict.py [--site school-building-01]
        [--version latest|2026-10-28.2] [--out src/voice_agent/config/poi_semantic_dict.json]

校验不通过时非零退出（node_id/place_id 不在包内、数量不符、版本戳缺失）。
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_DIR.parent
DEFAULT_OUT = REPO_ROOT / "src" / "voice_agent" / "config" / "poi_semantic_dict.json"
OVERRIDES_PATH = REPO_ROOT / "src" / "voice_agent" / "config" / "poi_alias_overrides.json"

CN_FLOOR = {"1": "一楼", "2": "二楼", "3": "三楼", "4": "四楼", "5": "五楼", "6": "六楼"}

#: 不作为导航目的地的空间类型（与 convert_map_v9.NON_DESTINATION_ROOM_TYPES 同源）
NON_DESTINATION_ROOM_TYPES = {
    "infrastructure",
    "corridor",
    "staircase",
    "stair_lobby",
    "elevator_lobby",
}

#: (room type, roomSubType) -> category 默认映射；overrides 可覆盖
_CATEGORY_BY_SUBTYPE = {
    "classroom": "classroom",
    "office": "office",
    "lab": "classroom_lab",
    "equipment": "equipment",
    "activity": "activity",
    "reception": "reception",
    "meeting": "meeting",
    "storage": "storage",
    "medical": "medical",
    "room": "general",
}

#: 默认公共设施（可被 overrides.public_utility 覆盖）
_PUBLIC_UTILITY_CATEGORIES = {"restroom"}


def _floor_label(floor_id: str) -> str:
    return CN_FLOOR.get(floor_id, f"{floor_id}楼")


def _default_category(room: dict) -> str:
    if room.get("type") == "toilet":
        return "restroom"
    return _CATEGORY_BY_SUBTYPE.get(room.get("roomSubType") or "", "general")


def _zone_hint(floor_id: str, centroid: list[float], bounds: tuple[float, float, float, float]) -> str:
    """按房间质心在本层包围盒中的相对位置生成区域提示（人工 overrides 优先）。"""
    minx, miny, maxx, maxy = bounds
    xr = 0.5 if maxx <= minx else (centroid[0] - minx) / (maxx - minx)
    yr = 0.5 if maxy <= miny else (centroid[1] - miny) / (maxy - miny)
    horiz = "东" if xr >= 2 / 3 else ("西" if xr <= 1 / 3 else "中")
    vert = "北" if yr >= 2 / 3 else ("南" if yr <= 1 / 3 else "中")
    if horiz == "中" and vert == "中":
        part = "中段"
    elif horiz == "中":
        part = f"{vert}段"
    elif vert == "中":
        part = f"{horiz}侧"
    else:
        part = f"{horiz}{vert}角"
    return f"{_floor_label(floor_id)}{part}"


def _floor_bounds(rooms: list[dict]) -> tuple[float, float, float, float]:
    xs = [r["centroid"][0] for r in rooms]
    ys = [r["centroid"][1] for r in rooms]
    return min(xs), min(ys), max(xs), max(ys)


def build_dictionary(pkg: dict, overrides: dict) -> dict:
    floors = pkg["floors"]
    aliases: dict[str, list[str]] = overrides.get("aliases") or {}
    categories: dict[str, str] = overrides.get("categories") or {}
    public_utility: dict[str, bool] = overrides.get("public_utility") or {}
    zone_hints: dict[str, str] = overrides.get("zone_hints") or {}

    node_ids = {
        node["id"]
        for floor in floors.values()
        for node in floor["topology"]["nodes"]
    }
    room_by_id = {room["id"]: room for floor in floors.values() for room in floor["semantic"]["rooms"]}
    room_node_ids = {
        node["roomId"]: node["id"]
        for floor in floors.values()
        for node in floor["topology"]["nodes"]
        if node.get("type") == "room" and node.get("roomId")
    }
    bounds = {floor_id: _floor_bounds(floor["semantic"]["rooms"]) for floor_id, floor in floors.items()}

    # 可导航目的地：地图包 places 全集，按标准名聚合
    pois: dict[str, dict] = {}
    for place in pkg["places"]:
        room = room_by_id.get(place["placeId"])
        category = categories.get(place["name"]) or (
            _default_category(room) if room else "general"
        )
        is_public = public_utility.get(place["name"], category in _PUBLIC_UTILITY_CATEGORIES)
        entry = pois.setdefault(
            place["name"],
            {
                "standard_name": place["name"],
                "category": category,
                "is_public_utility": is_public,
                "aliases": sorted(set(aliases.get(place["name"], []))),
                "instances": [],
            },
        )
        hint = zone_hints.get(place["placeId"]) or _zone_hint(
            place["floorId"], room["centroid"] if room else [0.0, 0.0], bounds[place["floorId"]]
        )
        entry["instances"].append(
            {
                "floor": f"{place['floorId']}F",
                "floor_id": place["floorId"],
                "place_id": place["placeId"],
                "node_id": place["nodeId"],
                "zone_hint": hint,
            }
        )

    # 不可导航房间：全部命名房间 - 可导航目的地，附原因
    navigable_ids = {place["placeId"] for place in pkg["places"]}
    non_navigable = []
    for floor_id, floor in floors.items():
        for room in floor["semantic"]["rooms"]:
            if room["id"] in navigable_ids:
                continue
            if room["type"] in NON_DESTINATION_ROOM_TYPES:
                reason = "not_destination"
            elif not room.get("accessible", False):
                reason = "not_accessible"
            elif room["id"] not in room_node_ids:
                reason = "no_room_node"
            else:
                reason = "not_destination"
            non_navigable.append(
                {
                    "floor": f"{floor_id}F",
                    "floor_id": floor_id,
                    "room_id": room["id"],
                    "label": room["label"],
                    "type": room["type"],
                    "reason": reason,
                }
            )

    for entry in pois.values():
        entry["instances"].sort(key=lambda i: (i["floor_id"], i["place_id"]))
    return {
        "building_name": pkg.get("venueName") or pkg["siteId"],
        "site_id": pkg["siteId"],
        "map_version": pkg["mapVersion"],
        "exported_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "pois": [pois[name] for name in sorted(pois)],
        "non_navigable_rooms": sorted(non_navigable, key=lambda r: (r["floor_id"], r["room_id"])),
        "category_aliases": overrides.get("category_aliases") or {},
        "related_fallbacks": overrides.get("related_fallbacks") or {},
    }


def validate(dictionary: dict, pkg: dict) -> None:
    """导出后一致性校验：不通过直接失败，禁止静默产出坏词典。"""
    node_ids = {
        node["id"]
        for floor in pkg["floors"].values()
        for node in floor["topology"]["nodes"]
    }
    place_ids = {place["placeId"] for place in pkg["places"]}

    assert dictionary["map_version"] == pkg["mapVersion"], (
        f"map_version 不一致：词典 {dictionary['map_version']} vs 地图 {pkg['mapVersion']}"
    )
    names = [poi["standard_name"] for poi in dictionary["pois"]]
    assert len(names) == len(set(names)), "standard_name 出现重复聚合"

    instance_total = 0
    for poi in dictionary["pois"]:
        for inst in poi["instances"]:
            instance_total += 1
            assert inst["node_id"] in node_ids, f"node_id 不在包内：{inst['node_id']}"
            assert inst["place_id"] in place_ids, f"place_id 不在包内：{inst['place_id']}"
        assert poi["instances"], f"{poi['standard_name']} 没有物理实例"

    assert instance_total == len(pkg["places"]), (
        f"可导航实例数不符：词典 {instance_total} vs 地图 {len(pkg['places'])}"
    )
    room_total = sum(len(floor["semantic"]["rooms"]) for floor in pkg["floors"].values())
    assert instance_total + len(dictionary["non_navigable_rooms"]) == room_total, (
        "命名房间未全覆盖：可导航"
        f"{instance_total} + 不可导航{len(dictionary['non_navigable_rooms'])} != 房间{room_total}"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--site", default="school-building-01")
    parser.add_argument("--version", default="latest")
    parser.add_argument("--out", default=str(DEFAULT_OUT))
    args = parser.parse_args()

    for p in REPO_ROOT, BACKEND_DIR:
        if str(p) not in sys.path:
            sys.path.insert(0, str(p))
    from pathai_backend.map_repository import MapRepository  # noqa: E402

    repo = MapRepository.load(BACKEND_DIR / "maps_data", "https://localhost")
    published = (
        repo.latest(args.site)
        if args.version == "latest"
        else repo.get(args.site, args.version)
    )
    assert published is not None, f"地图版本未发布：{args.site}@{args.version}"

    overrides = json.loads(OVERRIDES_PATH.read_text(encoding="utf-8"))
    dictionary = build_dictionary(published.data, overrides)
    validate(dictionary, published.data)

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(dictionary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(
        f"已导出 {out}（map_version={dictionary['map_version']}）："
        f"{len(dictionary['pois'])} 个标准名 / {sum(len(p['instances']) for p in dictionary['pois'])} 实例，"
        f"不可导航房间 {len(dictionary['non_navigable_rooms'])} 条"
    )


if __name__ == "__main__":
    main()
