# 

### **本设计文档定义了基于大语言模型（LLM）的视障室内无障碍导航语音交互系统架构，涵盖语义词典预注入、Top\-3 目的地结构化消歧、自我中心空间坐标转换及流式语音播报规范，可直接作为 Coding Agent 的开发技术规格说明书（Spec）。**



---



### **1\. 系统背景与架构总览**



#### **1\.1 业务背景与输入数据基座**

本模块服务于\*\*“初中学部 1\# 教学楼（v9 米制坐标系）”\*\*室内无障碍导航系统。底层地图已具备完善的几何与拓扑要素：

- **五类拓扑节点**：房间节点（`room`）、门口节点（`doorway`）、走廊交叉口（`intersection`）、跨层设施（`facility`，含 10 部楼梯 `II-Bx-xx#ST` 与 3 部无障碍电梯 `II-0x#EL`）、出入口（`facility_entrance`）。

- **精细门体与通道图层**：普通摆弧门、无摆弧门洞（DK 洞口）、常开防火门、常闭防火门、走道骨架。

- **无障碍与安全图层**：盲道路径、坡道、地面材质、风险点（`!`）、柱子、蓝牙信标部署点（`BK-xx-xxx`，支持三点定位）。

    

#### **1\.2 核心交互与低延迟架构流**

为解决传统语音助手“指令僵硬”与大模型 Agent “二次调用延迟高”的矛盾，系统采用\*\*“实时空间上下文预注入 \+ 结构化工具调用 \+ 业务规则矩阵消歧 \+ 流式分句 TTS”\*\*的混合架构：



```Plain Text
flowchart TD
    A[视障用户语音输入] --> B[ASR 语音转文本]
    I[BLE信标定位 + IMU罗盘朝向 + 加速度计步态] --> C[空间上下文预构建器 Context Builder]
    C -->|预查询半径5m拓扑/风险/盲道| D{快慢路由拦截网关}
    B --> D
    
    D -->|高频确定性指令: 我在哪/重复一遍| E[Fast Path: 跳过意图解析直接组装空间数据]
    D -->|自然语言复杂指令: 去校长室/附近厕所在哪| F[Slow Path: LLM 意图解析与 Top-3 目的地对齐]
    
    G[(v9 地图聚合语义词典<br/>Prompt Caching)] -.->|静态前缀注入| F
    H[(会话状态 Session State<br/>暂存待澄清候选)] <--> F
    
    F --> J[业务逻辑决策矩阵 Disambiguation Engine]
    J -->|分支1: 唯一高置信度实例| K[调用路径规划 API: mode=blind]
    J -->|分支2: 公共设施多实例| L[计算最短通行成本自动选最近节点]
    J -->|分支3: 语义歧义或特定同名房间| M[生成澄清反问并挂起会话]
    J -->|分支4: 拒识或楼内无此设施| N[生成拒识提示与近似设施推荐]
    
    L --> K
    K --> O[空间语义转换器 Egocentric Transformer]
    E --> O
    O --> P[LLM 视障自然语言播报生成]
    M --> Q[SSE 标点分句流式 TTS 合成]
    N --> Q
    P --> Q
    Q --> R[视障用户听觉反馈]
```



---



### **2\. 推荐项目目录结构（供 Coding Agent 创建工程）**



```Plain Text
src/voice_agent/
├── config/
│   ├── poi_semantic_dict.json       # 从当前最新版地图 GeoJSON 自动导出的房间语义词典（含 map_version 戳，禁止手工编辑）
│   ├── poi_alias_overrides.json     # 人工维护的口语别名/设施分类/分区提示标注（以 standard_name 为键，导出时合并）
│   └── prompts.py                   # 系统提示词模板（意图解析 Prompt + 语音播报生成 Prompt）
├── schemas/
│   ├── spatial_context.py           # 自我中心空间上下文、传感器状态数据模型
│   ├── llm_intent.py                # 大模型结构化输出 Schema (Top-3 候选、匹配分档、工具参数)
│   └── navigation_api.py            # 底层地图拓扑查询与路径规划接口数据结构
├── services/
│   ├── spatial_transformer.py       # 几何坐标 (x,y) + 朝向角 -> 时钟方位、步数、门体/风险语义转换
│   ├── intent_parser.py             # LLM 意图解析服务（支持 Context Caching 与快慢路由）
│   ├── disambiguation_engine.py     # Top-3 目的地消歧状态机与四级业务决策矩阵
│   ├── speech_generator.py          # 区分静止/行进态的流式播报文本生成服务
│   └── tts_streamer.py              # 按标点符号切分的低延迟流式 TTS 管道
└── orchestrator.py                  # 语音交互主控制器（串联 ASR -> Agent -> TTS 完整生命周期）
```



