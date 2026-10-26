# 第二阶段：微信小程序客户端对接（SDD 里程碑计划）

## Context

第一阶段后端已完成（M0-M8，9 个 API，202 测试全绿）。本阶段构建原生微信小程序客户端（TypeScript），在微信开发者工具中对接本地后端跑通「登录→语音意图→定位→导航会话→播报→到达/结束」全流程；真机专项（BLE 实扫、WechatSI 插件、切后台）后置为检查清单文档。

**已确认决策**：新建 `client/` 原生 TS 工程（与 fingerprint-collector 平行，后者不动）；模拟数据优先（BLE/ASR/TTS 引擎接口化，mock 可注入）；SDD 分步提交，每个里程碑带测试。

**权威依据**：[docs/24-第一阶段前后端需求与API定义.md](file:///d:/code/pathai/docs/24-第一阶段前后端需求与API定义.md) FE-01..05、§8 API 契约、§10 时序；后端实现为权威（[schemas/nav.py](file:///d:/code/pathai/backend/pathai_backend/schemas/nav.py)、[schemas/common.py](file:///d:/code/pathai/backend/pathai_backend/schemas/common.py)）。

**已核实的契约事实**（客户端设计依据）：
- 统一错误 `{error:{code,message,retryable,details},requestId}`，响应头 `X-Request-Id`
- segments 用 `instructionId`/`instructionSequence`（非 doc24 示例的 segmentId）
- `NavUpdateResponse.instruction` 可为 `null`（客户端必须处理）
- `NavigationInstruction.expiresAt` 后端当前恒为 null（客户端视为不过期，但保留判定逻辑）
- 地图 data 端点：`GET /api/v1/sites/{siteId}/map/data`，dataUrl 即指向此处，带 ETag
- 幂等按请求体 sha256 比对（`observedAt` 变化即 409 IDEMPOTENCY_CONFLICT——Phase 1 教训）
- API-05 sequence 按 (subject, site) 全局单调持久（`<` 拒收）；API-07 按 session 单调

## 已知集成缺口（本阶段修复）

1. **信标映射缺失**：iBeacon 扫描返回 (uuid, major, minor)，但地图包 `BeaconEntry`（[schema.py L149-157](file:///d:/code/pathai/backend/pathai_backend/map_package/schema.py#L149-L157)）无此字段，客户端无法解析出 API-05 所需 beaconId → C1 后端补字段。
2. **样例地图信标不足**：每层仅 2 信标，三点定位必然退化（lost）→ C1 每层补第 3 信标（参照 M7 测试夹具 BK-01-003/BK-02-003 做法），否则 C6/C8 联调冒烟跑不通。
3. **本地联调无真实 appid/secret 时无法登录** → C1 后端加显式开关的 fake 微信客户端（仅 `PATHAI_ENV=local` 且 `PATHAI_WECHAT_FAKE=1` 时 code 即 openid；demo/prod 禁止）。

## 里程碑

### C1 后端配套：信标映射 + fake 登录开关（先于客户端 BLE）
- `BeaconEntry` 增加可选 `uuid: str`、`major: int(0..65535)`、`minor: int(0..65535)`，model_validator 强制三字段同现同缺
- `validate.py` 信标语义校验增加 (uuid,major,minor) 组合唯一性
- 样例地图 `backend/maps_data/school-building-01/2026-10-01.1.json`：每层信标补 uuid/major/minor + 每层加第 3 信标（非共线位置）
- [config.py](file:///d:/code/pathai/backend/pathai_backend/config.py) 增加 `wechat_fake` 开关；[wechat.py](file:///d:/code/pathai/backend/pathai_backend/wechat.py)/[app.py](file:///d:/code/pathai/backend/pathai_backend/app.py) 按 env 装配 FakeWechatAuthClient（code 直接映射 openid，仅 local 生效）
- 测试：`backend/tests/test_map_package.py` 增组合缺失/重复用例；fake 登录开关用例
- 验证：`python -m pytest backend\tests -q` 全绿；提交 `feat(backend): beacon ibeacon mapping and local fake auth switch`

### C0 client 工程骨架
- `client/`：project.config.json（`useCompilerPlugins:["typescript"]`、`urlCheck:false`、appid 占位）、tsconfig strict、package.json（仅 devDeps：typescript、miniprogram-api-typings、jest、ts-jest、@types/jest）、jest.config.js（ts-jest preset）、app.json/ts/wxss 三页面占位、sitemap
- `config/env.ts`：`API_BASE_URL`（默认 `http://localhost:8000`）、`SITE_ID`、`DATA_SOURCE: 'mock'|'live'`
- `utils/`：kv（storage 封装）、logger、event-emitter、ids（uuid v4）、clock、sha256（vendor js-sha256 单文件）
- `typings/api.d.ts`：后端契约 TS 镜像（逐字段对齐 schemas/common.py、schemas/nav.py）
- `packOptions.ignore` 排除 tests/*.test.ts/node_modules
- 测试：sha256 已知向量、clock、kv 冒烟；提交 `chore(client): project scaffold with jest`

### C2 API 客户端与令牌
- `core/http/api-client.ts`：wx.request Promise 封装、Bearer 注入、401（UNAUTHENTICATED/TOKEN_EXPIRED）→ 令牌作废+重登+**同 body 重放一次**、错误信封→ApiClientError（按 code 分支不按 message）、X-Request-Id 透出、网络失败→`NETWORK_ERROR`(retryable)、超时 10s（导航更新 5s）
- `core/http/token-manager.ts`：storage 持久化、过期前 300s 刷新（wx.login→API-01 静默无感）、并发单飞（inflight Promise 共享）
- `core/http/error-messages.ts`：错误码→本地话术映射
- 测试（`tests/setup-wx.ts` 全局 mock wx）：401 单次重放、并发单飞只调一次 login、requestId 透出、304 分支
- 提交 `feat(client): api client and token manager`

### C3 地图服务与信标注册表
- `core/map/map-service.ts`：API-02 摘要缓存（ETag 存 storage）→ 304 短路；data 下载（`responseType:'arraybuffer'`）+ sha256 校验（**联调模式只重写下载源为 API 同源 `/map/data`，摘要校验不关闭**）；提供 beacons/places/scanUuids/requireVersion（未加载抛 MAP_UNAVAILABLE，禁止开始导航 FE-01）
- `core/ble/beacon-registry.ts`：(uuid,major,minor)→beaconId 解析表（来自地图包，未命中丢弃不虚构）、uuid 去重列表
- 测试：304 不再下载、摘要不符拒绝入库、解析/未命中
- 提交 `feat(client): map cache with etag and beacon registry`

### C4 TTS 播报队列与降级
- `core/speech/speech-queue.ts`：优先级 safety>turn>normal；safety 清空立即播、turn 打断 normal、normal FIFO+出队时按 expiresAt 丢弃；engine reject → `onUnavailable`（震动+可访问文本+通知导航层安全暂停；第一阶段不做本地语音兜底 FE-04）；`clearNormal()`（reroute/relocalizing 时）
- `core/speech/wechatsi-tts.ts` / `mock-tts.ts` / `haptic.ts`（vibrateShort/Long）/ `utils/plugin-loader.ts`（requirePlugin 薄包装，可替换）
- 测试：打断矩阵、过期丢弃、失败 onUnavailable 恰一次、clearNormal 只清 normal
- 提交 `feat(client): speech queue with priority and fallback`

### C5 按住说话 + 意图闭环
- `core/asr/push-to-talk.ts`：onTouchStart/End/Cancel、切后台 handleAppHide（cancel+release）、音频中断挂接；状态机 idle/recording/recognizing/error
- `core/asr/wechatsi-asr.ts` / `mock-asr.ts`（调试文本注入）
- `services/intent.service.ts`（API-04）；`pages/index` 最小 UI：按住说话热区（aria-label）、识别文本回显、候选澄清选择、确认后进入导航
- 测试：PTT 生命周期与中断、取消不提交、start reject → error 状态
- 提交 `feat(client): push-to-talk and intent loop`

### C6 BLE 定位管线（mock/live 双源）
- `core/ble/scan-window.ts`：2s 窗口聚合、每信标滑动窗口中位数、RSSI 截断 [-100,-20]、不足 3 信标→null
- `core/ble/beacon-scanner.ts`：wx iBeacon 封装（参考 [fingerprint-collector/utils/beacon.js](file:///d:/code/pathai/fingerprint-collector/utils/beacon.js)：openBluetoothAdapter→startBeaconDiscovery({uuids})→onBeaconUpdate）
- `core/ble/mock-beacon-source.ts`：沿样例地图 polyline 轨迹合成 (uuid,major,minor,rssi)，走与 live 完全相同的 registry/聚合/节流路径
- `core/position/location-pipeline.ts`：聚合→API-05→节流（待命 8s/导航 2.5s）→onEstimate/onUnusable
- `core/position/sequence-counter.ts`：**init 时 base = max(storage 持久值, 当前 epoch 秒)**，内存自增写回——重启/重装都不回退，从根上规避 STALE_LOCATION_BATCH 409 死锁（服务端只拒 `<`，重置后需 50 次被拒才能爬回，故用 epoch 基线自愈）
- 测试：窗口聚合/中位数/截断、seq 持久化与 epoch 基线、节流、mock 轨迹→本地后端 estimate 返回 reliable（集成冒烟）
- 提交 `feat(client): location pipeline with mock and live sources`

### C7 导航状态机与会话
- `core/nav/nav-state-machine.ts`（纯逻辑）：镜像服务端 active↔recovering→arrived/ended；输入 SESSION_CREATED/UPDATE_RESULT/BACKGROUND/RESUMED/END_ACK → 输出 NavEffects（announce/clearNormal/stopPipeline）
- `core/nav/instruction-tracker.ts`：instructionId 去重、expiresAt 判定（null=不过期）、重播只重播当前有效指令
- `core/nav/nav-session-controller.ts`：API-06 创建（**Idempotency-Key 生成 uuid 与冻结 body 一起暂存，重试复用，2xx 后清除**）；事件映射：instruction→入队、reroute→clearNormal+播新首条+更新 route、relocalizing→停确定性播报+safety 提示+转 recovering、arrived→按 confidence 播「已到达/已到达附近」+停管线；handleForeground 次序（FE-05）：先 API-08 → 校验 status/mapVersion/sequence → 重启采集 → 重播有效指令；客户端从不发 reason=arrived；unreliable/lost 上报仅带 quality（服务端转 recovering）
- `pages/navigation`：当前指令 aria-live 文本、双击结束、recovering 震动模式
- 测试：状态迁移表全覆盖、事件→副作用断言、幂等 key 重试复用、后台恢复调用次序、终态拒更新
- 提交 `feat(client): nav session state machine and controller`

### C8 全流程联调 + 真机清单
- `pages/settings`：数据源开关、API_BASE_URL、siteId、令牌状态、引擎自检
- 开发者工具 mock 源 + `http://localhost:8000` 端到端演练：登录→意图→定位→创建会话→更新→reroute/relocalize/arrived→结束
- 新文档 `docs/25-第二阶段真机联调检查清单.md`：真机 iBeacon（工具不支持）、WechatSI 后台添加/授权/配额、切后台/锁屏/音频中断、安卓系统定位开关（硬约束）、隐私指引核对（蓝牙/麦克风/传感器，不含位置）
- 提交 `feat(client): e2e integration and device checklist`

依赖：C1→C6；C2→C3..C7；C4→C5/C7；C5 与 C6 可并行。

## 测试策略

- **Jest + ts-jest**，`tests/setup-wx.ts` 全局 mock：wx（内存 storage/request 记录器/震动记录器/蓝牙桩）、requirePlugin（经 plugin-loader 间接调用可整体替换）、fake timers
- 纯逻辑全部分支在 Jest 闭环；集成冒烟（C6/C8）用 mock 信标源 + 本地 uvicorn 真进程
- 后端回归：`python -m pytest backend\tests -q`（cwd=d:\code\pathai）；客户端：`cd client && npm test`、`npm run typecheck`（提交门禁）

## 风险与预案

1. 开发者工具无 iBeacon 数据 → BeaconSource 抽象 + mock 一等公民（已纳入设计）
2. WechatSI 工具内表现不确定 → plugin-loader isAvailable 检测 + mock 引擎兜底，真机清单覆盖
3. dataUrl HTTPS 契约 vs 本地 http → 联调开关重写下载源为 API 同源，**sha256 校验不关闭**
4. 时钟偏差影响 expiresAt → 首期本地时间 + 2s 容差（后端当前 expiresAt 恒 null，实际不生效）
5. 幂等 body 冻结（observedAt 变化即 409）→ IdempotencyStore 复用同一序列化 body（已纳入 C7 设计）

## 验证

1. C1 后：`python -m pytest backend\tests -q` 全绿
2. 每个 C 里程碑：`cd client && npm run typecheck && npm test` 全绿后提交
3. C8 验收：开发者工具内，mock 信标源沿样例地图移动，完整走通登录→「带我去一楼教室」→确认→创建会话→位置更新推进指令→到达播报→结束；后端日志可见 API-01..09 依次调用且 sequence 单调


