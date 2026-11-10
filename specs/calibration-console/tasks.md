# Tasks：阶段一标定闭环（对数等距测量页 + 定位常量计算控制台）

> 关联 spec：`specs/calibration-console/spec.md`（v0.1.0 评审稿）
> 状态：待评审通过后执行；本轮不实施代码。

## 任务清单

### A. 对数等距测量页

- [x] A1 客户端新增 `client/pages/collect/logdist/` 页面与 `client/core/collection/logdist-store.ts`；注册 `client/app.json`。
- [x] A2 首页采集连体候选新增第三项「对数等距」，复用现有按钮尺寸、圆角、无障碍属性。
- [x] A3 复用 2 s 扫描窗口与中位数统计；支持固定斜距 `d = √(h² + (installHeight-1.2)²)`，不接入 `LocationPipeline`。
- [x] A4 实现单信标自动锁定 + 多信标人工选择 + 锁定后不切换。
- [x] A5 实现安装高度 2.0–4.0 m 可调、10 档 5×2 选择器、30/60/120 秒倒计时、振动、后台暂停。
- [x] A6 实现 `col:logdist:v1` 存储：只存有会话档位、会话追加、清理二次确认。
- [x] A7 实现 `buildLogdistExport`：`positioning_calibration_logdist`、`deployable:false`、导出前检查与警告。
- [x] A8 `src/tools/fit_location_params.py` 按 `type` 分派；新增 logdist A/n 拟合、敏感性、稳定性门槛。
- [ ] A9 单测：存储/斜距/导出 schema/拟合分派（已完成）；真机验收：入口、锁定、倒计时、导出（待现场）。

### B. 计算控制台

- [x] B1 设置页新增「定位常量计算」section 与 `client/pages/calc/calc` 页面骨架。
- [x] B2 服务端新增 `calibration_samples`、`calibration_runs`、`location_param_versions`、`fingerprint_grid_versions`、`calibration_active_pointer` 表与迁移。
- [x] B3 新增 `/api/v1/calibration/*` 接口：samples、readiness、runs、apply、versions、rollback。
- [x] B4 实现 ready 判定：客户端存在性 + 服务端 schema/语义/基线校验。
- [x] B5 接入静态标定拟合：A/n、阈值、分楼层质量、warnings；分楼层参数仅展示。
- [x] B6 实现按钮状态机（empty → local_ready → uploaded → computed → applied，含 failed/stale/blocked）。
- [x] B7 实现 apply：二次确认、CAS、staging 校验、单事务提交、审计、运行时热切换。
- [x] B8 实现版本历史、成对回滚、保留 20 对、同基线限制。
- [x] B9 实现无鉴权最小防护：私网限制、限流、文件上限、输入白名单、只读接口无副作用。
- [x] B10 新增 `GET /api/v1/config/location-params` 与客户端 `runtime-config` 缓存。
- [x] B11 单测：CAS、ready、回滚、限流、状态机、运行时缓存均通过。

### C. 指纹网格重算与交付

- [x] C1 抽取 `src/tools/generate_fingerprint_grid.py` 可复用核心；CLI 行为不变；支持 A/n 与逐信标安装高度输入。
- [x] C2 服务端 `grid_recompute`：`PATHAI_GRID_BASELINE_DIR`、staging、`grid.json`/`overlay.json`/manifest、超时、互斥、校验。
- [x] C3 compute 阶段产出网格预览；预览失败则 `compute_failed`，apply 不可用。
- [x] C4 apply 阶段校验 staging 并与参数同事务提交；失败不改变线上。
- [x] C5 新增 `GET /api/v1/config/fingerprint-grid/overlay`；ETag/304、`baseGridVersion` 校验。
- [x] C6 客户端新增 `grid-overlay`：拉取、校验、按 `fpId` 合并、缓存 `grid:overlay:v1`、回退内置快照。
- [x] C7 指纹页使用合并点集；记录写入 `gridVersion/paramsVersion`；顶部显示版本与来源。
- [x] C8 旧记录兼容：无版本标记 `unknown`、不回填、不丢弃；已采点变不可采显示角标。
- [ ] C9 单测：合并规则、基线不匹配、缓存回退、旧记录导出（已完成）；集成：apply 成功/失败/回滚的覆盖层行为（待端到端环境）。

### D. 验收

- [ ] D1 阶段一闭环：10 档采集 → 导出 → 拟合 → 结果 `deployable:false`。
- [ ] D2 阶段二闭环：上传 → 计算 → 评审 → apply → 客户端版本刷新。
- [ ] D3 原子性：网格失败/apply 失败时线上参数与网格均不变。
- [ ] D4 回滚：参数与网格成对回退，客户端重新拉取覆盖层。
- [ ] D5 兼容性：旧静态导出、旧指纹导出、旧内置网格均可继续使用。
- [ ] D6 无鉴权最小防护全部生效，风险已随 spec 评审确认。
