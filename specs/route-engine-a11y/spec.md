# 路线引擎适盲约束与物理距离规格

- **状态：** 已确认（2026-11-16），待实施
- **版本：** 0.1.1（2026-11-16 用户确认：routeRulesVersion 升 1.1.0；接受物理距离口径修正；normal 模式保持现状；accessibilityLevel=2 仅保留元数据）
- **来源：** 用户 2026-11-16 讨论结论；[docs/05-路线规划算法.md](../../docs/05-路线规划算法.md)、[docs/24-第一阶段前后端需求与API定义.md](../../docs/24-第一阶段前后端需求与API定义.md)、[docs/34-视障危险区标注与路线避让方案.md](../../docs/34-视障危险区标注与路线避让方案.md)、[specs/phase-1-indoor-navigation/spec.md](../phase-1-indoor-navigation/spec.md)
- **范围：** 后端路线规划的适盲硬约束、模式策略、危险层软惩罚、导航物理距离口径，以及 `wheelchair` 模式下 `accessibilityLevel=999` 的修复。

---

## 0. 结论摘要

1. **安全规则继续用硬约束，不改成有限惩罚。** 有限惩罚只能表达“尽量避开”，不能保证“绝不走楼梯”；硬约束或等价的 `FORBIDDEN` 才是适盲安全规则的正确表达。
2. **一份拓扑、两个数值。** `cost` 只用于选路，`physicalDistance` 只用于导航距离/播报；不建两张 1:1 图，不改地图 `distance`。
3. **搜索核心不写模式分支。** 通过集中的 `ModePolicy`（或等价策略表）返回 `FORBIDDEN` 或数值成本；Dijkstra/A* 只消费策略结果。
4. **修复 `wheelchair` 缺陷。** `accessibilityLevel=999` 是“视障结构等级”，不隐含轮椅不可用；轮椅只由 `wheelchairAccessible` 决定。移除轮椅分支对 `999` 的禁行判断。
5. **本次不引入 A\\*、不建双层图、不改门惩罚/`avoid` 惩罚语义。** 当前数据下路线节点序列保持不变；只修正用户看到的距离口径，并修复当前数据尚未触发的轮椅分支缺陷。

---

## 1. 目标与成功条件

### 1.1 目标

- 视障模式绝不使用不适合视障的路线，至少包括：
  - `blindAccessible=false` 的边；
  - 跨层楼梯边（盲模式必须走电梯）；
  - 危险层 `block` 且 `appliesTo` 包含 `blind` 的边。
- 导航展示、播报和距离计算全部使用**纯物理距离**：
  - `RouteInfo.distanceM` = 路径各边物理 `distance` 之和；
  - `NavigationInstruction.distanceM` = 到终点的剩余物理距离；
  - “直行 X 米”中的 X = 该段物理长度。
- 门惩罚、危险层 `avoid` 惩罚只影响选路，不进入任何用户可见的距离。
- 修复 `wheelchair` 模式把 `accessibilityLevel=999` 当作禁行条件的问题。

### 1.2 成功条件

- 使用真实地图包与真实危险层数据，视障路线中不出现任何禁止边。
- 路线的 `distanceM` 等于所经过边的原始 `distance` 之和，与门惩罚、`avoid` 惩罚无关。
- 当前地图包下，`normal` / `blind` / `wheelchair` 的路线节点序列不因本变更改变（轮椅缺陷在当前数据无触发边）。
- `wheelchair` 模式对 `accessibilityLevel=999` 且 `wheelchairAccessible=true` 的边允许通行；对 `wheelchairAccessible=false` 的边禁止通行。

---

## 2. 范围与边界

### 2.1 包含

- 后端 `RouteGraph` 的硬约束/软惩罚集中为模式策略。
- `RouteGraph` 同时累加选路成本 `cost` 与物理距离 `physicalDistance`。
- `nav_service.build_route_info` 使用物理距离生成总距离和分段剩余距离。
- `accessibilityLevel=999` 的语义收敛与数据校验。
- `wheelchair` 模式 `999` 判断修复。
- 单测、契约测试、真实地图回归、文档同步。

