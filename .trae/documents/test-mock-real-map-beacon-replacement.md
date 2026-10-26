# 测试 mock 数据全面替换为真实地图与蓝牙信标数据

## Context

当前后端 202 个测试与客户端 19 个测试的 mock 数据均为合成样例：后端用 `2026-10-01.1.json`（小坐标系 (4,4)/(5,5)/(10,5)、placeId classroom-1f、3 个合成信标 txPower=-59），客户端硬编码合成 UUID `FDA50693-...` 与 BK-1/2/3 坐标。v9 真实地图包 `2026-10-25.1.json`（708 节点、56 地点、61 信标）与真实信标部署（uuid `B9407F30-F5F8-466E-AFF9-25556B57FE6D`、major=楼层、minor=10101 格式、txPower=-10）已入库。

目标：**所有测试的 mock 数据改用上述真实数据**，提升测试对真实业务数据的代表性；specs/ 无样例坐标引用、src/ 无独立测试文件，均不涉及。样例文件 `2026-10-01.1.json` 保留在 maps_data 不删（历史样例），仅测试不再引用。

## 数据替换策略（按断言类型）

| 断言类型 | 做法 |
|---|---|
| 版本/站点字面量（mapVersion、publishedAt、siteId） | 硬编码真实值，各文件顶部收敛为常量 |
| 标识（placeId、beaconId、uuid/major/minor、nodeId） | 硬编码真实 id（契约稳定，坐标会漂移） |
| 坐标/信标属性（x/y、txPower） | 模块级读一次真实地图 JSON，按 id 反查 |
| 几何断言（5m 到达、三点定位期望点） | 选定真实 id，距离由读取坐标现算；请求前先 assert 几何前提，失败即暴露选点问题 |
| 列表断言（places 过滤/排序/limit） | 从包内 places 动态推导期望 |
| test_schemas_contract | 仅换字面量，不引真实数据（保持契约与数据解耦） |

真实数据关键值（已验证）：publishedAt `2026-10-25T14:10:01Z`；1 层信标 BK-01-001(-30.25,-75.08)、BK-01-004(-18.777,-83.987)、BK-01-005(-36.91,-59.51)；F1-RM-0003 资源教室；F1-TF-0011 电梯节点 (-28.96,-74.89)。

## 分步实施（每批验证通过后提交，提交信息英文、注释中文）

