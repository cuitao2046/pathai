# -*- coding: utf-8 -*-
"""回归（spec v0.2.6 / 决策 8）：最新发布地图包的公共空间拓扑边不得穿墙。

公共空间边 = TI↔TI、TD↔TI、TEN↔TD/TI；房间质心↔门与设施↔门为语义边不计。
判定逻辑复用转换器的发布前校验，避免规则两处漂移。
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parent.parent
MAPS_DIR = BACKEND / "maps_data" / "school-building-01"
CONVERTER = BACKEND / "scripts" / "convert_map_v9.py"


def _load_converter():
    spec = importlib.util.spec_from_file_location("convert_map_v9_test", CONVERTER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _latest_package() -> tuple[str, dict]:
    candidates = []
    for path in MAPS_DIR.glob("*.json"):
        data = json.loads(path.read_text(encoding="utf-8"))
        candidates.append((data.get("publishedAt", ""), data, path.name))
    assert candidates, "maps_data 下没有地图包"
    published_at, data, name = max(candidates, key=lambda x: x[0])
    return name, data


def test_latest_published_map_has_no_public_wall_crossing():
    """最新发布包（按 publishedAt）公共空间边穿墙数必须为 0。"""
    _, package = _latest_package()
    converter = _load_converter()
    # 断言在 verify_no_public_wall_crossing 内部：违例即 AssertionError 列出明细
    converter.verify_no_public_wall_crossing(package)


def test_specific_former_violations_are_gone():
    """曾经的穿墙边不再作为公共空间边存在：跨墙 TI-TI（F1-TE-0036）与 40m 室外入口边。"""
    _, package = _latest_package()
    edges = {
        edge["id"]: edge
        for floor in package["floors"].values()
        for edge in (floor.get("topology") or {}).get("edges") or []
    }
    assert "F1-TE-0036" not in edges
    assert "F1-TE-0412" not in edges