### 2.2 不包含

- A\\* 替换 Dijkstra、双层规划图、分层预处理。
- 修改地图包 `edges[].distance` 或重新发布地图版本。
- 修改门惩罚值、`avoid` 惩罚值或其选路语义。
- 危险区 Polygon、管理后台 UI、小程序上报、危险区审核流。
- `normal` 模式新增危险层避让或无障碍过滤。
- 节点级 `blindAccessible` / `wheelchairAccessible` 纳入路由。
- `RouteGraph` 构建性能优化/缓存（另立需求）。

---

## 3. 术语

| 术语 | 定义 |
|---|---|
| 物理距离 `physicalDistance` | 路径所经过边的地图包 `distance` 之和，单位米，不含任何惩罚 |
| 选路成本 `cost` | `physicalDistance + 门惩罚 + avoid 惩罚`，只用于 Dijkstra/A* 选路 |
| `FORBIDDEN` | 该边在当前模式下不可通行；策略返回 `None`/`inf`，搜索核心必须跳过 |
| 硬约束 | 所有回退层都不可绕过的约束，用于“绝不”类规则 |
| 软惩罚 | 只影响优先级、不保证禁行的数值成本，用于“尽量”类规则 |
| `999` | `accessibilityLevel=999`，表示含楼梯/视障结构不可用；不隐含轮椅不可用 |

---

## 4. 规范性要求

### REQ-COST-01 成本与物理距离分离

- `RouteGraph._dijkstra`（及未来 A*）必须同时维护并返回：
  - `distance`：选路成本 `cost`，保持现有内部语义，供 Dijkstra/A* 与工具使用；
  - `physicalDistance`：路径物理距离；
  - `edgeDistances`：与路径边一一对应的物理距离列表。
- `RouteGraph.generate_route` 必须透传 `physicalDistance` 与 `edgeDistances`。
- `nav_service.build_route_info` 必须用 `physicalDistance` 生成 `RouteInfo.distanceM`；缺失时回退到 `distance`，仅用于兼容旧调用。
- `build_route_info` 计算 `remaining` 时必须优先使用 `edgeDistances`，不得通过节点对反查后再混入加权值。
- `NavigationInstruction.distanceM` 保持“到终点的剩余距离”语义，但必须是物理距离。
- 播报文本中的“直行 X 米”必须使用物理段长，不受门惩罚/`avoid` 惩罚影响。

### REQ-COST-02 模式策略集中化

- 引入 `ModePolicy`（或等价的集中策略表），输入 `(mode, edge, context)`，输出数值成本或 `FORBIDDEN`。
- 搜索核心（`_dijkstra` / `_astar`）不得新增 `if mode == "blind"` 之类的模式分支；模式差异只存在于策略表。
- 邻接构建阶段必须剔除 `FORBIDDEN` 边；四层回退（最佳门 → 所有门 → 穿墙 → 跨层绕行）必须复用同一策略。
- 现有结构过滤保持不变：常闭防火门、纯管井门、穿墙走廊边。
- 现有门惩罚与 `avoid` 惩罚保持不变，只作为 `cost` 的组成部分。

### REQ-COST-03 `accessibilityLevel` 语义与数据校验

- `accessibilityLevel` 保留为分级结构属性：
  - `0`：平直走廊；
  - `2`：含门槛/坡度，当前允许通行，本期不作为硬禁行；
  - `999`：含楼梯/视障结构不可用。
- `999` 的规范语义是**视障不可用**，不得推导出轮椅不可用。
- 视障硬约束的规范字段是 `blindAccessible`；`accessibilityLevel==999` 可作为防御性二次检查，但数据必须归一化：
  - `accessibilityLevel==999` → 必须 `blindAccessible==false`；
  - `accessibilityLevel in (0,2)` → 不强制禁行；
  - `999` 且 `blindAccessible==true` 必须视为地图数据错误；
  - `999` 边必须显式声明 `wheelchairAccessible`，避免 schema 默认 `true` 误放行轮椅。
