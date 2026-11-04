# Spec：现场采集工具集成到导航小程序（静态标定 + 网格指纹）

> 版本 0.1.1（草案，待评审确认）
> 日期：2026-11-03
> 变更：0.1.1 补入采集页地图缩放/平移与选点命中随视口变换的要求（§3.7、§4.1、§7、D7）；仍为草案，未经评审。
> 关联：`docs/28`（标定方案）、`docs/31`（动态标定执行手册）、`docs/24` §4/§6、独立工具 `positioning-calibrator/`、`fingerprint-collector/`

---

## 1. 背景与目标

现有静态标定（`positioning-calibrator`）与网格指纹采集（`fingerprint-collector`）是两个独立微信小程序，与导航主小程序（`client/`）分离，现场需在三个小程序间切换，且独立工具**无地图模式**（列表选点），找点靠 locationDesc 文字描述。

**目标**：把两个采集工具集成进导航小程序，统一入口、提供地图选点/定中交互、复用导航小程序既有的 BLE 扫描栈与地图画布，数据格式与现有 PC 工具链完全兼容（`fit_location_params.py`、指纹入库流程零改动）。

**非目标**：
- 不改变后端（零后端改动；数据仍本地导出 + 微信分享回传）
- 不替代/不删除两个独立小程序（保留作为降级手段）
- 不影响导航/定位算法与播报（采集页面独立，不进入导航会话）

---

## 2. 入口与门禁（决策 D1）

- 设置页新增「现场采集工具」开关（`cfg:fieldCollectionTool`，kv 持久化，**即时生效**——仅控制入口可见性，与标定模式"重启生效"不同）。
- 开关开启后，首页（`pages/map/map`）地图右上角显示「**采集**」按键。
- 点击「采集」→ **横向弹出面板**（从按键处横向展开，两个并排按键）：
  - 「静态标定」→ `pages/collect/static`
  - 「网格指纹」→ `pages/collect/fingerprint`
- 开关默认关闭；视障终端用户默认不可见。
- 无障碍：弹出面板与两按键带 `aria-label`，与现有设置页交互一致。

---

## 3. 静态标定页面（`pages/collect/static`）

### 3.1 点位数据（决策 D2）

- **预置点位**：docs/28 §3.1 配额的 20 个标定点（F1 16 / F2 4），含真值坐标、楼层、场景类别，由脚本从台账生成数据文件 `client/data/calibration_points.ts`（AUTO-GENERATED，来源与 `positioning-calibrator` 相同的点位清单）。
- **可补标**：地图**长按**任意位置新增自定义标定点——真值坐标取长按处的地图坐标，需输入点位名称；自定义点与预置点同样参与采集与导出（`custom: true` 标记）。
- 点位可编辑名称/备注、可删除（仅自定义点可删，预置点只可隐藏）。

### 3.2 地图模式

- 复用导航地图画布渲染（楼层数据、缩放/平移、楼层切换），叠加点位图层：
  - 未采集点：空心圆 + 点位名
  - 已采集点：实心绿圆 + 次数角标
  - 当前定位估计：与导航地图同样式（可信蓝/恢复黄）
- 楼层切换沿用导航地图的楼层控件。

### 3.3 定中与到达确认（决策 D3）

语义：**到达确认以现场参考系为准（人工判定），App 不做位置门禁**——真值点现场可通过门牌、墙角、柱子、预设标记等参照物确认，BLE 定位估计在走廊外不保证可信，不能作为到达判定依据。

交互流程：

```
采集者走到目标点位（通过现场参照物确认到达）
  → 点击地图上该点位
  → 弹出点位确认卡（点位名/楼层/真值坐标 + 【参考系核对提示】）
  → 确认后 wx.vibrateShort 振动反馈（两短振）
  → 倒计时自动开始（默认 60 s，可在设置区调 30/60/120 s）
  → 期间屏幕显示倒计时 + 已收到的逐信标窗口中位数实时表
  → 倒计时结束自动写入本地记录，toast「已记录」，点位变绿
```

- **现场定位数据同步记录**：采集起止期间缓存当前定位估计快照（`estimateAtStart/estimateAtEnd`，含 quality/confidence），供 PC 侧分析"真值点的定位表现"。
- **辅助提示（非门禁）**：确认卡上显示当前估计与点位真值的平面距离（`≈3.2 m`），仅作参考——定位不可信时显示"—"。

