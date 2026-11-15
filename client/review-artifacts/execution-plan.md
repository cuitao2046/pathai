修复执行计划与状态（2026-11-15）

用户已要求合入 master。11 项已实现修复分别保留独立分支/提交，集成冲突已解决；详细勾选状态以 specs/phase-1-indoor-navigation/tasks.md 的 R-001..R-017 为准。

已完成：微信静默登录、最小测试授权、定位生命周期、导航文案保护、路线投影、定位原子水位、地图版本固定、断网安全暂停、观测新鲜度、创建幂等恢复、地址重新发现。

仍待处理：
1. 查周边契约（codex/fix-surroundings-contract）。
2. 下载版本/字节范围一致性及服务端发布校验；不在手机端计算大文件哈希（替代原 fix-map-integrity 方案，相关试验未提交）。
3. 并发地图发布与多 worker 一致性（codex/fix-map-publish-atomicity）。
4. 管理员首次凭据与登录限流（codex/fix-admin-bootstrap-security）。

真机微信、BLE、旁白、PostgreSQL 并发及多 worker 验收另外记录，不以 mock 或 SQLite 替代。部署需先执行 0013_client_permissions 迁移并配置真实微信凭据。