- `accessibilityLevel=2` 本期不改变选路，也不新增播报；仅保留数据。
- 地图转换/校验流程必须增加上述一致性检查。

### REQ-COST-04 `wheelchair` 模式修复

- `wheelchair` 模式只依据 `wheelchairAccessible` 判断硬禁行，不得因 `accessibilityLevel==999` 禁行。
- 行为要求：
  - `999 + wheelchairAccessible=true`：轮椅允许通行；
  - `999 + wheelchairAccessible=false`：轮椅禁止通行；
  - `wheelchairAccessible=false`：无论 `accessibilityLevel` 为何，轮椅禁止通行。
- 该修复必须覆盖地图加载、路线规划、四层回退与危险层过滤。
- 当前发布包 `2026-11-08.2` 中没有 `999 + wheelchairAccessible=true` 的边，因此当前路线不变化；但必须补测试防止回归。

### REQ-COST-05 危险层与回退

- `block`：进入策略的 `FORBIDDEN`，所有回退层不可绕过。
- `avoid`：保留为有限惩罚，进入 `cost`，不进入 `physicalDistance`。
- `notice`：不改变选路与成本，只随路线返回提示。
- `normal` 模式保持现有行为：不加载危险层、不读无障碍字段；本变更不改变普通模式路线。
- 盲模式 `wall_fallback` 仍不得作为公开路线；危险层 `block` 不得被 `wall_fallback` 放行。

### REQ-COST-06 兼容性与版本

- `RouteInfo` / `NavigationInstruction` 的 JSON schema 不新增必填字段；客户端无需改类型。
- `RouteInfo.distanceM` 的数值语义修正为物理距离。经过门惩罚或 `avoid` 惩罚的路线，其数值会变化；这是本次要修复的误导口径。
- `routeRulesVersion` 由 `1.0.0` 升至 `1.1.0`（2026-11-16 用户确认），因为轮椅适盲判断与距离口径发生变化；必须同步后端契约测试、客户端测试与 spec。
- `RouteGraph` 内部 `distance` 暂保留为选路成本，避免影响 `gen_beacon_plan.py`、`generate_fingerprint_grid.py` 等读取该字段的工具；后续可另立需求改名为 `cost`。

---

## 5. 模式策略行为表

| 模式 | 硬禁行（`FORBIDDEN`） | 软惩罚（进入 `cost`） | 危险层 |
|---|---|---|---|
| `normal` | 无无障碍硬禁行；保留常闭防火门/管井门/穿墙边过滤 | 门惩罚；`avoid` 不加载 | 当前不加载 |
| `blind` | `blindAccessible=false`；跨层 `type=staircase`；`appliesTo` 含 `blind` 的 `block`；`999` 防御性检查 | 门惩罚；`avoid` 惩罚 | `block` 硬禁行；`avoid` 软绕行；`notice` 仅提示 |
| `wheelchair` | `wheelchairAccessible=false`；`appliesTo` 含 `wheelchair` 的 `block`；**不因 `999` 禁行** | 门惩罚；`avoid` 惩罚 | 同 blind，按 `appliesTo` 过滤 |

---

## 6. 距离口径

| 输出 | 变更前 | 变更后 |
|---|---|---|
| `RouteInfo.distanceM` | `dist[end]`，包含门惩罚与 `avoid` 惩罚 | 路径各边 `distance` 之和，纯物理 |
| `NavigationInstruction.distanceM` | 剩余距离，来自 `PackageIndex` 原始边距离 | 剩余物理距离，来自 `edgeDistances` |
| “直行 X 米” | 物理边距离 | 物理边距离（保持） |
| 内部 `route["distance"]` | 选路成本 | 保持选路成本，新增 `physicalDistance` |

