# 指纹采集微信小程序（fingerprint-collector）

为 pathai 室内导航项目采集 **BLE 指纹库** 的现场工具。工程师带着手机走到每个 FP（Fingerprint Point）网格点，扫描周围 iBeacon 信标，记录 RSSI 指纹，导出 JSON 直接作为数据库导入原始数据。

> 设计依据：`docs/03-地图构建指南 §八`（指纹网格生成规则）、`docs/18-蓝牙信标语义优化TODO`（L2 定位层 = 三点定位 + 指纹网格，真值底座）。
> FP 网格由 `src/tools/generate_fingerprint_grid.py` 生成；本小程序内置的 `data/fingerprint_grid.js` 是从 `result/fingerprint_grid.json` / `result/fingerprint_grid_routes.json` 转换来的扁平快照。

---

## 1. 目录结构

```
fingerprint-collector/
├── app.js / app.json / app.wxss       # 全局：场地配置 + 会话元数据
├── project.config.json                # 微信开发者工具工程配置（需替换 appid）
├── sitemap.json
├── data/
│   ├── fingerprint_grid.js            # AUTO-GENERATED：FP 网格扁平模块（全楼1434 + 路线647）
│   └── reference_elements.js          # AUTO-GENERATED：锚点候选（信标98 + 拓扑节点465），供分区选锚点
├── utils/
│   ├── beacon.js                      # iBeacon 扫描封装（wx.startBeaconDiscovery + onBeaconUpdate）
│   ├── fpstore.js                     # 采集记录存储 / 会话 / 导出 JSON（支持按区导出）
│   └── zones.js                       # 分区(Zone)管理：CRUD + 相对坐标计算
├── pages/
│   ├── index/                        # 仪表盘：采集人、网格切换、进度、入口
│   ├── points/                       # 选 FP 点（楼层筛选 + 搜索，已采集置顶）
│   ├── collect/                       # 核心：实时信标列表 + 采集此点指纹（可选活动分区）
│   ├── zones/                         # 分区管理：列表 + 新增/编辑/删除 + 锚点选择
│   └── export/                        # 复核记录 + 按分区多选导出 JSON
└── tools/
    ├── convert_fingerprint_json.py     # 导出 JSON -> 数据库原始数据（CSV / SQL，含分区列）
    └── gen_reference_elements.py       # 由 result/* 生成 data/reference_elements.js
```

---

## 2. 在微信开发者工具中运行

1. 微信开发者工具 → 导入项目 → 选择本 `fingerprint-collector/` 目录。
2. 把 `project.config.json` 里的 `"appid": "wxREPLACE_WITH_YOUR_APPID"` 换成你的小程序 AppID（iBeacon 扫描需真实 AppID，测试号可能无法扫描）。
3. 编译运行。真机调试时，在手机上打开小程序并**开启蓝牙**。
4. ⚠️ 微信 `startBeaconDiscovery` 后台限制：iOS 需 `app.json` 配置 `requiredBackgroundModes: ["ble"]`（如需后台持续扫描），本版本默认前台采集即可。

---

## 3. 采集流程

1. **首页** 填写「采集人」，选择网格（路线网格 1m / 全楼网格 2m）。
2. **（可选）分区管理** 进入「分区管理」创建分区：填名称、选楼层、从锚点候选里挑一个**参考锚点元素**（信标或拓扑节点），保存。每个分区独立持有一个锚点。
3. **选点** 在列表里点一个 FP（已采集的会带「N 次」徽标并置顶）。
4. **采集** 进入后先选「采集分区」（默认「无分区」= 绝对坐标）；点「开始扫描」→ 等出信标 → 点「采集此点指纹」（对当前信标列表拍快照，存为一条样本）。
   - 若选了分区：该点坐标以**相对锚点**方式存储（`relCoordinates = 绝对坐标 − 锚点绝对坐标`），同时绝对坐标 `coordinates` 仍保留。采集页会实时显示本点相对坐标预览。
   - 若「无分区」：仅以绝对坐标存储。
   - 同一 FP 可多次采集（不同朝向/时间），每次 `captureIndex+1`。
5. **导出** 在导出页：勾选要导出的**分区（多选）**后点「导出选中分区 JSON」；或点「导出全部 JSON」导出所有记录。导出文件写入 `wx.env.USER_DATA_PATH` 的 `.json` + 复制到剪贴板 + 调起分享。

> **保留所有采集结果**：所有样本全部持久化于本地存储（按 FP 累积多次样本），清空仅在首页「清空全部数据」主动触发；按分区导出是**过滤视图**，不会删除任何数据。

---

## 4. 导出 JSON 结构（可直接入库的原始数据）