---



### **3\. 核心数据字典与接口 Schema 定义**



Coding Agent 需使用 Pydantic（Python）或 Zod（TypeScript）严格实现以下数据契约。



#### **3\.1 地图 POI 语义聚合词典（****`poi_semantic_dict.json`****）**

在系统启动或离线构建阶段，扫描 v9 地图数据，将同名房间按标准名称聚合，并补充口语同义词与设施分类属性（`is_public_utility` 标识是否可按空间就近原则自动消歧，无需反问）：



```JSON
{
  "building_name": "初中学部1#教学楼",
  "map_version": "v9",
  "exported_at": "2026-10-27T00:00:00+08:00",
  "pois": [
    {
      "standard_name": "领导办公",
      "category": "office",
      "is_public_utility": false,
      "instances": [
        {"floor": "1F", "node_id": "F1-TD-0018", "zone_hint": "一楼东侧走廊"},
        {"floor": "1F", "node_id": "F1-TD-0020", "zone_hint": "一楼中段走廊"},
        {"floor": "1F", "node_id": "F1-TD-0021", "zone_hint": "一楼南侧走廊"}
      ],
      "aliases": ["校长办公室", "副校长室", "校领导", "主任办公室", "领导室", "行政办公室"]
    },
    {
      "standard_name": "卫生室",
      "category": "medical",
      "is_public_utility": true,
      "instances": [
        {"floor": "1F", "node_id": "F1-TD-0034", "zone_hint": "一楼西侧"}
      ],
      "aliases": ["医务室", "校医室", "保健室", "看病", "拿药", "医疗室"]
    },
    {
      "standard_name": "传达室",
      "category": "security",
      "is_public_utility": true,
      "instances": [
        {"floor": "1F", "node_id": "F1-TD-0006", "zone_hint": "一楼门厅入口旁"}
      ],
      "aliases": ["保安室", "门卫室", "收发室", "门房", "值班室"]
    },
    {
      "standard_name": "物理（声光电）",
      "category": "classroom_lab",
      "is_public_utility": false,
      "instances": [
        {"floor": "2F", "node_id": "F2-TD-0014", "zone_hint": "二楼北侧"}
      ],
      "aliases": ["声光电物理实验室", "光学实验室", "电学实验室", "物理实验室"]
    },
    {
      "standard_name": "物理（热）",
      "category": "classroom_lab",
      "is_public_utility": false,
      "instances": [
        {"floor": "2F", "node_id": "F2-TD-0018", "zone_hint": "二楼中段"}
      ],
      "aliases": ["热学物理实验室", "热学实验室", "物理实验室"]
    },
    {
      "standard_name": "饮水",
      "category": "service",
      "is_public_utility": true,
      "instances": [
        {"floor": "2F", "node_id": "F2-TD-0005", "zone_hint": "二楼化学教室旁"}
      ],
      "aliases": ["茶水间", "开水间", "饮水处", "接水的地方", "打水处", "直饮水"]
    },
    {
      "standard_name": "男卫生间",
      "category": "restroom",
      "is_public_utility": true,
      "instances": [
        {"floor": "1F", "node_id": "F1-TD-0003", "zone_hint": "一楼北段"},
        {"floor": "1F", "node_id": "F1-TD-0030", "zone_hint": "一楼中段"},
        {"floor": "1F", "node_id": "F1-TD-0036", "zone_hint": "一楼南段"},
        {"floor": "2F", "node_id": "F2-TD-0023", "zone_hint": "二楼中段"},
        {"floor": "2F", "node_id": "F2-TD-0029", "zone_hint": "二楼南段"}
      ],
      "aliases": ["男厕", "男洗手间", "男茅房", "洗手间", "厕所", "卫生间"]
    }
  ]
}
```

