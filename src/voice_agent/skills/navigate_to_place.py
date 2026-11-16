# -*- coding: utf-8 -*-
"""navigate_to_place：导航到某个具体地点（目的地来自 top_candidates）。"""
from __future__ import annotations

from .base import ActionParam, ActionSkill, SkillContext, SkillOutcome


def execute(ctx: SkillContext) -> SkillOutcome:
    return ctx.resolve_destination()


SKILL = ActionSkill(
    name="navigate_to_place",
    description=(
        "导航到某个具体地点：用户说出楼内地点，或表达「去 / 到 / 带我去 / 导航到某处」。"
        "必须把目的地标准名写入 top_candidates；"
        "不要用它表示「开始当前预览路线」（那是 start_navigation）。"
    ),
    params=(
        ActionParam("destination", True, "目的地标准名，写入 top_candidates[0].standard_name"),
        ActionParam("floor", False, "楼层；同名多实例且用户指明楼层时写入候选 floor"),
    ),
    intent="start_navigation",
    execute=execute,
)
