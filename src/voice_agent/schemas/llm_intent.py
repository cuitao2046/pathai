# -*- coding: utf-8 -*-
"""LLM 意图解析结构化输出契约（docs/26 §3.3；spec REQ-VOICE-03/04）。

`action` 由模型按系统提示词里的动作清单自行选择，注册表决定它是否真实存在；
`intent_type` 仅为兼容字段（动作注册表可推导），因此**不做 Literal 枚举限制**，
新增动作不需要改本文件。
"""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

ResolutionStatus = Literal[
    "resolved_unique_concept",
    "ambiguous_concepts",
    "not_found_in_building",
]
MatchType = Literal[
    "exact_or_synonym",
    "category_submatch",
    "related_fallback",
    "contextual_selection",
]


class DestinationCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    standard_name: str = Field(min_length=1, description="必须严格匹配词典 standard_name")
    #: 候选清单里的 POI 标识，便于日志追溯与前端说明
    place_id: Optional[str] = None
    floor: Optional[str] = None
    match_type: MatchType
    confidence: float = Field(ge=0.0, le=1.0)
    reasoning: str = Field(min_length=1)
    #: 多轮澄清已锁定到具体物理实例时携带；引擎据此跳过实例展开直接规划（spec §5-D11）
    node_id: Optional[str] = None


class IntentParseResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    #: 模型选定的动作名；不存在于动作注册表时按未知动作降级
    action: Optional[str] = None
    #: 兼容字段：模型可留空，服务端按动作注册表推导
    intent_type: str = "out_of_scope"
    raw_target_mention: Optional[str] = None
    resolution_status: Optional[ResolutionStatus] = None
    top_candidates: list[DestinationCandidate] = Field(default_factory=list, max_length=3)
    clarification_question: Optional[str] = None
    direct_response_speech: Optional[str] = None
