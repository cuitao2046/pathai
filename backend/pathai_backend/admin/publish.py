# -*- coding: utf-8 -*-
"""候选版本构造、原子发布与 repository 刷新（map-admin 技术设计 §7）。

提交点语义：完整版本文件通过 os.replace 原子重命名成功即为发布完成；
此后 repository 刷新失败时不得对外提供过期快照（管理端重读版本状态确认）。
"""
from __future__ import annotations

import hashlib
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


def _geometry_fingerprint(package: dict) -> str:
    """楼层拓扑/几何 + 跨层边的稳定指纹，用于判断危险层 edgeIds 是否仍有效。"""
    floors = package.get("floors") or {}
    signature = {
        "floors": {
            floor_id: {
                "topology": floor.get("topology"),
                "geometry": floor.get("geometry"),
            }
            for floor_id, floor in floors.items()
        },
        "crossFloorEdges": package.get("crossFloorEdges") or [],
    }
    encoded = json.dumps(signature, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def sync_hazard_layer_version(
    site_id: str,
    map_data_dir: Path,
    hazard_data_dir: Path,
    map_version: str,
) -> dict:
    """发布/回滚后把危险层 mapVersion 同步到新地图版本。

    危险层带 mapVersion，导航服务遇到不一致会整层忽略。仅当能证明新旧地图包
    楼层拓扑/几何完全一致时自动改写元数据；否则保持原样并以 ERROR 告警，提示
    复核 edgeIds 后重新导出，避免危险层被版本校验静默丢弃。
    """
    hazard_file = Path(hazard_data_dir) / f"{site_id}.json"
    if not hazard_file.is_file():
        return {"status": "missing", "mapVersion": map_version}
    try:
        payload = json.loads(hazard_file.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        logger.error("危险层文件无法解析，未能同步版本：%s", hazard_file, exc_info=True)
        return {"status": "invalid", "mapVersion": map_version}

    previous_version = payload.get("mapVersion")
    if previous_version == map_version:
        return {"status": "up_to_date", "mapVersion": map_version}

    site_dir = Path(map_data_dir) / site_id
    new_package_path = site_dir / f"{map_version}.json"
    old_package_path = (
        site_dir / f"{previous_version}.json" if isinstance(previous_version, str) else None
    )
    if not new_package_path.is_file() or old_package_path is None or not old_package_path.is_file():
        logger.error(
            "危险层版本未同步：%s 标记 %s，新地图 %s；缺少可比对的地图包，请人工复核后重新导出",
            site_id,
            previous_version,
            map_version,
        )
        return {
            "status": "skipped_unknown_base",
            "fromVersion": previous_version,
            "mapVersion": map_version,
        }

    try:
        new_fingerprint = _geometry_fingerprint(
            json.loads(new_package_path.read_text(encoding="utf-8"))
        )
        old_fingerprint = _geometry_fingerprint(
            json.loads(old_package_path.read_text(encoding="utf-8"))
        )
    except Exception:  # noqa: BLE001
        logger.error("危险层版本同步失败：地图包无法解析 %s", site_id, exc_info=True)
        return {"status": "failed", "mapVersion": map_version}

    if new_fingerprint != old_fingerprint:
        logger.error(
            "危险层未随地图升级：%s %s → %s 楼层拓扑/几何有变化，"
            "请复核 edgeIds 后重新导出危险层（当前危险层将被版本校验忽略）",
            site_id,
            previous_version,
            map_version,
        )
        return {
            "status": "topology_changed",
            "fromVersion": previous_version,
            "mapVersion": map_version,
        }

    payload["mapVersion"] = map_version
    notes = payload.get("notes")
    if isinstance(notes, str) and previous_version:
        payload["notes"] = notes.replace(previous_version, map_version)
    try:
        fd, tmp_name = tempfile.mkstemp(
            prefix=".hazard-", suffix=".part", dir=hazard_file.parent
        )
        tmp_path = Path(tmp_name)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp_path, hazard_file)
        except OSError:
            tmp_path.unlink(missing_ok=True)
            raise
    except Exception:  # noqa: BLE001 - 同步失败不得回滚已完成的发布
        logger.error("危险层版本同步写入失败：%s", site_id, exc_info=True)
        return {"status": "failed", "mapVersion": map_version}

    logger.info(
        "危险层版本已同步：%s %s → %s（楼层拓扑一致）",
        site_id,
        previous_version,
        map_version,
    )
    return {
        "status": "updated",
        "fromVersion": previous_version,
        "mapVersion": map_version,
    }
