# `result/ble_deployment.json` 数据结构与字段说明

> 文档版本：1.0 ｜ 对应数据版本：`schemaVersion 1.0`，61 信标（P0 密度修复前基线，commit `88c3549` 状态）
> 生成方式：`generatedBy = "pathai-manual-deploy"`，经 `render_interactive.py` 部署模式拖拽/点选导出，由人工确认后入库 `result/`。

## 1. 文件定位

`ble_deployment.json` 是**信标部署方案（位置规划）**，描述每枚 iBeacon 的部署位置与属性。**它不直接内嵌地图几何**（墙体/房间多边形在 `school_building_01_map_v9.geojson`），与地图的关联靠 **ID 引用 + 描述字段**：

- 通过 `sourceNodeId` 关联到 geojson 的拓扑节点（`topology.nodes` 中的 TR/TD/TI 等）。
- 通过 `adjacentRooms` 关联到 geojson 的房间要素。
- 通过 `coordinates` 与 geojson 共用同一坐标系（米，原点在地图左下角，Y 向上为正）。

### 坐标系约定
- `coordinates: [x, y]`，单位**米**，与 `school_building_01_map_v9.geojson` 同源。
- `floor: 1 | 2` 为物理楼层，不进入坐标值，仅作分层标识。
- 标定常量（`parse_cad_pdf.py`）：`SCALE=0.0529 m/pt`、原点 `(2019.1, 1154.8) pt`、Y 翻转。

## 2. 顶层结构

| 字段 | 类型 | 含义 |
|---|---|---|
| `schemaVersion` | string | 数据格式版本，当前 `"1.0"` |
| `generatedBy` | string | 生成工具标识，当前 `"pathai-manual-deploy"` |
| `generatedAt` | string | 生成时间戳（ISO 8601，UTC） |
| `beacons` | array\<Beacon\> | 信标对象数组（当前 61 枚） |
| `summary` | object | 统计汇总，见 §4 |

## 3. 单信标字段（Beacon 对象）

> 出现率：`61/61` 为全部信标必填；标注 `n/61` 的为可选字段（仅部分信标含）。

### 3.1 标识与协议字段

| 字段 | 类型 | 必填 | 含义 / 取值 |
|---|---|---|---|
| `beaconId` | string | ✅ | 信标唯一编号，格式 `BK-{floor:02d}-{seq:03d}`，如 `BK-01-001` |
| `uuid` | string | ✅ | iBeacon UUID，全楼统一 `B9407F30-F5F8-466E-AFF9-25556B57FE6D` |
| `major` | number | ✅ | iBeacon major（方案/楼层标识），当前 `1` 或 `2` |
| `minor` | number | ✅ | iBeacon minor（信标序号），`10101` 起递增 |
| `coordinates` | array\<number,2\> | ✅ | 平面坐标 `[x, y]`（米，同源 geojson 坐标系） |

### 3.2 部署位置与安装参数

| 字段 | 类型 | 必填 | 含义 / 取值 |
|---|---|---|---|
| `floor` | number | ✅ | 物理楼层：`1`（52 枚）/ `2`（9 枚） |
| `locationDesc` | string | ✅ | 位置人类可读描述，如 `1F 电梯口（II-01#EL）· 门套或呼梯板旁` |
| `mountType` | string | ✅ | 安装方式：`wall`（壁装，54）/ `door_frame`（门框，7） |
| `installHeight` | number | ✅ | 安装高度（米），统一 `2.2` |
| `txPower` | number | ✅ | 发射功率（dBm），统一 `-10` |
| `broadcastInterval` | number | ✅ | 广播间隔（ms），统一 `300` |
| `batteryModel` | string | ✅ | 电池型号，统一 `CR2477` |
| `expectedLifespan` | number | ✅ | 预期寿命（年），统一 `5` |
| `installDate` | string | 43/61 | 现场安装日期 `YYYY-MM-DD`，如 `2026-08-14` |
| `direction` | string | 13/61 | 朝向描述（带方向性布点用），如 `南` |

### 3.3 语义与来源（L2 定位层布点角色）

> 这些字段表达的是**定位层（L1–L5 模型中的 L2）的布点角色**，不是 L5 应用语音语义。

