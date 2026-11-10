# -*- coding: utf-8 -*-
"""标定控制台请求契约（specs/calibration-console §6）。"""
from __future__ import annotations

from pydantic import Field

from pathai_backend.schemas.common import StrictModel


class SampleUploadRequest(StrictModel):
    fileName: str = Field(min_length=1, max_length=160)
    #: 样本文件的原始 JSON 文本（服务端负责解析与校验）
    content: str = Field(min_length=2)
    operator: str | None = Field(default=None, max_length=64)


class ComputeRunRequest(StrictModel):
    sampleId: str = Field(min_length=1, max_length=40)


class ApplyRunRequest(StrictModel):
    confirm: bool
    expectParamVersion: str | None = Field(default=None, max_length=40)


class RollbackRequest(StrictModel):
    paramVersion: str = Field(min_length=1, max_length=40)
    confirm: bool
