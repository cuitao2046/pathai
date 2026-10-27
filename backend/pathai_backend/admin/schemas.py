# -*- coding: utf-8 -*-
"""管理后台 API 请求 schema（严格模式：未知字段一律拒绝）。"""
from __future__ import annotations

from pydantic import Field

from pathai_backend.schemas.common import StrictModel


class LoginRequest(StrictModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)


class PasswordChangeRequest(StrictModel):
    currentPassword: str = Field(min_length=1, max_length=256)
    newPassword: str = Field(min_length=1, max_length=256)
    confirmPassword: str = Field(min_length=1, max_length=256)


class PlacePatch(StrictModel):
    """地点白名单 patch：仅允许语义字段；只读字段混入即 400。"""

    name: str | None = Field(default=None, max_length=256)
    spokenName: str | None = Field(default=None, max_length=256)
    aliases: list[str] | None = None
    type: str | None = Field(default=None, max_length=256)


class BeaconPatch(StrictModel):
    """信标白名单 patch：首版仅允许位置描述。"""

    locationDesc: str | None = Field(default=None, max_length=256)


class DraftPatchRequest(StrictModel):
    """草稿 patch 全量替换：前端持有完整有效视图后整体提交。"""

    places: dict[str, PlacePatch] = Field(default_factory=dict)
    beacons: dict[str, BeaconPatch] = Field(default_factory=dict)
