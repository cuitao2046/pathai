# SPEC · 基于空间语义的智能导航规划（M2 数据基座 + M3 LLM 解析）

> 版本：0.2.0（2026-10-28）
> 上游依据：`docs/26-语音交互Agent技术规格.md`、`.trae/documents/voice-agent-semantic-nav.md`
> 本版范围：**M2（P0~P3）+ M3（P4 LLM 接入）**——地图数据口径、POI 词典、空间语义转换、
> 规则版解析与四级消歧、LLM 意图解析与降级。
> **不含** 新 API/SSE 流式播报、客户端改造（P5~P6，下一版本）。

---

## 1. 背景与目标

规格 `docs/26` 定义了「空间语义 + 大模型」的语音导航架构。当前系统只有规则关键词意图解析与结构化导航指令，缺少三块地基：

1. 机器可用、带版本戳的 **POI 语义词典**；
2. 以用户身体为原点的 **时钟方位/步数** 空间描述；
3. 能把「同名多处 / 类别多义 / 楼内没有」变成确定动作的 **消歧决策矩阵**。

本版完成这三块地基，并用**真实地图包**验证；LLM 作为后续阶段的「解析器实现」接入同一套 Schema 与决策矩阵。

---

## 2. 需求

### REQ-VOICE-01 POI 语义词典（派生数据）

- 唯一事实源是**已发布地图包**（`backend/maps_data/<site>/<version>.json`）。词典只能由导出脚本生成，禁止手工编辑实例、`node_id`、楼层。
- 人工标注（口语别名、`is_public_utility`、`zone_hint`、类别别名、弱相关推荐）统一维护在 `src/voice_agent/config/poi_alias_overrides.json`；导出时按 `standard_name` 合并，**重导出不得丢失**。
- 输出 `src/voice_agent/config/poi_semantic_dict.json`，携带 `map_version`（= 发布包 `mapVersion`，如 `2026-10-28.2`）与 `exported_at`。
- 收录口径：
  - `pois`：**可导航目的地**（地图包 `places` 全集，当前 55 个），按 `standard_name`（= `places[].name`）聚合，每项含 `instances[]`。
  - `non_navigable_rooms`：全部命名房间中不可作为目的地的部分（当前 81 个），带 `reason`（`not_destination` / `unreachable` / `no_room_node`）。
  - 两者合计 = 全部命名房间 136（1F 81 / 2F 55）。
- 校验（不通过则非零退出）：`node_id`/`place_id` 全部在包内、可导航数 = 55、同名聚合无异常、`map_version` 与发布包一致。
- 运行时一致性：`DictionaryStore` 加载时校验 `map_version`；与当前发布版本不一致时抛 `DICTIONARY_STALE`，调用方拒绝消歧。

### REQ-VOICE-02 自我中心空间语义转换

- 坐标 `(x,y)` + 朝向 `heading_deg` → **1~12 点钟方向**（12 = 正前，3 = 正右，6 = 正后，9 = 正左），严格按 `docs/26 §4.1` 公式；扇区边界取「±15° 闭开区间」，`clock==0 → 12`。
- 步数：`round(distance_m / 0.6)`（1 步 ≈ 0.6m）。
- `UserSpatialContext` 字段对齐 `docs/26 §3.2`，例外见 §5-D9：内部楼层键使用发布包真实键 `"1"/"2"`。
- 周边要素（半径 8m，按距离升序，最多 8 条）：门（`swing`/`opening`/`fire`常开/`fire`常闭）、电梯、楼梯、风险点、柱子、坡道。
- 区域分类：`corridor` / `lobby` / `elevator_hall` / `stair_hall` / `room` / `activity_zone` / `entrance`；区域名来自所在语义房间 `label`（点面包含判定，未命中取最近走廊）。
- 朝向对齐：`heading_offset_deg` 配置（默认 0，标注为**待现场标定**）。

### REQ-VOICE-03 规则版语义解析（LLM 未接入期的解析器；同时是 LLM 失败时的降级通道）

