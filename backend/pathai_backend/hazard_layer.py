# -*- coding: utf-8 -*-
"""危险层（Hazard Layer）加载、降级与生效判定。

危险层是独立于地图包的运营数据，格式见
`specs/phase-1-indoor-navigation/hazard-layer.schema.json`：

- 目录约定：`<hazard_data_dir>/<siteId>.json`，默认 `backend/hazards_data/`；
- 加载失败/版本不匹配时不阻塞导航：该场所按「无危险层」处理，并在响应里
  通过 `hazardLayerStatus` / `hazardLayerNotice` 明示降级原因；
- `activeFrom/activeUntil` 在每次规划时按当前时间判定，过期条目不生效；
- 一个 edgeId 可出现在多条危险中，路由侧按 block > avoid > notice 取最严格档位。

本模块只做解析与判定，不依赖 FastAPI，也不依赖 JSON Schema 库（运行环境未安装），
严格校验由入库流程用 `specs/.../hazard-layer.schema.json` 完成。
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

#: 档位严格程度：用于同边多危险时取最严格档位。
LEVEL_RANK = {"notice": 1, "avoid": 2, "block": 3}
#: 路由真正消费的档位（notice 不改变选路）。
ROUTING_LEVELS = ("block", "avoid")
#: 默认 avoid 惩罚（与 schema 的 default 一致）。
DEFAULT_PENALTY_M = 60.0

#: 降级状态（写进 RouteInfo.hazardLayerStatus）。
STATUS_OK = "ok"
STATUS_MISSING = "missing"
STATUS_INVALID = "invalid"
STATUS_VERSION_MISMATCH = "version_mismatch"
STATUS_UNKNOWN = "unknown"


def _parse_dt(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _as_float(value: object, default: float) -> float:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    return default


@dataclass(frozen=True)
class HazardZone:
    """一条已解析的危险记录；只保留路由与播报需要的字段。"""

    hazard_id: str
    floor_id: str
    level: str
    hazard_type: str
    reason: str
    spoken_text: str | None
    penalty_m: float | None
    edge_ids: tuple[str, ...]
    applies_to: frozenset[str]
    active_from: datetime | None
    active_until: datetime | None
    status: str
    source: str
    revision: int

    def is_active(self, now: datetime) -> bool:
        if self.status != "active":
            return False
        if self.active_from is not None and now < self.active_from:
            return False
        if self.active_until is not None and now > self.active_until:
            return False
        return True

    def applies_to_mode(self, mode: str) -> bool:
        return mode in self.applies_to

    def to_rule(self) -> dict:
        """路由引擎（RouteGraph）使用的精简结构。"""
        return {
            "hazardId": self.hazard_id,
            "level": self.level,
            "penaltyM": self.penalty_m,
            "edgeIds": list(self.edge_ids),
        }


@dataclass(frozen=True)
class HazardLayer:
    """某个场所 + 某个地图版本的危险层快照。"""

    site_id: str
    map_version: str
    hazard_version: str | None = None
    status: str = STATUS_MISSING
    notice: str | None = None
    hazards: tuple[HazardZone, ...] = field(default_factory=tuple)

    def active(self, now: datetime, mode: str) -> tuple[HazardZone, ...]:
        """当前时刻对指定人群生效的危险（含 notice，供响应展示）。"""
        return tuple(
            h for h in self.hazards
            if h.is_active(now) and h.applies_to_mode(mode)
        )

    def degraded(self) -> bool:
        return self.status != STATUS_OK


def _parse_zone(raw: dict, site_id: str) -> HazardZone | None:
    hazard_id = raw.get("hazardId")
    level = raw.get("level")
    edge_ids = raw.get("edgeIds")
    if not isinstance(hazard_id, str) or not hazard_id:
        return None
    if level not in LEVEL_RANK or not isinstance(edge_ids, list) or not edge_ids:
        return None
    if not all(isinstance(e, str) and e for e in edge_ids):
        return None
    applies = raw.get("appliesTo")
    if not isinstance(applies, list) or not applies:
        applies = ["blind"]
    penalty = None
    if level == "avoid":
        penalty = _as_float(raw.get("penaltyM"), DEFAULT_PENALTY_M)
        if penalty <= 0:
            penalty = DEFAULT_PENALTY_M
    return HazardZone(
        hazard_id=str(raw.get("hazardId") or ""),
        floor_id=str(raw.get("floorId") or ""),
        level=level,
        hazard_type=str(raw.get("type") or "other"),
        reason=str(raw.get("reason") or ""),
        spoken_text=raw.get("spokenText") if isinstance(raw.get("spokenText"), str) else None,
        penalty_m=penalty,
        edge_ids=tuple(dict.fromkeys(edge_ids)),
        applies_to=frozenset(str(m) for m in applies),
        active_from=_parse_dt(raw.get("activeFrom")),
        active_until=_parse_dt(raw.get("activeUntil")),
        status=str(raw.get("status") or "active"),
        source=str(raw.get("source") or ""),
        revision=int(raw.get("revision") or 1),
    )


def parse_layer(site_id: str, payload: dict, expected_map_version: str | None = None) -> HazardLayer:
    """把危险层 JSON 解析为 HazardLayer；结构性损坏时返回 invalid 层而不是抛异常。"""
    try:
        if str(payload.get("siteId")) != site_id:
            return HazardLayer(
                site_id, expected_map_version or "",
                status=STATUS_INVALID,
                notice=f"危险层 siteId={payload.get('siteId')} 与场所 {site_id} 不一致，已忽略",
            )
        map_version = str(payload.get("mapVersion") or "")
        raw_zones = payload.get("hazards")
        if not isinstance(raw_zones, list):
            return HazardLayer(
                site_id, map_version, status=STATUS_INVALID,
                notice="危险层缺少 hazards 数组，已忽略",
            )
        zones: list[HazardZone] = []
        skipped = 0
        for raw in raw_zones:
            if not isinstance(raw, dict):
                skipped += 1
                continue
            zone = _parse_zone(raw, site_id)
            if zone is None:
                skipped += 1
                continue
            if zone.hazard_id and zone.hazard_id in {z.hazard_id for z in zones}:
                skipped += 1
                continue
            zones.append(zone)
        if skipped:
            logger.warning("危险层 %s 有 %d 条记录结构不合法，已跳过", site_id, skipped)
        layer = HazardLayer(
            site_id=site_id,
            map_version=map_version,
            hazard_version=payload.get("hazardVersion"),
            status=STATUS_OK,
            hazards=tuple(zones),
        )
        if expected_map_version is not None and map_version != expected_map_version:
            return HazardLayer(
                site_id, map_version, hazard_version=layer.hazard_version,
                status=STATUS_VERSION_MISMATCH,
                notice=(
                    f"危险层针对地图版本 {map_version or '<空>'}，"
                    f"当前地图版本 {expected_map_version}，已忽略（请复核 edgeIds 后重新导出）"
                ),
            )
        return layer
    except Exception as exc:  # noqa: BLE001 - 坏文件不得影响导航
        logger.exception("危险层 %s 解析失败，已降级为无危险层", site_id)
        return HazardLayer(
            site_id, expected_map_version or "", status=STATUS_INVALID,
            notice=f"危险层解析失败：{exc}",
        )


@dataclass(frozen=True)
class HazardRepository:
    """启动时加载全部场所危险层；缺失/损坏只降级，不影响服务启动。"""

    layers: dict[str, HazardLayer] = field(default_factory=dict)

    @classmethod
    def load(cls, hazard_data_dir: Path) -> "HazardRepository":
        layers: dict[str, HazardLayer] = {}
        if not hazard_data_dir.is_dir():
            logger.warning("危险层目录不存在：%s（按无危险层运行）", hazard_data_dir)
            return cls(layers={})
        for file in sorted(hazard_data_dir.glob("*.json")):
            site_id = file.stem
            try:
                payload = json.loads(file.read_text(encoding="utf-8"))
            except Exception as exc:  # noqa: BLE001
                logger.warning("危险层文件无法解析：%s（%s）", file, exc)
                layers[site_id] = HazardLayer(
                    site_id, "", status=STATUS_INVALID,
                    notice=f"危险层文件无法解析：{file.name}",
                )
                continue
            layers[site_id] = parse_layer(site_id, payload)
            layer = layers[site_id]
            if layer.degraded():
                logger.warning("危险层降级：%s（%s）", site_id, layer.notice)
            else:
                logger.info(
                    "危险层加载：%s mapVersion=%s hazards=%d version=%s",
                    site_id, layer.map_version, len(layer.hazards), layer.hazard_version,
                )
        return cls(layers=layers)

    def get(self, site_id: str, map_version: str | None) -> HazardLayer:
        """取指定场所的基础层；地图版本不匹配时返回空层并标记降级。"""
        layer = self.layers.get(site_id)
        if layer is None:
            return HazardLayer(
                site_id, map_version or "", status=STATUS_MISSING,
                notice=f"场所 {site_id} 未配置危险层，按无危险处理",
            )
        if map_version is not None and layer.status == STATUS_OK and layer.map_version != map_version:
            return HazardLayer(
                site_id, map_version, hazard_version=layer.hazard_version,
                status=STATUS_VERSION_MISMATCH,
                notice=(
                    f"危险层针对地图版本 {layer.map_version or '<空>'}，"
                    f"当前地图版本 {map_version}，已忽略（请复核 edgeIds 后重新导出）"
                ),
            )
        return layer


def log_version_mismatches(
    repo: HazardRepository, latest_by_site: dict[str, str]
) -> list[str]:
    """启动审计：危险层版本落后于当前地图时打 ERROR，避免整层被静默忽略。"""
    mismatched: list[str] = []
    for site_id, layer in repo.layers.items():
        latest = latest_by_site.get(site_id)
        if latest is None or not layer.map_version or layer.map_version == latest:
            continue
        logger.error(
            "危险层版本与当前地图不一致：%s 危险层=%s 地图=%s"
            "（将被版本校验忽略，请复核 edgeIds 后重新导出）",
            site_id,
            layer.map_version,
            latest,
        )
        mismatched.append(site_id)
    return mismatched
