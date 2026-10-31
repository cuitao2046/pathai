# 29-LLM 智能语义交互（第一阶段）：接口定义与提示词

> 版本：v1.0（2026-10-29）  
> 关联：`docs/24`（API-01..09）、`docs/26`（语音 Agent 规格）、`specs/voice-agent-semantic-nav/spec.md`（REQ-VOICE-07）
> 实现：`backend/pathai_backend/semantic_intent.py`、`backend/pathai_backend/nav_text.py`、
> `src/voice_agent/config/prompts.py`

---

## 1. 范围与边界

本阶段**只升级“听懂用户”与“把路线说人话”两件事**，不动语音链路本身：

| 用例 | 输入示例 | 大模型职责 | 输出去向 |
|---|---|---|---|
| UC1 当前位置查询 | “我在哪里”“我在什么地方” | 结合定位与空间上下文生成直答 | `responseText` → 现有 TTS |
| UC2 路线规划（目的地理解） | “我要去卫生间”“带我去拿药” | 语义解析到词典标准名 → 具体地点 | `destination` → 现有路线规划 |
| UC3 导航文案生成 | 路线规划结果 `segments` | 批量改写为视障友好提示语 | `segments[].text` → 现有节点播报 |

**不做**：竞速策略、SSE 分句、ASR/TTS 改造、客户端改造、节点实时调用模型、多轮会话持久化。
导航播报的**触发节点、时机、优先级、TTS 模块**全部保持现状。

---

## 2. API-04：`POST /api/v1/intents/parse`

### 2.1 请求（新增可选 `location`）

```json
{
  "siteId": "school-building-01",
  "mapVersion": "latest",
  "text": "我要去卫生间",
  "locale": "zh-CN",
  "location": {
    "x": -30.2,
    "y": -84.6,
    "floorId": "1",
    "confidence": 0.92,
    "headingDeg": 18.5
  }
}
```

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `location.x` / `location.y` | number | 是（提供 location 时） | 局部米制坐标 |
| `location.floorId` | string \| null | 否 | 内部楼层键 `"1"/"2"`；缺失时不能做就近选择 |
| `location.confidence` | number 0..1 | 是 | 定位置信度；仅用于上下文，不做门禁 |
| `location.headingDeg` | number [0,360) | 否 | 朝向；缺失按 0°（正北）处理，建议客户端携带 |

`location` 整体可选：不传时接口行为与旧版完全一致（规则解析）。
**控制类指令**（结束/取消/重播）即使带 location 也不会触发大模型。

### 2.2 触发规则（只在“规则不灵”时调用）

| 规则解析结果 | 是否调用 LLM |
|---|---|
| 唯一匹配目的地（`needsClarification=false`，如“带我去资源教室”） | 否 |
| 控制类指令（结束/取消/重播） | 否 |
| `needsClarification=true`（未识别意图、未匹配地点、多候选） | **是** |
| `intent=query_location`（UC1） | **是**（无 location 则直接沿用模板，不调用） |

### 2.3 响应（契约不变）

```json
{
  "intentId": "intent_9f1c...",
  "intent": "start_navigation",
  "confidence": 0.86,
  "needsClarification": false,
  "destination": { "placeId": "F1-RM-0002", "name": "女卫生间", "floorId": "1" },
  "candidates": [],
  "responseText": "准备导航到女卫生间，请确认。"
}
```

映射关系：

| 来源 | 映射 |
|---|---|
| LLM `intent_type=navigate_to_destination` | `intent=start_navigation` |
| LLM `intent_type=query_where_am_i` | `intent=query_location` |
| 其他 LLM 意图（进度/门/控制/闲聊） | 不接管，返回规则结果 |
| LLM `top_candidates[].standard_name` | 经词典展开为物理实例 → 发布包 `placeId` → `PlaceCandidate` |
| 唯一目的地 | `needsClarification=false`、`destination` 非空、`candidates=[]` |
| 多候选/未找到 | `needsClarification=true`、`destination=null`、`candidates≤5` |
| `responseText` | 唯一：`准备导航到{name}，请确认。`；澄清：LLM 反问句优先，否则候选列表句；未找到：LLM 反问句或“楼内没有找到这个地点，请换个说法。” |

### 2.4 类别型目的地（“卫生间”）

1. 用户明确说“男/女” → LLM 直接给唯一标准名。
2. 未说明性别且有同层定位 → 取**同层最近**实例；
   若最近与次近差值 `< 3m`（相邻门），返回候选反问，避免猜错。
3. 无定位或同层无实例 → 返回候选反问（`候选≤5`，按距离/楼层排序）。
4. 已锁定具体房间（多轮“第一个”）→ 候选可携带 `node_id`，跳过实例展开。

