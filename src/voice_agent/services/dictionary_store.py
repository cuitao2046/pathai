# -*- coding: utf-8 -*-
"""POI 语义词典加载与版本校验（spec REQ-VOICE-01）。

词典是派生数据：`poi_semantic_dict.json` 只能由
`backend/scripts/export_poi_semantic_dict.py` 生成；人工标注在
`poi_alias_overrides.json`，导出时合并。
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

DEFAULT_DICT_PATH = Path(__file__).resolve().parent.parent / "config" / "poi_semantic_dict.json"


class DictionaryError(Exception):
    """词典不可用（缺失/损坏/版本过期）。"""


class DictionaryStale(DictionaryError):
    """词典版本与线上地图版本不一致：必须拒绝消歧并提示地图更新中。"""


@dataclass(frozen=True)
class PoiInstance:
    floor: str  # "1F"（展示用）
    floor_id: str  # "1"（发布包楼层键）
    place_id: str
    node_id: str
    zone_hint: str


@dataclass(frozen=True)
class PoiEntry:
    standard_name: str
    category: str
    is_public_utility: bool
    aliases: tuple[str, ...]
    instances: tuple[PoiInstance, ...]

    def instances_on_floor(self, floor_id: str | None) -> tuple[PoiInstance, ...]:
        if floor_id is None:
            return self.instances
        return tuple(i for i in self.instances if i.floor_id == floor_id)


class PoiDictionary:
    def __init__(self, data: dict):
        self.building_name: str = data["building_name"]
        self.site_id: str = data["site_id"]
        self.map_version: str = data["map_version"]
        self.exported_at: str = data["exported_at"]
        self.pois: dict[str, PoiEntry] = {
            item["standard_name"]: PoiEntry(
                standard_name=item["standard_name"],
                category=item["category"],
                is_public_utility=bool(item["is_public_utility"]),
                aliases=tuple(item.get("aliases") or ()),
                instances=tuple(
                    PoiInstance(
                        floor=inst["floor"],
                        floor_id=inst["floor_id"],
                        place_id=inst["place_id"],
                        node_id=inst["node_id"],
                        zone_hint=inst["zone_hint"],
                    )
                    for inst in item["instances"]
                ),
            )
            for item in data["pois"]
        }
        self.non_navigable_rooms: tuple[dict, ...] = tuple(data.get("non_navigable_rooms") or ())
        self.category_aliases: dict[str, tuple[str, ...]] = {
            key: tuple(value) for key, value in (data.get("category_aliases") or {}).items()
        }
        self.related_fallbacks: dict[str, str] = dict(data.get("related_fallbacks") or {})

    # ------------------------------------------------------------------ #
    # 加载与校验
    # ------------------------------------------------------------------ #
    @classmethod
    def from_path(cls, path: Path | str = DEFAULT_DICT_PATH) -> "PoiDictionary":
        file = Path(path)
        if not file.is_file():
            raise DictionaryError(f"POI 词典缺失：{file}（请运行导出脚本）")
        try:
            data = json.loads(file.read_text(encoding="utf-8"))
        except json.JSONDecodeError as err:
            raise DictionaryError(f"POI 词典损坏：{file}：{err}") from err
        return cls(data)

    def ensure_version(self, map_version: str) -> None:
        """运行时版本校验：与线上地图不一致时禁止用于消歧（spec REQ-VOICE-01）。"""
        if map_version != self.map_version:
            raise DictionaryStale(
                f"词典版本过期：词典 {self.map_version} vs 地图 {map_version}，"
                "请重新导出并热加载"
            )

    # ------------------------------------------------------------------ #
    # 查询
    # ------------------------------------------------------------------ #
    def get(self, standard_name: str) -> PoiEntry | None:
        return self.pois.get(standard_name)

    def entries_for_category_alias(self, spoken: str) -> tuple[PoiEntry, ...]:
        return tuple(
            self.pois[name] for name in self.category_aliases.get(spoken, ()) if name in self.pois
        )

    def alias_to_names(self) -> dict[str, set[str]]:
        """口语别名/标准名 → 标准名集合（同词可能指向多个标准名，用于最长匹配消歧）。"""
        index: dict[str, set[str]] = {}
        for entry in self.pois.values():
            for name in (entry.standard_name, *entry.aliases):
                index.setdefault(name, set()).add(entry.standard_name)
        return index

    def navigable_names(self) -> Iterable[str]:
        return self.pois.keys()
