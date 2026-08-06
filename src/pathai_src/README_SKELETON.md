# PathAI 骨架导航管线（T1–T12）

## 任务完成状态

| 任务 | 状态 | 说明 |
|------|------|------|
| T1 Walkable | ✅ | `generate_walkable_polygons` + 外轮廓裁剪 |
| T2 前室细分 | ✅ | `elevator_lobby` / `stair_lobby` |
| T3 Medial Axis | ✅ | `skeleton/medial_axis.py` |
| T4 矢量化 | ✅ | `skeleton/skeleton_vectorize.py` |
| T5 悬挂剪枝 | ✅ | `prune_dangling_branches` |
| T6 Junction | ✅ | `junction_detector.py` |
| T7 门投影 | ✅ | `door_projector.py` (STRtree) |
| T8 拓扑重构 | ✅ | `pipeline.build_skeleton_topology` |
| T9 集成 | ✅ | `USE_SKELETON` + GeoJSON 新字段 |
| T10 跨层 | ✅ | 编号优先配对 + matchedBy |
| T11 可视化 | ✅ | 走廊骨架 / 交叉口图层 |
| T12 验收 | ✅ | `validate_geojson.py` |

## 目录

```
src/
├── parse_cad_pdf.py
├── topology.py
├── render_interactive.py
├── validate_geojson.py
└── skeleton/
    ├── __init__.py
    ├── medial_axis.py
    ├── skeleton_vectorize.py
    ├── junction_detector.py
    ├── door_projector.py
    └── pipeline.py
```

## 依赖

```bash
pip install scikit-image shapely networkx numpy pymupdf
```

## 命令

```bash
# 解析（默认开骨架）
python src/parse_cad_pdf.py
python src/parse_cad_pdf.py --no-skeleton

# 交互可视化（含骨架图层开关）
python src/render_interactive.py

# 质量校验
python src/validate_geojson.py result/school_building_01_map_v9.geojson
```

## GeoJSON 新增

每层 `floors.N`：

- `skeleton` — FeatureCollection of LineString（中轴段）
- `walkable_regions` — FeatureCollection of Polygon
- `topology` — TI 为骨架交叉口；TD 为门投影点

跨层 `crossFloorEdges[]` 增加 `matchedBy`: `"code"` | `"geometry"`。

## 校验项（T12）

- TD 覆盖率 vs geometry.doors
- 主连通分量覆盖率
- 楼梯边 a=999 / blindAccessible=false
- TF 不孤立
- XE 引用存在的 TF，电梯/楼梯区分
- 骨架短段比例（若有）
