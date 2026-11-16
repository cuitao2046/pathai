# -*- coding: utf-8 -*-
"""describe_surroundings：描述周边有什么设施 / 环境。"""
from __future__ import annotations

from .base import ActionSkill, SkillContext, SkillOutcome


def execute(ctx: SkillContext) -> SkillOutcome:
    speech = ctx.result.direct_response_speech
    return SkillOutcome(
        intent="query_location",
        confidence=0.9,
        needs_clarification=False,
        destination=None,
        candidates=(),
        response_text=speech or "请稍等，正在为您查询周边环境。",
    )


SKILL = ActionSkill(
    name="describe_surroundings",
    description=(
        "描述用户周边有什么（附近设施、门口、通道等）。"
        "用户问「周围有什么 / 附近有什么 / 旁边是什么」时选它；"
        "问自己在哪应选 query_current_location。"
    ),
    intent="query_location",
    execute=execute,
)
