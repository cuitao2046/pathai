# -*- coding: utf-8 -*-
"""query_current_location：回答「我在哪」。"""
from __future__ import annotations

from .base import ActionSkill, SkillContext, SkillOutcome


def execute(ctx: SkillContext) -> SkillOutcome | None:
    return ctx.answer_location()


SKILL = ActionSkill(
    name="query_current_location",
    description=(
        "回答用户当前所在位置（楼层 / 区域 / 朝向）。"
        "用户问「我在哪 / 这是什么位置」时选它；"
        "问「周围/附近有什么」应选 describe_surroundings，两者不要混用。"
    ),
    intent="query_location",
    execute=execute,
)