### 2.5 降级矩阵（绝不新增错误码）

| 情况 | 行为 |
|---|---|
| 未配置 `PATHAI_LLM_*` | 跳过语义层，返回规则结果 |
| 词典 `map_version` ≠ 请求地图版本 | 跳过语义层（避免旧词典答新地图） |
| 超时 / HTTP 错误 / 非法 JSON / 词典外候选 | 捕获异常 → 返回 `None` → 规则结果 |
| UC1 缺 location / 无法构建上下文 | 不硬答，沿用“请稍等，正在查询您的当前位置。” |

> 语义层是**附加能力**：任何失败都必须让原有链路照常工作。

### 2.6 时延

同步调用，仅在触发时发生。实测（火山方舟 `deepseek-v4-1-flash`）：短句 1.4~2.5s，
长上下文位置直答 5.1~7.0s。默认超时 15s。本阶段不做竞速。

---

## 3. API-06 行为变更：导航文案改写

### 3.1 时机

`POST /api/v1/nav/sessions` 创建会话时，路线规划完成后**一次性批量改写** `route.segments[].text`，
改写结果随 `route_json` 一起持久化（幂等键命中时返回同一份改写结果）。
不在节点到达时调用模型，节点播报零额外等待。

### 3.2 契约不变

| 字段 | 是否变化 |
|---|---|
| `instructionId` / `instructionSequence` | 不变 |
| `action`（直行/左转/右转/进电梯/出电梯/到达） | 不变 |
| `distanceM` / `priority` / `trigger.nodeId` / `expiresAt` | 不变 |
| `polyline` / `distanceM`（整条路线） | 不变 |
| `riskNotices` / `safeForBlind` / `fallbackType` | 不变 |
| `text` | **替换为改写文案** |

客户端播报机制（按 `trigger` 到点播放、TTS 接口）完全不变。

### 3.3 校验与回退

改写输出必须同时满足：

1. `instructionId` 全覆盖且不多不少；
2. 每条 `4 ≤ 字数 ≤ 40`；
3. 原 `text` 中的**数字全部保留**（米数/楼层）；
4. 不含坐标、节点/指令编号。

任一不满足 → **整条路线回退原文**（不部分替换），并记录告警。
未配置模型、词典版本不一致、超时、非法 JSON 同样回退原文。

### 3.4 示例

原文：

```
ins_1 直行   "请沿当前通道直行 10 米。"
ins_2 左转   "前方路口请左转。"        （trigger: F1-TR-0002）
ins_3 到达   "已到达目的地附近，请留意周围环境。"
```

改写后（`action`/`distance`/`trigger` 原样保留）：

```
ins_1 "沿通道直行 10 米。"
ins_2 "前方路口左转。"
ins_3 "已到目的地附近，请留意脚下。"
```

---

## 4. 提示词定义

### 4.1 意图解析（UC1 / UC2）

代码：`intent_system_prompt()`、`build_intent_messages()`（`src/voice_agent/config/prompts.py`）。

**system 静态前缀（可缓存，逐请求不变）**，顺序固定：

```
### 一、建筑可选目的地标准词典（静态缓存区，禁止改写、禁止捏造词典外名称）
<标准名/别名/楼层/区域/实例>
### 二、角色与语义对齐规则
<同义词与倒装、类别歧义、多轮消歧、楼内没有该设施>
### 三、实时位置与环境直答规则（单次调用）
<时钟方向、米/步、安全与门体前置、静止 40~65 字 / 行走 ≤20 字>
### 四、本楼无障碍事实（服务端已知，禁止臆测）
<未铺设盲道、无坡道、普通地面；禁止暗示附近有盲道>
### 五、意图类型
query_where_am_i / navigate_to_destination / query_route_progress /
check_hazard_or_door / navigation_control / out_of_scope
### 六、近似设施推荐（人工配置，仅当楼内无此设施）
食堂→饮水、小卖部→饮水、超市→饮水
### 七、输出 Schema（字段与取值必须严格遵守）
<IntentParseResult JSON Schema>
```

**user 消息（逐请求，不进入缓存）**：

```
【实时自我中心空间上下文】<UserSpatialContext JSON>   ← 仅带 location 时
【当前待澄清会话状态】<pending>                        ← 本阶段为空
【用户语音输入】<text>
```

输出契约（`IntentParseResult`，`extra=forbid`）：

```json
{
  "intent_type": "navigate_to_destination",
  "raw_target_mention": "卫生间",
  "resolution_status": "ambiguous_concepts",
  "top_candidates": [
    {"standard_name": "女卫生间", "floor": "1F", "match_type": "exact_or_synonym",
     "confidence": 0.8, "reasoning": "类别词匹配", "node_id": null}
  ],
  "clarification_question": "请问去男卫生间还是女卫生间？",
  "direct_response_speech": null
}
```

