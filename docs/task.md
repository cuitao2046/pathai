# PathAI 公共空间识别与导航拓扑 · 任务拆解

## 现有代码基础

| 模块 | 文件 | 已实现功能 |
|------|------|-----------|
| CAD PDF 解析 | [parse_cad_pdf.py](file:///e:/code/pathai/src/parse_cad_pdf.py) | PyMuPDF 图层提取、墙线合并、门（摆弧+DK洞口）识别、房间识别（栅格化分水岭）、楼梯/电梯bbox聚类、门归属（5级兜底）、坐标转换（SCALE=0.0529） |
| 拓扑建模 | [topology.py](file:///e:/code/pathai/src/topology.py) | TR/TD/TI/TF/TEN/TE/XE 标准化节点ID、房间/门口/走廊/设施节点、最近邻连接边、跨楼层边（电梯a=0,楼梯a=999） |
| 可视化 | [render_map.py](file:///e:/code/pathai/src/render_map.py) / [render_interactive.py](file:///e:/code/pathai/src/render_interactive.py) | GeoJSON 渲染、交互式 HTML 可视化 |

## 核心差距

| 差距 | 现状 | 方案要求 |
|------|------|---------|
| **走廊建模方式** | 走廊质心作为TI节点，最近邻直连 | 需要 Medial Axis 骨架，沿走廊中轴走 |
| **Walkable Polygon** | 未扣除柱子/设备井 | 必须扣除障碍物生成可通行区域 |
| **Door Projection** | TD 直接连最近质心 | 门中心点需正交投影到骨架段 |
| **Junction 检测** | 无，靠距离阈值连边 | 骨架度数≥3的点才是真正交叉口 |
| **公共空间细分** | 只分 corridor/lobby/activity | 需区分 ElevatorLobby / StairLobby |
| **悬挂剪枝** | 无 | 骨架冗余分支需 Dangle Pruning |
| **R-Tree 索引** | 部分使用 STRtree | 骨架最近邻、门投影需全面使用 |

---

## 任务阶段划分

按依赖关系分为四个阶段，共 12 个任务：

```
Phase 1: 几何增强（Walkable Polygon）
  ├─ T1: 障碍物提取与 Walkable Polygon 生成
  └─ T2: 公共空间细分类（ElevatorLobby/StairLobby）

Phase 2: 骨架提取（Skeleton）
  ├─ T3: 栅格化 Medial Axis 提取实现
  ├─ T4: 骨架矢量化与图结构构建
  └─ T5: 悬挂分支剪枝（Dangle Pruning）

Phase 3: 拓扑重构（基于骨架）
  ├─ T6: Junction 节点检测（度数≥3）
  ├─ T7: Door 正交投影到骨架（R-Tree加速）
  └─ T8: 重构 topology.py 的节点/边生成逻辑

Phase 4: 集成验证
  ├─ T9: parse_cad_pdf.py 集成新骨架管线
  ├─ T10: 跨楼层连接适配新拓扑
  ├─ T11: 可视化调试工具增强
  └─ T12: 验收测试与质量校验
```

---

## 各任务详细说明

### Phase 1: 几何增强

#### T1: 障碍物提取与 Walkable Polygon 生成
- **优先级**: P0
- **输入**: 现有 `struct_segs`（墙线）、columns（柱子图层 LAYER_COLUMNS）、电梯井/楼梯井 polygon、房间 polygon
- **输出**: 每个公共空间（corridor/lobby）的 Walkable Polygon（Shapely Polygon）
- **核心逻辑**:
  1. 从 LAYER_COLUMNS 提取柱子 bbox → buffer 后作为障碍物
  2. 从 LAYER_ELEVATOR/STAIR 提取电梯井/楼梯井 polygon（已有 _stair_boxes）
  3. 墙体 buffer（WALL_BUFFER=0.12m）作为障碍物
  4. 对已识别的 corridor/lobby polygon，difference 所有障碍物 → Walkable Polygon
- **验收**: Walkable Polygon 不包含任何柱子/墙体/电梯井内部区域
- **修改文件**: `src/parse_cad_pdf.py`，新增函数 `generate_walkable_polygons()`

#### T2: 公共空间细分类（ElevatorLobby/StairLobby）
- **优先级**: P1
- **输入**: 现有 rooms 列表（含 roomType）、stairs/elevators 位置
- **输出**: roomType 细化，新增 `elevator_lobby` / `stair_lobby` 类型
- **核心逻辑**:
  1. 遍历所有 `roomType == "corridor"` 或 `"lobby"` 的空间
  2. 计算 polygon 到最近电梯/楼梯的距离
  3. 若距离 < 3m 且面积在 8~60㎡ → 重分类为 elevator_lobby/stair_lobby
  4. 在 topology.py 的 OBJ_TYPE 中新增 `ELB`/`SLB` 类型缩写
- **验收**: 电梯前室、楼梯前室被正确区分，不再归入普通走廊
- **修改文件**: `src/parse_cad_pdf.py`、`src/topology.py`

---

### Phase 2: 骨架提取

#### T3: 栅格化 Medial Axis 提取实现
- **优先级**: P0
- **输入**: T1 输出的 Walkable Polygon 列表
- **输出**: 每个公共空间的二值骨架图像 + 像素坐标
- **核心逻辑**:
  1. 对每个 Walkable Polygon 栅格化（分辨率 0.05m，即 SKELETON_RESOLUTION）
  2. 使用 `skimage.morphology.medial_axis` 提取中轴
  3. 需考虑多连通区域（带柱子的走廊会有洞）
- **依赖**: scikit-image（需确认是否已安装，否则 requirements.txt 加依赖）
- **验收**: 走廊中轴在视觉上居中，不贴墙，不穿墙/穿柱
- **新建文件**: `src/skeleton/medial_axis.py`

#### T4: 骨架矢量化与图结构构建
- **优先级**: P0
- **输入**: T3 的二值骨架图像
- **输出**: NetworkX Graph（节点为像素坐标，边为8邻域连接），矢量化 LineString 列表
- **核心逻辑**:
  1. 遍历骨架像素，8邻域找连接，构建无向图
  2. 用 `skimage.measure.find_contours` 或走链码将像素路径矢量化为 LineString
  3. 像素坐标 → 米制坐标（反推 px→pt→m 转换）
  4. 合并共线短段（Douglas-Peucker 简化，容差 0.1m）
- **验收**: 矢量化骨架与像素骨架一致，无明显锯齿
- **新建文件**: `src/skeleton/skeleton_vectorize.py`

#### T5: 悬挂分支剪枝（Dangle Pruning）
- **优先级**: P0
- **输入**: T4 的骨架 Graph + 门/出入口位置
- **输出**: 剪枝后的骨架 Graph
- **核心逻辑**:
  1. 计算所有节点度数
  2. 迭代移除：端点（degree=1）且（不靠近任何门/出入口，或边长<0.5m）
  3. 重复直到无新边可移除
  4. 保留通往门口/出入口/设施的分支（即使短）
- **验收**: 凸角/死角的毛刺被剪除，通往门的路径保留
- **修改文件**: `src/skeleton/medial_axis.py`，新增 `prune_dangling_branches()`

---

### Phase 3: 拓扑重构

#### T6: Junction 节点检测（度数≥3）
- **优先级**: P0
- **输入**: T5 剪枝后的骨架 Graph
- **输出**: TI 节点列表（交叉口，degree≥3）+ Terminal 列表（degree=1，端点）
- **核心逻辑**:
  1. 遍历骨架图节点，按度数分类：
     - degree ≥ 3 → TI（交叉口/决策点）
     - degree = 1 → Terminal（端点，通常是门口接入点或走廊尽头）
     - degree = 2 → Passing（普通路径点，可合并简化）
  2. 简化：连续 degree=2 的路径点可以合并为一条边（减少节点数）
- **验收**: 走廊十字/T字交叉口都有 TI 节点，数量与实际交叉口一致
- **新建文件**: `src/skeleton/junction_detector.py`

#### T7: Door 正交投影到骨架（R-Tree加速）
- **优先级**: P0
- **输入**: 门中心点列表、骨架段列表（LineString）、Shapely STRtree
- **输出**: TD 节点列表（门在骨架上的正交投影点）+ 分割后的骨架段
- **核心逻辑**:
  1. 构建所有骨架段的 STRtree 索引
  2. 对每个门中心 DoorCenter：
     a. 用 STRtree 找最近的骨架段
     b. 计算正交投影点 ProjectedPoint
     c. 在 ProjectedPoint 处分割该骨架段
     d. 生成 TD 节点，坐标为投影点
  3. TR（房间质心）→ TD 的边保留（房间内路径，垂直于墙方向）
  4. TD → 骨架的接入边长度=门中心到投影点距离
- **验收**: 所有门都有 TD 节点落在骨架上，TD-TR 连线不穿墙
- **新建文件**: `src/skeleton/door_projector.py`

#### T8: 重构 topology.py 的节点/边生成逻辑
- **优先级**: P0
- **输入**: T6 的 TI/Terminal、T7 的 TD、现有 TR/TF/TEN、剪枝后骨架
- **输出**: 新的 nodes/edges 列表（替换现有最近邻连接逻辑）
- **核心逻辑**:
  1. 节点来源整合：
     - TI: 骨架交叉口（T6）
     - TD: 门投影点（T7）
     - TR: 房间质心（保留）
     - TF: 楼梯/电梯设施节点（保留）
     - TEN: 室外出入口（保留）
  2. 边构建：
     - 骨架边：TI-TI、TI-TD、TI-Terminal 沿骨架连接（长度=沿骨架测地距离，非欧氏直线）
     - 房间接入：TD-TR（直线距离）
     - 设施接入：TF-最近TD/TI（投影到最近骨架点）
  3. 删除现有 `corridor_adjacency` 距离阈值连边逻辑（第264-275行的 30m 阈值直连）
- **验收**: 边沿走廊走向，无穿墙边，任意两点路径沿走廊走
- **修改文件**: `src/topology.py`（重构 `build_floor_topology` 函数）

---

### Phase 4: 集成验证

#### T9: parse_cad_pdf.py 集成新骨架管线
- **优先级**: P0
- **输入**: 所有 Phase1-3 的模块
- **输出**: 新的 GeoJSON 输出（包含 skeleton、walkable_regions 图层）
- **核心逻辑**:
  1. 在 `parse_floor()` 中，房间识别和门归属完成后，调用 T1-T8
  2. 新的 GeoJSON 结构增加：
     ```json
     {
       "skeleton": {"type": "FeatureCollection", "features": [...]},
       "walkable_regions": {"type": "FeatureCollection", "features": [...]},
       "topology": {"nodes": [...], "edges": [...]}
     }
     ```
  3. 保留原有输出格式兼容（旧字段不删除，新字段增量添加）
  4. 增加命令行选项 `--use-skeleton` 控制是否启用新管线（便于A/B对比）
- **验收**: 运行 parse_cad_pdf.py 无报错，新 GeoJSON 包含 skeleton 和 walkable 图层
- **修改文件**: `src/parse_cad_pdf.py`

#### T10: 跨楼层连接适配新拓扑
- **优先级**: P1
- **输入**: T8 输出的 F1/F2 拓扑、楼梯/电梯位置
- **输出**: XE 跨层边正确连接到新的 TI/TF 节点
- **核心逻辑**:
  1. 现有 `build_cross_floor_edges` 按 TF 序号引用，需确认新拓扑中 TF 节点的生成顺序不变
  2. 电梯/楼梯口的 TF 节点位置应投影到最近的骨架点（与 T7 同理）
  3. 验证：F1 电梯 TF → F2 电梯 TF 的 XE 边正确
- **验收**: 跨楼层路径规划 F1→F2 走电梯，不走楼梯（视障模式）
- **修改文件**: `src/topology.py`

#### T11: 可视化调试工具增强
- **优先级**: P1
- **输入**: 新 GeoJSON 输出
- **输出**: render_interactive.py 支持可视化骨架、Walkable区域、Junction节点
- **核心逻辑**:
  1. 不同颜色渲染：骨架（青色）、TI节点（红）、TD节点（黄）、Walkable Polygon（半透明绿）
  2. 鼠标悬停显示节点ID和类型
  3. 可选显示/隐藏各图层（checkbox控制）
- **验收**: 打开 HTML 可视化可清晰看到骨架沿走廊中轴，节点位置正确
- **修改文件**: `src/render_interactive.py`

#### T12: 验收测试与质量校验
- **优先级**: P0
- **输入**: 最终输出 GeoJSON
- **输出**: 质量报告
- **校验项**（对应 11号文档第八章验收标准）：
  1. **几何精度**: 墙体/门偏差 <0.3m
  2. **骨架质量**: 无穿墙，悬挂边 <5%
  3. **门投影**: 100% 门都有 TD 节点在骨架上
  4. **拓扑连通**: 任意两房间可达（图连通性检测）
  5. **跨层连接**: 电梯/楼梯 XE 边正确
  6. **无障碍路径**: 视障模式 a=999 避开楼梯
- **方法**: 新增 `validate_topology()` 自动校验函数 + 可视化人工检查
- **验收**: 所有自动化校验通过，可视化无明显错误
- **修改文件**: `src/validate_geojson.py`（扩展现有校验逻辑）

---

## 依赖关系图

```
T1 (Walkable) ──→ T3 (Medial Axis) ──→ T4 (矢量化) ──→ T5 (剪枝) ──→ T6 (Junction)
                                                                      │
T2 (细分类) ──────────────────────────────────────────────────────────→┤
                                                                      ↓
                                                      T7 (Door投影) ──→ T8 (重构拓扑)
                                                                      │
                                                      T10 (跨层) ─────→┤
                                                                      ↓
                                                      T9 (集成) ──→ T11 (可视化) ──→ T12 (验收)
```

## 模块目录结构（最终）

```
src/
├── parse_cad_pdf.py          # 主入口（修改：集成新管线）
├── topology.py               # 拓扑构建（重构：基于骨架）
├── render_map.py             # 静态渲染
├── render_interactive.py     # 交互可视化（增强）
├── validate_geojson.py       # 校验工具（扩展）
└── skeleton/                 # 新建：骨架提取模块
    ├── __init__.py
    ├── medial_axis.py        # T3+T5: 中轴提取+剪枝
    ├── skeleton_vectorize.py # T4: 矢量化
    ├── junction_detector.py  # T6: 交叉口检测
    └── door_projector.py     # T7: 门投影
```

## 建议实施顺序

1. **第一批（P0，跑通最小闭环）**: T1 → T3 → T4 → T5 → T6 → T7 → T8 → T9
2. **第二批（P1，完善体验）**: T2 → T10 → T11
3. **第三批（P0，质量保障）**: T12

预计第一批完成后即可看到骨架沿走廊走的效果，替代现有的质心直连。