示例：

| 场景 | 物理距离 | `cost` | 用户看到的 `distanceM` |
|---|---:|---:|---:|
| 穿过一扇 `opening` 门 | 10m | 11m | **10m** |
| 经过一段 `avoid` 危险边 | 10m | 70m | **10m** |
| 绕开 `avoid` 危险边 | 30m | 30m | **30m** |

---

## 7. 行为对照表

| 场景 | 变更前 | 变更后 |
|---|---|---|
| 盲模式跨层楼梯 | 禁行 | 禁行（不变） |
| 盲模式 `blindAccessible=false` | 禁行 | 禁行（不变） |
| 轮椅模式 `999 + wheelchair=true` | **错误禁行** | 允许通行（修复） |
| 轮椅模式 `wheelchairAccessible=false` | 禁行 | 禁行（不变） |
| 普通模式 `999` | 允许 | 允许（不变） |
| 路线经过门/`avoid` 的 `distanceM` | 含惩罚，偏大 | 纯物理（修复） |
| 路线节点序列（当前地图） | — | 不变 |
| `avoid` 选路影响 | 软绕行 | 软绕行（不变） |
| `block` 硬禁行 | 四层回退不可绕过 | 不变 |

---

## 8. 验收标准

### 8.1 适盲安全

- [ ] 盲模式路线不包含 `blindAccessible=false` 的边。
- [ ] 盲模式跨层路线只使用电梯，不包含 `staircase` 跨层边。
- [ ] 盲模式路线不包含 `appliesTo` 含 `blind` 的 `block` 危险边。
- [ ] `wall_fallback` 不会放回 `block` 边。
- [ ] 轮椅模式对 `999 + wheelchairAccessible=true` 的边允许通行。
- [ ] 轮椅模式对 `wheelchairAccessible=false` 的边禁止通行。

### 8.2 距离口径

- [ ] `RouteInfo.distanceM == round(sum(edge.distance for edge in path), 2)`。
- [ ] 经过 `opening`/`fire` 门的路线，`distanceM` 不含 1.0/0.5 米门惩罚。
- [ ] 经过 `avoid` 危险边的路线，`distanceM` 不随 `penaltyM` 变化。
- [ ] `avoid` 仍然能改变选路结果；`block` 仍然硬禁行。
- [ ] `NavigationInstruction.distanceM` 与路径剩余物理距离一致。
- [ ] 预览接口与建会话接口的 `distanceM` 一致。

### 8.3 回归

- [ ] 当前地图包下，`normal` / `blind` / `wheelchair` 的路线节点序列不变。
- [ ] `door_fallback` / `wall_fallback` / `cross_floor_roundtrip` 标志语义不变。
- [ ] 现有 `tests/test_route_rules_consistency.py` 的 `sp["distance"]` 成本对拍继续通过；新增 `physicalDistance` 断言。
- [ ] 后端 `pytest` 全通过；客户端 `tsc` + `jest` 全通过（若契约版本变更）。

---

## 9. 测试计划

| 层级 | 用例 | 说明 |
|---|---|---|
| 单元 | `ModePolicy` 硬禁行 | 盲模式楼梯/`blindAccessible=false`；轮椅 `999+wheelchair=true` 允许 |
| 单元 | `999` 数据归一化 | `999+blindAccessible=true` 报错；`999+wheelchair=true` 合法 |
| 单元 | 物理距离累加 | 门惩罚/`avoid` 惩罚不进入 `physicalDistance` |
| 单元 | 四层回退 | `block` 不被 `wall_fallback` 放行 |
| API | 预览/建会话 | `distanceM` 为物理距离；`riskNotices` 不变 |
| 回归 | 真实地图抽样 | 同层/跨层 × 三模式，路线节点序列与变更前一致 |
| 回归 | 轮椅缺陷 | 基于真实地图的 `999` 边，仅改 `wheelchairAccessible` 构造测试夹具，验证允许/禁止 |