- 输出 `IntentParseResult`（`docs/26 §3.3`）：`intent_type`（6 类）、`resolution_status`、Top-3 候选（`match_type`/`confidence`/`reasoning`）、`clarification_question`。
- 匹配优先级：**最长别名命中** > 类别别名 > 弱相关推荐；`standard_name` 必须∈词典。
- `resolution_status` 语义：同一 `standard_name`（哪怕多物理实例）= `resolved_unique_concept`；命中多个不同标准名 = `ambiguous_concepts`；无命中 = `not_found_in_building`。
- 多轮指代：命中会话待澄清候选（「第一个」「热学那个」「最近的」）→ `contextual_selection`，`confidence=1.0`。
- 控制类/查询类意图（`query_where_am_i` / `check_hazard_or_door` / `navigation_control` / `out_of_scope`）用关键词表判定，不解析目的地。

### REQ-VOICE-04 四级消歧决策矩阵

严格按 `docs/26 §4.2` 顺序与阈值（0.50 / 0.35 / 0.15 分差）：

| 分支 | 条件 | 动作 |
|---|---|---|
| 4 | 无候选 / `not_found_in_building` / top1.confidence<0.50 | 有 `related_fallback≥0.35` → 挂起候选并反问；否则拒识话术 |
| 3A | `ambiguous_concepts` 或 top1-top2<0.15 | 挂起 Top-3 并反问 |
| 1 | 该 `standard_name` 唯一物理实例 | `START_NAVIGATION` |
| 2 | `is_public_utility` 且多实例 | 按盲模式通行成本就近自动选，不打扰 |
| 3B | 非公共设施且多实例 | 按 `zone_hint` 列举反问，挂起全部实例 |

- 就近择优：逐实例调用盲模式规划；`成本 = 路线距离 + (异层 ? 15m 加成 : 0)`；全部不可达 → 降级为 3B 反问。
- 会话状态：待澄清候选进程内 TTL 存储（180s），过期清空（D4）。

### REQ-VOICE-05 地图包 `.10` 数据口径

- 新发布包携带 `floors[].accessibility`（`riskNodes`/`ramps`/`tactilePaths`/`groundMaterialChanges`/`surveyRequired`）为 extra 字段，客户端忽略。
- **防火门状态回填**：以 `geometry.doors[].properties.isNormallyOpen` 为准，经 `topology.nodes[].sourceDoorIds` 关联写入门节点；多门共享节点取「任一常开即常开」。
- 顶部 `accessibilityDefaults` 声明人工确认的建筑级事实：无障碍专篇缺失、无盲道、无坡道、地面为正常材质。
- 发布前必须断言：**55 个目的地盲模式全部可达**；若因常闭防火门导致任一目的地不可达，发布失败。

### REQ-VOICE-06 LLM 意图解析与降级（M3）

- **接入方式**：OpenAI 兼容 `/chat/completions`（SSE 流式聚合），配置项 `PATHAI_LLM_BASE_URL` /
  `PATHAI_LLM_API_KEY` / `PATHAI_LLM_MODEL` / `PATHAI_LLM_TIMEOUT_S`（默认 15s；
  实测火山方舟首 token 1.5~5.1s，8s 会把长上下文直答误判为超时）；
  密钥仅存后端环境变量，禁止入库、禁止打日志。
- **结构化输出**：system 提示词包含 JSON Schema 约束；`response_format` 默认 `json_object`
  （兼容 DeepSeek/Qwen），OpenAI 系可配置 `json_schema`（决策 D12）。
- **静态前缀**：POI 词典压缩文本置于 system 消息最前，缓存 key 含 `map_version`；
  词典版本变化即视为新的前缀缓存域。
- **解析优先级**：精确快路径正则 → LLM → 结构化校验（`standard_name` ∈ 词典、枚举/置信度合法、
  Top-3 截断）→ 任一步失败回落规则解析器，并返回 `degraded=true`。
- **快路径**（`docs/26 §6.1`）：`^(我在哪儿?|现在什么位置|周围有什么)$` 与
  `^(再说一遍|重复|没听清)$` 不调用 LLM；前者用空间上下文模板直接播报（`source=fast_path`）。
- **直答约束**：`query_where_am_i` / `check_hazard_or_door` 注入 `UserSpatialContext` 后由 LLM 产出
  `direct_response_speech`；静止 40~65 字、行走 ≤20 字，超限按句读截断（决策 D14）。
