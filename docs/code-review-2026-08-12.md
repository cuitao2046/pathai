# PathAI 核心代码审查报告

- 审查日期：2026-08-12
- 审查范围：`src/` 下核心模块（解析、骨架、拓扑、路由规则、校验、渲染），及 `.workbuddy/memory`、`docs/` 中的项目约定
- 审查文件：
  - `src/parsing/parse_cad_pdf.py`（4472 行，全量）
  - `src/skeleton/pipeline.py`（全量）
  - `src/skeleton/medial_axis.py`、`skeleton_vectorize.py`、`junction_detector.py`、`door_projector.py`
  - `src/topology/topology.py`、`src/topology/route_rules.py`（全量）
  - `src/qa/validate_geojson.py`
  - `src/rendering/render_interactive.py`（关键段：外轮廓、路由规则预计算、JS Dijkstra、HTML 模板）
  - `src/tools/`（import_manual_skeleton.py、merge_manual_edges.py、field_survey_calibrate.py 等）

---

## 一、系统概览与数据流

```
PDF(矢量CAD) ──parse_cad_pdf.py──▶ 每层几何+语义（墙/门/窗/房间/楼梯/电梯）
                                       │
                                       ▼
                         ┌─── 中轴骨架（medial_axis → skeleton_vectorize → junction_detector）
                         │        或 手动骨架 JSON（skeleton_manual_parsed.json 覆盖）
                 build_skeleton_topology (pipeline.py) ──▶ TR/TI/TD/TF/TEN 节点 + TE 边
                         │
                         ▼
               build_floor_topology (topology.py) + 跨层边
                         │
                         ▼
                 GeoJSON（含 topology / walkable / skeleton）
                         │
        ┌───────────────┼────────────────┐
        ▼               ▼                ▼
 route_rules.py    validate_geojson  render_interactive.py
 （后端寻路）        （质量校验）      （HTML + 前端 JS Dijkstra）
```

核心技术要点：
- 坐标系：PDF pt → 米制（`SCALE = 0.0529` 米/pt，`ORIGIN_X/Y` 偏移，Y 翻转）
- 拓扑节点类型：TR(房间)/TD(门洞)/TI(交叉口)/TF(设施)/TEN(出入口)/TE(边)/XE(跨层边)
- 用户明确约定：**门不合并**（每扇门独立成 TD）、手动骨架优先于自动中轴、开放/封闭空间不合并编号、管井门禁入路径、无门卫生间允许穿墙

---

## 二、问题清单

> 每条问题按「编号 - 位置 - 问题 - 影响 - 建议」组织。编号分五类：
> A=正确性/潜在缺陷，B=架构与可维护性，C=性能，D=常量与配置，E=风格与其他。

### A. 正确性 / 潜在缺陷

#### A1. `topology.py` 的 `_merge_nearby_doors` 引用未定义变量 `centers`（高危潜伏 Bug）

