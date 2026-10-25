# -*- coding: utf-8 -*-
"""不可变地图包：schema、语义校验与加载（REQ-MAP）。"""
from __future__ import annotations

from pathai_backend.map_package.loader import (
    load_map_package,
    parse_map_package_bytes,
    sha256_of_bytes,
)
from pathai_backend.map_package.schema import (
    BeaconEntry,
    CrossFloorEdge,
    FloorData,
    MapCoordinateSystem,
    MapPackage,
    PlaceEntry,
    TopologyEdge,
    TopologyNode,
    WallFeature,
)
from pathai_backend.map_package.validate import validate_map_package

__all__ = [
    "MapPackage",
    "MapCoordinateSystem",
    "FloorData",
    "TopologyNode",
    "TopologyEdge",
    "CrossFloorEdge",
    "WallFeature",
    "PlaceEntry",
    "BeaconEntry",
    "validate_map_package",
    "load_map_package",
    "parse_map_package_bytes",
    "sha256_of_bytes",
]