- **可观测**：每次 LLM 调用记录 `ttft_ms`（首 token）与总耗时，供 TTFA 验收使用。

### REQ-VOICE-07 第一阶段接入（M4，2026-10-29）

- **范围**：只做智能语义理解接入；不做竞速/SSE/客户端改造，导航播报时机与 TTS 模块不变。
- **API-04 扩展**：`IntentParseRequest.location`（x/y/floorId/confidence/headingDeg）可选；
  仅在规则解析 `needsClarification=true` 或 `intent=query_location` 时调用 LLM，其余沿用规则。
- **目的地映射**：LLM 标准名 → 词典实例 → 发布包 `placeId`；类别型（如「卫生间」）优先同层最近，
  最近差值 `< 3m` 或缺少定位时返回候选反问。
- **位置直答**：必须携带定位才调用 LLM；无定位沿用「正在查询」模板，禁止无上下文编造。
- **导航文案**：API-06 创建会话时一次性改写 `segments[].text`；动作、距离、触发器、优先级不变
  （详见 `docs/29`）；校验 ID 全覆盖、4~40 字、原文字数字保留，任一失败整体回退原文。
- **降级**：未配置密钥 / 词典版本不一致 / 超时 / 非法输出 → 返回 `None` → 沿用规则结果，不新增错误码。

---

## 3. 验收用例（真实地图）

| 用例 | 输入 | 期望 |
|---|---|---|
| A | 医务室 | → `卫生室`(1F) 唯一实例 → `START_NAVIGATION` |
| B | 男厕所 | → `男卫生间` 6 实例、公共设施 → 就近自动导航，无反问 |
| C1 | 物理实验室 | → `ambiguous_concepts`（物理（声光电）/物理（热）/物理工作室）→ 反问 → 「热学那个」→ 命中 |
| C2 | 校长办公室 | → `领导办公` 3 实例 → 区域反问 → 「第一个」→ 命中 |
| D | 我现在在哪儿 | → `query_where_am_i`，携带空间上下文 |
| E | 食堂 | → `not_found_in_building` + 弱相关推荐（饮水）→ 反问 |
| F | 时钟边界 | 正前/正右/正后/正左 + 359.9°/0.1° 回绕 |
| G | LLM 正常（Case A~D） | `source=llm`，输出通过 Schema 与词典校验 |
| H | LLM 超时 / HTTP 500 / 非法 JSON | 回落规则解析，`degraded=true`，不抛异常 |
| I | 未配置密钥 | 规则解析，`degraded=true`，不阻塞调用方 |
| J | 快路径「我在哪儿 / 再说一遍」 | LLM 调用次数 = 0，`source=fast_path` |
| K | 直答超长 | 静止 ≤65 字、行走 ≤20 字（按句读截断） |

---

## 4. 测试要求

- `src/voice_agent` 单测（仓库根 `python -m unittest discover -s tests`）。
- 词典导出与地图包契约测试（`pytest backend/tests`，固定仓库根 cwd）。
- 第一阶段接入测试：`backend/tests/test_semantic_intent.py`（触发/映射/就近/降级）与
  `backend/tests/test_nav_text.py`（改写成功、ID 缺失、数字丢失、超时回退）。
- LLM 测试一律用注入的假 transport，**禁止真实网络**；真实密钥只做人工冒烟。
- **mock 数据必须是真实地图包**（当前 `2026-10-28.2`），禁止虚构房间名。

---

## 5. §10 决策记录