### 3.4 采集语义（与 `positioning-calibrator` 同口径）

- 扫描窗口 2 s、截断 -100~-20 dBm、逐信标中位数（偶数取中间两值平均后取整）——复用 `client/core/ble/scan-window.ts`，与生产同口径。
- 每点产出：`{beaconId, n, rssiMedian, rssiMin, rssiMax, rssiStd, truthDistanceM}`。
- `truthDistanceM` 按**三维斜距**：`√((x−xᵢ)² + (y−yᵢ)² + (installHeightM−1.2)²)`（spec 0.3.3 口径，与 `sampler.js`、`fit_location_params.py` 一致）。
- 同一点位可重复采集（`captureIndex` 递增），支持跨时段多次。

### 3.5 同一点多次采集的记录规则（静态标定）

采用**会话追加制**：每次倒计时采集 = 一条独立"会话"记录，同一点的多条会话互不覆盖、独立保留。

- **记录标识**：`pointId + startedAt`（ISO 时刻）唯一定位一条会话；同点第 N 次采集在 UI 上显示为 `captureIndex: N`（仅展示用，不是主键）。
- **存储**：本地 kv 追加式数组，按 `startedAt` 排序；采集完成后立即落盘，中断/杀进程不丢已完成会话。
- **结构示例**（schema 与 `positioning-calibrator` 导出一致）：

```json
{
  "points": [
    {
      "pointId": "CAL-F1-03",
      "truth": [-31.2, 8.7], "floorId": "1", "label": "走廊交叉口", "custom": false,
      "sessions": [
        { "startedAt": "2026-11-05T09:12:00Z", "durationS": 60, "captureIndex": 1,
          "estimateAtStart": {"x": -30.8, "y": 9.1, "quality": "reliable", "confidence": 0.9},
          "observations": [ { "beaconId": "BK-01-007", "n": 28, "rssiMedian": -62, "rssiMin": -71, "rssiMax": -55, "rssiStd": 3.8, "truthDistanceM": 5.31 } ] },
        { "startedAt": "2026-11-05T14:03:00Z", "durationS": 60, "captureIndex": 2,
          "observations": [ /* 第二次（跨时段） */ ] }
      ]
    }
  ]
}
```

- **导出语义**：默认导出全部会话；导出页支持按点位勾选、按会话勾选（可剔除明显废样，如现场有人为干扰的那次）。
- **PC 侧语义**：`fit_location_params.py` 把每条会话的每个 (点， 信标) 观测对视为**独立观测**参与拟合——多次采集（跨时段）正是为了覆盖不同多径/人流环境，**不去重、不平均**。

### 3.6 导出

- 导出 JSON **schema 与 `positioning-calibrator` 的 `positioning_calibration_static` 完全一致**（PC 侧 `fit_location_params.py` 零改动），新增可选字段 `estimateAtStart/estimateAtEnd`、`captureIndex`（向后兼容，PC 侧忽略未知字段）。
- 写 `USER_DATA_PATH` 文件 + 剪贴板兜底 + 微信分享文件。
- 导出前显示统计（已采点数 / 总会话数），支持按点位/会话多选导出。

### 3.7 地图缩放与平移（决策 D7）

- **复用导航地图视口**：采集页底图与 `pages/map` 同源，视口数学统一走
  `client/core/map/floor-map-viewport.ts`（世界坐标 ↔ 屏幕坐标、`fit`/`panBy`/`zoomAt`、
  缩放上下限与 drag-out 约束），不另写一套变换。
- **手势**：单指平移、双指围绕两指中点缩放；touch 事件绑定在 canvas 节点自身，
  覆盖控件（楼层/倒计时/导出/缩放键）为 canvas 的兄弟节点并显式 `z-index`，
  避免 `catchtouch` 容器吞掉按钮 `tap`（已知坑）。
- **单指精细调整**：提供「放大 / 缩小 / 全图」三个可点按控件，以画布中心为锚点按档位缩放；
  **不采用双击缩放**——单击已用于选中采集点位，双击会与选点语义冲突并引入延迟。
- **上下限**：`scale ∈ [fitScale, fitScale × 8]`（与导航地图一致）；到达上下限后继续点按
  或继续双指缩放时 `scale` 与偏移量保持不变（不漂移）。