*\(注：Coding Agent 在初始化时需将 v9 地图中全部 81\+55 个房间按上述格式完整导出\)*

**词典导出与地图版本同步规则（硬性约束）：**

1. **词典是派生数据，不是源数据**：唯一事实来源是当前最新版本的地图 GeoJSON；`poi_semantic_dict.json` 只能由导出脚本生成，禁止手工编辑实例、`node_id`、楼层等自动字段。
2. **人工标注与自动导出分离**：口语别名（`aliases`）、`is_public_utility`、`zone_hint` 等人工标注统一维护在 `poi_alias_overrides.json`；导出脚本将地图房间与标注文件按 `standard_name` 合并输出。地图更新重新导出时不得丢失人工标注。
3. **版本戳**：输出文件必须携带 `map_version`（与地图版本号一致，如 `v9`）与 `exported_at` 时间戳。
4. **地图版本更新触发同步刷新**：地图发布新版本（如 v9→v10）后，必须重跑导出脚本重新生成词典，并执行一致性校验后才可上线：新版本全部有效命名房间已收录、所有 `node_id` 在新地图拓扑中真实存在、同名聚合与 `is_public_utility` 分类无异常。校验不通过时保留旧版本词典运行并告警，禁止静默切换。
5. **运行时版本校验**：Agent 启动时及每次创建导航会话前，比对词典 `map_version` 与地图服务当前版本（`GET /api/v1/sites/{siteId}/map` 返回的 `mapVersion`）；不一致时拒绝用过期词典执行目的地消歧与路径规划，向用户播报“地图数据更新中，部分目的地查询暂不可用”，并触发重新导出与热加载。
6. **Prompt Caching 失效联动**：词典位于 system 消息前缀缓存区，缓存 key 必须包含 `map_version`；版本刷新后旧缓存前缀自动失效，防止旧词典继续被命中。



#### **3\.2 实时自我中心空间上下文（****`spatial_context.py`****）**

底层空间计算模块必须将米制坐标 `(x, y)` 与手机罗盘航向角 `heading_deg` 转换为以视障用户身体为中心的相对空间描述，严禁向 LLM 直接暴露未经转换的绝对坐标作为唯一定位依据：



```Python
from pydantic import BaseModel, Field
from typing import List, Literal, Optional

class NearbyElement(BaseModel):
    name: str = Field(description="要素或房间名称，如'音乐教室'、'独立柱子'、'II-01#EL无障碍电梯'")
    element_type: Literal["room_door", "open_doorway_dk", "fire_door_normally_open", "fire_door_normally_closed", "elevator", "stairs", "hazard", "pillar", "ramp"]
    clock_direction: int = Field(ge=1, le=12, description="相对用户当前朝向的时钟方向，12为正前方，3为正右方，6为正后方，9为正左方")
    distance_meters: float = Field(description="直线或沿走道距离（米）")
    distance_steps: int = Field(description="折算步数，按 1步 ≈ 0.6米 四舍五入")
    accessibility_note: Optional[str] = Field(default=None, description="特殊无障碍说明，如'常闭防火门需推开'、'无摆弧门洞可直接穿过'、'盲:✗ 视障不建议通行'")

class UserSpatialContext(BaseModel):
    floor: Literal["1F", "2F"]
    current_region_type: Literal["corridor", "lobby", "elevator_hall", "stair_hall", "room", "activity_zone", "entrance"]
    current_region_name: str = Field(description="当前所在区域名称，如'1楼门厅'、'1楼中段走道'")
    motion_state: Literal["stationary", "walking"] = Field(description="由手机IMU加速度计判定的运动状态")
    on_tactile_paving: bool = Field(description="当前是否处于盲道路径覆盖范围内")
    floor_surface: Optional[str] = Field(default=None, description="当前地面材质")
    nearby_elements: List[NearbyElement] = Field(description="周边5~8米内的关键拓扑要素与风险点，按距离升序排列")
```



#### **3\.3 LLM 意图解析与 Top\-3 目的地结构化输出（****`llm_intent.py`****）**

强制大模型使用 Structured Outputs 返回以下 JSON Schema：



