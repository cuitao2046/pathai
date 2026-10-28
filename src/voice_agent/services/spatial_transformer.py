# -*- coding: utf-8 -*-
"""几何坐标 → 自我中心语义（docs/26 §4.1；spec REQ-VOICE-02）。

地图坐标系：x 向东、y 向北，单位米；航向角以地图北为 0°、顺时针为正。
时钟方向：12 = 正前方，3 = 正右，6 = 正后，9 = 正左，扇区边界 ±15°。
"""
from __future__ import annotations

import math

#: 1 步 ≈ 0.6 米（docs/26 §3.2）
METERS_PER_STEP = 0.6

#: 罗盘朝向相对地图北的固定偏移（度）。默认 0；现场标定后冻结（spec §2）。
DEFAULT_HEADING_OFFSET_DEG = 0.0


def target_bearing_deg(
    origin_x: float, origin_y: float, target_x: float, target_y: float
) -> float:
    """目标相对地图北的方位角，范围 [0, 360)。"""
    theta = math.degrees(math.atan2(target_x - origin_x, target_y - origin_y))
    return (theta + 360.0) % 360.0


def align_heading(heading_deg: float, offset_deg: float = DEFAULT_HEADING_OFFSET_DEG) -> float:
    """把设备原始朝向换算到地图坐标系；偏移未标定时为 0。"""
    return (heading_deg + offset_deg) % 360.0


def clock_direction(target_bearing: float, heading_deg: float) -> int:
    """相对用户朝向的时钟刻度 1~12（12 为正前 [-15°, +15°)）。"""
    delta = (target_bearing - heading_deg + 360.0) % 360.0
    clock = int(((delta + 15.0) % 360.0) // 30.0)
    return 12 if clock == 0 else clock


def steps_for(distance_m: float) -> int:
    """米数折算步数（四舍五入，非银行家舍入）。"""
    return int(math.floor(max(distance_m, 0.0) / METERS_PER_STEP + 0.5))


def describe(
    origin_x: float, origin_y: float, target_x: float, target_y: float, heading_deg: float
) -> tuple[int, float]:
    """一次性返回（时钟方向, 直线距离米）。"""
    distance = math.hypot(target_x - origin_x, target_y - origin_y)
    clock = clock_direction(target_bearing_deg(origin_x, origin_y, target_x, target_y), heading_deg)
    return clock, distance