### 批 1：后端 fixture 与低耦合测试
- [backend/tests/conftest.py](file:///d:/code/pathai/backend/tests/conftest.py) `SAMPLE_MAP_FILE` → `2026-10-25.1.json`
- [test_map_package.py](file:///d:/code/pathai/backend/tests/test_map_package.py)：`SAMPLE_PATH` 与版本断言；L176-199 路由兼容测试节点改真实（同层 blind 选 2 层已连通节点、跨层用 F1-TF-0011→F2 侧电梯边、normal 楼梯用 F1-TF-0001→F2-TF-0001，实施时先跑通再固化）；附带修复 `test_isolated_node_rejected` 追加的节点 id 在真实包已存在（改用 F1-TR-9001 + 唯一 roomId）
- [test_map_api.py](file:///d:/code/pathai/backend/tests/test_map_api.py)：版本/publishedAt/dataUrl 断言
- [test_intent_api.py](file:///d:/code/pathai/backend/tests/test_intent_api.py)、[test_schemas_contract.py](file:///d:/code/pathai/backend/tests/test_schemas_contract.py)：mapVersion 字面量
- 注意 test_map_api/intent 中按名称匹配用例：真实数据无"图书馆"等名 → 换真实地点名（unique match 用"资源教室"、歧义用"女卫生间"、类型过滤用 toilet；places 接口默认 limit=10，列表断言须带 `limit=50`）

验证：`python -m pytest backend/tests/test_map_package.py backend/tests/test_map_api.py backend/tests/test_intent_api.py backend/tests/test_schemas_contract.py -q` → 全量 `python -m pytest backend/tests -q`

### 批 2：定位测试 test_location_api.py
- `SAMPLE` 常量 → `2026-10-25.1`
- **三点定位**（L110-129）：信标换 BK-01-001/004/005，期望点取三角形外侧（各距信标 ≥4m——RSSI 上限 -20 对应 3.16m，过近会 400），用 `rssi_for` 按真实 txPower=-10 反推 RSSI；断言 quality=reliable、坐标偏差 <1.5m、confidence≥0.8
- 其余用例中 BK-01-002/003（真实包不存在，会被判未知信标→lost）换真实 id；跨层 majority/floorHint 用 1 层 vs 2 层真实信标
- **rich_maps/two_versions（MAP_VERSION_CONFLICT）**：读真实包 → 改 `mapVersion="2026-10-26.1"`、`publishedAt="2026-10-26T08:00:01Z"`（必须晚于真实 publishedAt，latest 按其排序）→ 写入 tmp maps_dir → reload。旧 SAMPLE 成为历史版本，409 与 latest 断言原样成立；404 用例不动

验证：`python -m pytest backend/tests/test_location_api.py -q` → 全量

### 批 3：导航测试 test_nav_api.py
- `_create` 默认 mapVersion/destinationId 改真实（目的地选 F1-RM-0003 资源教室，非卫生间避免 wall_fallback 触发盲 422）；跨层目的地选 F2 可达地点，**避开盲不可达 F2-RM-0029/0031/0032**
- 新增按 nodeId 取坐标的小 helper（模块级读地图 JSON）：到达点 = 目的地节点坐标；非到达/常规更新点 = 起点节点坐标（polyline 首顶点，距终点 >5m 且偏航距离 0）；偏航点 = 远离路线 >8m 的同层节点；`_origin()` 改真实节点坐标+小偏移
- 每个几何用例请求前先 assert 距离前提（防到达误判）

验证：`python -m pytest backend/tests/test_nav_api.py -q` → 全量 202 测试

### 批 4：客户端测试
- 新增 `client/tests/fixtures/real-map.ts`：模块级缓存读取真实地图 JSON（jest moduleFileExtensions 含 json；tsconfig 无 resolveJsonModule，用 `declare const require` 先例，同 smoke 测试的 `declare const process`），导出 REAL_UUID、真实 1 层前 3 信标等
- [beacon-registry.test.ts](file:///d:/code/pathai/client/tests/beacon-registry.test.ts)：UUID 换 `B9407F30-...`，信标换真实三元组（保留 1 条手工"缺三元组"条目——真实数据无此样本，属健壮性用例）
- [location-pipeline.test.ts](file:///d:/code/pathai/client/tests/location-pipeline.test.ts)、[scan-window.test.ts](file:///d:/code/pathai/client/tests/scan-window.test.ts)：CATALOG 换真实信标（beaconId/坐标/三元组），上报断言同步
- [map-service.test.ts](file:///d:/code/pathai/client/tests/map-service.test.ts)：mock 包改用真实包 JSON（`JSON.stringify` 作 RAW + 其 sha256 作摘要），断言 places=56、`resolve(uuid.toLowerCase(),1,10101)→'BK-01-001'` 等
- smoke-location.int.test.ts：轨迹点改 3 个真实信标附近点（原 (6,8)/(9,9) 距真实信标 ~100m 必然 unreliable）
- 运行时 mock 源 `core/ble/mock-beacon-source.ts` 与 navigation 页 mock 轨迹已是数据驱动（吃地图包 beacons），地图真实即真实，**无需改**

验证：`cd client && npx jest && npx tsc --noEmit`

## 风险规避

1. RSSI 上限 -20 → 所有合成观测点距信标 ≥4m
2. txPower -59→-10 必须同步（忘改则距离放大 100 倍全 unreliable）
3. BK-01-002/003、classroom-1f、"图书馆"等合成标识在真实数据不存在 → 全部换真实 id/名称
4. 真实包 4.6MB 每用例解析+校验可能拖慢测试 → 批 1 先记录基线耗时，必要时 fixture 层缓存
5. 新节点对可能 reachable/valid 不成立 → 每对先跑通再固化断言
6. 到达误判（≤5m）与偏航误判（>8m） → 请求前先 assert 几何前提

## 端到端验证

1. `python -m pytest backend/tests -q`（仓库根，202 测试全绿）
2. `cd client && npx jest && npx tsc --noEmit`
3. 重跑转换脚本自校验 `python backend/scripts/convert_map_v9.py --verify-only` 确认地图包未变
4. 分 4 批提交（英文提交信息）
