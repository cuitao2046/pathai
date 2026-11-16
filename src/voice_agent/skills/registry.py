# -*- coding: utf-8 -*-
"""动作注册表：自动发现本包内的 skill 单元，并按名字加载执行。"""
from __future__ import annotations

import importlib
import pkgutil
from pathlib import Path

from .base import ActionSkill

_SKIP_MODULES = {"base", "registry"}
_PACKAGE_DIR = Path(__file__).resolve().parent
_PACKAGE_NAME = __package__ or "src.voice_agent.skills"


def discover() -> dict[str, ActionSkill]:
    """扫描本包模块：暴露 `SKILL` 即注册；动作名重复直接报错（启动期暴露）。"""
    found: dict[str, ActionSkill] = {}
    for info in pkgutil.iter_modules([str(_PACKAGE_DIR)]):
        if info.name.startswith("_") or info.name in _SKIP_MODULES:
            continue
        module = importlib.import_module(f"{_PACKAGE_NAME}.{info.name}")
        skill = getattr(module, "SKILL", None)
        if skill is None:
            continue
        if skill.name in found:
            raise RuntimeError(f"动作名重复：{skill.name}（模块 {info.name}）")
        found[skill.name] = skill
    return found


SKILLS: dict[str, ActionSkill] = discover()


def get_skill(name: str | None) -> ActionSkill | None:
    return SKILLS.get(name) if name else None


def action_catalog_text() -> str:
    """系统提示词里的动作段落（唯一来源：注册表）。"""
    return "\n".join(
        skill.catalog_line() for skill in sorted(SKILLS.values(), key=lambda item: item.name)
    )
