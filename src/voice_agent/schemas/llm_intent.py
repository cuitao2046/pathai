# -*- coding: utf-8 -*-
"""LLM 意图解析结构化输出契约（docs/26 §3.3；spec REQ-VOICE-03/04）。

本版由规则解析器产出同一 Schema；后续 LLM 接入直接复用。
"""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

IntentType = Literal[
    "query_where_am_i",
    "navigate_to_destination",
    "query_route_progress",
    "check_hazard_or_door",
    "navigation_control",
    "out_of_scope",
]
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
    floor: Optional[str] = None
    match_type: MatchType
    confidence: float = Field(ge=0.0, le=1.0)
    reasoning: str = Field(min_length=1)
    #: 多轮澄清已锁定到具体物理实例时携带；引擎据此跳过实例展开直接规划（spec §5-D11）
    node_id: Optional[str] = None


class IntentParseResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    intent_type: IntentType
    raw_target_mention: Optional[str] = None
    resolution_status: Optional[ResolutionStatus] = None
    top_candidates: list[DestinationCandidate] = Field(default_factory=list, max_length=3)
    clarification_question: Optional[str] = None
    direct_response_speech: Optional[str] = None