| 字段 | 类型 | 必填 | 含义 / 取值 |
|---|---|---|---|
| `semanticTag` | string | ✅ | L2 布点大类：`trilateration_route_base`（路线基线，43）/ `trilateration_route_fill`（密度填补，10）/ `manual_deploy`（人工补点，8） |
| `subType` | string | ✅ | 细分类型：`base`(21) / `fill`(17) / `dir`(13) / `manual_deploy`(8) / `elevator_door`(2) |
| `sourceNodeId` | string | ✅ | 挂靠的地图拓扑节点 ID（geojson `topology.nodes`），如 `F1-TF-0011`、`F1-TI-0023` |
| `sourceNodeType` | string | ✅ | 来源节点类型：`intersection`(24) / `route_fill`(10) / `route_corridor`(7) / `doorway`(6) / `manual_adjusted`(4) / `facility`(2) / `""`(8，manual_deploy 无挂靠) |
| `adjacentRooms` | array\<string\> | ✅ | 相邻房间 ID 列表（R6 校验要求门口/交叉口信标必填），如 `["F1-CR-0053","F1-RM-0066"]` |
| `riskLevel` | number\|string | ✅ | 定位风险等级：`low` / `0.3` / `0.5` / `1` / `2`（数值越大风险越高） |

### 3.4 审计回溯字段（可选，微调/复核用）

| 字段 | 类型 | 必填 | 含义 |
|---|---|---|---|
| `originalPlannedCoordinates` | array\<number,2\> | 41/61 | 审计回溯用的原始规划坐标（人工微调前的位置） |
| `originalSourceNodeId` | string | 11/61 | 原始来源拓扑节点 ID（位置微调后节点变更时记录） |
| `originalSourceNodeType` | string | 11/61 | 原始来源节点类型 |
| `originalLocationDesc` | string | 11/61 | 原始位置描述，含 `原:` 前缀，如 `1F 交叉口（交叉口55） · 原: 交叉口11` |

## 4. `summary` 统计汇总

```json
{
  "total": 61,
  "byFloor":   { "1": 52, "2": 9 },
  "bySemantic":{ "trilateration_route_base": 43,
                 "trilateration_route_fill": 10,
                 "manual_deploy": 8 },
  "byMount":   { "door_frame": 7, "wall": 54 }
}
```

> ⚠️ `summary.total` 必须与 `beacons.length` 一致。本版为 61，与正文一致；历史上曾出现扩容后 summary 未同步（total=61 但正文 98）的数据质量缺陷，已通过回滚修正。

## 5. 与地图的关联方式（消费方须知）

1. **拓扑节点关联**：`sourceNodeId` 应能在 `school_building_01_map_v9.geojson` 的 `topology.nodes` 中找到对应 `id`；导航引擎据此把信标挂到房间中心(TR)/门(TD)/走廊交汇(TI)节点上。
2. **房间关联**：`adjacentRooms` 中的房间 ID 应能在 geojson 的房间要素中找到；用于"进入缓冲带→播报经过房间"等 L5 交互。
3. **坐标对齐**：`coordinates` 与 geojson 共用同一米制坐标系，可直接叠加渲染，无需额外变换。
4. **iBeacon 广播**：定位端（`fingerprint-collector` 小程序、`validate_beacon_deployment.py`）按 `uuid`+`major`+`minor` 扫描匹配，再据 `coordinates`/`floor` 做三点/指纹定位。

## 6. 版本与变更说明

| 版本 | commit | 信标数 | 说明 |
|---|---|---|---|
| 密度修复前基线 | `88c3549` | 61 | 人工部署 + adjacentRooms 注入（R6） |
| P0 密度修复 | `44c2658` | 98 | F1 按 5m 有效范围补点实现全覆盖、降 GDOP 退化 |
| **当前生效（回滚）** | `fix-rollback-ble-61` @ `533a006` | **61** | 按用户要求撤销 P0 补点，恢复 `88c3549` 状态 |

> 后续若重新启用 P0 密度方案，应同步更新 `summary` 与 `beacons`，并跑 `validate_beacon_deployment.py` 回归。

## 7. 相关工具与文档

- 生成/编辑：`src/rendering/render_interactive.py`（部署模式拖拽/点选 → 导出 `ble_deployment.json`）
- 校验：`src/tools/validate_beacon_deployment.py`（含 R6：门口/交叉口信标须有 `adjacentRooms`）
- 坐标修正：`src/tools/fix_beacon_coords.py`、`src/tools/improve_p0_coverage.py`
- 语义审计：`docs/17-信标部署语义审计报告.md`
- 字段增补背景：`docs/18-蓝牙信标语义优化TODO.md`（R6 要求注入 `adjacentRooms`）
- 指纹采集消费方：`fingerprint-collector/`（把信标作为分区采集的参考锚点候选）
