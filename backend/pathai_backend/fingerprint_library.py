# -*- coding: utf-8 -*-
"""指纹库：导出入库、版本化、按点聚合与在线匹配。

设计要点（本轮新增：版本保留 + 删除语义）：
- 入库不做鉴权，但沿用私网限制与限流（与校准控制台一致）；
- 每次有效入库生成不可变版本，原始记录与聚合向量都按版本保存，便于追溯/回滚；
- 幂等：同一内容哈希直接复用旧版本，不写库、不切 active；墓碑重复应用不产生新版本；
- 合并：以 (fpId, collectedAt) 为键做增量 upsert，未出现在本批次的点保留；
  同键内容变化以本次为准并计入 updated（旧字段 conflict 保留同值）；
- 删除：只接受显式墓碑 deletions[]（fpId + collectedAt + 内容哈希），
  绝不把「本批未出现」推断为删除（分批采集不会误删整层）；
- 整层重采：必须显式 mode=replaceFloor + replaceFloors，作用域仅限所选楼层；
- 保留策略：只保留最近 10 个版本的明细，最老的非 active 版本在同一事务内裁剪明细、
  保留摘要行（审计可追溯）；active 版本永不裁剪；回滚到已裁剪版本报 VERSION_EXPIRED。
- 查询：对 active 版本的聚合向量做最近邻，阈值不满足时返回原因，由调用方回退三点定位。
"""
from __future__ import annotations

import hashlib
import json
import secrets
import statistics
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from pathai_backend.models import (
    FingerprintLibraryPoint,
    FingerprintLibraryPointer,
    FingerprintLibraryRecord,
    FingerprintLibraryVersion,
)

FP_EXPORT_TYPE = "ble_fingerprint_collection"
#: 当前导出 schema；同时兼容 1.1.0（无 deletions/mode 字段）。
FP_EXPORT_SCHEMA = "1.2.0"
FP_EXPORT_SCHEMA_ACCEPTED = ("1.1.0", "1.2.0")
DEFAULT_SITE_ID = "school-building-01"

#: 单条记录信标上限（导出器每 2s 窗口最多解析出的信标数远小于此值）。
MAX_BEACONS_PER_RECORD = 64
#: 单次入库记录上限（按 2109 点 × 5 次留足余量）。
MAX_RECORDS_PER_INGEST = 20000
#: 单次入库墓碑上限（与记录同量级）。
MAX_DELETIONS_PER_INGEST = 20000

#: 版本保留窗口：最近 10 个版本的明细保留；更老的非 active 版本只留摘要行。
RETAIN_VERSION_COUNT = 10
#: 单版本明细的实测估算（记录 + 聚合向量 + 索引），用于对外说明存储占用。
ESTIMATED_BYTES_PER_VERSION = 5_200_000

#: 入库模式。
MODE_MERGE = "merge"
MODE_REPLACE_FLOOR = "replaceFloor"
INGEST_MODES = (MODE_MERGE, MODE_REPLACE_FLOOR)

#: 匹配门槛：重叠、RMSE、置信度、与次优的差距。
MATCH_MIN_OVERLAP = 3
MATCH_MAX_RMSE_DB = 8.0
MATCH_MIN_CONFIDENCE = 0.55
MATCH_MIN_MARGIN_DB = 3.0
MATCH_CONFIDENCE_RMSE_DB = 20.0


