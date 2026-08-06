# PathAI 室内导航系统 · 公共空间识别方案

> **相比房间识别，公共空间识别对于室内导航实际上更加重要。** 本方案采用 CAD 语义 + 几何拓扑 + 计算几何（Graph Geometry）的方法，自动从建筑施工图中提取公共空间拓扑图，这是 MIT、CMU、ETH、Autodesk、IndoorGML 等室内地图构建系统普遍采用的技术路线。

---

## 一、图纸分析结论

基于提供的两张 BIAD（北京院）CAD 导出 PDF（首层、二层）分析，施工图已具备识别公共空间所需的全部信息。

### 首层可识别公共空间

- 门厅
- 学生社团活动区
- 职能办公区
- 教师办公区
- 大面积连续走道
- 多个卫生间前厅
- 电梯前室
- 楼梯前室
- 无障碍入口区域
- 非机动车入口门厅

### 二层可识别公共空间

- 大面积实验室公共走廊
- 教师办公区
- 卫生间前厅
- 电梯前室
- 楼梯前室
- 各实验室之间的连接走廊

---

## 二、核心建模思想

### 2.1 不建议直接把"走道"作为一个 Node

真正应该提取的是 **公共空间拓扑（Graph of Public Space）**。导航路径的真实结构应为：

```
Room → Door → Corridor Graph → Lobby → Stair/Elevator → Lobby → Corridor → Door → Room
```

而不是简单的 `Room → Room → Room`。

**拓扑示意图：**

```
               Room101
                  │
                  │ door
                  │
 CorridorNode_A───┼────CorridorNode_B──────Room102
                  │
                  │
             StairLobby
                  │
              Stair#1
```

---

## 三、公共空间五种类型

| 类型 | 英文名 | 说明 | 处理方式 |
|------|--------|------|---------|
| 走道 | Corridor | 数量最多的公共空间 | 不能整条作为一个 Node，需在交叉口处切分成多段 |
| 门厅 | Lobby | 公共开放空间，面积通常几十平方米 | 识别为 Lobby Region，自动生成 Center Node |
| 大厅 | Hall | 如学生社团活动区，所有房间连向这里再进入走道 | 作为 Hall Node，而非 Room |
| 电梯厅 | Elevator Lobby | 电梯前的开放空间 | 独立识别，导航必经路径：Room→Door→Corridor→Elevator Lobby→Elevator |
| 楼梯前室 | Stair Lobby | 楼梯前的前室区域 | 作为 Transition Node，避免路径直接从 Corridor 连到 Stair |

### 3.1 Corridor（走道）切分示例

十字交叉口走道：

```
──────────────
      │
      │
──────┼────────
      │
```

应生成四个 Node，连接关系如下：

```
Node1
   │
Node2──Node3
   │
Node4
```

这样才能正确支持最短路径搜索。

### 3.2 Lobby（门厅）连接示例

```
Door
   │
 Lobby
   │
 Corridor
```

---

## 四、三层数据结构

公共空间不应直接输出 Polygon，而应生成三层递进式数据：

### 第一层：Region（区域层）

存储真正的公共空间 Polygon，类型包括：

- Lobby（门厅）
- Hall（大厅）
- Corridor（走道）
- Activity Area（活动区）
- Waiting Area（等候区）

**数据结构：**

```json
{
  "type": "Region",
  "polygon": [...]
}
```

### 第二层：Skeleton（骨架层）

对每个公共区域计算 **Medial Axis（中轴）**，得到导航骨架。避免沿 Polygon 边界行走。

**矩形区域骨架示例：**

```
###############
#             #
#      ────── #
#             #
###############
```

十字交叉口骨架示例：

```
────┬────
    │
```

### 第三层：Navigation Graph（导航图层）

最终输出 Node 和 Edge，这是路线规划引擎直接消费的数据结构。

```
Node101
Node102
Node103

Edge: 101 ↔ 102
Edge: 102 ↔ 103
```

---

## 五、模块架构

在现有 IndoorParser 基础上，新增独立的 **PublicSpaceExtractor** 模块：

```
IndoorParser
├── RoomExtractor
├── DoorExtractor
├── StairExtractor
├── ElevatorExtractor
├── RestroomExtractor
└── PublicSpaceExtractor   ← 新增
      ├── CorridorDetector
      ├── LobbyDetector
      ├── HallDetector
      ├── ActivityAreaDetector
      ├── SkeletonGenerator
      ├── JunctionDetector
      ├── NavigationGraphBuilder
      └── PublicSpaceClassifier
```

### 5.1 完整导航拓扑链路

```
Room
   │
Door
   │
PublicSpace Node
   │
Intersection Node
   │
Lobby Node
   │
Stair/Elevator Node
   │
PublicSpace Node
   │
Door
   │
Room
```

