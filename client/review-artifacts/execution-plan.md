修复执行计划（2026-11-15）

每项独立分支、独立提交、独立验证。不修改 master 上的既有文档，不自动合并。

01 codex/fix-wechat-silent-auth：无感登录、身份缓存、单飞刷新和请求重放边界（当前）。
02 codex/fix-tester-permissions：依赖 01；普通用户/测试人员及配置应用独立开关，后台授权页面、数据库迁移、客户端权限展示。
03 codex/fix-nav-text-invariants：导航动作使用确定性模板。
04 codex/fix-nav-offline-safety：本地失联安全暂停。
05 codex/fix-location-lifecycle：异步代际隔离、单飞。
06 codex/fix-map-version-session：活跃会话固定地图。
07 codex/fix-route-projection：点到线段投影。
08 codex/fix-location-freshness：时间窗口及到达确认。
09 codex/fix-nav-create-idempotency：保留创建结果未知的键及请求。
10 codex/fix-surroundings-contract：周边查询后端契约。
11 codex/fix-location-sequence：水位详情及原子更新。
12 codex/fix-endpoint-recovery：地址失效与受控重发现。
13 codex/fix-map-integrity：下载摘要校验。
14 codex/fix-map-publish-atomicity：发布并发与 worker 同步。
15 codex/fix-admin-bootstrap-security：初始化凭据和登录限流。

每项先补充回归测试，再执行针对性验证；阶段性运行全量 client/backend。真机微信、BLE、PostgreSQL 并发及多 worker 另外验收，不以 mock 替代。
