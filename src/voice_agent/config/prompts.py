# -*- coding: utf-8 -*-
"""提示词模板（docs/26 §5.1；spec REQ-VOICE-06）。

system 消息 = 静态前缀（词典 + 规则 + JSON Schema），逐请求不变，用于前缀缓存；
用户位置/待澄清候选/语音文本全部放在 user 消息，避免污染缓存前缀。
缓存 key 含 `map_version`，地图版本变化即换缓存域。
"""
from __future__ import annotations

import json
from typing import Sequence

from ..schemas.spatial_context import UserSpatialContext
from ..services.dictionary_store import PoiDictionary
from ..schemas.llm_intent import IntentParseResult

DICT_HEADER = "### 一、建筑可选目的地标准词典（静态缓存区，禁止改写、禁止捏造词典外名称）"

_ROLE_AND_RULES = """### 二、角色与语义对齐规则

你是一个专为视障人士设计的室内无障碍导航 AI 核心引擎，当前服务于「{building}」。
你的任务是解析视障用户的语音输入，只输出一个 JSON 对象（不要 Markdown、不要解释）。

1. 同义词与倒装对齐：校长办公室→领导办公、医务室→卫生室、保安室→传达室、男厕所→男卫生间。
   候选的 `standard_name` 必须严格等于词典中的标准名，不得捏造。
2. 类别歧义：用户说「物理实验室」而词典中有多个物理类标准名时，
   `resolution_status` 设为 `ambiguous_concepts`，按相关性返回 Top-3，并生成一句温和简短的
   `clarification_question`（例：二楼有物理（声光电）、物理（热）、物理工作室，请问您要去哪一个？）。
   注意：同一个标准名对应多个物理房间（如 3 间领导办公）时，`resolution_status` 仍为
   `resolved_unique_concept`，不要自行反问，交给下游按方位反问。
3. 多轮消歧：若提供【当前待澄清会话状态】，结合用户的简短回复（「第一个」「热学那个」「最近的」）
   锁定目标，`confidence` 设为 1.0，`match_type` 设为 `contextual_selection`；
   已锁定具体房间时在候选里填 `node_id`。
4. 楼内没有该设施时：`resolution_status` 设为 `not_found_in_building`；若有词典外的近似设施可给
   `related_fallback` 候选；否则 `top_candidates` 留空。

### 三、实时位置与环境直答规则（单次调用）

若提供【实时自我中心空间上下文】，且 `intent_type` 为 `query_where_am_i` 或 `check_hazard_or_door`，
请直接依据上下文在 `direct_response_speech` 字段生成最终播报文字：
- 严禁播报 X/Y 坐标或拓扑编号（如 F1-TD-0001），严禁编造上下文中不存在的设施；
- 必须使用时钟方向（12 点钟为正前、9 点钟为正左、3 点钟为正右）和米数或步数；
- 安全与门体前置：前方有风险点、独立柱子、常闭防火门时必须明确提醒；
  无摆弧门洞可提示「直接穿过无需推门」；
- 字数：静止 40~65 字（所在区域 + 盲道状态 + 前后左右关键参照物）；
  行走时 20 字以内（只给最核心的前方指引与安全预警）。

### 四、本楼无障碍事实（服务端已知，禁止臆测）

本楼施工图无无障碍设计专篇：**未铺设盲道、无坡道、地面为普通材质**。
因此上下文里的 `on_tactile_paving=false` 只表示用户当前不在盲道上，
播报时必须说「本楼未铺设盲道」，**禁止**暗示附近有盲道或建议"沿盲道行走"。

### 五、意图类型

query_where_am_i（我在哪/周围有什么）、navigate_to_destination（去某地）、
query_route_progress（行进中问剩余距离/下一步）、check_hazard_or_door（问门/风险/好不好走）、
navigation_control（暂停/取消/重播）、out_of_scope（与室内导航无关）。

若楼内没有用户所说的设施，但下方「近似设施推荐」给出了映射，必须返回 `related_fallback` 候选
（confidence ≥ 0.4）并生成确认反问，不要留空候选。
"""


def compact_dictionary_text(dictionary: PoiDictionary) -> str:
    """词典压缩文本：标准名 | 楼层 | 公共设施 | 别名。按标准名排序保证前缀稳定。"""
    lines = ["标准名 | 楼层 | 公共设施 | 口语别名"]
    for name in sorted(dictionary.pois):
        entry = dictionary.pois[name]
        floors = "/".join(sorted({instance.floor for instance in entry.instances}))
        aliases = "、".join(entry.aliases[:8]) or "-"
        lines.append(
            f"{entry.standard_name} | {floors} | {'是' if entry.is_public_utility else '否'} | {aliases}"
        )
    return "\n".join(lines)


def compact_related_fallbacks(dictionary: PoiDictionary) -> str:
    """近似设施映射（人工配置）：楼内没有时给用户的替代选项。"""
    return "、".join(
        f"{spoken}→{target}" for spoken, target in sorted(dictionary.related_fallbacks.items())
    ) or "（无）"


def prompt_cache_key(dictionary: PoiDictionary) -> str:
    return f"voice-agent-intent-{dictionary.map_version}"


def intent_system_prompt(dictionary: PoiDictionary) -> str:
    """system 静态前缀：词典在最前，随后规则、近似设施与 Schema。"""
    schema_text = json.dumps(
        IntentParseResult.model_json_schema(), ensure_ascii=False, separators=(",", ":")
    )
    return (
        f"{DICT_HEADER}\n{compact_dictionary_text(dictionary)}\n\n"
        + _ROLE_AND_RULES.format(building=dictionary.building_name)
        + "\n\n### 六、近似设施推荐（人工配置，仅当楼内无此设施）\n"
        + compact_related_fallbacks(dictionary)
        + "\n\n### 七、输出 Schema（字段与取值必须严格遵守）\n"
        + schema_text
    )


def build_intent_messages(
    dictionary: PoiDictionary,
    user_text: str,
    spatial_context: UserSpatialContext | None = None,
    pending: Sequence[dict] = (),
) -> tuple[list[dict], str]:
    """构造 messages；返回（messages, 缓存 key）。缓存前缀只含静态内容。"""
    messages = [{"role": "system", "content": intent_system_prompt(dictionary)}]
    blocks: list[str] = []
    if spatial_context is not None:
        blocks.append(
            "【实时自我中心空间上下文】\n"
            + spatial_context.model_dump_json(exclude_none=False)
        )
    if pending:
        blocks.append(
            "【当前待澄清会话状态】\n" + json.dumps(list(pending), ensure_ascii=False)
        )
    blocks.append("【用户语音输入】\n" + user_text)
    messages.append({"role": "user", "content": "\n\n".join(blocks)})
    return messages, prompt_cache_key(dictionary)


def intent_json_schema() -> dict:
    return IntentParseResult.model_json_schema()
