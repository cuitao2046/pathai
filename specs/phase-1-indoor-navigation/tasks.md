# 第一阶段室内导航实施任务

- **状态：** 已获准按分层门槛实施；业务模块仍须满足对应契约冻结门槛

- **关联文档：** [spec.md](spec.md)、[checklist.md](checklist.md)

> **对齐口径（2026-10-27）**：`[x]` = 已合入 master 且有自动化测试或可重复产物支撑；
> `[ ]` = 仍需真机、现场、评审或冻结确认（未勾选不代表未实现）。
> 语音实现、页面架构以 spec v0.2.7 为准：**讯飞在线 WebSocket** 取代 WechatSI，
> **单页架构**取代语音首页 + 导航页；v0.2.6 新增公共空间边禁止穿墙（T-011A），
> v0.2.7 新增拓扑精简（T-011B）。
> Gate 1 与 Gate 2 各有一个 `T-011`（历史编号重号），
> 引用时以所在 Gate 区分。

## Gate 0：规格审阅与决策冻结

- [x] T-001 评审 `spec.md` 的范围、状态机、产品行为和排除项；经用户批准后标记规格版本。（已批准，当前 v0.2.3）
- [x] T-002 冻结首发场所、地图包 API schema、坐标/楼层约定、地点索引、信标映射及转换/验证/打包/复核/发布流程样例；不在对应地图契约冻结前实现转换工具。（`siteId=school-building-01`、地图包 `backend/maps_data/school-building-01/2026-10-25.1.json`、校验器 `map_package/validate.py`）
- [x] T-003 将已确定的强制微信登录落实到 API 契约：code 换 token、openid 主体绑定、令牌校验和会话所有权隔离。（`auth.py` + `routers/auth.py`，`test_auth_api`）
- [ ] T-004 对**讯飞开放平台在线语音**（语音听写大模型 V2.0 + 超拟人合成）执行目标设备集成、权限/隐私数据流和失败降级验证；该项取代原 WechatSI 方案（spec §10 决策 2）。服务端签名与协议已冒烟验证，**仍未完成目标设备验收**：须配置 socket 合法域名（`docs/25 §2.2`）。
- [ ] T-005 采用规格中的工程初始定位/导航默认值开展开发联调；制定现场采集、参数调优、验收指标与用户路径安全策略，未验证前不作为上线门槛通过。
- [ ] T-006 落实已建议的权限重试、未授权功能边界、候选选择确认、屏幕阅读器控件/语音替代；冻结目标设备/辅助技术版本及后台安全策略。
- [ ] T-007 完成 360 天脱敏分析数据的隐私/安全审查、账号删除及备份/日志到期清理方案；更新验收清单。
- [x] D-008 后端首期技术栈：Python 3.13+、FastAPI/Pydantic 2、Uvicorn、SQLAlchemy 2、Alembic；本地开发使用 SQLite，Demo 使用托管 PostgreSQL（psycopg 3）。通过环境配置显式选择数据库，禁止 Demo 静默回退 SQLite；数据库兼容查询以 PostgreSQL 集成测试验证。Demo 采用单体 API + 对象存储，不引入 Redis/消息队列/Kubernetes；托管厂商及密钥配置随部署环境冻结。

## Gate 1：接口和地图契约

- [x] T-009 为 API-01..09 冻结机器可验证的请求/响应 schema、错误码、鉴权策略、幂等规则和版本行为。（`schemas/` + `test_schemas_contract`，含 voice 接口）
- [x] T-010 将现有扩展 GeoJSON 和信标部署数据转换为版本化 API 地图包；校验 schema、拓扑连通/视障可达性、地点/楼层/信标引用、摘要和发布清单，保存可重复构建记录。（2026-10-25.1 发布包；sha256 即 ETag）
- [x] T-011 为定位、路线和指令生成建立可重复的基准数据与契约/回归测试。（`test_location_api` / `test_nav_api` 使用真实地图包与真实信标）
- [x] T-011A 落实 spec v0.2.6「公共空间边禁止穿墙」：门/设施挂接改为同侧可见（不穿墙）+ 距离上限，出入口挂接加距离上限与可见性，骨架导入拒绝跨墙 TI↔TI 边；转换器发布前校验公共空间边穿墙数为 0，并对当前地图包整改后重新生成、发布。（决策 8；房间质心↔门语义边不在范围内。已发布 2026-10-27.8：公共边穿墙 169→0）
- [x] T-011B 落实 spec v0.2.7「拓扑精简」：仅合并同层 <0.5m 的 TI↔TI（保留原边长、代表节点取度数最大者），跨类型节点不合并；合并后复核公共空间边穿墙=0 并重新生成、发布。（`src/tools/merge_nearby_tis.py`；2026-10-27.9：删除 24 个 TI，节点 564→540）

