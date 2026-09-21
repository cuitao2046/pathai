# PathAI — 教学楼室内导航语义地图生成管线

初中学部 1# 教学楼（1~2 层）室内导航地图生成管线：从 CAD 平面图 PDF 解析出语义 GeoJSON（几何 + 语义 + 拓扑三段式），并渲染为交互式 HTML。核心服务视障人群导航。

## 目录结构（src/ 为正式源码包）

```
src/
├── common/        全局常量唯一来源（constants.py）与制图配置
├── parsing/       CAD PDF 解析：图层提取、坐标归一化、房间/门洞识别
├── geometry/      几何基础：栅格化、轮廓、线段、聚类
├── semantics/     语义识别：房间类型、门摆弧、合班、楼梯电梯
├── skeleton/      骨架：中轴 / 矢量化 / 交叉口 / 门投影 / 拓扑生成（pipeline.py）
├── topology/      拓扑建模（intersection/doorway/facility 节点）与导航路由规则
├── rendering/     交互式 HTML 渲染（SVG + 原生 JS）与 PNG 平面图渲染
├── qa/            GeoJSON 拓扑质量校验
├── io/            GeoJSON 写出（含可通行区域、骨架图层）
├── tools/         数据修复 / 后处理 / 信标与指纹工具（约 35 个）
└── sdk/           第三方信标配置工具（MinewBeaconAdmin 等，非本项目代码）
```

调试与一次性脚本放 `debug/`，根目录不留 `.py`。

## 快速开始

```bash
pip install -r requirements.txt

# 1. 解析 CAD PDF -> result/school_building_01_map_v9.geojson
python src/parsing/parse_cad_pdf.py

# 2. 质量校验（拓扑：无门封闭房间 / 穿墙边 / 跨层边 / 门投影 / 骨架悬空）
python src/qa/validate_geojson.py

# 3. 渲染交互式 HTML（result/floor_layout_v9_interactive.html）
python src/rendering/render_interactive.py

# 4. 回归测试（golden 基线）
python -m unittest discover -s tests
```

> 骨架拓扑：若 `result/skeleton_manual_parsed.json` 存在（人工 SVG 标注导入），`src/skeleton/pipeline.py` 会**优先使用手动骨架**并跳过自动中轴提取。

## 关键产物（result/）

| 文件 | 说明 |
|---|---|
| `school_building_01_map_v9.geojson` | 语义地图（v9.0.0，geometry/semantic/topology/skeleton/walkable_regions/accessibility 六图层 + 跨层边） |
| `floor_layout_v9_interactive.html` | 交互式楼层图（图层开关/详情/路由演示/拓扑编辑） |
| `skeleton_manual_parsed.json` | 手动骨架标注（存在时优先于自动中轴） |
| `ble_deployment.json` | 信标台账（当前 61 枚：F1 52 / F2 9） |
| `fingerprint_grid.json` | 指纹采集网格（全楼，2m 间距，1434 点） |
| `fingerprint_grid_routes.json` | 指纹采集网格（路线，1m 间距，647 点） |
| `fingerprint_valid_region.json` | 指纹采集有效区域多边形 |
| `beacon_deployment_evaluation.json` | 信标部署覆盖评估（≥3 信标可见覆盖率等） |

## 导航规则（与前端 Dijkstra 同步）

- 同层路线中间节点禁止楼梯/电梯等非公共设施节点
- 视障模式跨层必须走电梯（禁用楼梯跨层边）
- 门优先级 swing > fire > opening；房间经门连公共空间，禁止穿墙
- 常量集中在 `src/common/constants.py`，前端由 `build_path_rules_js` 序列化注入，禁止在 JS 内嵌常量

## 设计文档

见 `docs/`（28 篇）。常用入口：

| 分类 | 文档 |
|---|---|
| 产品与架构 | `01-产品概述`、`02-系统架构设计` |
| 地图与算法 | `03-地图构建指南`、`04-定位融合算法`、`05-路线规划算法`、`20-CAD图纸数字化地图生成方案` |
| 交互 | `06-视障交互设计` |
| 部署与实施 | `07-信标部署方案`、`08-信标部署与指纹采集实施方案`、`08-实施路线图`、`07-现场勘测记录表` |
| 质量与优化 | `13-信标部署质量分析`、`16-纯三点定位亚米级误差优化方案`、`19-ble_deployment.json数据结构与字段说明` |
| 工具手册 | `12-工具与脚本使用手册` |
| 汇报与预算 | `14-项目进展汇报-导师用`(docx)、`15-工具材料采购预算说明`、`21-工具材料采购预算申请-报批版` |
| 历史记录 | `项目迭代日志`、`设计决策记录`、`code-review-2026-08-12`、`审查解决日志-2026-08-12` |

## 指纹采集工具

`fingerprint-collector/` 为现场采集微信小程序（分区锚点 + 相对坐标，导出 JSON 直接入库），见其 `README.md`。

## Git 约定

- **禁止直接在 master 工作**：所有功能/优化/bugfix 一律在新分支完成
- 分支名**不含斜杠**（连字符，如 `feature-xxx`）；一个分支只含一个需求
- 合入前需人工校验，**fast-forward only**（禁 `--force`）；合入后默认删除已合入分支
- 禁止多会话并行直推 master

## 已知限制

- 走廊骨架为**简化模型**（人工标注优先），未重建 v7 式 700 边中轴路网
- 门仅有 Point + `width_m`，无铰链/朝向信息
- 施工图**无无障碍设计专篇图层**，坡道/盲道/地面材质三类数据缺失，已在数据中标记待现场勘测
