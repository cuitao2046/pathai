修复进度（2026-11-15）

11 项已实现修复现已按用户要求全部合入 master；各 fix 分支和提交保留。集成提交：1b669a8。当前权威任务状态见 specs/phase-1-indoor-navigation/tasks.md 的 R-001..R-017。

已提交：

| fix | 分支 | 提交 | 验证 |
| --- | --- | --- | --- |
| 微信静默登录 | codex/fix-wechat-silent-auth | 5df1567 | 类型检查、client 589 项 |
| 最小测试人员授权 | codex/fix-tester-permissions | f5b9ce6 | 依赖静默登录；backend 447 项、client 589 项、admin 构建、迁移 |
| 定位生命周期 | codex/fix-location-lifecycle | 0e9df94 | 类型检查、client 584 项 |
| 导航行动文案固定 | codex/fix-nav-text-invariants | 2a39af2 | 文案、导航 API、路线服务专项；含篡改方向/否定/距离/危险提示测试 |
| 路线线段投影 | codex/fix-route-projection | 0bc864d | 投影及导航 API 专项 |
| 定位原子水位 | codex/fix-location-sequence | 7097362 | 并发/水位专项与定位 API；SQLite 实测 |
| 地图版本固定 | codex/fix-map-version-session | ff05d79 | 定位/导航 API；新版发布后继续使用已保留版本 |
| 本地断网安全暂停 | codex/fix-nav-offline-safety | 922ad0d | client 全量 584 项后增加迟到成功响应回归；最终控制器专项 34 项及类型检查 |
| 观测时间新鲜度 | codex/fix-location-freshness | 35c7102 | backend 全量 449 项（448 通过、1 跳过）；专项 66 项 |

新增已提交：

- codex/fix-nav-create-idempotency — c036052；依赖静默登录；跨控制器恢复记录、确认并结束旧会话后按当前意图新建；client 类型检查和 594 项测试通过。
- codex/fix-endpoint-recovery — 45db571；依赖静默登录；网络失效后重新发现，不重放失败写请求；client 类型检查和 594 项测试通过。

待完成：

2. 查周边前后端契约。
4. 地图下载版本/字节范围一致性与服务端发布校验；用户明确禁止手机端大文件哈希，本轮相关试验已全部撤回且未提交。
5. 地图发布并发、多 worker 快照一致性。
6. 管理员首次凭据与登录限流。

部署/集成前仍需：真实微信登录与权限验收、BLE/慢网测试、PostgreSQL 并发、分支合并后的整体回归。保留版本策略不等于强制退役策略；新鲜度门限当前为最大过去 15 秒/未来 5 秒。到达连续观测确认、严格观测时间水位持久化仍可作为进一步增强，当前修复先阻止过期坐标触发到达。

集成验证：client 类型检查及 606 项测试通过（1 跳过），backend 472 项通过，admin-web 构建通过，独立 SQLite 迁移至 0013_client_permissions 通过。未引入手机端大文件哈希。
