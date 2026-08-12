# PathAI golden 回归测试

以已验收输出 `result/school_building_01_map_v9.geojson`（v9.0.0）为基线，
为后续高风险重构（A2 路由收敛 / B1 巨型文件拆分 / B5 配置外置等）提供安全网。

## 运行

```bash
cd E:\code\pathai
python -m unittest discover -s tests -v
```

## 测试组成

| 文件 | 层级 | 说明 |
| --- | --- | --- |
| `test_geometry_segments.py` | 单元 | `segments_properly_cross` 判定矩阵（纯标准库，可离线） |
| `test_constants.py` | 单元 | constants.py 唯一来源数值锁定 |
| `test_geojson_golden.py` | 静态 golden | 结果文件 SHA-256 + 统计断言（复用 validate_geojson 口径） |
| `test_parsing_result_golden.py` | 静态 golden | 解析结果结构指纹（节点/边/门/房间/骨架/可步行区 id 集合、拓扑连接、跨层边清单）+ 引用完整性不变量 |
| `test_invariants.py` | 静态 | 门不合并 / 跨层边特殊值 / 常量无重复定义 |
| `test_pipeline_golden.py` | 全链路 | 重跑 CAD PDF 解析管线对比统计与结构指纹；缺 shapely/pikepdf 时自动跳过 |
| `golden_stats.json` | 参照 | 基线统计值 + 源文件 SHA-256 |
| `parsing_result_golden.json` | 参照 | 基线结构指纹 + 源文件 SHA-256 |

## 更新基线（有意变更输出时）

```bash
set REGEN_GOLDEN=1
python -m unittest tests.test_geojson_golden -v
python -m unittest tests.test_parsing_result_golden -v
set REGEN_GOLDEN=
```

`REGEN_GOLDEN=1` 时 `assert_golden` / `assert_struct_golden` 会用当前结果覆盖
`golden_stats.json` / `parsing_result_golden.json`
（同时更新源文件哈希与版本）。提交前请人工核对 diff，确认变更确实有意。

## 注意

- 统计口径与 `src/qa/validate_geojson.py` 完全同源（同一函数计算），
  避免「校验逻辑」与「测试断言」分叉。
- 结构指纹（`parsing_result_golden.json`）是 B1 巨型文件拆分的回归安全网：
  id 漂移 / 拓扑连接变更 / 要素增删会定位到具体楼层与集合。
- 门数据位于 `floors[fk].geometry.doors`；拓扑门口节点 type 为 `doorway`（TD）。
- 已知遗留（A2）：`render_interactive.py` JS 端仍有 `0.8` 步速硬编码，不在本测试断言范围。