```Python
from pydantic import BaseModel, Field
from typing import List, Literal, Optional

class DestinationCandidate(BaseModel):
    standard_name: str = Field(description="必须严格匹配可选目的地词典中的 standard_name")
    floor: Optional[Literal["1F", "2F"]] = Field(default=None, description="用户明确指定或词典唯一对应的楼层，未指定且跨层存在时可为None")
    match_type: Literal[
        "exact_or_synonym",    # 精确匹配或明确同义词（如：医务室 -> 卫生室，校长室 -> 领导办公）
        "category_submatch",   # 类别包含匹配（如：物理实验室 -> 物理（声光电） / 物理（热））
        "related_fallback",    # 弱相关替代推荐（如：找食堂 -> 推荐 饮水）
        "contextual_selection" # 多轮对话中的指代消解命中（如：去第一个 / 热学的那个）
    ]
    confidence: float = Field(ge=0.0, le=1.0, description="语义匹配配置信度评分")
    reasoning: str = Field(description="简短说明匹配依据")

class IntentParseResult(BaseModel):
    intent_type: Literal[
        "query_where_am_i",          # 询问当前位置与周边环境
        "navigate_to_destination",   # 请求前往某目的地
        "query_route_progress",      # 导航行进中询问剩余距离/下一步动作/前方路况
        "check_hazard_or_door",      # 专门询问前方风险点、坡道或门体开关状态
        "navigation_control",        # 暂停、取消、重播上一句、语速调整
        "out_of_scope"               # 闲聊或与室内导航无关的请求
    ]
    raw_target_mention: Optional[str] = Field(default=None, description="用户原话中提及的目的地原始词")
    resolution_status: Optional[Literal[
        "resolved_unique_concept",   # 语义上唯一确定（含唯一标准名，哪怕底层有多个同名物理房间）
        "ambiguous_concepts",        # 存在多个不同标准名的候选（如声光电物理 vs 热学物理）
        "not_found_in_building"      # 楼内无此设施
    ]] = Field(default=None)
    top_candidates: List[DestinationCandidate] = Field(default_factory=list, max_length=3, description="按置信度降序排列的Top-3候选目的地")
    clarification_question: Optional[str] = Field(default=None, description="当需要反问用户澄清时，直接生成温和简短的视障友好反问句")
    direct_response_speech: Optional[str] = Field(default=None, description="当 intent_type 为 query_where_am_i 或 check_hazard_or_door 时，利用预注入的空间上下文直接生成最终播报文本，省去二次LLM调用")
```



---



### **4\. 核心算法与业务逻辑规范**



#### **4\.1 几何坐标到自我中心语义转换算法（****`spatial_transformer.py`****）**

Coding Agent 在实现 `spatial_transformer.py` 时，需严格按照以下数学公式将地图坐标系（x 向右，y 向上）转换为相对于用户朝向的**时钟方向（1\\\~12点钟）**：

1. 设用户当前坐标为 $(x_u, y_u)$，目标拓扑节点或风险点坐标为 $(x_t, y_t)$。

2. 计算目标向量在地图笛卡尔坐标系下的方位角（以正北/Y轴正向为 $0^\circ$，顺时针为正）：

    $\theta_{\text{target}} = \left( \text{atan2}(x_t - x_u, y_t - y_u) \times \frac{180}{\pi} + 360 \right) \bmod 360$

3. 设手机电子罗盘/PDR 航向角在地图坐标系下的对齐角度为 $\theta_{\text{heading}} \in [0, 360)$。计算相对偏角 $\Delta \theta$：

    $\Delta \theta = (\theta_{\text{target}} - \theta_{\text{heading}} + 360) \bmod 360$

4. 映射为时钟刻度（每 $30^\circ$ 为一个钟点，$\pm 15^\circ$ 为扇区边界）：

    $\text{clock} = \left\lfloor \frac{(\Delta \theta + 15) \bmod 360}{30} \right\rfloor$

    若计算结果 $\text{clock} == 0$，则置为 $12$（即 12 点钟方向代表正前方 $[-15^\circ, +15^\circ)$）。

    

#### **4\.2 Top\-3 目的地消歧与四级业务决策矩阵（****`disambiguation_engine.py`****）**

当 `intent_type == "navigate_to_destination"` 时，业务层接收到 `IntentParseResult` 后，禁止仅凭单一数值阈值判断，必须结合 `poi_semantic_dict.json` 中的实例属性执行以下四级状态机：



