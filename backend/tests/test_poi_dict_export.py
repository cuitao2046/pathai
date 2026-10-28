# -*- coding: utf-8 -*-
"""POI 语义词典导出测试（spec REQ-VOICE-01）。

以真实地图包为源，验证聚合口径、人工标注合并、版本戳与一致性校验。
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_DIR.parent
for _path in (REPO_ROOT, BACKEND_DIR):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from pathai_backend.map_repository import MapRepository  # noqa: E402
from src.voice_agent.services.dictionary_store import (  # noqa: E402
    DictionaryStale,
    PoiDictionary,
)

OVERRIDES_PATH = REPO_ROOT / "src" / "voice_agent" / "config" / "poi_alias_overrides.json"


def _export_module():
    path = BACKEND_DIR / "scripts" / "export_poi_semantic_dict.py"
    module_spec = importlib.util.spec_from_file_location("export_poi_semantic_dict", path)
    assert module_spec and module_spec.loader
    module = importlib.util.module_from_spec(module_spec)
    module_spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def published():
    repo = MapRepository.load(BACKEND_DIR / "maps_data", "https://localhost")
    latest = repo.latest("school-building-01")
    assert latest is not None
    return latest


@pytest.fixture(scope="module")
def overrides():
    return json.loads(OVERRIDES_PATH.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def dictionary_data(published, overrides):
    module = _export_module()
    data = module.build_dictionary(published.data, overrides)
    module.validate(data, published.data)
    return data


class TestExportSanity:
    def test_version_stamp(self, dictionary_data, published):
        assert dictionary_data["map_version"] == published.map_version
        assert dictionary_data["site_id"] == "school-building-01"
        assert dictionary_data["building_name"] == "初中学部1#教学楼"

    def test_counts_cover_all_named_rooms(self, dictionary_data, published):
        instances = sum(len(p["instances"]) for p in dictionary_data["pois"])
        assert instances == len(published.pkg.places) == 55
        assert len(dictionary_data["non_navigable_rooms"]) == 81
        rooms = sum(len(f.semantic.rooms) for f in published.pkg.floors.values())
        assert instances + len(dictionary_data["non_navigable_rooms"]) == rooms == 136

    def test_every_instance_points_into_map(self, dictionary_data, published):
        node_ids = {
            n.id for f in published.pkg.floors.values() for n in f.topology.nodes
        }
        place_ids = {p.placeId for p in published.pkg.places}
        for poi in dictionary_data["pois"]:
            for inst in poi["instances"]:
                assert inst["node_id"] in node_ids
                assert inst["place_id"] in place_ids

    def test_aliases_merged_from_overrides(self, dictionary_data):
        health = next(p for p in dictionary_data["pois"] if p["standard_name"] == "卫生室")
        assert "医务室" in health["aliases"]
        assert health["is_public_utility"] is True
        assert health["category"] == "medical"

    def test_same_name_aggregation(self, dictionary_data):
        leader = next(p for p in dictionary_data["pois"] if p["standard_name"] == "领导办公")
        assert len(leader["instances"]) == 3
        # 同名非公共房间必须能靠 zone_hint 区分，否则反问无法落地
        assert len({i["zone_hint"] for i in leader["instances"]}) == 3

        music = next(p for p in dictionary_data["pois"] if p["standard_name"] == "音乐教室")
        assert len({i["zone_hint"] for i in music["instances"]}) == len(music["instances"])

    def test_category_aliases(self, dictionary_data):
        assert dictionary_data["category_aliases"]["物理实验室"] == [
            "物理（声光电）",
            "物理（热）",
            "物理工作室",
        ]

    def test_rebuild_is_stable(self, published, overrides, dictionary_data):
        module = _export_module()
        rebuilt = module.build_dictionary(published.data, overrides)
        assert rebuilt["pois"] == dictionary_data["pois"]
        assert rebuilt["non_navigable_rooms"] == dictionary_data["non_navigable_rooms"]


class TestRuntimeContract:
    def test_dictionary_round_trip(self, dictionary_data, published):
        dictionary = PoiDictionary(dictionary_data)
        dictionary.ensure_version(published.map_version)
        assert len(dictionary.pois) == 34
        male = dictionary.get("男卫生间")
        assert male is not None and len(male.instances) == 6
        assert {i.floor_id for i in male.instances} == {"1", "2"}

    def test_stale_dictionary_rejected(self, dictionary_data):
        dictionary = PoiDictionary(dictionary_data)
        with pytest.raises(DictionaryStale):
            dictionary.ensure_version("2026-10-25.1")

    def test_exported_file_matches_package(self, published):
        """仓库内已导出的词典必须与当前最新发布包同版本（防止忘记重导出）。"""
        dictionary = PoiDictionary.from_path()
        dictionary.ensure_version(published.map_version)
        instance_total = sum(len(e.instances) for e in dictionary.pois.values())
        assert instance_total == len(published.pkg.places)