关键约束（节选，完整文本在代码中）：

- `standard_name` 必须严格等于词典标准名，不得捏造；
- 同一个标准名对应多个物理房间时**不反问**，交给下游按方位/距离处理；
- 位置直答不得播报坐标/编号，必须使用时钟方向与米/步；
- 本楼未铺设盲道，禁止暗示“沿盲道走”。

缓存：key = `voice-agent-intent-{map_version}`，地图换版即换缓存域。

### 4.2 导航文案改写（UC3）

代码：`navigation_system_prompt()`、`build_navigation_messages()`、`navigation_json_schema()`。

**system（静态）**：

```
你是「{building}」室内无障碍导航的播报文案改写器，服务对象是视障人士。
输入是已经规划好的导航指令序列（动作、距离、触发节点均已确定），
你的任务是只改写每条指令的 text，让它更适合边走边听。

硬约束：
1. 不得改变动作语义：左转仍是左转，进电梯仍是进电梯，到达仍是到达。
2. 不得改变或省略原 text 中的数字（米数/楼层），数字必须原样保留。
3. 不得新增输入中不存在的地点、房间名、参照物、风险点。
4. 不出现坐标、节点编号、指令编号（如 F1-TD-0001、ins_3）。
5. 每条不超过 30 个汉字，一句话；能省则省，行走中听不清长句。
6. 安全优先：含门/风险/电梯的指令，先提醒注意，再给动作与距离。
7. 口语化、第二人称，不使用「稍后」「立即」等与播报时机冲突的词。
```

**user（逐请求）**：

```json
{
  "destination": "女卫生间",
  "risk_notices": ["路线包含贴墙引导段，未经盲道验证，请谨慎通行。"],
  "instructions": [
    {"instructionId": "ins_1", "instructionSequence": 1, "action": "continue",
     "text": "请沿当前通道直行 10 米。", "distanceM": 10.0,
     "priority": "normal", "triggerType": "immediate"}
  ]
}
```

**输出**：

```json
{
  "instructions": [
    {"instructionId": "ins_1", "text": "沿通道直行 10 米。"}
  ]
}
```

缓存：key = `voice-agent-nav-text-{map_version}`。
代码侧再做一次严格校验（§3.3），模型输出不合格不会进入路线。

---

## 5. 配置与开关

| 环境变量 | 说明 |
|---|---|
| `PATHAI_LLM_BASE_URL` | OpenAI 兼容地址（如 `https://ark.cn-beijing.volces.com/api/v3`） |
| `PATHAI_LLM_API_KEY` | 密钥；仅存本地环境变量，禁止入库/日志 |
| `PATHAI_LLM_MODEL` | 模型名 |
| `PATHAI_LLM_TIMEOUT_S` | 默认 15 |
| `PATHAI_LLM_STRUCTURED_MODE` | `json_object`（默认）或 `json_schema` |

三项（base/key/model）缺一即视为未启用：语义层与文案改写自动跳过，服务照常启动。
词典版本必须等于当前请求的地图版本，否则语义层跳过（导航文案改写同样跳过）。

---

## 6. 验收与测试

| 用例 | 期望 |
|---|---|
| UC1 “我在什么地方” + location | `intent=query_location`，`responseText` 为 LLM 直答（含区域/盲道/门），无坐标 |
| UC1 “我在哪” + 无 location | 不调用 LLM，沿用“正在查询”模板 |
| UC2 “我要去卫生间” + 同层定位 | 取同层最近实例，`needsClarification=false` |
| UC2 “我要去卫生间” + 两实例相距 <3m | `needsClarification=true`，返回候选反问 |
| UC2 “我要去卫生间” 无定位 | 返回候选反问 |
| UC2 “我要去拿药” | 规则未命中 → LLM → `卫生室` 唯一实例 |
| UC3 创建会话 | `segments[].text` 被改写；`action/distanceM/trigger/priority` 不变 |
| UC3 模型输出缺 ID / 丢数字 / 超时 | 整条路线回退原文 |
| 未配置密钥 | 全部用例退化为现有规则行为，无报错 |

自动化：`backend/tests/test_semantic_intent.py`、`backend/tests/test_nav_text.py`；
LLM 一律注入假实现，禁止真实网络。

---

## 7. 后续（不在本阶段）

- P5：`/api/v1/voice/turn` + SSE 分句播报，模板先出声与 LLM 竞速，压低 TTFA；
- 客户端接入新接口、按句 TTS、澄清追问交互；
- 多轮会话状态（pending）持久化与跨请求消歧。