```Python
def resolve_destination_and_act(parse_result: IntentParseResult, user_ctx: UserSpatialContext, session_state: dict):
    candidates = parse_result.top_candidates
    
    # 分支 4：拒识或楼内不存在该设施
    if not candidates or parse_result.resolution_status == "not_found_in_building" or candidates[0].confidence < 0.50:
        if candidates and candidates[0].match_type == "related_fallback" and candidates[0].confidence >= 0.35:
            # 存在弱相关推荐（例如找食堂 -> 推荐2F饮水处）
            session_state["pending_candidates"] = [candidates[0]]
            return {"action": "TTS_SPEAK", "text": parse_result.clarification_question}
        return {"action": "TTS_SPEAK", "text": f"这栋教学楼内没有找到{parse_result.raw_target_mention or '该地点'}，您可以换个名称试试。"}

    top1 = candidates[0]
    top2 = candidates[1] if len(candidates) > 1 else None

    # 分支 3A：语义概念层存在歧义（Top-1 与 Top-2 分差小于 0.15，或 resolution_status 为 ambiguous_concepts）
    if parse_result.resolution_status == "ambiguous_concepts" or (top2 and (top1.confidence - top2.confidence) < 0.15):
        session_state["pending_candidates"] = candidates[:3]
        return {"action": "TTS_SPEAK", "text": parse_result.clarification_question}

    # 此时 Top-1 在语义概念层已唯一胜出，查询地图字典中该 standard_name 对应的物理房间实例列表
    poi_entry = get_poi_by_standard_name(top1.standard_name, floor_filter=top1.floor)
    instances = poi_entry.instances

    # 分支 1：唯一物理实例 -> 直接规划无障碍路径
    if len(instances) == 1:
        session_state.clear()
        route = plan_accessible_route(start=user_ctx, target_node_id=instances[0]["node_id"], mode="blind")
        return {"action": "START_NAVIGATION", "target": top1.standard_name, "route": route}

    # 存在多个同名物理实例（len(instances) > 1）
    # 分支 2：通用公共设施（男/女卫生间、饮水、无障碍电梯）-> 按空间最短无障碍路径自动消歧，不打扰用户
    if poi_entry.is_public_utility:
        session_state.clear()
        best_instance, best_route = find_nearest_accessible_instance(user_ctx, instances, same_floor_bonus=True)
        return {"action": "START_NAVIGATION", "target": f"{best_instance['floor']}{top1.standard_name}", "route": best_route}

    # 分支 3B：非通用特定房间存在多实例（如1F有3间“领导办公”、4间“音乐教室”）-> 结合空间方位反问用户
    session_state["pending_candidates"] = [
        {"standard_name": top1.standard_name, "node_id": inst["node_id"], "zone_hint": inst["zone_hint"]}
        for inst in instances
    ]
    zone_list_str = "、".join([inst["zone_hint"] for inst in instances])
    clarify_text = f"一楼共有{len(instances)}间{top1.standard_name}，分别位于{zone_list_str}。请问您要去哪一间，还是直接带您去离您最近的一间？"
    return {"action": "TTS_SPEAK", "text": clarify_text}
```



#### **4\.3 跨层无障碍路由硬性约束（调用 v9 拓扑引擎时）**

在 `plan_accessible_route(mode="blind")` 中，Coding Agent 必须确保底层寻路算法（Dijkstra / A\*）满足以下权重规则：

1. **跨层过滤**：v9 地图中 7 条楼梯连接（`II-B1-01#ST` 至 `II-B3-02#ST`）标记为 `盲:✗`，在视障模式下默认设为**极高阻抗或禁行**，优先路由至标记为 `盲:✓` 的 3 部电梯（`II-01#EL`、`II-02#EL`、`II-03#EL`）。仅当用户明确语音要求“走楼梯”时才放开楼梯跨层边。

2. **风险点与门体惩罚项**：途经存在风险点（`!`）的走廊段增加权重惩罚；途经“常闭防火门”需在生成的路径节点序列中附带 `action_required: "push_closed_fire_door"` 标记，供后续语音生成模块提前预警。

    

---