| 编号 | 决策 | 理由 |
|---|---|---|
| D1 | 代码落点 `src/voice_agent/`，由后端以 `src.voice_agent` 导入；`src/` 不引入 FastAPI 依赖 | 与 `src.topology` 复用方式一致，纯逻辑可单测 |
| D2 | 本版不接 LLM；规则解析器先落地 | 用户确认 M2 范围；规则器同时作为 LLM 失败降级通道 |
| D3 | 流式播报后续用「SSE 文本 + 客户端逐句 TTS」，后端不代理 TTS | 现有 TTS 在客户端、密钥零下发 |
| D4 | 待澄清会话状态用进程内 TTL（180s） | 单实例 Demo 足够，不新增表 |
| D5 | 词典 `pois` 只放 55 个可导航目的地，另有 `non_navigable_rooms` 81 条 | 兼顾「全部命名房间收录」与「不可达空间不得作为目的地」 |
| D6 | 词典 `map_version` 用发布包真实版本号，不用数据世代名 `v9` | 需与 `GET /map` 的 `mapVersion` 比对 |
| D7 | 后续 API 采用 `/api/v1/voice/turn`，保留 `/intents/parse` 兼容 | 不破坏已上线契约 |
| D8 | 防火门**不采用「全部常闭」假设**，改用 geometry 真实状态；缺失时才默认常闭并告警 | 全闭会使 3 个房间盲不可达（见 §6） |
| D9 | 内部楼层键用发布包 `"1"/"2"`，播报层再转「一楼/二楼」 | 避免与 `floors` 键二次映射 |
| D10 | 无障碍图层以 extra 字段进包，不做 schema 硬约束 | 客户端忽略 extra；后续 admin 编辑可扩展 |
| D11 | `DestinationCandidate` 增加可选 `node_id`（规格 §3.3 之外） | 多轮澄清「第几间」已锁定物理实例时，引擎需跳过实例展开直接规划 |
| D12 | `response_format` 默认 `json_object` + 提示词内嵌 Schema，可配 `json_schema` | DeepSeek/Qwen 普遍只支持 json_object；严格校验由本地 Pydantic 兜底 |
| D13 | 快路径「我在哪」用模板生成播报，不走 LLM | 目标 TTFA ≤350ms，且该场景无需语义理解 |
| D14 | LLM 直答超长时按句读截断（静止 65 / 行走 20 字） | 行走中长播报会遮挡近距安全提示，硬上限必须由代码保证 |
| D15 | LLM 客户端用 `httpx` + 可注入 transport | 单测零网络；SSE 聚合同时产出 TTFT 指标 |
| D16 | LLM 失败/超时后，位置与环境问答仍用模板播报兜底 | 实测长上下文直答首 token 可达 7s，降级不能变成"没声音" |
| D17 | 默认超时 15s；P5 必须以「模板先出声 + LLM 竞速」保住 TTFA | 云 LLM 首 token 实测 1.4~7.0s，无法直接满足 800ms 目标 |
| D18 | 语义兜底只在规则失败/歧义/位置直答时触发，不全量调用 | 控制类短命令与唯一匹配无需大模型，省时延与成本 |
| D19 | 类别实例按同层最近选一个，最近差值 <3m 则反问 | 卫生间/办公室相邻时猜错代价高，安全优先 |
| D20 | 导航文案在创建会话时一次性批量改写，失败整体回退原文 | 避免节点实时调模型造成播报卡顿；契约与播报时机不变 |

---

## 6. 数据缺口与评估结论

| 项 | 现状 | 处理 |
|---|---|---|
| 盲道 `tactilePaths` | 空（施工图无无障碍专篇），经确认为**本楼未铺设** | `on_tactile_paving=false`；播报不得声称有盲道 |
| 坡道 `ramps` | 空，确认**无坡道** | `nearby_elements` 不出现 `ramp` |
| 地面材质 | 空，确认**均为正常材质** | `floor_surface="普通地面"` |
| 防火门常开/常闭 | 拓扑门节点全为 null；`geometry.doors` 有真实值 | 回填：25 常开 / 66 常闭 |
| 风险点 | 源图层有（1F 73 / 2F 48），发布包未携带 | 随 `.10` 进包 |

**防火门评估结论（2026-10-28，真实包实测）**：
- 现状（门状态缺失、按可通过处理）：55/55 目的地盲模式可达。
- 若强制全部防火门常闭：**52/55**，`档案`(1F)、`配电室`(1F)、`仪器室`(2F) 变为盲不可达。
- 使用 geometry 真实状态（66 闭 / 25 开）：**55/55** 可达。
→ 因此采用真实状态回填（D8），并把「全闭导致目的地不可达」纳入发布前断言。

后续：管理后台可直接编辑门状态，无需重跑几何管线。