- 位置：[topology.py:253-279](file:///E:/code/pathai/src/topology/topology.py#L253-L279)，具体为第 273 行 `ci = centers[i]`、第 279 行 `cj = centers[j]`
- 问题：函数签名是 `_merge_nearby_doors(doors, max_dist_m=0.8, coords=None)`，函数体内只定义了 `coords`（含 fallback 推导），从未定义 `centers`。任何调用都会抛 `NameError: name 'centers' is not defined`。
- 影响：目前因用户「门不合并」约定该函数未被调用，**属于潜伏 bug**，一旦有人启用门合并逻辑即崩溃。
- 补充：该函数与 `pipeline.py` 中的同名 `_merge_nearby_doors` 功能完全重复，且 pipeline 版本内部正确使用了 `coords`——两处显然是同一段代码复制而来，一处修好、一处没修，已产生分叉。
- 建议：
  1. 删除 `topology.py` 中该函数（用户约定不再合并门），或
  2. 若保留，抽到公共工具模块（如 `src/common/geometry.py`）只留一份，并修正为 `ci = coords[i]`；
  3. 无论哪种，加一行 `# DISABLED by user convention: 每扇门独立成 TD` 说明。

#### A2. 路由规则逻辑三处重复实现（高漂移风险）

- 位置：
  1. 后端 [route_rules.py](file:///E:/code/pathai/src/topology/route_rules.py)：`DOOR_PENALTY`(L39)、`_build_adjacency`(L303)、`shortest_path` 三层回退(L347)、`_dijkstra`(L367)
  2. 渲染端预计算 [render_interactive.py:440 compute_route_rule_extras](file:///E:/code/pathai/src/rendering/render_interactive.py#L440-L535)：重复定义 `DOOR_PENALTY`(L449)、`edge_door_type`、`room_best_door`、`wall_crossing_titi`、`infra_doorway_ids`
  3. 前端 JS [render_interactive.py:3093-3316](file:///E:/code/pathai/src/rendering/render_interactive.py#L3093-L3316)：`buildPathAdj`、`dijkstraCore`(L3250)、`dijkstra` 三层回退(L3303)，连「常开防火门 penalty=0」等细节都逐行复刻
- 问题：同一套导航规则（规则 1-5）在 Python 后端、Python 预计算、浏览器 JS 各实现一遍，一致性仅靠注释「对齐 route_rules」维持，**无编译期/测试期保障**。
- 影响：任何规则修改须同步三处，漏改即出现「前端路径与后端路径不一致」的难查问题。
- 建议（按投入从小到大）：
  1. 最小方案：抽一个 `rules.py` 定义常量与规则数据的**唯一来源**，`route_rules` 与 `compute_route_rule_extras` 均从此导入，JS 端只接收序列化后的规则数据（不再内嵌常量）；
  2. 中期方案：前端 Dijkstra 改为调用后端 `/route` 接口（或 WebSocket），消除 JS 复刻；
  3. 若必须保留前端寻路，用代码生成（从 Python 规则生成 JS）或对三处行为做 golden-test 对拍（同一图生成三份路径断言一致）。

#### A3. 跨层边构建两套实现并存，其中一套为死代码

- 位置：
  1. 实际使用：[parse_cad_pdf.py:4352 cross_floor_edges](file:///E:/code/pathai/src/parsing/parse_cad_pdf.py#L4352-L4412)（内嵌于 `build_geojson`）：按**图纸井道编号配对**（`II-B2-01#ST` 等），无编号退化几何配对（<3.5m）
  2. 从未调用：[topology.py:574 build_cross_floor_edges](file:///E:/code/pathai/src/topology/topology.py#L574)（几何配对版），但已在 [parse_cad_pdf.py:40](file:///E:/code/pathai/src/parsing/parse_cad_pdf.py#L40) 被 import
- 问题：两套跨层配对逻辑，逻辑不一致（编号配对 vs 纯几何），topology 版本是死代码。
- 影响：维护者不知道该信哪个版本；若后续启用 topology 版本会因「编号配对」缺失而漏配大井道楼梯。
- 建议：把编号配对逻辑迁入 `topology.py` 作为唯一实现，删除 `parse_cad_pdf.py` 内嵌版本与多余 import；或反之。跨层边的 `distance=4.2`、`estimatedTime=60/15`、`accessibilityLevel=999/0`、`riskLevel=10/1` 等魔法数字（见 D5）一并移入常量。

#### A4. `_heban_real_polygon` v1（形态学版）是死代码

- 位置：[parse_cad_pdf.py:2497](file:///E:/code/pathai/src/parsing/parse_cad_pdf.py#L2497) 定义 `_heban_real_polygon`，实际生效的是 v2（射线投票版，~L2799 被调用）
- 问题：v1 从未被调用，与 v2 并存约 300 行，读者会困惑该信哪个版本。
- 建议：删除 v1，或在 v1 顶部注明「已废弃，v2 替代，原因：射线投票对窄缝/多连通域更稳」后保留作为参考。

#### A5. `LAYERS_FURNITURE = ()` 空配置造成死代码链

- 位置：配置区 `LAYERS_FURNITURE = ()`（空元组）；`furn_segs` 收集循环（~L2948-2956）恒为空
- 问题：`rasterize_walls`、`build_rooms`、`_heban_real_polygon(_v2)`、`inject_heban_classroom_rooms` 整条参数链都传递 `furn_segs`（如栅格化时 `for a, b in furn_segs` 与 all_segs 合并），但该列表恒空，相关分支全部失效。
- 影响：形成「看起来有家具处理、实际恒空」的误导性代码；若家具层（如 A-METAL-S）已并入 `LAYERS_STRUCT`，应明确移除家具链，避免重复/误伤。
- 建议：二选一——(a) 若不再单独处理家具，删掉 `furn_segs` 参数链；(b) 若保留，补回真实家具图层配置并加测试断言。

#### A6. 视障速度 `0.8` 在多处裸写，且 `BLIND_WALK_SPEED` 三处重复定义

- 位置：
  - [parse_cad_pdf.py ~L2185/2209](file:///E:/code/pathai/src/parsing/parse_cad_pdf.py#L2185) `attach_elevator_door_nodes` 内 `round(d / 0.8, 1)` 直接写字面量
  - `BLIND_WALK_SPEED = 0.8` 分别在 [topology.py:22](file:///E:/code/pathai/src/topology/topology.py#L22)、[pipeline.py:39](file:///E:/code/pathai/src/skeleton/pipeline.py#L39)、`tools/merge_manual_edges.py:38`
- 影响：速度参数改动需改 4 处；附载电梯门的 `estimatedTime` 与主图不一致时难以排查。
- 建议：新建 `src/common/constants.py`，统一导出 `SCALE/ORIGIN_X/ORIGIN_Y/BLIND_WALK_SPEED/NORMAL_WALK_SPEED/DOOR_PENALTY` 等，各处 import 引用。

---

### B. 架构与可维护性

#### B1. 超大单文件，职责混杂

- 位置：[parse_cad_pdf.py](file:///E:/code/pathai/src/parsing/parse_cad_pdf.py)（4472 行）、[render_interactive.py](file:///E:/code/pathai/src/rendering/render_interactive.py)（3900+ 行，内嵌 HTML/CSS/JS 大字符串）
- 问题：`parse_cad_pdf.py` 一个文件同时承载：PDF 图层提取、几何算法（中轴前置的栅格化、聚类、轮廓追踪）、标签/OCR 解析、房间语义分类（含合班教室识别）、拓扑注入、GeoJSON 组装、CLI 入口。函数间通过大量模块级常量耦合。
- 影响：单点改动需通读全文件；函数行号 600-4400 段几乎无复用边界，新人上手成本高；`render_interactive.py` 内 JS/CSS 字符串无法走 lint/格式化。
- 建议：按依赖方向拆分：
  - `src/parsing/pdf_layers.py`（PDF 读取与图层提取）
  - `src/geometry/`（shapely 几何工具、聚类、栅格化、轮廓追踪）
  - `src/semantics/`（房间分类、楼梯/电梯/合班教室识别、门属性）
  - `src/io/geojson_writer.py`（GeoJSON 组装）
  - `render_interactive.py` 的 HTML 模板改为独立 `.html` 资源文件（`templates/`），JS 抽到 `static/`。

#### B2. 依赖方向倒挂：解析模块 import 渲染模块

- 位置：[parse_cad_pdf.py:51](file:///E:/code/pathai/src/parsing/parse_cad_pdf.py#L51) `from src.rendering.render_interactive import building_outline`
- 问题：数据生产层（parsing）依赖展示层（rendering）的几何函数；而 `building_outline` 是纯几何算法（栅格化+轮廓追踪，[render_interactive.py:227](file:///E:/code/pathai/src/rendering/render_interactive.py#L227)）。
- 影响：渲染模块无法独立演进；任何对 render_interactive 的改动都可能间接破坏解析管线；同时也把 3900 行大文件拖入解析依赖链。
- 建议：将 `building_outline`（及 `_rasterize_rings`、`_dist_transform`、`_dilate`、`_trace_contour*`、`_simplify`、`_area`）下沉到 `src/geometry/contour.py`，parsing 与 rendering 共同引用。

#### B3. `sys.path` 导入副作用

- 位置：[parse_cad_pdf.py:29-32](file:///E:/code/pathai/src/parsing/parse_cad_pdf.py#L29-L32)
- 问题：模块导入时把项目根插入 `sys.path`，影响同进程内所有模块。
- 影响：作为库被 import 时产生全局副作用；多项目/虚拟环境路径污染难排查。
- 建议：用包安装（`pip install -e .`，pyproject 已具备）或入口脚本 `run.py` 处理路径；模块内不再改 `sys.path`。

#### B4. `MANUAL_SKELETON` 模块级全局变量 + 惰性加载

- 位置：[parse_cad_pdf.py:67 _load_manual_skeleton](file:///E:/code/pathai/src/parsing/parse_cad_pdf.py#L67)，[main 内 global 修改](file:///E:/code/pathai/src/parsing/parse_cad_pdf.py#L4456-L4458)
- 问题：全局可变状态，`main()` 用 `global` 改；`--no-manual-skeleton` 与 `--use-skeleton` 组合状态靠全局贯穿。
- 影响：多次调用 `parse_floor`/`build_geojson`（测试、批量处理）行为不确定；全局读文件缓存使「热更新 JSON」需重启进程。
- 建议：改为显式参数注入（如 `manual_skeleton: Optional[dict]` 传入 `parse_floor`/`build_geojson`），或引入轻量 Context/Config 对象。

#### B5. 图纸专属配置散落硬编码

- 位置：`PDF_F1/PDF_F2/OUT_GEOJSON` 等模块常量、[TITLE_BLOCK_X = 2900.0](file:///E:/code/pathai/src/parsing/parse_cad_pdf.py#L163)、`build_geojson` 内 `venueId="school-building-01"`、`venueName`、`version="9.0.0"`（[parse_cad_pdf.py:4414-4417](file:///E:/code/pathai/src/parsing/parse_cad_pdf.py#L4414-L4417)）
- 问题：换一栋楼就要改源码。
- 建议：图纸级参数（PDF 路径、输出路径、图签区坐标、场馆元信息）抽到 CLI 参数 / `config.yaml` / `@dataclass Config`。

#### B6. 函数体开头重复 docstring

- 位置：[pipeline.py build_skeleton_topology ~L451-470](file:///E:/code/pathai/src/skeleton/pipeline.py#L451-L470)（函数内连续两个 docstring，内容重叠）
- 建议：合并为一个 docstring。

#### B7. 用单元素列表做「可变计数器」

- 位置：[topology.py:427 `edge_seq = [0]`](file:///E:/code/pathai/src/topology/topology.py#L427-L432)、`pipeline.py` 的 `edge_seq = [_manual_max_seq]`
- 问题：利用 list 可变性模拟 nonlocal 计数，风格绕、易误读。
- 建议：改用 `itertools.count(start)`，或显式闭包/`nonlocal` 变量。

#### B8. 因用户约定被禁用的逻辑保留但无系统标注

- 位置：`dedupe_doorways`、`_merge_nearby_doors`（topology.py 与 pipeline.py 各一份）、pipeline 中「每扇门独立成 TD」的禁用注释、`USE_SKELETON/_HAS_SKELETON` 分支
- 问题：禁用原因只散落在注释里，缺少统一登记；新维护者容易「顺手启用」旧逻辑导致行为回归。
- 建议：建立 `docs/设计决策记录.md`（ADR）登记「门不合并」「手动骨架优先」等约定；被禁用函数标注 `# DISABLED: 见 ADR-xxx`，或直接删除后靠 git 历史找回。

#### B9. 注释与实现不一致

- 位置：[validate_geojson.py:97](file:///E:/code/pathai/src/qa/validate_geojson.py#L97) 注释「同开口的摆弧/防火/门洞合并为一个 TD」，与用户约定「每扇门独立成 TD」矛盾
- 影响：校验逻辑按「房间必须有 TD 边」设计是对的，但注释会给维护者错误预期。
- 建议：同步注释与 `docs/` 验收标准表述。

#### B10. 穿墙几何判定多份「同源」实现

- 位置：[route_rules.py:458 _segment_crosses_wall](file:///E:/code/pathai/src/topology/route_rules.py#L458)、[render_interactive.py:408 _seg_crosses_wall](file:///E:/code/pathai/src/rendering/render_interactive.py#L408)（注释「与 route_rules 同源」）、`_seg_crosses_any_wall`（route_rules:226）
- 建议：`side()` 叉积判定抽到 `src/geometry/`，三处共用。

---

### C. 性能

#### C1. `_dijkstra` 用「列表 + 每轮全量 sort + pop(0)」模拟优先队列

- 位置：[route_rules.py:367-401](file:///E:/code/pathai/src/topology/route_rules.py#L367-L401)
- 问题：每轮 `pq.sort(key=...)` + `pop(0)` 是 O(V log V) 与 O(V)，总复杂度 O(V² log V)，且每次弹最小要全排一次。
- 影响：当前单楼 2 层节点数（~200-500）可接受；扩展到多楼/全校区或实时交互会明显变慢。
- 建议：改用 `heapq`（`heappush/heappop`），并配 `dist` 字典跳过过时项（目前用 `visited` 集合可保留）。

#### C2. `_edge_by_id` 线性扫描且在循环内被反复调用

- 位置：[route_rules.py:441-445](file:///E:/code/pathai/src/topology/route_rules.py#L441-L445)，被 `_dijkstra` 返回段（L422-429）多次调用，每次 O(E)
- 建议：`RouteGraph.__init__` 里建 `self.edge_by_id = {e["id"]: e for e in edges}`，查询 O(1)。

#### C3. 多处 O(n²) 聚类/邻近搜索

- 位置：
  - [parse_cad_pdf.py:315 cluster_items](file:///E:/code/pathai/src/parsing/parse_cad_pdf.py#L315)（两两试探链接）
  - [parse_cad_pdf.py:1920 bbox_clusters](file:///E:/code/pathai/src/parsing/parse_cad_pdf.py#L1920)
  - `pipeline.py::_merge_nearby_ti_nodes`（半径 1.5m 两两距离）
  - [route_rules.py:155 _add_doorless_toilet_links](file:///E:/code/pathai/src/topology/route_rules.py#L155)（对每个无门卫生间 20m 半径线性扫全部节点）
- 影响：节点/线段数千量级时（复杂楼层）每次解析耗时明显。
- 建议：统一用 `shapely.STRtree` 或等距网格分桶做近邻查询；若规模稳定很小，至少在函数 docstring 标注 O(n²) 与预期规模上限。

#### C4. `compute_route_rule_extras` 每次渲染全量重算

- 位置：[render_interactive.py:511-528](file:///E:/code/pathai/src/rendering/render_interactive.py#L511-L528)（边 × 墙嵌套循环）
- 问题：同一 GeoJSON 每次渲染都重跑 O(边×墙) 的穿墙判定、门归属统计。
- 建议：把规则辅助量（`edge_door_type`、`room_best_door`、`wall_crossing_titi`、`infra_doorway_ids`）在 `build_geojson` 生成时一次性写入输出（如顶层 `routeExtras` 字段），渲染端直接读取。

---

### D. 常量与配置管理

| 编号 | 常量 | 出现位置 | 问题与建议 |
|------|------|----------|-----------|
| D1 | `SCALE = 0.0529`（米/pt） | [parse_cad_pdf.py:91](file:///E:/code/pathai/src/parsing/parse_cad_pdf.py#L91)；另外在 [pipeline.py:716](file:///E:/code/pathai/src/skeleton/pipeline.py#L716)、[topology.py:361](file:///E:/code/pathai/src/topology/topology.py#L361) 裸写 `* 0.0529`；`tools/field_survey_calibrate.py:14-15` 也硬编码 | 统一 import 自 `constants.py`，禁止裸写 |
| D2 | `ORIGIN_X=2019.1 / ORIGIN_Y=1154.8` | [parse_cad_pdf.py:92-93](file:///E:/code/pathai/src/parsing/parse_cad_pdf.py#L92-L93)；`tools/field_survey_calibrate.py` 再次硬编码 | 同上 |
| D3 | `BLIND_WALK_SPEED = 0.8` | [topology.py:22](file:///E:/code/pathai/src/topology/topology.py#L22)、[pipeline.py:39](file:///E:/code/pathai/src/skeleton/pipeline.py#L39)、`tools/merge_manual_edges.py:38`，及 parse_cad_pdf.py 裸写 `0.8`（A6） | 合并为单一定义 |
| D4 | `DOOR_PENALTY` | [route_rules.py:39](file:///E:/code/pathai/src/topology/route_rules.py#L39) 与 [render_interactive.py:449](file:///E:/code/pathai/src/rendering/render_interactive.py#L449) 各一份 | 单一定义，渲染端引用 |
| D5 | 跨层边魔法数字 `distance=4.2`、`estimatedTime=60/15`、`accessibilityLevel=999/0`、`riskLevel=10/1` | [parse_cad_pdf.py:4404-4407](file:///E:/code/pathai/src/parsing/parse_cad_pdf.py#L4404-L4407) | 抽成命名常量，注明单位与语义 |
| D6 | `TITLE_BLOCK_X = 2900.0` | [parse_cad_pdf.py:163](file:///E:/code/pathai/src/parsing/parse_cad_pdf.py#L163) | 图纸专属坐标，随 B5 一起外置 |

---

### E. 风格与其他

- **E1** 函数内 import：[route_rules.py:156](file:///E:/code/pathai/src/topology/route_rules.py#L156) `import math as _m`（模块头部已 import math）。统一移到文件头。
- **E2** 「常开防火门 penalty=0」的判定在 [route_rules.py:125](file:///E:/code/pathai/src/topology/route_rules.py#L125)、L271-276、L338-339 三处重复书写，逻辑一致但易改漏。抽为 `_door_penalty(node)` 辅助函数。
- **E3** `_edge_weight`（[route_rules.py:262](file:///E:/code/pathai/src/topology/route_rules.py#L262)）取 `isNormallyOpen` 时用「三目链」从两端猜门节点，可读性差；`_edge_door_type` 已有同样逻辑，建议提取 `_door_node_of_edge(e)`。
- **E4** 代码中出现 T8/T12 等任务代号注释（如 validate_geojson.py:3），缺正式设计文档索引；建议在 `docs/` 建「任务代号 → 文档」对照表，或直接引用文档标题。
- **E5** `parse_floor` 内再次定义局部 `_bez_mid`（[parse_cad_pdf.py:2992](file:///E:/code/pathai/src/parsing/parse_cad_pdf.py#L2992)），而模块级/`detect_doors` 内已有 `bezier_mid`（L628）——同名不同名混用，建议统一为模块级工具函数。

---

## 三、改进建议优先级汇总

### P0（尽快处理，正确性相关）
| 编号 | 事项 | 工作量 |
|------|------|--------|
| A1 | 修复/删除 `topology.py::_merge_nearby_doors` 的 `centers` NameError 隐患 | 极小 |
| A2 | 路由规则三处实现建立唯一来源，至少收敛常量与规则数据 | 中 |
| A3 | 跨层边两套实现合一，删除死代码与多余 import | 小 |

### P1（架构债，影响长期维护）
| 编号 | 事项 | 工作量 |
|------|------|--------|
| B1 | 拆分 `parse_cad_pdf.py` / `render_interactive.py` 巨型单文件 | 大 |
| B2 | `building_outline` 下沉到 geometry 层，解除 parsing→rendering 依赖 | 中 |
| B4 | `MANUAL_SKELETON` 全局改参数注入 | 小-中 |
| B5 | 图纸配置外置（CLI/config） | 小-中 |
| D1-D4 | 常量统一到 `constants.py` | 小 |

### P2（性能与体验优化）
| 编号 | 事项 | 工作量 |
|------|------|--------|
| C1/C2 | `_dijkstra` 换 heapq、`_edge_by_id` 建索引 | 小 |
| C3 | 聚类/近邻搜索换 STRtree | 中 |
| C4 | 路由规则辅助量随 GeoJSON 生成一次写死 | 小-中 |
| A5 | 清理 `LAYERS_FURNITURE` 空配置死代码链 | 小 |

### P3（代码卫生）
| 编号 | 事项 |
|------|------|
| A4 / B6 / B7 / B8 / B9 / B10 / E1 / E2 / E3 / E4 / E5 | 死代码清理、重复 docstring、列表计数器、禁用逻辑标注、注释对齐、穿墙判定收敛、风格统一 |

---

## 四、审查结论

1. **整体评价**：管线设计思路清晰，领域规则（无障碍导航、管井禁入、无门卫生间例外）实现细致，GeoJSON 输出规范、ID 约定统一，校验脚本（validate_geojson）有明确的验收目标。手动骨架 JSON 覆盖机制是很好的工程兜底。
2. **最值得优先处理**的是 **A1**（潜伏 NameError）与 **A2**（路由规则三处重复）。A2 是当前架构中风险最高的部分——导航是本项目核心价值，其规则却在三份代码里各自维护。
3. **不建议一次性大重构**：`parse_cad_pdf.py` 拆分的收益大但回归风险高（缺少针对解析结果的 golden 测试）。建议先补一组「输入 PDF → 期望 GeoJSON 关键断言」的回归测试，再逐步拆分。
4. 建议在 `.workbuddy/memory` 中登记本次审查的决策项，供后续迭代引用。
