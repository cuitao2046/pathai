# -*- coding: utf-8 -*-
"""speak_answer：直接回答现场问题（门体 / 风险 / 路线进度等）。"""
from __future__ import annotations

from .base import ActionParam, ActionSkill, SkillContext, SkillOutcome


def execute(ctx: SkillContext) -> SkillOutcome | None:
    speech = ctx.result.direct_response_speech
    if not speech:
        return None
    return SkillOutcome(
        intent="unknown",
        confidence=0.85,
        needs_clarification=False,
        destination=None,
        candidates=(),
        response_text=speech,
        action="speak_answer",
    )


SKILL = ActionSkill(
    name="speak_answer",
    description=(
        "直接播报答案，不触发导航动作：适用于问门体状态、前方风险、好不好走、"
        "还剩多远等现场问题。答案必须写在 direct_response_speech，"
        "不得编造上下文里没有的设施。"
    ),
    params=(
        ActionParam("answer", True, "最终播报文字，写入 direct_response_speech"),
    ),
    intent="unknown",
    execute=execute,
)