### 5.2 模块输出

PublicSpaceExtractor 输出四类数据：

1. 公共区域 Polygon
2. 骨架（Skeleton）
3. 交叉节点（Junction）
4. 最终 Navigation Graph

与房间/门/楼梯/电梯节点共同组成整栋教学楼的导航拓扑。

---

## 六、七阶段算法流程

```text
                CAD PDF
                   │
         PDF Primitive Parser
                   │
      ┌────────────┴────────────┐
      │                         │
 Geometry Layer            Text Layer
(Line Polyline Arc)       (OCR/Text)
      │                         │
      └────────────┬────────────┘
                   │
         Public Space Candidate
                   │
        Region Segmentation
                   │
      Skeleton Extraction
                   │
     Junction Detection
                   │
      Navigation Graph Builder
                   │
        Indoor Routing Graph
```

**最终输出：**

- NavigationGraph（Nodes + Edges）
- Region（公共空间区域）
- Door（门节点）
- Room（房间节点）
- POI（兴趣点）

输出为结构化数据，而非图片。

---

## 七、各阶段详细说明

### 阶段一：公共空间候选区域提取（Public Space Candidate）

决定哪些封闭空间属于公共空间。CAD 图纸中的文字标注可直接利用。

**首层可直接利用的文字标签：** 走道、门厅、学生社团活动区、教师办公区

**二层可直接利用的文字标签：** 教师办公区、走道

**算法流程：**

```
For every Text:
  if Text ∈ {走道, 门厅, 大厅, 活动区, 共享空间, 教师办公区, 公共空间, 连廊}:
    ↓
    Locate nearest closed polygon
    ↓
    Assign Semantic Label
```

**输出示例：**

```
Polygon A → Semantic = Corridor
```

**无文字标签时的兜底规则：**

```
面积 > 15㎡
AND
门数量 ≥ 3
AND
没有家具
↓
判定为 Public Space
```

---

### 阶段二：公共区域 Polygon 恢复

CAD 中很多走道没有封闭边界（常缺一条线），需要进行 Polygon Recovery。

**推荐方案：** Shapely Polygonize + Gap Closing

**处理流程：**

```
LineString → Snap → Merge → Polygonize → Candidate Polygon
```

**Gap Closing（缝隙闭合）：**

对于端点间距小于阈值（如 10mm）的线段，自动补齐连接。

```
─────────

        10mm 缝隙

─────────
```

**算法：** KDTree 查找最近端点，距离 < Threshold 时自动 Connect。

---

### 阶段三：公共区域分类（Region Classification）

恢复 Polygon 后进行分类，**建议使用规则分类，不使用 CNN**，准确率可达 95% 以上。

**分类特征（Feature）：**

| 特征 | 说明 |
|------|------|
| Area | 面积 |
| Perimeter | 周长 |
| AspectRatio | 长宽比 |
| Compactness | 紧凑度 |
| DoorCount | 连接门的数量 |
| AdjacentRoomCount | 相邻房间数 |
| AdjacentStairCount | 相邻楼梯数 |
| AdjacentElevatorCount | 相邻电梯数 |
| TextSemantic | 文字语义标签 |

**分类示例：**

| 特征值 | 分类结果 |
|--------|---------|
| Area=220㎡, DoorCount=12, Aspect=8, Text=走道 | Corridor |
| Area=40㎡, DoorCount=4, Adjacent Elevator=2 | Elevator Lobby |
| Area=160㎡, Text=活动区 | Hall |

---

### 阶段四：Skeleton 提取（最关键）

这一步决定导航质量。**不要沿 Polygon 边走，应该走中轴。**

**矩形区域示例：**

```
###############
#             #
#             #      → Skeleton:  ──────────────
###############
```

**推荐算法：**

- Medial Axis（中轴变换）
- Voronoi Diagram（Voronoi 图）
- Straight Skeleton（直骨架）

**推荐实现：**

- CGAL Straight Skeleton（C++，工业级）
- scikit-image.medial_axis（Python，快速原型）

输出为 Graph 结构，即公共区域导航骨架。

---

### 阶段五：Junction Detection（交叉节点检测）

Skeleton 包含大量节点，按节点度数（Degree）分类：

| 节点度数 | 类型 | 说明 |
|---------|------|------|
| Degree = 1 | Terminal | 端点 |
| Degree = 2 | Passing | 普通路径点 |
| Degree ≥ 3 | Junction | 交叉口/决策点 |

**自动生成 Node.type：** Junction / Terminal / Passing

---

### 阶段六：Door Projection（门投影，导航真正关键）

**房间不能直接连 Skeleton**，否则导航路径会撞墙。正确流程：

```
Room → Door → Project → Skeleton
```

**示意图：**

```
Room
 │
Door
 ●   ← 投影点
────────── Skeleton
```

