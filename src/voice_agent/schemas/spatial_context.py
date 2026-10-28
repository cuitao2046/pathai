# -*- coding: utf-8 -*-
"""自我中心空间上下文契约（docs/26 §3.2；spec REQ-VOICE-02）。

楼层键使用发布包真实键（"1"/"2"），播报层再转「一楼/二楼」（spec §5-D9）。
"""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

ElementType = Literal[
    "room_door",
    "open_doorway_dk",
    "fire_door_normally_open",
    "fire_door_normally_closed",
    "elevator",
    "stairs",
    "hazard",
    "pillar",
    "ramp",
]
RegionType = Literal[
    "corridor",
    "lobby",
    "elevator_hall",
    "stair_hall",
    "room",
    "activity_zone",
    "entrance",
]
MotionState = Literal["stationary", "walking"]


class NearbyElement(BaseModel):
    """周边关键要素：时钟方向 + 距离，禁止暴露拓扑编号与绝对坐标。"""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    element_type: ElementType
    clock_direction: int = Field(ge=1, le=12)
    distance_meters: float = Field(ge=0)
    distance_steps: int = Field(ge=0)
    accessibility_note: Optional[str] = None


class UserSpatialContext(BaseModel):
    model_config = ConfigDict(extra="forbid")

    floor: str = Field(min_length=1, description="发布包楼层键：1/2")
    current_region_type: RegionType
    current_region_name: str = Field(min_length=1)
    motion_state: MotionState
    on_tactile_paving: bool
    floor_surface: Optional[str] = None
    nearby_elements: list[NearbyElement] = Field(default_factory=list)