## Gate 2：后端能力

- [x] T-011 建立 API 服务基础：请求校验、requestId、统一错误、鉴权/会话所有权和幂等存储。（`request_context.py` / `errors.py` / `auth.py` / `models.py`）
- [x] T-012 实现发布地图与地点读取、版本/摘要和地点候选查询。（`routers/map.py`）
- [x] T-013 实现受控意图解析；模型结果 schema 校验、地点消歧，禁止模型输出动作。（`intent_parser.py`，纯规则引擎、不依赖大模型）
- [x] T-014 实现 BLE RSSI 预处理、三点定位、质量分类、序号/新鲜度/地图版本检查，并运行现场参数回归。（`location_solver.py`；**现场标定回归仍待做**，见 T-005/T-027）
- [x] T-015 实现受限 Dijkstra 路由与规则回归；明确回退标志及首条受控指令。（复用 `src/topology/route_rules`，`plan_route` 在 `nav_service.py`）
- [x] T-016 实现导航会话状态机、位置更新、偏航重规划、恢复、到达判定、查询和幂等结束。（`nav_service.py` + `routers/nav.py`）
- [ ] T-017 实现最小数据留存、脱敏日志、到期删除及授权安全测试。（仅落地结构化脱敏日志字段约定；**留存/到期删除流程未实现**）
- [x] T-030 定位参数配置化与版本追溯：`Settings` 新增 `PATHAI_LOCATION_*`，`location_solver` 接收可注入 `LocationParams`；API-05 响应与会话（创建/查询）返回 `paramsVersion`，`NavSession` 增 `location_params_version` 列（alembic `0005`）；pytest 覆盖配置注入生效、缺省回落、响应字段及「基准误用 txPower 必判 unreliable」反例。（`config.py` / `location_solver.py` / `schemas/location.py` / `routers/location.py` / `schemas/nav.py` / `routers/nav.py` / `models.py` / `alembic/versions/0005_*.py`；`test_location_api` / `test_schemas_contract` / `test_nav_api` / `test_app_skeleton`）
- [x] T-031 新建 `positioning-calibrator` 标定小程序：现场手输标定点、2 s 窗口/中位数/截断采样、60–120 s 停留与高峰/低谷标记、导出 `static_samples.json`；台账→`data/reference_beacons.js` 生成工具与 JS 单测。（`positioning-calibrator/`；`node --test`）
- [x] T-032 主小程序「标定模式」：设置页开关（重启生效）、操作人、可信估计+可见信标数+指令序号+会话状态本地记录、JSON 导出/清空；不改变算法，附 jest 单测。（`client/core/calibration/`、`pages/map`、`pages/settings`）
- [x] T-033 新建 `src/tools/fit_location_params.py`：台账重算校验、OLS 拟合 `A`/`n`、`R²`/RMSE、按楼层分组判据（≥50 对且 RMSE 降幅 >30%）、生产解算回放 CEP/阈值建议、参数文件+报告；pytest 覆盖合成反演、分组边界与错误路径。（`src/tools/fit_location_params.py`；`tests/test_fit_location_params.py`）
- [x] T-034 路线引擎适盲策略与物理距离口径：`ModePolicy` 集中硬约束；修复 wheelchair 不因 `accessibilityLevel=999` 禁行；`RouteGraph` 新增 `physicalDistance`/`edgeDistances`，`build_route_info` 使用纯物理距离；`routeRulesVersion=1.1.0`；补真实地图回归与文档。（`src/topology/route_rules.py`、`backend/pathai_backend/nav_service.py`、`tests/test_route_cost_distance.py`）

## Gate 3：小程序能力

- [x] T-018 将默认脚手架首页替换为 accessible 导航交互流程与客户端状态机。（单页化：`pages/map` + `pages/settings`）
- [x] T-019 实现权限/地图启动流程、**讯飞在线语音**接入及按住说话、播报队列和生命周期清理。（`XfyunAsrEngine` / `XfyunTts` / `SpeechQueue` / `PushToTalk`；`onHide` 停采集、`onUnload` 结束会话）
- [x] T-020 实现地点/意图请求、候选澄清、确认/取消、导航创建和错误反馈。（`intent.service.ts` + 地图页居中浮层）
- [x] T-021 实现 BLE 扫描、窗口采样、序号、定位请求；IMU 仅按冻结契约处理，不加入首阶段解算。（`scan-window` / `location-pipeline` / `sequence-counter`）
- [x] T-022 实现导航更新、当前指令过滤、恢复/重规划、主动结束、到达和后台恢复校验。（`NavSessionController` 状态机 + `handleForeground`）
- [x] T-023 实现地图版本缓存、摘要校验、语音失败安全暂停和资源停止逻辑。（`map-cache` 文件缓存 + `sha256Async`；FE-04 降级）