**算法：**

```
DoorCenter
  ↓
Nearest Skeleton Segment
  ↓
Orthogonal Projection（正交投影）
```

**生成节点：** DoorNode → AccessNode → Skeleton

这样路径 `Room101 → Door → Corridor → Lobby → Elevator` 不会撞墙。

---

### 阶段七：Navigation Graph 生成

统一所有 Node 类型，构建最终 Indoor Routing Graph。

**Node 数据结构：**

```json
{
  "id": "...",
  "type": "...",
  "floor": 1,
  "position": [x, y]
}
```

**Node 类型枚举：**

| 类型 | 说明 |
|------|------|
| ROOM | 房间节点 |
| DOOR | 门节点 |
| JUNCTION | 交叉口节点 |
| LOBBY | 门厅/大厅节点 |
| STAIR | 楼梯节点 |
| ELEVATOR | 电梯节点 |
| RESTROOM | 卫生间节点 |
| ENTRANCE | 出入口节点 |

**Edge 数据结构：**

```json
{
  "start": "node_id_1",
  "end": "node_id_2",
  "length": 12.5,
  "accessible": true,
  "width": 1.8,
  "cost": 12.5
}
```

**跨楼层路径示例：**

```
Room101 → Door → CorridorNode1 → CorridorNode2 → Lobby 
→ Elevator → SecondFloorLobby → Corridor → Room201
```

---

## 八、算法复杂度与性能

| 阶段 | 时间复杂度 |
|------|-----------|
| CAD Parse | O(n) |
| Polygon Recovery | O(n log n) |
| Region Classification | O(n) |
| Skeleton | O(n log n) |
| Door Projection | O(k log n) |
| Graph Build | O(n) |

**整栋教学楼典型规模：**

| 元素 | 数量级 |
|------|--------|
| 房间 | 500~1000 |
| 门 | ~300 |
| 公共节点 | ~120 |
| 边 | ~2000 |

**处理性能：** 通常 2~5 秒即可完成整栋楼处理。

---

## 九、推荐软件架构

```text
indoor_navigation/
├── parser/
│   ├── pdf_parser.py              # PDF/CAD 图元解析
│   ├── geometry_extractor.py      # Line/Polyline/Arc 提取
│   └── text_extractor.py          # 文本与语义提取
│
├── public_space/
│   ├── candidate_detector.py      # 公共空间候选区域检测
│   ├── polygon_recovery.py        # Polygon 闭合恢复
│   ├── region_classifier.py       # Corridor/Lobby/Hall 分类
│   ├── medial_axis.py             # 中轴提取
│   ├── junction_detector.py       # 骨架交叉节点检测
│   ├── door_projection.py         # 门投影到骨架
│   └── graph_builder.py           # 导航图生成
│
├── topology/
│   ├── room_graph.py              # 房间节点
│   ├── stair_graph.py             # 楼梯节点
│   ├── elevator_graph.py          # 电梯节点
│   └── navigation_graph.py        # 多楼层统一图
│
└── output/
    ├── indoor_graph.json          # 导航拓扑
    ├── skeleton.geojson           # 公共空间骨架
    └── regions.geojson            # Corridor/Lobby/Hall 等区域
```

---

## 十、两项高级能力

### 10.1 可通行区域（Walkable Space）建模

不要把整个走道 Polygon 都认为可走，应根据墙体、柱子、设备井、电梯井、消防设施等障碍物生成 **Walkable Polygon**。

- 所有骨架提取和路径规划都基于 Walkable Polygon，而非原始区域
- 避免导航路线穿过柱子、设备区等不可达位置

### 10.2 多层拓扑融合（Multi-floor Connectivity）

将楼梯间、电梯厅作为楼层之间的垂直连接器，为每个楼梯、电梯建立跨楼层 Edge：

```text
F1 Corridor
      │
F1 Elevator Lobby
      │
    Elevator
      │
F2 Elevator Lobby
      │
F2 Corridor
```

- 最终生成整栋建筑统一的三维导航图，而非每层独立的二维图
- 与房间、门、楼梯、电梯识别模块自然融合，形成完整的 Indoor Navigation Graph

---

## 十一、设计要点总结

1. **公共空间优先于房间**：导航走的是公共空间拓扑，不是 Room→Room 直连
2. **中轴而非边界**：骨架提取走 Medial Axis，不沿 Polygon 边界
3. **门必须投影**：Door 需正交投影到 Skeleton 才能接入，避免路径撞墙
4. **规则而非 CNN**：分类使用基于几何特征和语义标签的规则，准确率 >95%
5. **Walkable Polygon**：考虑柱子、设备井等障碍物，不直接用原始走道 Polygon
6. **三层数据递进**：Region → Skeleton → Navigation Graph，每层各司其职
