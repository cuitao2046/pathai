# 运行 run_20261121 审计包

本目录是 `tools/analyze_field_experiment.py` 的一次完整运行存档：输入、指标、逐条中间数据、运行清单与报告均在同一目录内，可离线复核。

## 本次运行

| 项目 | 值 |
|---|---|
| 生成时间（UTC） | 2026-11-21T00:39:20 |
| 指标版本 | 1.0 |
| 工具脚本 SHA-256 | `2acc9d5e5c124da08835dd9be1d54235248f1af5461065fd203299ed215dc291` |
| Git 提交 | `544cb45`（工作区有未提交改动，`gitDirty=true`） |
| 输入 ZIP SHA-256 | `c6f8d46b82af877d94b98c88e5008dcc12655ab3c315eb008189e9223f611db6` |
| 数据段（CAL/FP/ST/DY/NV） | 13 / 17 / 5 / 2 / 0 |
| 重放输入 | 无（尚未冻结配置） |

## 文件

| 路径 | 内容 |
|---|---|
| `input/export_20261121.zip` | 原始导出包，只读，勿覆盖 |
| `metrics_20261121.json` | 指标完整结构 |
| `metrics_20261121.csv` | 指标扁平表（维度/任务/方案/视图/指标/值） |
| `details/segments.csv` | 37 段清单、真值绑定与缺口原因 |
| `details/runs.csv` | 16 次运行汇总 |
| `details/exclusions.csv` | 4 个被排除段及原因 |
| `details/accuracy_checkpoints.csv` | 逐检查点误差（本次为空，缺重放） |
| `details/nav_estimates.csv` | 逐拍估计（本次为空，缺 NV） |
| `details/timing_events.csv` | 转向/到达时差（本次为空，缺 NV） |
| `manifest_20261121.json` | 输入/输出哈希、参数、工具版本 |
| `report_20261121.html` | 人工阅读报告 |

## 结论摘要

本次数据不足以给出 A/B/C 相对排序：没有冻结配置（无法重放），没有 NV 段（无可用性与时效），T6/T7 均为试采（无真值）。报告中的空白单元格均由上述缺口导致，不是计算失败。

## 复核入口

复核步骤与字段说明见仓库内 [`docs/实验数据分析复核说明.md`](../../../docs/实验数据分析复核说明.md)。最小校验：

```powershell
Get-FileHash input\export_20261121.zip -Algorithm SHA256
# 应与 manifest_20261121.json 中 inputs.archive.sha256 一致
```

## 使用限制

数据来自本地开发库，仅用于验证分析链路；不得作为现场实验结论引用。
