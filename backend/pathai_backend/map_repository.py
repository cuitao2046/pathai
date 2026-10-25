# -*- coding: utf-8 -*-
"""已发布地图包仓库：启动时从磁盘加载全部发布物到内存（REQ-MAP）。

- 目录约定：<map_data_dir>/<siteId>/<mapVersion>.json；
- 目录存在即场所存在（区分 404 SITE_NOT_FOUND 与 404 MAP_NOT_PUBLISHED）；
- 发布物校验失败时启动失败（不允许带病上线）；
- 加载后不可变：版本内容与 sha256 在进程生命周期内恒定，支撑 ETag/304。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from pathai_backend.map_package import MapPackage, load_map_package


@dataclass(frozen=True)
class PublishedMap:
    site_id: str
    map_version: str
    pkg: MapPackage
    raw: bytes
    sha256: str
    data_url: str

    @property
    def etag(self) -> str:
        return f'"{self.sha256}"'


@dataclass(frozen=True)
class MapRepository:
    known_sites: frozenset[str]
    _maps: dict[str, dict[str, PublishedMap]] = field(repr=False)

    @classmethod
    def load(cls, map_data_dir: Path, public_base_url: str) -> "MapRepository":
        known: set[str] = set()
        maps: dict[str, dict[str, PublishedMap]] = {}
        if map_data_dir.is_dir():
            for site_dir in sorted(p for p in map_data_dir.iterdir() if p.is_dir()):
                site_id = site_dir.name
                known.add(site_id)
                for file in sorted(site_dir.glob("*.json")):
                    pkg, digest = load_map_package(file)
                    raw = file.read_bytes()
                    version = pkg.mapVersion
                    data_url = f"{public_base_url.rstrip('/')}/api/v1/sites/{site_id}/map/data"
                    published = PublishedMap(
                        site_id=site_id,
                        map_version=version,
                        pkg=pkg,
                        raw=raw,
                        sha256=digest,
                        data_url=data_url,
                    )
                    existing = maps.setdefault(site_id, {})
                    if version in existing:
                        raise ValueError(f"地图包版本重复：{site_id}/{version}")
                    existing[version] = published
        return cls(known_sites=frozenset(known), _maps=maps)

    def is_known_site(self, site_id: str) -> bool:
        return site_id in self.known_sites

    def get(self, site_id: str, version: str) -> PublishedMap | None:
        return self._maps.get(site_id, {}).get(version)

    def latest(self, site_id: str) -> PublishedMap | None:
        versions = self._maps.get(site_id)
        if not versions:
            return None
        return max(
            versions.values(),
            key=lambda m: (m.pkg.publishedAt, m.map_version),
        )