class FingerprintLibraryError(Exception):
    def __init__(self, status_code: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message


@dataclass(frozen=True)
class FingerprintMatch:
    library_version: str
    fp_id: str
    floor_id: str
    x: float
    y: float
    rmse_db: float
    confidence: float
    overlap: int
    margin_db: float


def beacon_key(beacon: dict[str, Any]) -> str:
    """记录/地图信标统一键：major:minor（uuid 全场一致，不重复占用体积）。"""
    return f"{int(beacon['major'])}:{int(beacon['minor'])}"


def record_sha(record: dict[str, Any]) -> str:
    """记录内容哈希（墓碑/对账清单共用；客户端 tombstones.ts 同口径）。

    口径：fpId|collectedAt|floor|x(2位),y(2位)|major:minor:rssi(1位) 按信标键排序。
    只哈希会进入向量语义的字段，避免 relCoordinates 等冗余字段造成假差异。
    """
    beacons = sorted(
        record["beacons"], key=lambda b: (int(b["major"]), int(b["minor"]))
    )
    beacon_text = ",".join(
        f"{int(b['major'])}:{int(b['minor'])}:{float(b['rssi']):.1f}" for b in beacons
    )
    x, y = record["coordinates"]
    text = "|".join(
        [
            str(record["fpId"]),
            str(record["collectedAt"]),
            str(int(record["floor"])),
            f"{float(x):.2f},{float(y):.2f}",
            beacon_text,
        ]
    )
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _parse_content(content: str) -> dict[str, Any]:
    if not isinstance(content, str) or content.strip() == "":
        raise FingerprintLibraryError(400, "EMPTY_CONTENT", "入库内容为空")
    try:
        payload = json.loads(content)
    except json.JSONDecodeError as exc:
        raise FingerprintLibraryError(400, "INVALID_JSON", f"JSON 解析失败：{exc.msg}") from exc
    if not isinstance(payload, dict):
        raise FingerprintLibraryError(400, "INVALID_PAYLOAD", "入库内容必须是 JSON 对象")
    if payload.get("type") != FP_EXPORT_TYPE:
        raise FingerprintLibraryError(
            400, "INVALID_TYPE", f"样本类型必须是 {FP_EXPORT_TYPE}"
        )
    if payload.get("schemaVersion") not in FP_EXPORT_SCHEMA_ACCEPTED:
        raise FingerprintLibraryError(
            400,
            "INVALID_SCHEMA",
            "schemaVersion 必须是 " + " / ".join(FP_EXPORT_SCHEMA_ACCEPTED),
        )
    records = payload.get("records")
    if not isinstance(records, list):
        raise FingerprintLibraryError(400, "EMPTY_RECORDS", "records 必须是数组")
    if len(records) > MAX_RECORDS_PER_INGEST:
        raise FingerprintLibraryError(
            413, "TOO_MANY_RECORDS", f"记录数超过上限 {MAX_RECORDS_PER_INGEST}"
        )
    deletions = payload.get("deletions", [])
    if deletions is None:
        deletions = []
    if not isinstance(deletions, list):
        raise FingerprintLibraryError(400, "INVALID_DELETIONS", "deletions 必须是数组")
    if len(deletions) > MAX_DELETIONS_PER_INGEST:
        raise FingerprintLibraryError(
            413, "TOO_MANY_DELETIONS", f"墓碑数超过上限 {MAX_DELETIONS_PER_INGEST}"
        )
    mode = payload.get("mode", MODE_MERGE)
    if not isinstance(mode, str) or mode not in INGEST_MODES:
        raise FingerprintLibraryError(
            400, "INVALID_MODE", "mode 必须是 merge / replaceFloor"
        )
    replace_floors = payload.get("replaceFloors", [])
    if replace_floors is None:
        replace_floors = []
    if not isinstance(replace_floors, list) or any(
        not isinstance(f, (str, int)) for f in replace_floors
    ):
        raise FingerprintLibraryError(
            400, "INVALID_REPLACE_FLOORS", "replaceFloors 必须是楼层数组"
        )
    replace_floors = [str(int(f)) for f in replace_floors]
    if mode == MODE_REPLACE_FLOOR and not replace_floors:
        raise FingerprintLibraryError(
            400, "MISSING_REPLACE_FLOORS", "replaceFloor 模式必须显式指定楼层"
        )
    if mode == MODE_REPLACE_FLOOR and deletions:
        raise FingerprintLibraryError(
            400,
            "INVALID_MODE_COMBINATION",
            "replaceFloor 模式不接受 deletions（整层作用域已含删除语义）",
        )
    if not records and not deletions and not replace_floors:
        raise FingerprintLibraryError(
            400, "EMPTY_RECORDS", "records 与 deletions 不能同时为空"
        )
    return {
        "payload": payload,
        "records": records,
        "deletions": deletions,
        "mode": mode,
        "replace_floors": sorted(set(replace_floors)),
    }


def _normalize_record(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise FingerprintLibraryError(400, "INVALID_RECORD", "record 必须是对象")
    fp_id = raw.get("fpId")
    if not isinstance(fp_id, str) or not fp_id or len(fp_id) > 32:
        raise FingerprintLibraryError(400, "INVALID_FP_ID", "fpId 缺失或过长")
    collected_at = raw.get("collectedAt")
    if not isinstance(collected_at, str) or not collected_at:
        raise FingerprintLibraryError(400, "INVALID_TIME", f"{fp_id} 缺少 collectedAt")
    coordinates = raw.get("coordinates")
    if (
        not isinstance(coordinates, list)
        or len(coordinates) != 2
        or not all(isinstance(v, (int, float)) for v in coordinates)
    ):
        raise FingerprintLibraryError(400, "INVALID_COORDS", f"{fp_id} coordinates 非法")
    floor = raw.get("floor")
    if not isinstance(floor, (int, str)) and not isinstance(floor, float):
        raise FingerprintLibraryError(400, "INVALID_FLOOR", f"{fp_id} floor 非法")
    beacons = raw.get("beacons")
    if not isinstance(beacons, list) or not beacons:
        raise FingerprintLibraryError(400, "EMPTY_BEACONS", f"{fp_id} 没有信标观测")
    if len(beacons) > MAX_BEACONS_PER_RECORD:
        raise FingerprintLibraryError(400, "TOO_MANY_BEACONS", f"{fp_id} 信标数超限")
    clean: list[dict[str, Any]] = []
    for b in beacons:
        if not isinstance(b, dict):
            raise FingerprintLibraryError(400, "INVALID_BEACON", f"{fp_id} 信标项非法")
        major, minor, rssi = b.get("major"), b.get("minor"), b.get("rssi")
        if not isinstance(major, int) or not isinstance(minor, int):
            raise FingerprintLibraryError(400, "INVALID_BEACON_ID", f"{fp_id} major/minor 非法")
        if not isinstance(rssi, (int, float)) or not (-120 <= rssi <= -10):
            raise FingerprintLibraryError(400, "INVALID_RSSI", f"{fp_id} RSSI 越界")
        clean.append(
            {
                "major": major,
                "minor": minor,
                "rssi": float(rssi),
                # 保留原始字段供审计；查询只用 major/minor/rssi
                **{
                    k: v
                    for k, v in b.items()
                    if k in ("uuid", "txPower", "accuracy")
                },
            }
        )
    normalized: dict[str, Any] = {
        "fpId": fp_id,
        "floor": str(int(floor)),
        "coordinates": [float(coordinates[0]), float(coordinates[1])],
        "collectedAt": collected_at,
        "beacons": clean,
    }
    # 采集时的网格/参数版本（可选）：用于入库版本追溯
    for field in ("gridVersion", "paramsVersion"):
        value = raw.get(field)
        if isinstance(value, str) and value:
            normalized[field] = value[:40]
    return normalized


def _normalize_tombstone(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise FingerprintLibraryError(400, "INVALID_TOMBSTONE", "deletion 必须是对象")
    fp_id = raw.get("fpId")
    collected_at = raw.get("collectedAt")
    if not isinstance(fp_id, str) or not fp_id or len(fp_id) > 32:
        raise FingerprintLibraryError(400, "INVALID_TOMBSTONE", "墓碑 fpId 缺失或过长")
    if not isinstance(collected_at, str) or not collected_at:
        raise FingerprintLibraryError(400, "INVALID_TOMBSTONE", f"{fp_id} 墓碑缺少 collectedAt")
    content_sha = raw.get("contentSha256")
    if content_sha is not None:
        if not isinstance(content_sha, str) or len(content_sha) != 64:
            raise FingerprintLibraryError(
                400, "INVALID_TOMBSTONE", f"{fp_id} 墓碑 contentSha256 非法"
            )
        content_sha = content_sha.lower()
    floor = raw.get("floor")
    return {
        "fpId": fp_id,
        "collectedAt": collected_at,
        "contentSha256": content_sha,
        "floor": str(int(floor)) if isinstance(floor, (int, str, float)) else None,
    }


def _canonical_sha(
    records: list[dict[str, Any]],
    deletions: list[dict[str, Any]],
    mode: str,
    replace_floors: list[str],
) -> str:
    """幂等键：完整覆盖记录、墓碑、模式与楼层作用域。"""
    ordered_records = sorted(records, key=lambda r: (r["fpId"], r["collectedAt"]))
    ordered_deletions = sorted(
        deletions, key=lambda d: (d["fpId"], d["collectedAt"])
    )
    canonical = json.dumps(
        {
            "records": ordered_records,
            "deletions": ordered_deletions,
            "mode": mode,
            "replaceFloors": sorted(replace_floors),
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _version_id() -> str:
    # 微秒级时间前缀：字符串字典序 = 创建顺序，保留裁剪的“最老/最新”判定稳定。
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S%f")
    return f"fpl-{stamp}-{secrets.token_hex(3)}"


#: 进程内 active 指针缓存（site_id → version_id | None）：查询热点路径省掉
#: pointer 主键查询；单进程部署，ingest/rollback 更新指针时同步刷新。
_ACTIVE_POINTER_CACHE: dict[str, str | None] = {}

#: 缓存未命中哨兵（区别于「已知无 active」的 None）。
_MISS = object()


def reset_active_pointer_cache() -> None:
    """清空 active 指针缓存（测试换库/多进程部署时调用）。"""
    _ACTIVE_POINTER_CACHE.clear()


def _cache_pointer(site_id: str, version_id: str | None) -> None:
    _ACTIVE_POINTER_CACHE[site_id] = version_id


def active_version(session: Session, site_id: str) -> FingerprintLibraryVersion | None:
    version_id = _ACTIVE_POINTER_CACHE.get(site_id, _MISS)
    if version_id is _MISS:
        pointer = session.get(FingerprintLibraryPointer, site_id)
        version_id = pointer.version if pointer is not None else None
        _cache_pointer(site_id, version_id)
    if version_id is None:
        return None
    return session.get(FingerprintLibraryVersion, version_id)


def _epoch_iso(epoch: int | None) -> str | None:
    if epoch is None:
        return None
    return datetime.fromtimestamp(epoch, tz=timezone.utc).isoformat()


def _version_summary(
    row: FingerprintLibraryVersion,
    *,
    duplicated: bool = False,
    active: bool = True,
    no_changes: bool = False,
) -> dict[str, Any]:
    return {
        "version": row.version,
        "siteId": row.site_id,
        "recordCount": row.record_count,
        "fpCount": row.fp_count,
        "insertedCount": row.inserted_count,
        "updatedCount": row.updated_count,
        "conflictCount": row.conflict_count,
        "deletedCount": row.deleted_count,
        "retainedCount": row.retained_count,
        "previousCount": row.previous_count,
        "mode": row.ingest_mode,
        "replaceFloors": json.loads(row.replace_floors_json)
        if row.replace_floors_json
        else [],
        "operator": row.operator,
        "device": json.loads(row.device_json) if row.device_json else None,
        "appVersion": row.app_version,
        "mapVersion": row.map_version,
        "gridVersion": row.grid_version,
        "paramsVersion": row.params_version,
        "sourceFileName": row.source_file_name,
        "createdAt": datetime.fromtimestamp(
            row.created_at_epoch, tz=timezone.utc
        ).isoformat(),
        "active": active,
        "duplicated": duplicated,
        "noChanges": no_changes,
        "detailsPruned": bool(row.details_pruned),
        "prunedAt": _epoch_iso(row.pruned_at_epoch),
    }


def _existing_records(
    session: Session, version: FingerprintLibraryVersion | None
) -> dict[tuple[str, str], dict[str, Any]]:
    if version is None:
        return {}
    rows = session.scalars(
        select(FingerprintLibraryRecord).where(
            FingerprintLibraryRecord.version == version.version
        )
    ).all()
    return {
        (row.fp_id, row.collected_at): json.loads(row.record_json)
        for row in rows
    }


def _aggregate_points(records: dict[tuple[str, str], dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for record in records.values():
        grouped.setdefault(record["fpId"], []).append(record)
    points: list[dict[str, Any]] = []
    for fp_id, items in grouped.items():
        buckets: dict[str, list[float]] = {}
        floor_id = items[0]["floor"]
        for item in items:
            for beacon in item["beacons"]:
                buckets.setdefault(beacon_key(beacon), []).append(float(beacon["rssi"]))
        vector = {key: round(float(statistics.median(vals)), 1) for key, vals in buckets.items()}
        x, y = items[0]["coordinates"]
        points.append(
            {
                "fpId": fp_id,
                "floorId": floor_id,
                "x": float(x),
                "y": float(y),
                "recordCount": len(items),
                "beaconCount": len(vector),
                "vector": vector,
            }
        )
    return points


def _dominant_version(
    records: list[dict[str, Any]], field: str
) -> str | None:
    """从记录里取出现次数最多的版本号（导出载荷无顶层版本字段时兜底）。"""
    counts: dict[str, int] = {}
    for record in records:
        value = record.get(field)
        if isinstance(value, str) and value:
            counts[value] = counts.get(value, 0) + 1
    if not counts:
        return None
    return max(counts.items(), key=lambda item: item[1])[0][:40]


def _prune_versions(
    session: Session, site_id: str, active_version_id: str, now: int
) -> list[str]:
    """保留最近 RETAIN_VERSION_COUNT 个版本的明细；更老的只留摘要行。

    active 版本永不裁剪（即使它已不在最近窗口内，例如回滚到旧版本后）。
    返回本次被裁剪明细的版本号列表。
    """
    rows = session.scalars(
        select(FingerprintLibraryVersion)
        .where(FingerprintLibraryVersion.site_id == site_id)
        .order_by(
            FingerprintLibraryVersion.created_at_epoch.desc(),
            FingerprintLibraryVersion.version.desc(),
        )
    ).all()
    pruned: list[str] = []
    non_active_seen = 0
    for row in rows:
        if row.version == active_version_id:
            continue
        non_active_seen += 1
        # 窗口内保留：active 占 1 个名额，其余按最新顺序补足。
        if non_active_seen <= RETAIN_VERSION_COUNT - 1:
            continue
        if row.details_pruned:
            continue
        session.execute(
            delete(FingerprintLibraryRecord).where(
                FingerprintLibraryRecord.version == row.version
            )
        )
        session.execute(
            delete(FingerprintLibraryPoint).where(
                FingerprintLibraryPoint.version == row.version
            )
        )
        row.details_pruned = True
        row.pruned_at_epoch = now
        pruned.append(row.version)
    return pruned


def ingest_export(
    session: Session,
    *,
    site_id: str = DEFAULT_SITE_ID,
    file_name: str,
    content: str,
    operator: str | None = None,
) -> dict[str, Any]:
    """入库一份导出 JSON；返回版本摘要。调用方负责提交事务。"""
    parsed = _parse_content(content)
    payload = parsed["payload"]
    records = [_normalize_record(r) for r in parsed["records"]]
    deletions = [_normalize_tombstone(d) for d in parsed["deletions"]]
    mode = parsed["mode"]
    replace_floors = parsed["replace_floors"]
    sha = _canonical_sha(records, deletions, mode, replace_floors)

    duplicate = session.scalar(
        select(FingerprintLibraryVersion)
        .where(
            FingerprintLibraryVersion.content_sha256 == sha,
            # 幂等键按场所隔离：同内容入库到不同场所各自成版本
            FingerprintLibraryVersion.site_id == site_id,
        )
        .order_by(FingerprintLibraryVersion.created_at_epoch.desc())
    )
    if duplicate is not None:
        if duplicate.details_pruned:
            # 同内容但旧明细已被裁剪：摘要行不再能作为幂等依据（不可查询/回滚），
            # 删除旧摘要后按新版本重建（内容等价，避免唯一约束冲突）。
            session.delete(duplicate)
            session.flush()
        else:
            pointer = session.get(FingerprintLibraryPointer, site_id)
            return _version_summary(
                duplicate,
                duplicated=True,
                active=pointer is not None and pointer.version == duplicate.version,
            )

    current = active_version(session, site_id)
    previous = _existing_records(session, current)
    previous_count = len(previous)
    merged = dict(previous)
    incoming = {(r["fpId"], r["collectedAt"]): r for r in records}

    deleted = 0
    # 1) 整层重采：仅清理所选楼层中「本批未再次提供」的记录。
    if mode == MODE_REPLACE_FLOOR:
        floors = set(replace_floors)
        for key, record in previous.items():
            if record["floor"] in floors and key not in incoming:
                merged.pop(key, None)
                deleted += 1

    # 2) 墓碑：显式删除，重复应用幂等（键已不存在时跳过）。
    tombstone_keys = {(d["fpId"], d["collectedAt"]) for d in deletions}
    overlap = tombstone_keys & set(incoming)
    if overlap:
        raise FingerprintLibraryError(
            400,
            "DELETION_CONFLICT",
            f"墓碑与本次记录指向同一键：{sorted(overlap)[0][0]}",
        )
    for tombstone in deletions:
        key = (tombstone["fpId"], tombstone["collectedAt"])
        old = merged.get(key)
        if old is None:
            continue
        expected = tombstone["contentSha256"]
        if expected is not None and record_sha(old) != expected:
            raise FingerprintLibraryError(
                409,
                "TOMBSTONE_MISMATCH",
                f"墓碑内容哈希与库中记录不一致：{key[0]}，请重新对账后再入库",
            )
        merged.pop(key, None)
        deleted += 1

    # 3) 合并入库：未出现的记录永远保留（分批采集不误删）。
    inserted = 0
    updated = 0
    for key, record in incoming.items():
        old = merged.get(key)
        if old is None:
            inserted += 1
        elif json.dumps(old, sort_keys=True) != json.dumps(record, sort_keys=True):
            updated += 1
        merged[key] = record
    retained = len(merged) - inserted - updated

    if inserted == 0 and updated == 0 and deleted == 0:
        # 墓碑重放 / 无变化入库：不产生空版本，直接返回当前 active（幂等）。
        if current is not None:
            summary = _version_summary(current, duplicated=True, no_changes=True)
            summary["prunedVersions"] = []
            return summary
        raise FingerprintLibraryError(
            409, "NO_ACTIVE_LIBRARY", "指纹库为空，本次入库没有任何可应用的变更"
        )

    version_id = _version_id()
    now = int(datetime.now(timezone.utc).timestamp())
    points = _aggregate_points(merged)
    row = FingerprintLibraryVersion(
        version=version_id,
        site_id=site_id,
        content_sha256=sha,
        source_file_name=file_name[:160],
        dataset=str(payload.get("dataset") or "routes")[:32],
        record_count=len(merged),
        fp_count=len(points),
        inserted_count=inserted,
        updated_count=updated,
        conflict_count=updated,
        deleted_count=deleted,
        retained_count=retained,
        previous_count=previous_count,
        ingest_mode=mode,
        replace_floors_json=json.dumps(replace_floors) if replace_floors else None,
        details_pruned=False,
        operator=operator or payload.get("operator"),
        device_json=json.dumps(payload.get("device"), ensure_ascii=False)
        if payload.get("device") is not None
        else None,
        app_version=str(payload.get("appVersion") or "")[:32] or None,
        map_version=str(payload.get("mapVersion") or "")[:32] or None,
        grid_version=str(payload["gridVersion"])[:40]
        if payload.get("gridVersion")
        else _dominant_version(records, "gridVersion"),
        params_version=str(payload["paramsVersion"])[:40]
        if payload.get("paramsVersion")
        else _dominant_version(records, "paramsVersion"),
        created_at_epoch=now,
    )
    session.add(row)
    for (fp_id, collected_at), record in merged.items():
        session.add(
            FingerprintLibraryRecord(
                version=version_id,
                fp_id=fp_id,
                collected_at=collected_at,
                floor_id=record["floor"],
                record_json=json.dumps(record, ensure_ascii=False, separators=(",", ":")),
            )
        )
    for point in points:
        session.add(
            FingerprintLibraryPoint(
                version=version_id,
                fp_id=point["fpId"],
                floor_id=point["floorId"],
                x=point["x"],
                y=point["y"],
                record_count=point["recordCount"],
                beacon_count=point["beaconCount"],
                vector_json=json.dumps(point["vector"], separators=(",", ":")),
            )
        )
    pointer = session.get(FingerprintLibraryPointer, site_id)
    if pointer is None:
        session.add(
            FingerprintLibraryPointer(
                site_id=site_id, version=version_id, updated_at_epoch=now
            )
        )
    else:
        pointer.version = version_id
        pointer.updated_at_epoch = now
    session.flush()
    _cache_pointer(site_id, version_id)
    pruned = _prune_versions(session, site_id, version_id, now)
    summary = _version_summary(row)
    summary["prunedVersions"] = pruned
    return summary


def rollback(
    session: Session,
    *,
    site_id: str = DEFAULT_SITE_ID,
    version: str,
) -> dict[str, Any]:
    """回滚：把 active 指针切回保留窗口内的既有版本（不复制明细）。"""
    row = session.get(FingerprintLibraryVersion, version)
    if row is None or row.site_id != site_id:
        raise FingerprintLibraryError(
            404, "VERSION_NOT_FOUND", f"指纹库版本不存在：{version}"
        )
    if row.details_pruned:
        raise FingerprintLibraryError(
            409,
            "VERSION_EXPIRED",
            f"版本已过期：{version} 的明细已被保留策略裁剪，无法回滚",
        )
    now = int(datetime.now(timezone.utc).timestamp())
    pointer = session.get(FingerprintLibraryPointer, site_id)
    if pointer is None:
        session.add(
            FingerprintLibraryPointer(
                site_id=site_id, version=version, updated_at_epoch=now
            )
        )
    else:
        pointer.version = version
        pointer.updated_at_epoch = now
    session.flush()
    _cache_pointer(site_id, version)
    summary = _version_summary(row, active=True)
    summary["rolledBack"] = True
    return summary


def list_versions(
    session: Session, site_id: str = DEFAULT_SITE_ID, limit: int = 20
) -> dict[str, Any]:
    active = active_version(session, site_id)
    rows = session.scalars(
        select(FingerprintLibraryVersion)
        .where(FingerprintLibraryVersion.site_id == site_id)
        .order_by(
            FingerprintLibraryVersion.created_at_epoch.desc(),
            FingerprintLibraryVersion.version.desc(),
        )
        .limit(max(1, min(limit, 100)))
    ).all()
    all_versions = session.scalars(
        select(FingerprintLibraryVersion).where(
            FingerprintLibraryVersion.site_id == site_id
        )
    ).all()
    detail_versions = sum(1 for row in all_versions if not row.details_pruned)
    return {
        "active": _version_summary(active) if active is not None else None,
        "items": [
            _version_summary(row, active=active is not None and row.version == active.version)
            for row in rows
        ],
        #: 保留策略与存储占用说明（按实测每版本约 5.2MB 估算）。
        "retention": {
            "keepVersions": RETAIN_VERSION_COUNT,
            "versionCount": len(all_versions),
            "detailVersions": detail_versions,
            "prunedVersions": len(all_versions) - detail_versions,
            "estimatedBytesPerVersion": ESTIMATED_BYTES_PER_VERSION,
            "estimatedDetailBytes": detail_versions * ESTIMATED_BYTES_PER_VERSION,
        },
    }


def manifest(
    session: Session,
    *,
    site_id: str = DEFAULT_SITE_ID,
    version: str | None = None,
    floor: str | None = None,
) -> dict[str, Any]:
    """对账清单：fpId + collectedAt + 内容哈希（可选按楼层过滤）。"""
    if version is None or version == "active":
        row = active_version(session, site_id)
    else:
        row = session.get(FingerprintLibraryVersion, version)
    if row is None or row.site_id != site_id:
        return {
            "version": None,
            "siteId": site_id,
            "floor": floor,
            "recordCount": 0,
            "items": [],
        }
    if row.details_pruned:
        raise FingerprintLibraryError(
            409,
            "VERSION_EXPIRED",
            f"版本已过期：{row.version} 的明细已被保留策略裁剪，无法生成对账清单",
        )
    query = select(FingerprintLibraryRecord).where(
        FingerprintLibraryRecord.version == row.version
    )
    if floor is not None:
        query = query.where(FingerprintLibraryRecord.floor_id == str(floor))
    records = session.scalars(query).all()
    items = []
    for record in records:
        body = json.loads(record.record_json)
        items.append(
            {
                "fpId": record.fp_id,
                "collectedAt": record.collected_at,
                "floorId": record.floor_id,
                "contentSha256": record_sha(body),
            }
        )
    items.sort(key=lambda item: (item["fpId"], item["collectedAt"]))
    return {
        "version": row.version,
        "siteId": site_id,
        "floor": floor,
        "recordCount": len(items),
        "items": items,
    }


def _live_vectors(pkg: Any, beacons: list[Any]) -> dict[str, float]:
    catalog = {b.beaconId: b for b in pkg.beacons}
    vectors: dict[str, float] = {}
    for obs in beacons:
        beacon = catalog.get(obs.beaconId)
        if beacon is None or beacon.major is None or beacon.minor is None:
            continue
        key = f"{int(beacon.major)}:{int(beacon.minor)}"
        # 同一信标多条观测保留最强值（与三点定位去重口径一致）
        if key not in vectors or obs.rssi > vectors[key]:
            vectors[key] = float(obs.rssi)
    return vectors


def query(
    session: Session,
    *,
    pkg: Any,
    beacons: list[Any],
    site_id: str = DEFAULT_SITE_ID,
    floor_hint: str | None = None,
) -> tuple[FingerprintMatch | None, str | None]:
    """返回 (命中, 未命中原因)。原因用于客户端/日志解释降级。"""
    active = active_version(session, site_id)
    if active is None:
        return None, "no_active_library"
    live = _live_vectors(pkg, beacons)
    if len(live) < MATCH_MIN_OVERLAP:
        return None, "too_few_known_beacons"

    floors = set(pkg.floors.keys())
    if floor_hint is not None and floor_hint in floors:
        floors = {floor_hint}

    rows = session.scalars(
        select(FingerprintLibraryPoint).where(
            FingerprintLibraryPoint.version == active.version
        )
    ).all()
    best: tuple[float, FingerprintLibraryPoint, int, float] | None = None
    second_rmse = float("inf")
    for row in rows:
        if row.floor_id not in floors:
            continue
        stored = json.loads(row.vector_json)
        common = [key for key in live if key in stored]
        if len(common) < MATCH_MIN_OVERLAP:
            continue
        rmse = (
            sum((live[key] - float(stored[key])) ** 2 for key in common) / len(common)
        ) ** 0.5
        coverage = len(common) / max(1, min(len(live), len(stored)))
        if best is None or rmse < best[0]:
            if best is not None:
                second_rmse = min(second_rmse, best[0])
            best = (rmse, row, len(common), coverage)
        else:
            second_rmse = min(second_rmse, rmse)

    if best is None:
        return None, "no_overlap"
    rmse, row, overlap, coverage = best
    confidence = max(0.0, 1.0 - rmse / MATCH_CONFIDENCE_RMSE_DB) * coverage
    if rmse > MATCH_MAX_RMSE_DB:
        return None, "rmse_too_large"
    if confidence < MATCH_MIN_CONFIDENCE:
        return None, "low_confidence"
    margin = second_rmse - rmse if second_rmse != float("inf") else float("inf")
    if margin < MATCH_MIN_MARGIN_DB:
        return None, "ambiguous"
    return (
        FingerprintMatch(
            library_version=active.version,
            fp_id=row.fp_id,
            floor_id=row.floor_id,
            x=row.x,
            y=row.y,
            rmse_db=round(rmse, 3),
            confidence=round(min(1.0, confidence), 3),
            overlap=overlap,
            margin_db=margin,
        ),
        None,
    )
