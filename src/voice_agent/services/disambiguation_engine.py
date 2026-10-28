# -*- coding: utf-8 -*-
"""目的地消歧与四级业务决策矩阵（docs/26 §4.2；spec REQ-VOICE-04）。

引擎只做「语义决策」，不做路网计算：路径由注入的 `RoutePlanner` 提供，
便于后端接 `nav_service.plan_route`，单测接假实现。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Protocol, Sequence

from ..schemas.llm_intent import DestinationCandidate, IntentParseResult
from ..schemas.spatial_context import UserSpatialContext
from .dictionary_store import PoiDictionary, PoiEntry, PoiInstance

#: 阈值（docs/26 §4.2，评审后冻结）
NOT_FOUND_MAX_CONFIDENCE = 0.50
RELATED_FALLBACK_MIN_CONFIDENCE = 0.35
AMBIGUITY_CONFIDENCE_GAP = 0.15
#: 跨层就近加价（米）：同层优先，但不过度惩罚合理的跨层电梯路线
CROSS_FLOOR_PENALTY_M = 15.0

FLOOR_LABEL = {"1": "一楼", "2": "二楼", "3": "三楼", "4": "四楼"}


@dataclass(frozen=True)
class RoutePlan:
    distance_m: float
    reachable: bool = True


class RoutePlanner(Protocol):
    """盲模式路线规划（实现方持有用户起点）。"""

    def plan(self, target_node_id: str, mode: str = "blind") -> RoutePlan: ...


Action = Literal["speak", "start_navigation", "no_action"]


@dataclass
class Decision:
    action: Action
    text: str | None = None
    target: str | None = None
    node_id: str | None = None
    route: RoutePlan | None = None
    pending_candidates: tuple[dict, ...] = field(default_factory=tuple)


def _pending_item(entry: PoiEntry, instance: PoiInstance | None = None) -> dict:
    return {
        "standard_name": entry.standard_name,
        "floor": instance.floor if instance else None,
        "node_id": instance.node_id if instance else None,
        "zone_hint": instance.zone_hint if instance else None,
    }


def _floor_label(floor_id: str | None) -> str:
    return FLOOR_LABEL.get(floor_id or "", "楼内")


def _clarify_instances(entry: PoiEntry, instances: Sequence[PoiInstance]) -> str:
    floors = {inst.floor_id for inst in instances}
    prefix = _floor_label(next(iter(floors))) if len(floors) == 1 else "楼内"
    zones = "、".join(inst.zone_hint for inst in instances)
    return (
        f"{prefix}共有{len(instances)}间{entry.standard_name}，分别位于{zones}。"
        "请问您要去哪一间，还是直接带您去离您最近的一间？"
    )


def _nearest_accessible(
    entry: PoiEntry,
    instances: Sequence[PoiInstance],
    user_floor: str,
    planner: RoutePlanner,
) -> tuple[PoiInstance, RoutePlan] | None:
    best: tuple[PoiInstance, RoutePlan, float] | None = None
    for inst in instances:
        plan = planner.plan(inst.node_id, "blind")
        if not plan.reachable:
            continue
        cost = plan.distance_m + (0.0 if inst.floor_id == user_floor else CROSS_FLOOR_PENALTY_M)
        if best is None or cost < best[2]:
            best = (inst, plan, cost)
    return None if best is None else (best[0], best[1])


def resolve_destination_and_act(
    parse_result: IntentParseResult,
    user_ctx: UserSpatialContext,
    dictionary: PoiDictionary,
    planner: RoutePlanner,
) -> Decision:
    candidates = parse_result.top_candidates

    # 分支 4：拒识 / 楼内无此设施
    if (
        not candidates
        or parse_result.resolution_status == "not_found_in_building"
        or candidates[0].confidence < NOT_FOUND_MAX_CONFIDENCE
    ):
        top = candidates[0] if candidates else None
        if (
            top is not None
            and top.match_type == "related_fallback"
            and top.confidence >= RELATED_FALLBACK_MIN_CONFIDENCE
        ):
            entry = dictionary.get(top.standard_name)
            return Decision(
                action="speak",
                text=parse_result.clarification_question
                or f"楼内没有找到{parse_result.raw_target_mention or '该地点'}，您是指附近的{top.standard_name}吗？",
                pending_candidates=(_pending_item(entry),) if entry else (),
            )
        return Decision(
            action="speak",
            text=f"这栋教学楼内没有找到{parse_result.raw_target_mention or '该地点'}，您可以换个名称试试。",
        )

    top1 = candidates[0]
    top2 = candidates[1] if len(candidates) > 1 else None

    # 分支 3A：语义概念层存在歧义
    if parse_result.resolution_status == "ambiguous_concepts" or (
        top2 is not None and (top1.confidence - top2.confidence) < AMBIGUITY_CONFIDENCE_GAP
    ):
        pending = tuple(
            _pending_item(dictionary.get(c.standard_name))
            for c in candidates[:3]
            if dictionary.get(c.standard_name) is not None
        )
        return Decision(
            action="speak",
            text=parse_result.clarification_question or "楼内有多个相近的地点，请问您要去哪一个？",
            pending_candidates=pending,
        )

    entry = dictionary.get(top1.standard_name)
    if entry is None:
        return Decision(
            action="speak",
            text=f"这栋教学楼内没有找到{parse_result.raw_target_mention or top1.standard_name}，您可以换个名称试试。",
        )

    # 多轮澄清已锁定到具体实例：直接规划
    if top1.node_id:
        plan = planner.plan(top1.node_id, "blind")
        if plan.reachable:
            return Decision(
                action="start_navigation",
                target=entry.standard_name,
                node_id=top1.node_id,
                route=plan,
            )
        return Decision(
            action="speak", text=f"前往{entry.standard_name}的路线暂时无法规划，请稍后再试。"
        )

    # 多轮指代「离我最近的」：跨实例按盲模式成本择优（不限于公共设施）
    if top1.match_type == "contextual_selection":
        best = _nearest_accessible(entry, entry.instances, user_ctx.floor, planner)
        if best is not None:
            inst, plan = best
            return Decision(
                action="start_navigation",
                target=f"{inst.floor}{entry.standard_name}",
                node_id=inst.node_id,
                route=plan,
            )
        return Decision(action="speak", text=f"附近的{entry.standard_name}暂时都没有无障碍路线。")

    instances = entry.instances_on_floor(top1.floor)
    if not instances:
        return Decision(action="speak", text=f"这栋教学楼内没有找到{top1.standard_name}。")

    # 分支 1：唯一物理实例
    if len(instances) == 1:
        inst = instances[0]
        plan = planner.plan(inst.node_id, "blind")
        if plan.reachable:
            return Decision(
                action="start_navigation",
                target=entry.standard_name,
                node_id=inst.node_id,
                route=plan,
            )
        return Decision(
            action="speak", text=f"前往{entry.standard_name}的路线暂时无法规划，请稍后再试。"
        )

    # 分支 2：公共设施多实例 → 就近自动消歧，不打扰
    if entry.is_public_utility:
        best = _nearest_accessible(entry, instances, user_ctx.floor, planner)
        if best is not None:
            inst, plan = best
            return Decision(
                action="start_navigation",
                target=f"{inst.floor}{entry.standard_name}",
                node_id=inst.node_id,
                route=plan,
            )
        return Decision(action="speak", text=f"附近的{entry.standard_name}暂时都没有无障碍路线。")

    # 分支 3B：非公共设施存在多物理实例 → 结合方位反问
    return Decision(
        action="speak",
        text=_clarify_instances(entry, instances),
        pending_candidates=tuple(_pending_item(entry, inst) for inst in instances),
    )