- **选点精度**：点位命中半径固定为**屏幕像素**（静态标定 24 px / 网格指纹 16 px），
  命中判定先把点位投影为屏幕坐标再与点击点比较。缩放/平移后同一点选的屏幕半径不变，
  其对应的世界范围按 `scale` 收窄——放大后相邻点位更易区分，选点更精确，且不产生偏移。
- 切换楼层沿用既有行为：新层全图适配居中（`setFloor` + `fit`）。

---

## 4. 网格指纹页面（`pages/collect/fingerprint`）

### 4.1 点位数据（决策 D4）

- **内置全楼 1434 点网格**（`result/fingerprint_grid.json`，2 m 间距；F1 走廊面 710 + 加密 7 + F2 29 + 7 + …），由脚本生成 `client/data/fingerprint_grid.ts`（AUTO-GENERATED）。
- 地图模式显示**全部 1434 点**（小圆点，按楼层切换；已采集点高亮绿 + 角标），**仅现场实际采集的点才写入记录**。
- 点位支持按区域/名称过滤与「未采集」筛选，便于按分区推进。
- 地图缩放/平移、可点按缩放控件与选点命中随视口变换的行为与 §3.7 **完全一致**
  （命中半径 16 px；放大后 2 m 网格点更易精确选中）。

### 4.2 采集交互（与静态标定类似）

```
采集者走到网格点（现场参照物确认）
  → 点击地图上该点
  → 确认卡（fpId / 楼层 / 坐标 / 参考系核对提示 + 距离辅助提示）
  → 确认 → 振动两短振 → 短倒计时 3 s（快照语义，走-停-拍）
  → 自动写入记录，toast「已记录 fpId」，点位变绿
```

- 每点快照：`{fpId, floor, coordinates, captureIndex, collectedAt, beaconCount, beacons:[{uuid,major,minor,rssi,txPower,accuracy}]}`（逐信标为 2 s 窗口中位数）。
- 同一点可多次采集（跨时段，`captureIndex` 递增）——与 `fingerprint-collector` 语义一致。
- **不带分区（zones）**：`zoneId/zoneName/anchor/relCoordinates` 恒为 null（决策 D5）。

### 4.3 同一点多次采集的记录规则（网格指纹）

采用**记录追加制**（扁平数组，与 `fingerprint-collector` 的 `fp_records_v1` 一致）：

- **记录标识**：`fpId + collectedAt`（ISO 时刻）；`captureIndex` = 该 fpId 当前已有记录数 + 1（导出时重算，删除中间记录后自动收缩）。
- **存储**：本地 kv 扁平数组 `[{fpId 记录}, {fpId 记录}, …]`，每次快照立即落盘；同一 fpId 出现多条记录即为多次采集。
- **示例**：

```json
{ "records": [
  { "fpId": "F1-FP-0123", "floor": 1, "coordinates": [-30.0, 8.0],
    "captureIndex": 1, "collectedAt": "2026-11-05T09:20:00Z",
    "zoneId": null, "zoneName": null, "anchor": null, "relCoordinates": null,
    "regionType": "corridor", "beaconCount": 9,
    "beacons": [ { "uuid": "B940…", "major": 1, "minor": 7, "rssi": -63, "txPower": -59, "accuracy": 1.2 } ] },
  { "fpId": "F1-FP-0123", "captureIndex": 2, "collectedAt": "2026-11-05T14:11:00Z", "…": "第二次（跨时段）" }
] }
```

- **导出语义**：`ble_fingerprint_collection` 的 `records` 数组原样携带全部记录（含同点多条）；导出页支持按点位勾选、按单条记录勾选剔除废样。
- **入库语义**：指纹入库按 `(fpId, captureIndex)` 视为该点的多组独立快照，与现有 `fingerprint-collector` 产物的消费方式完全相同。

### 4.4 导出

- schema `ble_fingerprint_collection` **1.1.0 完全一致**，`dataset` 字段标注采集数据集（默认 `routes`），PC/入库流程零改动。
- 导出与分享机制同 §3.5。

---

## 5. 架构与复用

```
client/
├── data/
│   ├── calibration_points.ts      # AUTO-GENERATED：20 标定点
│   └── fingerprint_grid.ts        # AUTO-GENERATED：1434 网格点
├── core/collection/
│   ├── static-sampler.ts          # 静态标定采样（2s 窗口中位数聚合 → 逐信标统计 + 3D truthDistanceM）
│   ├── fp-snapshot.ts             # 指纹快照（2s 窗口中位数 → 逐信标快照）
│   ├── points-store.ts            # 点位/记录本地存储（wx 同步 kv；点位 CRUD + 记录追加）
│   └── export-builder.ts          # 两个 schema 的导出构造（USER_DATA_PATH + 剪贴板）
└── pages/collect/
    ├── static/                    # 静态标定页
    └── fingerprint/               # 网格指纹页
```