> 测试夹具必须基于真实地图包 `2026-11-08.2` 与真实危险层数据；不得虚构拓扑。轮椅缺陷用例可在真实 `999` 边上仅翻转 `wheelchairAccessible` 字段，并在用例中注明。

---

## 10. 文档同步

确认后实施时，至少同步：

- `specs/phase-1-indoor-navigation/spec.md`：版本升 `0.3.9`，REQ-NAV 增加对本规格的引用；
- [`docs/02-系统架构设计.md`](../../docs/02-系统架构设计.md)：路线引擎描述增加“成本与物理距离分离”；
- [`docs/03-地图构建指南.md`](../../docs/03-地图构建指南.md)：明确 `999` 是视障结构等级，不隐含轮椅不可用；
- [`docs/05-路线规划算法.md`](../../docs/05-路线规划算法.md)：补充模式策略、硬约束与软惩罚、`cost` / `physicalDistance` 口径；
- [`docs/24-第一阶段前后端需求与API定义.md`](../../docs/24-第一阶段前后端需求与API定义.md)：明确 `RouteInfo.distanceM` 与 `NavigationInstruction.distanceM` 为物理距离；
- [`docs/34-视障危险区标注与路线避让方案.md`](../../docs/34-视障危险区标注与路线避让方案.md)：`avoid` 惩罚只影响选路，不影响播报距离；
- [`docs/问题与踩坑日志.md`](../../docs/问题与踩坑日志.md)：记录“搜索成本与展示距离共用变量”“999 误伤轮椅”两个问题；
- `specs/phase-1-indoor-navigation/tasks.md` / `checklist.md`：补充验收项（如适用）。

---

## 11. 非目标

- 不替换 Dijkstra 为 A\\*；
- 不建规划层/导航层两张图；
- 不修改地图包 `distance`；
- 不修改门惩罚、`avoid` 惩罚的数值与语义；
- 不改变 `normal` 模式的危险层行为；
- 不引入节点级无障碍判断；
- 不做 `RouteGraph` 缓存/性能优化；
- 不做危险区 Polygon、后台 UI、用户上报。

---

## 12. 决策记录

1. **硬约束 vs 软惩罚：** 安全规则（楼梯、`blindAccessible=false`、`block`）保留硬约束；有限惩罚不保证“绝不”，不得替代。
2. **一份拓扑、两个数值：** `cost` 用于选路，`physicalDistance` 用于导航；不建 1:1 双图，避免同步漂移。
3. **`999` 语义：** `999` = 视障结构不可用，不隐含轮椅不可用；轮椅由 `wheelchairAccessible` 决定。
4. **轮椅修复：** 移除 `wheelchair` 分支的 `999` 禁行判断；保留 `wheelchairAccessible` 硬约束。
5. **普通模式：** 保持现状，不加载危险层、不读无障碍字段。
6. **版本：** `routeRulesVersion` 升至 `1.1.0`（2026-11-16 用户确认）。
7. **A\\*：** 本次不引入；当前瓶颈是图构建（约 1103ms/次），不是搜索（1.5~15ms）。

---

## 13. 已确认项

| 项 | 结论 | 说明 |
|---|---|---|
| 1. `routeRulesVersion` | 升至 `1.1.0` | 轮椅适盲判断与距离口径发生变化 |
| 2. `distanceM` 物理距离修正 | 接受 | 判断依据是“是否误导用户”：旧值含门/`avoid` 惩罚，会虚增距离；新值为纯物理距离 |
| 3. `normal` 模式 | 保持现状 | 不加载危险层、不做无障碍过滤 |
| 4. `accessibilityLevel=2` | 仅保留元数据 | 本期不新增播报/惩罚 |

确认依据（2026-11-16 用户）：安全规则继续硬约束；物理距离修正以“不误导用户”为准；`normal` 行为不变；`accessibilityLevel=2` 本期不扩展。