### **5\. Prompt 工程规范（****`prompts.py`****）**



Coding Agent 应将以下两套提示词模板写入配置文件，并将静态词典部分置于 `system` 消息最前端以触发主流大模型 API 的 **Prompt Caching（前缀缓存）**。



#### **5\.1 阶段一：意图解析与空间问答合一 System Prompt**

```Markdown
你是一个专为视障人士设计的室内无障碍导航AI核心引擎，当前服务于“初中学部1#教学楼（v9地图）”。
你的任务是解析视障用户的语音输入，输出严格符合 Schema 的 JSON 数据。

### 一、 建筑可选目的地标准词典（静态缓存区）
{{POI_SEMANTIC_DICT_COMPACT_TEXT}}

### 二、 目的地语义对齐规则
1. 同义词与倒装对齐：用户口语可能与图纸标准名不同（如“校长办公室”对应标准名“领导办公”，“医务室”对应“卫生室”，“保安室”对应“传达室”）。你必须将 `standard_name` 严格映射为词典中存在的标准名称，绝不可捏造词典外的房间名。
2. 类别歧义提取：当用户说“去物理实验室”，而词典中有“物理（声光电）”、“物理（热）”、“物理工作室”时，将 `resolution_status` 设为 `ambiguous_concepts`，按相关性返回 Top-3，并在 `clarification_question` 中生成一句简短自然的反问（如：“二楼有声光电和热学两个物理实验室，请问您要去哪一个？”）。
3. 多轮消歧指代：若【当前待澄清会话状态】不为空，结合用户的简短回复（如“去第一个”、“热学那个”、“离我最近的”）直接锁定目标，将 `confidence` 设为 1.0。

### 三、 实时位置与环境直答规则（单次调用优化）
下方已预注入用户当前的【实时自我中心空间上下文】。
当用户的意图是 `query_where_am_i`（如“我在哪儿”、“周围有什么”）或 `check_hazard_or_door`（如“前面好走吗”、“门开着吗”）时，你**无需调用外部工具**，请直接根据预注入的空间上下文，在 `direct_response_speech` 字段中生成最终播报文字，并严格遵守以下视障播报准则：
- 严禁播报 X/Y 坐标或拓扑编号（如 F1-TD-0001）。
- 必须使用时钟方向（12点钟为正前方，9点钟为正左方，3点钟为正右方）和米数/步数。
- 安全与门体前置：若前方有风险点、独立柱子、坡道或常闭防火门，必须在播报中明确提醒（无摆弧门洞可提示“直接穿过无需推门”）。
- 字数控制：若用户 `motion_state` 为 `stationary`（静止），控制在 40~65 字，提供所在区域+盲道状态+前后左右关键参照物；若为 `walking`（行走中），严格控制在 20 字以内，只给最核心的前方指引与安全预警。
```



#### **5\.2 阶段二：路径规划结果转自然语音播报 System Prompt**

当触发 `START_NAVIGATION` 拿到路径规划 API 返回的拓扑节点序列后，使用此 Prompt（推荐使用高吞吐轻量模型如 Qwen\-Turbo / GPT\-4o\-mini / DeepSeek\-V3 流式调用）生成导航启动播报：



```Markdown
你正在为视障用户播报刚刚规划好的室内无障碍导航路线。
请根据输入的【起点空间状态】与【路径关键节点序列】，生成一段自然、温暖、无冗余的语音指引。

播报结构要求（总字数控制在 50~80 字以内，纯文本无 Markdown 符号）：
1. 目的地确认与楼层定调：若用户原话（如“校长办公室”）与地图标准名（“一楼领导办公室”）不同，自然地在首句完成对齐确认（例：“已为您规划前往一楼领导办公室的路线。”）。
2. 首段动作指引（最关键）：明确告知用户现在应该朝哪个时钟方向迈步、走多少米（多少步）、脚下是否有盲道可循。
3. 首个关键转折或设施预告：预告第一个转弯处、门洞（DK）或无障碍电梯的位置，以及途中是否需要推开常闭防火门或注意避让风险点。不要一次性背诵整条复杂路线的全部转弯，只讲清第一段路怎么走。
```



---



### **6\. 低延迟工程优化规范（供 Coding Agent 实现）**



