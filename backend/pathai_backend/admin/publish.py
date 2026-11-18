# -*- coding: utf-8 -*-
"""候选版本构造、原子发布与 repository 刷新（map-admin 技术设计 §7）。

提交点语义：完整版本文件通过 os.replace 原子重命名成功即为发布完成；
此后 repository 刷新失败时不得对外提供过期快照（管理端重读版本状态确认）。
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import tempfile
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from pathai_backend.admin import drafts
from pathai_backend.errors import ApiError
from pathai_backend.map_package import MapPackage
from pathai_backend.map_package.validate import validate_map_package
from pathai_backend.map_repository import MapRepository

logger = logging.getLogger(__name__)

_REPO_ROOT = Path(__file__).resolve().parents[3]
_DEFAULT_MAP_DATA_DIR = Path(__file__).resolve().parents[2] / "maps_data"
_EDITOR_BUILD_SCRIPT = _REPO_ROOT / "tools" / "hazard-editor" / "build_data.py"


@dataclass(frozen=True)
class PublishResult:
    map_version: str
    published_at: str


def next_version_number(repo: MapRepository, site_id: str) -> str:
    """按 `<UTC日期>.<序号>` 分配当前场所未占用的版本号。"""
    date_prefix = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    existing = set()
    site_versions = repo.get_site_versions(site_id)
    for version in site_versions:
        if version.startswith(f"{date_prefix}."):
            suffix = version.split(".", 1)[1]
            if suffix.isdigit():
                existing.add(int(suffix))
    sequence = (max(existing) + 1) if existing else 1
    candidate = f"{date_prefix}.{sequence}"
    while candidate in site_versions:
        sequence += 1
        candidate = f"{date_prefix}.{sequence}"
    return candidate


def build_candidate(
    repo: MapRepository, site_id: str, base_version: str, patch: dict
) -> tuple[dict, str]:
    """由基准包 + patch 构造候选包，并完成 schema/语义校验与只读字段断言。"""
    published = repo.get(site_id, base_version)
    if published is None:
        raise ApiError(
            409, "DRAFT_BASE_MISSING", f"草稿基准版本不存在：{site_id}@{base_version}"
        )
    candidate = drafts.apply_patch(published.data, patch)
    issues: list[dict] = []
    try:
        pkg = MapPackage.model_validate(candidate)
    except Exception as exc:  # noqa: BLE001 - 映射为结构校验问题
        raise ApiError(
            422,
            "VALIDATION_FAILED",
            "候选地图包结构校验失败",
            details={"issues": [{"code": "SCHEMA_INVALID", "message": str(exc)}]},
        ) from exc
    for problem in validate_map_package(pkg):
        issues.append({"code": "SEMANTIC_INVALID", "message": problem})
    if drafts.strip_editable_fields(candidate) != drafts.strip_editable_fields(published.data):
        issues.append(
            {"code": "READONLY_FIELD_DRIFT", "message": "候选包中存在不可编辑字段变化"}
        )
    if issues:
        raise ApiError(422, "VALIDATION_FAILED", "草稿校验未通过", details={"issues": issues})
    return candidate, published.map_version


def publish_candidate(
    repo: MapRepository,
    map_data_dir: Path,
    site_id: str,
    candidate: dict,
) -> PublishResult:
    """分配版本号并原子写入新版本文件；不覆盖任何既有版本。"""
    version = next_version_number(repo, site_id)
    published_at = datetime.now(timezone.utc).replace(microsecond=0).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    candidate["mapVersion"] = version
    candidate["publishedAt"] = published_at
    # 写入前再以最终元数据校验一次（mapVersion/publishedAt 已变更）
    pkg = MapPackage.model_validate(candidate)
    problems = validate_map_package(pkg)
    if problems:
        raise ApiError(
            422,
            "VALIDATION_FAILED",
            "候选地图包校验失败",
            details={"issues": [{"code": "SEMANTIC_INVALID", "message": p} for p in problems]},
        )

    site_dir = map_data_dir / site_id
    site_dir.mkdir(parents=True, exist_ok=True)
    final_path = site_dir / f"{version}.json"
    if final_path.exists():
        raise ApiError(409, "VERSION_CONFLICT", f"版本号已被占用：{version}")
    payload = json.dumps(candidate, ensure_ascii=False, indent=2)
    fd, tmp_name = tempfile.mkstemp(prefix=".publish-", suffix=".part", dir=site_dir)
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, final_path)
    except OSError:
        tmp_path.unlink(missing_ok=True)
        raise
    return PublishResult(map_version=version, published_at=published_at)


def refresh_repository(app, map_data_dir: Path, public_base_url: str) -> None:
    """重建内存 repository；失败时置空以避免继续提供过期快照。"""
    try:
        app.state.map_repository = MapRepository.load(map_data_dir, public_base_url)
    except Exception:
        app.state.map_repository = None
        raise


def sync_hazard_editor_data(site_id: str, map_data_dir: Path) -> None:
    """发布/回滚成功后重建危险区编辑器的内联地图数据（失败只告警，不影响发布结果）。

    只在读取仓库默认地图目录时同步：测试或自定义 PATHAI_MAP_DATA_DIR 指向临时地图，
    重建会把临时数据写进开发用的 `tools/hazard-editor/data/map-data.js`。
    """
    try:
        if Path(map_data_dir).resolve() != _DEFAULT_MAP_DATA_DIR.resolve():
            return
        if not _EDITOR_BUILD_SCRIPT.is_file():
            return
        proc = subprocess.run(
            [sys.executable, str(_EDITOR_BUILD_SCRIPT), "--site", site_id],
            cwd=str(_REPO_ROOT),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env={**os.environ, "PYTHONIOENCODING": "utf-8"},
            timeout=120,
        )
        if proc.returncode != 0:
            logger.warning(
                "危险区编辑器数据重建失败（%s）：%s",
                site_id,
                (proc.stderr or proc.stdout).strip()[-500:],
            )
        else:
            logger.info("危险区编辑器数据已同步：%s", site_id)
    except Exception:  # noqa: BLE001 - 派生数据失败不得回滚已完成的发布
        logger.warning("危险区编辑器数据重建失败：%s", site_id, exc_info=True)
