# -*- coding: utf-8 -*-
"""地图包加载：JSON 读取、schema 校验、语义校验与 SHA-256 摘要（REQ-MAP）。"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from pathai_backend.map_package.schema import MapPackage
from pathai_backend.map_package.validate import validate_map_package


def sha256_of_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def load_map_package(path: str | Path) -> tuple[MapPackage, str]:
    """从文件加载地图包，返回（地图包, sha256 摘要）。

    schema 或语义校验失败时抛出异常，发布物不允许带病上线。
    """
    raw = Path(path).read_bytes()
    return parse_map_package_bytes(raw)


def parse_map_package_bytes(raw: bytes) -> tuple[MapPackage, str]:
    digest = sha256_of_bytes(raw)
    data = json.loads(raw.decode("utf-8"))
    pkg = MapPackage.model_validate(data)
    problems = validate_map_package(pkg)
    if problems:
        raise ValueError("地图包校验失败：" + "；".join(problems))
    return pkg, digest
