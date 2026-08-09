# PathAI — 教学楼室内导航语义地图生成管线

初中学部 1# 教学楼（1~2 层）室内导航地图生成管线：从 CAD 平面图 PDF 解析出语义 GeoJSON（几何 + 语义 + 拓扑三段式），并渲染为交互式 HTML。核心服务视障人群导航。

## 目录结构（src/ 为正式源码包）

```
src/
├── parsing/        CAD PDF 解析：图层提取、坐标归一化、房间/门洞识别 → GeoJSON
├── topology/       拓扑建模（intersection/doorway/facility 节点）与导航路由规则
├── skeleton/       骨架提取子包：中轴 / 矢量化 / 剪枝 / 交叉口 / 门投影
├── rendering/      交互式 HTML 渲染（SVG + 原生 JS）与 PNG 平面图渲染
├── qa/             GeoJSON 拓扑质量校验
└── tools/          数据修复 / 后处理工具（手动骨架、边合并、绕行修复等）
```

## 快速开始

```bash
pip install -r requirements.txt

# 1. 解析 CAD PDF -> result/school_building_01_map_v9.geojson
python src/parsing/parse_cad_pdf.py

# 2. 质量校验
python src/qa/validate_geojson.py

# 3. 渲染交互式 HTML（result/floor_layout_v9_interactive.html）
python src/rendering/render_interactive.py
```

## 关键产物（result/）

| 文件 | 说明 |
|---|---|
| `school_building_01_map_v9.geojson` | 语义地图（geometry/semantic/topology 三段式） |
| `floor_layout_v9_interactive.html` | 交互式楼层图（图层开关/详情/路由演示/拓扑编辑） |
| `skeleton_manual_parsed.json` | 手动骨架标注（存在时优先于自动中轴） |
| `fingerprint_grid.json` | 指纹采集网格 |

## 设计文档

见 `docs/`（产品概述、系统架构、地图构建指南、定位融合、路线规划、视障交互、信标部署、实施路线图等 8+ 篇）。

## 导航规则（与前端 Dijkstra 同步）

- 同层路线中间节点禁止楼梯/电梯等非公共设施节点
- 视障模式跨层必须走电梯（禁用楼梯跨层边）
- 门优先级 swing > fire > opening；房间经门连公共空间，禁止穿墙

## Git 约定

- **禁止直接在 master 工作**：所有功能/优化/bugfix 一律在新分支完成，合入 master 前需人工校验
- 阶段性改动直接 commit（fast-forward，禁 --force）