为确保视障用户在行走过程中的交互安全，端到端首字发声延迟（Time\-To\-First\-Audio, TTFA）必须控制在 $800\text{ms}$** 以内**。Coding Agent 需在代码中落实以下三项机制：



1. **快慢路由网关（Fast/Slow Path Router）**：

在 `intent_parser.py` 入口处设置正则/关键词快速拦截器：

- 若 ASR 文本精确匹配 `^(我在哪儿?|现在什么位置|周围有什么)$`，直接走 **Fast Path**：跳过意图分类 Schema 推理，直接将预构建的 `UserSpatialContext` 送入轻量级语音生成流，TTFA 可压缩至 $350\text{ms}$。

- 若匹配 `^(再说一遍|重复|没听清)$`，直接从内存缓存重放上一条 TTS 音频流（耗时 $<20\text{ms}$）。

2. **标点符号流式切分 TTS 管道（****`tts_streamer.py`****）**：

LLM 生成播报文本时必须开启 `stream=True`。编写异步生成器监听 Token 流，维护一个字符缓冲区 `buffer`：一旦检测到中文断句标点（`[，。！？；]`）且当前 `buffer` 长度 $\ge 8$ 个汉字，立即将首个分句推入 TTS WebSocket 管道合成并下发音频播放，实现“边想边说”。

3. **过期空间播报熔断机制（Stale Response Cancellation）**：

每次语音请求携带时间戳 `request_ts` 与起始坐标 $(x_0, y_0)$。在准备下发 TTS 音频前，校验用户当前最新坐标 $(x_{\text{now}}, y_{\text{now}})$：若用户在等待响应期间已快速移动超过 $3.5$ 米，且播报内容包含高精度的近距避障指令（如“正前方 1 米有柱子”），立即触发距离偏移修正或丢弃过期分句，防止因空间错位误导视障用户。



---



### **7\. Coding Agent 分步开发与测试验收清单**



Coding Agent 请按以下顺序推进代码编写与单元测试：



1. **Step 1：地图词典提取脚本**

    - 解析**当前最新版本**的楼层图数据，生成 `poi_semantic_dict.json`；脚本以地图版本为输入参数、可重复执行，并与 `poi_alias_overrides.json` 合并。
    - 验证 1F（81个房间）与 2F（55个房间）全部有效命名均已去重收录并附带常见口语别名。
    - 输出文件带 `map_version` 与 `exported_at`；编写“地图版本变更 → 重新导出 → node\_id 一致性校验”的回归测试。

2. **Step 2：自我中心空间转换模块（****`spatial_transformer.py`****）**

    - 实现坐标与朝向角到 1\\\~12 点钟方向及步数的转换函数，编写单元测试验证正前（12点）、正右（3点）、正后（6点）、正左（9点）边界条件。

3. **Step 3：LLM 意图解析与消歧引擎（****`intent_parser.py`**** \& ****`disambiguation_engine.py`****）**

    - 编写以下 4 个核心测试用例并确保全部通过：

        - **Case A（同义词唯一命中）**：输入“带我去医务室” $\rightarrow$ 命中 `卫生室(1F)` 唯一节点 $\rightarrow$ 直接触发路径规划。

        - **Case B（公共设施多实例就近消歧）**：输入“找一下男厕所” $\rightarrow$ 命中 `男卫生间` $\rightarrow$ 自动计算当前坐标到 1F/2F 各男厕的无障碍距离，直接导航至同层最近节点，不触发反问。

        - **Case C（类别歧义与特定同名房间多轮澄清）**：输入“我要去物理实验室”或“我要去校长办公室” $\rightarrow$ 触发反问并写入 `session_state["pending_candidates"]` $\rightarrow$ 第二轮输入“去热学那个”或“去东边第一间” $\rightarrow$ 成功消歧并启动导航。

        - **Case D（预注入上下文零工具调用直答）**：输入“我现在在哪儿，前面门是开着的吗？” $\rightarrow$ 单次 LLM 调用直接流式输出包含时钟方位、盲道状态及常开/常闭防火门属性的播报文本。

4. **Step 4：流式 TTS 集成与端到端联调（****`orchestrator.py`****）**

    - 串联分句流式输出，记录从请求发出到首个音频分片产出的耗时日志，完成最终交付。

