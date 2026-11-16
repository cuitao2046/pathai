# -*- coding: utf-8 -*-
"""cancel_navigation：取消当前导航。"""
from __future__ import annotations

from .base import ActionSkill, SkillContext, SkillOutcome


def execute(ctx: SkillContext) -> SkillOutcome:
    return SkillOutcome(
        intent="cancel",
        confidence=0.95,
        needs_clarification=False,
        destination=None,
        candidates=(),
        response_text="好的，已收到取消请求。",
        action="cancel_navigation",
    )


SKILL = ActionSkill(
    name="cancel_navigation",
    description="取消当前导航（用户说「取消导航 / 取消 / 不去了」）。",
    intent="cancel",
    execute=execute,
)