## Gate 4：联调、验证与受控发布

- [ ] T-024 端到端联调用户授权→语音请求→地点确认→定位/建会话→导航→到达/结束。（mock 全链路已通；**live 语音端到端待真机**）
- [ ] T-025 运行错误路径、安全状态机、并发/乱序/幂等及 API 契约测试。（自动化已覆盖主要错误路径，并发/乱序专项待补）
- [ ] T-026 在目标 iOS/Android 微信、VoiceOver/TalkBack 环境执行无障碍及生命周期验证。（**含讯飞 socket 合法域名与「开发调试」模式差异**）
- [ ] T-027 执行场地标定与预设路线测试，记录误差、错误转向、楼层识别、恢复时间、延迟和功耗；不得将未达到门槛的结果发布为可用能力。
- [ ] T-028 完成视障参与者受监护的风险评估；评审通过前限制为模拟/受控测试。
- [ ] T-029 满足 [checklist.md](checklist.md) 全部适用项后申请第一阶段受控试点；版本化冻结地图、算法参数、路由规则和接口契约。

## 依赖关系

- Gate 0 的用户批准是进入实现的前置条件；微信登录、权限交互、导航/定位的工程默认值等已由规格给出，但现场校准、设备验证、隐私审查和发布门槛仍不可跳过。
- Gate 1 的地图/API 契约先于后端与客户端并行实现。
- T-010 的路线与定位基线分别是 T-014..016 的回归前置。
- 后端核心 API 和小程序可在契约冻结后并行开发；端到端联调依赖双方实现完成。
- 真机/现场/视障参与者安全门槛是受控试点的前置条件，而非普通单元测试的替代品。

## 2026-11-15 client/backend 代码评审修复批次

本批按用户要求合入 master；每个 fix 保留独立分支和提交。以下勾选表示代码及自动化回归完成，不代表真机/现场验收完成。

- [x] R-001 微信静默登录与令牌单飞刷新（5df1567）。
- [x] R-002 普通用户/测试人员与配置应用独立权限，后台授权、身份展示及迁移（f5b9ce6）。
- [x] R-003 定位管线代际隔离、停止语义与 tick 单飞（0e9df94）。
- [x] R-004 导航行动文案保留确定性模板，禁止 LLM 改写（2a39af2）。
- [x] R-005 同层路线线段投影与剩余距离计算（0bc864d）。
- [x] R-006 定位水位原子更新与错误恢复详情（7097362）。
- [x] R-007 新版发布后允许显式使用已保留的地图版本（ff05d79）。
- [x] R-008 本地响应看门狗、安全暂停、会话与新鲜位置联合恢复、迟到响应隔离（922ad0d）。
- [x] R-009 观测时间戳脏数据下限、起点新鲜度下放客户端、过期坐标不触发到达（35c7102；新鲜度口径见 R-018）。
- [x] R-010 创建结果未知时持久化幂等事务；确认并结束旧会话后按当前意图重建（c036052）。
- [x] R-011 运行期地址失效、代际隔离和重新发现；不自动重放失败写请求（45db571）。
- [ ] R-012 查周边前后端契约补齐。
- [ ] R-013 地图下载版本/字节范围一致性及服务端发布校验。用户明确禁止在手机端计算大文件哈希；本批未引入该逻辑。
- [ ] R-014 地图并发发布、版本不可覆盖与多 worker 快照一致性。
- [ ] R-015 管理员首次凭据、登录限流与初始化竞争。
- [ ] R-016 真机微信登录/授权撤销、旁白、BLE 丢失恢复、慢网和网络切换验收。
- [ ] R-017 PostgreSQL 并发与真实多 worker 验收；自动化 SQLite 回归不替代该项。
- [x] R-018 起点新鲜度改由客户端判断：服务端只做时间戳脏数据下限（过去 300 s / 未来 60 s），预览、建会话、定位响应统一回带观测龄与 stale 提示，不再因新鲜度 422；客户端在开始导航前重取一次定位。

部署准备：配置真实微信 AppID/AppSecret 与固定 OpenID 盐，执行 `cd backend && python -m alembic upgrade head`（本批新增 0013_client_permissions）。地图历史版本必须保留才可继续使用；服务端只在时间戳明显非法时拒绝（过去 > 300 秒、未来 > 60 秒），起点/采集是否够新由客户端判断，服务端在响应里回带 `originObservationAgeS`/`observationAgeS` 与 stale 提示（R-018）。进一步增强项包括连续到达确认和严格观测时间水位持久化。

本批集成验证：client 类型检查及 606 项测试通过（另 1 项跳过）；backend 全量 472 项通过；admin-web 构建通过；独立 SQLite 从空库迁移至 0013_client_permissions 通过。上述结果不替代 R-016/R-017 的实机验收。