```jsonc
{
  "schemaVersion": "1.1.0",
  "type": "ble_fingerprint_collection",
  "venueId": "school-building-01",
  "venueName": "初中学部 1# 教学楼",
  "dataset": "route",                 // 'route' | 'full'
  "exportedAt": "2026-08-21T...",
  "appVersion": "1.0.0",
  "operator": "张三",
  "device": { "model": "...", "system": "...", "platform": "ios", "version": "..." },
  "beaconConfig": { "uuid": "B9407F30-F5F8-466E-AFF9-25556B57FE6D", "majors": [1, 2] },
  "exportScope": "selected_zones",    // 'selected_zones'（按区导出）| 'all'（全部）
  "zones": [                          // 本次导出涉及的分区元信息（按区导出时填充）
    { "zoneId": "Z01", "name": "1F-东区", "floor": 1,
      "anchor": { "anchorId": "BK-01-001", "anchorType": "beacon", "anchorLabel": "信标 BK-01-001", "abs": [-30.25, -75.08] } }
  ],
  "recordCount": 12,                  // 样本(记录)数
  "fpCount": 5,                       // 覆盖的 FP 点数
  "records": [
    {
      "fpId": "FP-1-R001",
      "floor": 1,
      "coordinates": [-41.064, 14.360],   // 绝对坐标（始终保留）
      "relCoordinates": [-10.814, 89.44],// 相对锚点坐标（仅当属于某分区时存在）
      "zoneId": "Z01",                   // 所属分区；无分区为 null
      "zoneName": "1F-东区",
      "anchor": { "anchorId": "BK-01-001", "anchorType": "beacon", "anchorLabel": "信标 BK-01-001", "abs": [-30.25, -75.08] },
      "regionType": "normal",         // 'normal' | 'safe'
      "captureIndex": 1,              // 该 FP 第几次采集
      "collectedAt": "2026-08-21T11:00:00.000Z",
      "beaconCount": 4,
      "beacons": [
        { "uuid": "B9407F30-...", "major": 1, "minor": 10101, "rssi": -65, "txPower": -10, "accuracy": 2.3 }
      ]
    }
  ]
}
```

**字段说明**：每条 `record` = 某个 FP 某一次采集时、周围可见信标的 RSSI 向量。`beacons[]` 里的 `accuracy` 为 iOS 测算距离（米），Android 常为 `null`；`txPower` 为 measuredPower（iOS 提供，Android 常 `null`）。RSSI 始终存在，是入库主字段。`relCoordinates` 与 `anchor` 仅在分区采集时写入，便于在分区局部坐标系内重建指纹、跨设备坐标对齐。

---

## 4.5 分区采集（Zone-based Collection）

- **目的**：大场地分片作业，单个分区用「锚点 + 相对坐标」描述，降低对全局绝对坐标精度的依赖，也便于把坐标对齐到分区内已知参考物。
- **锚点来源**：`data/reference_elements.js`（由 `tools/gen_reference_elements.py` 生成），汇集**信标（98）**与**拓扑节点（全楼 465）**的绝对坐标，三者与 FP 网格同一坐标系。
- **相对坐标计算**：`rel = 采集点绝对坐标 − 锚点绝对坐标`（平面偏移，同坐标系）。采集页实时预览，导出时随记录写入 `relCoordinates`。
- **按区导出**：导出页可多选分区；导出 JSON 的 `records` 仅含选中分区内的样本，并附带 `zones` 元信息（锚点 id/类型/坐标），下游可据此把相对坐标还原为绝对坐标。

> 锚点候选生成：`python tools/gen_reference_elements.py` → 写入 `data/reference_elements.js`（依赖 `result/ble_deployment.json` 与 `result/school_building_01_map_v9.geojson`）。

---

## 5. 导出 JSON → 数据库

用 `tools/convert_fingerprint_json.py` 把小程序导出的 JSON 展开成**行级观测表**（一行 = 一个 FP 某次采集中、一个信标的观测），可直接 `COPY` / `LOAD DATA` 入库：

```bash
# CSV（默认，UTF-8 BOM，Excel/数据库通用）
python tools/convert_fingerprint_json.py --in fingerprint_export_xxx.json --out samples.csv

# SQL（CREATE TABLE + INSERT，SQLite/MySQL 通用语法）
python tools/convert_fingerprint_json.py --in fingerprint_export_xxx.json --format sql --out samples.sql
```

目标表 `fingerprint_samples` 列：`fp_id, floor, x, y, region_type, capture_index, collected_at, uuid, major, minor, rssi, tx_power, accuracy, operator, venue_id, dataset, zone_id, zone_name, anchor_id, anchor_type, anchor_x, anchor_y, rel_x, rel_y`。

> 同一 (fp_id, capture_index) 会展开为多行（每个信标一行）。后续可在 SQL 层按 `AVG(rssi)` 聚合为「每 FP 每信标的平均指纹」用于离线定位匹配。`zone_*`/`anchor_*`/`rel_*` 列为分区采集附加信息，无分区记录这些列为空。

---

## 6. 已知限制 / 后续

- iBeacon `uuid` 当前写死为 pathai 全部信标的统一 uuid（见 `app.js` `beaconUuid`）。若未来分多 uuid，需在 `beacon.start([...])` 传入列表。
- Android 不回报 `accuracy`/`txPower`，入库时这两列为空，仅 RSSI 可用。
- 导出文件落在小程序沙箱 `USER_DATA_PATH`，真机需通过「分享文件」或开发者工具「文件系统」取出；如需直接上传服务器，可在 `export.js` 增加 `wx.uploadFile` 调用。
- 尚未接入 `wx.getLocation` 做「自动按当前坐标匹配最近 FP」——当前为手动选点（更可控，防止误采）。
```
