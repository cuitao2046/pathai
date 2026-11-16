# -*- coding: utf-8 -*-
"""end_navigation：结束本次导航（已到达或不再需要引导）。"""
from __future__ import annotations

from .base import ActionSkill, SkillContext, SkillOutcome


def execute(ctx: SkillContext) -> SkillOutcome:
    return SkillOutcome(
        intent="end_navigation",
        confidence=0.95,
        needs_clarification=False,
        destination=None,
        candidates=(),
        response_text="好的，将结束本次导航。",
        action="end_navigation",
    )


SKILL = ActionSkill(
    name="end_navigation",
    description=(
        "结束本次导航（用户说「结束导航 / 我到了 / 已到达」）。"
        "与 cancel_navigation 的区别：结束是完成/终止本次引导，取消是放弃这次导航请求。"
    ),
    intent="end_navigation",
    execute=execute,
)
