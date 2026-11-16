# -*- coding: utf-8 -*-
"""start_navigation：开始导航当前预览路线（无新目的地）。"""
from __future__ import annotations

from .base import ActionSkill, SkillContext, SkillOutcome


def execute(ctx: SkillContext) -> SkillOutcome:
    return SkillOutcome(
        intent="start_navigation",
        confidence=0.95,
        needs_clarification=False,
        destination=None,
        candidates=(),
        response_text="好的，开始导航。",
        action="start_navigation",
    )


SKILL = ActionSkill(
    name="start_navigation",
    description=(
        "开始导航当前已预览的路线：用户说「开始导航 / 开始吧 / 出发 / 走吧 / 带我走 / 可以走了」"
        "且没有给出新目的地。不要反问目的地，top_candidates 留空；"
        "若同一句里还有楼内地点，改用 navigate_to_place。"
    ),
    intent="start_navigation",
    execute=execute,
)
