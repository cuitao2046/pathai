# -*- coding: utf-8 -*-
"""API 契约公共类型与枚举。

冻结自 docs/24-第一阶段前后端需求与API定义.md §7-§8 与
specs/phase-1-indoor-navigation/spec.md（REQ-NAV / REQ-LOCATION / REQ-SESSION）。

工程初始默认值（RSSI 范围、expiresIn、routeRulesVersion、指令有效期）仅为
开发联调用，现场标定/评审后冻结，见 docs/24 §12。
"""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    """契约模型基类：禁止未知字段，避免客户端/服务端各自解释 schema。"""

    model_config = ConfigDict(extra="forbid")


class IntentType(str, Enum):
    START_NAVIGATION = "start_navigation"
    QUERY_LOCATION = "query_location"
    SEARCH_FACILITY = "search_facility"
    CANCEL = "cancel"
    END_NAVIGATION = "end_navigation"
    REPEAT_INSTRUCTION = "repeat_instruction"
    UNKNOWN = "unknown"


class LocationQuality(str, Enum):
    RELIABLE = "reliable"
    DEGRADED = "degraded"
    UNRELIABLE = "unreliable"
    LOST = "lost"


#: 可用于驱动转向/到达判断的质量集合；unreliable/lost 必须进入恢复流程。
TRUSTED_QUALITIES: tuple[LocationQuality, ...] = (
    LocationQuality.RELIABLE,
    LocationQuality.DEGRADED,
)


class SessionStatus(str, Enum):
    ACTIVE = "active"
    RECOVERING = "recovering"
    ARRIVED = "arrived"
    ENDED = "ended"


#: 终态不可逆（docs/24 §4.3）。
TERMINAL_SESSION_STATUSES: tuple[SessionStatus, ...] = (
    SessionStatus.ARRIVED,
    SessionStatus.ENDED,
)


class InstructionAction(str, Enum):
    CONTINUE = "continue"
    TURN_LEFT = "turn_left"
    TURN_RIGHT = "turn_right"
    ENTER_ELEVATOR = "enter_elevator"
    EXIT_ELEVATOR = "exit_elevator"
    ARRIVE = "arrive"
    STOP = "stop"
    RELOCALIZE = "relocalize"


class InstructionPriority(str, Enum):
    NORMAL = "normal"
    TURN = "turn"
    SAFETY = "safety"


class NavEvent(str, Enum):
    INSTRUCTION = "instruction"
    RELOCALIZING = "relocalizing"
    REROUTE = "reroute"
    ARRIVED = "arrived"


class EndReason(str, Enum):
    USER_CANCELLED = "user_cancelled"
    USER_FINISHED = "user_finished"
    ARRIVED = "arrived"
    SYSTEM_ERROR = "system_error"


class RouteFallbackType(str, Enum):
    NONE = "none"
    BEST_DOOR = "best_door"
    ALL_DOORS = "all_doors"
    WALL_FALLBACK = "wall_fallback"


class UserMode(str, Enum):
    BLIND = "blind"
    NORMAL = "normal"
    WHEELCHAIR = "wheelchair"


class GeoPosition(StrictModel):
    """局部米制坐标；floorId 在楼层不可靠时可为 null（docs/24 API-05 规则）。"""

    x: float
    y: float
    floorId: str | None = Field(default=None, min_length=1)


class LandmarkRef(StrictModel):
    """地标引用：只允许来自地图已发布地点，不得来自现场识别或模型生成。"""

    placeId: str = Field(min_length=1)
    name: str = Field(min_length=1)
    relativeDirection: Literal["left", "right", "ahead", "behind"] | None = None
    distanceM: float | None = Field(default=None, ge=0)


class Trigger(StrictModel):
    """指令触发条件：distance 按剩余距离、decision_point 按拓扑节点、immediate 立即。"""

    type: Literal["distance", "decision_point", "immediate"]
    valueM: float | None = Field(default=None, gt=0)
    nodeId: str | None = Field(default=None, min_length=1)

    @model_validator(mode="after")
    def _check_trigger_fields(self) -> "Trigger":
        if self.type == "distance" and self.valueM is None:
            raise ValueError("trigger type=distance 必须提供 valueM")
        if self.type == "decision_point" and not self.nodeId:
            raise ValueError("trigger type=decision_point 必须提供 nodeId")
        if self.type == "immediate" and (self.valueM is not None or self.nodeId is not None):
            raise ValueError("trigger type=immediate 不得携带 valueM/nodeId")
        return self


class NavigationInstruction(StrictModel):
    """结构化导航指令。

    instructionSequence 在会话内单调递增，用于去重与重播判定；
    expiresAt 为指令有效期（工程默认 10 秒），过期指令不得重播。
    """

    instructionId: str = Field(min_length=1)
    instructionSequence: int = Field(ge=1)
    action: InstructionAction
    text: str = Field(min_length=1)
    distanceM: float | None = Field(default=None, ge=0)
    landmark: LandmarkRef | None = None
    priority: InstructionPriority
    trigger: Trigger
    expiresAt: datetime | None = None


class PlaceCandidate(StrictModel):
    """意图候选/会话目的地共用的最小地点引用（docs/24 API-04）。"""

    placeId: str = Field(min_length=1)
    name: str = Field(min_length=1)
    floorId: str = Field(min_length=1)
