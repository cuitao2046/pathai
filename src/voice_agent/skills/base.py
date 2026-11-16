# -*- coding: utf-8 -*-
"""动作 skill 契约：一个 action = 一个自包含单元（含义 + 参数 + 执行体）。

新增动作只需在本包下新增一个模块并暴露 `SKILL`：注册表自动发现，
系统提示词的动作段落由注册表汇总生成，路由不需要新增分支。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from ..schemas.llm_intent import IntentParseResult


@dataclass(frozen=True)
class ActionParam:
    """参数契约：模型需要提供什么、是否必填。"""

    name: str
    required: bool
    description: str


@dataclass(frozen=True)
class PlaceRef:
    """与 API 契约无关的地点引用，由 API 层再转 PlaceCandidate。"""

    place_id: str
    name: str
    floor_id: str


@dataclass(frozen=True)
class SkillOutcome:
    """skill 执行结果：字段与 API-04 响应一一对应。"""

    intent: str
    confidence: float
    needs_clarification: bool
    destination: PlaceRef | None
    candidates: tuple[PlaceRef, ...]
    response_text: str
    #: 缺省时由服务层填成 skill 名
    action: str | None = None


@dataclass(frozen=True)
class SkillContext:
    """执行期注入的上下文与共享能力（skill 不反向依赖语义服务）。"""

    text: str
    result: IntentParseResult
    #: 把模型给的 top_candidates 展开成唯一目的地或澄清候选
    resolve_destination: Callable[[], SkillOutcome]
    #: 位置/周边直答：无空间上下文时返回 None（宁可不答也不编造）
    answer_location: Callable[[], SkillOutcome | None]


@dataclass(frozen=True)
class ActionSkill:
    """动作 skill：名字唯一，说明与参数自动进入系统提示词。"""

    name: str
    description: str
    execute: Callable[[SkillContext], SkillOutcome | None]
    intent: str = "unknown"
    params: tuple[ActionParam, ...] = ()
    confirmation_required: bool = False

    def catalog_line(self) -> str:
        params = "、".join(
            f"{param.name}（{'必填' if param.required else '可选'}：{param.description}）"
            for param in self.params
        ) or "无"
        return f"- `{self.name}`：{self.description} 参数：{params}"