- **BLE 扫描**：复用 `core/ble/scan-window.ts`（生产同口径）；采集页进入时启动扫描、离开时停止，不与导航会话并发（页面互斥，导航中不显示采集入口）。
- **地图画布**：复用 `pages/map` 的渲染模块（墙线/房间/楼层切换），点位与定位估计作为新图层叠加；不复制渲染代码，抽出可复用的图层接口。
- **振动**：`wx.vibrateShort({ type: 'medium' })` 两次短振（定中确认）。
- **数据生成脚本**：`src/tools/gen_client_collection_data.py`——从 `result/ble_deployment.json` + 标定点清单 + `result/fingerprint_grid.json` 生成两个 TS 数据文件（含信标 `installHeightM`）。

---

## 6. 状态与边界

| 场景 | 行为 |
|---|---|
| 导航会话进行中 | 首页不显示「采集」入口（会话结束后恢复） |
| 采集页内位置不可信（lost） | 距离辅助提示显示"—"；采集照常进行（静态标定允许在定位不可信点采集） |
| 倒计时中切后台/锁屏 | 计时器暂停（`onHide`），回前台继续；不静默作废 |
| 倒计时中蓝牙关闭 | 暂停倒计时并提示开启蓝牙 |
| 存储上限 | 与两个独立工具一致：记录条数超上限时导出前提示（静态 5000 条 / 指纹 20000 条） |
| 地图包版本 | 采集记录写入 `mapVersion`（从已加载地图包取）；版本与点位数据不一致时页面顶部提示 |

---

## 7. 测试计划

- **单元测试（jest，纯逻辑）**：`static-sampler`（窗口/中位数/3D 真值距离）、`fp-snapshot`、`points-store`（CRUD/追加/captureIndex）、`export-builder`（与 `positioning-calibrator/tests`、`fingerprint-collector` 导出样例的 schema 断言对齐）。
- **单元测试（地图视口/选点）**：基于真实地图包 `2026-10-25.1` 断言
  ①fit/缩放/平移后 `toScreen ↔ fromScreen` 自洽；②缩放锚点世界坐标不变；
  ③点选命中随视口变换（放大后同一世界偏移超出屏幕命中半径 → 不命中）；
  ④`scale` 到达上下限后继续缩放偏移量不变（不漂移）。
- **兼容性验收**：导出文件喂给 `src/tools/fit_location_params.py` 与指纹入库脚本，能正确解析（金样比对：同一静态点位在两个工具里各采一次，导出结构 diff 仅允许新增可选字段）。
- **真机手工验收**：入口门禁、弹出交互、振动反馈、倒计时暂停/恢复、导出分享、1434 点渲染性能（低配机帧率）；
  采集页双指缩放/单指平移、± 与全图控件、放大后选点不偏移。

---

## 8. 决策记录

| # | 决策 | 依据 |
|---|---|---|
| D1 | 入口由设置页「现场采集工具」开关控制，默认关闭，即时生效 | 评审确认；避免暴露给视障终端用户 |
| D2 | 静态标定预置 20 点 + 地图长按补标自定义点 | 评审确认；docs/28 §3.1 配额 |
| D3 | 到达确认以**现场参考系人工判定**为准，App 不做位置门禁；采集时同步记录定位估计快照作现场定位数据 | 评审确认：BLE 估计在点位处不保证可信，不能作为到达依据 |
| D4 | 指纹内置全楼 1434 点展示，仅记录现场实际采集的点；单点快照语义（3 s） | 评审确认 |
| D5 | 不带 zones 分区（独立工具功能，导航版不引入） | 数据集按 routes 组织，简化集成 |
| D6 | 导出 schema 与两个独立工具完全一致，PC 工具链零改动 | 「标定一套、线上另一套」禁令；复算可审计 |
| D7 | 采集页复用 `FloorMapViewport` 的缩放/平移；提供 ± 与全图控件，不做双击缩放 | 避免另写变换导致选点偏移；单击已用于选点，双击会冲突；单指精细调整靠可点按控件 |
