# -*- coding: utf-8 -*-
"""语音交互 Agent（M2）：POI 语义词典、自我中心空间语义、意图解析与目的地消歧。

分层：
- config/：词典与人工标注（`poi_semantic_dict.json` 由导出脚本生成，禁止手改）
- schemas/：对外数据契约（空间上下文 / 结构化意图）
- services/：纯逻辑服务（无网络、无框架依赖）

上游规格：`docs/26-语音交互Agent技术规格.md`、`specs/voice-agent-semantic-nav/spec.md`。
本包不依赖 FastAPI 与 wx；后端与客户端测试均可直接导入。
"""

__version__ = "0.1.0"
