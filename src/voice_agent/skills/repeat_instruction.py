# -*- coding: utf-8 -*-
"""repeat_instruction：重播上一条播报 / 指令。"""
from __future__ import annotations

from .base import ActionSkill, SkillContext, SkillOutcome


def execute(ctx: SkillContext) -> SkillOutcome:
    return SkillOutcome(
        intent="repeat_instruction",
        confidence=0.95,
        needs_clarification=False,
        destination=None,
        candidates=(),
        response_text="好的，将重复当前指令。",
        action="repeat_instruction",
    )


SKILL = ActionSkill(
    name="repeat_instruction",
    description="重播上一条导航指令或播报（用户说「再说一遍 / 没听清 / 重复」）。",
    intent="repeat_instruction",
    execute=execute,
)
