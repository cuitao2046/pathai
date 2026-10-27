# 手绘骨架交点与导航拓扑修复任务记录

## 目标

手绘走廊骨架中的几何线相交即代表可通行连接。导航拓扑应以这些几何交点和线段为依据，避免仅凭距离补桥引入与手绘骨架不一致的路线。重点关注音乐教室 `F1-TR-0016` 至组织办公 `F1-TR-0036` 的路线：其走廊链路应经过交叉口2、9、14、19，而不应由交叉口2直接跨接至19。

## 已完成

- 在 `src/tools/import_manual_skeleton.py` 中，交点候选保留对应的源线段及线段投影位置。交点吸附到已有 TI 时复用其 ID，但在线段实际交点处切分，不再因 TI 坐标偏移而漏切。
- 移除手绘骨架导入器仅依据距离建立的2米分量补桥。
- 在 `src/skeleton/pipeline.py` 中，手绘骨架模式跳过近邻 TI 合并、孤立 TI 距离挂接、25米 TI 软桥和大孤岛距离补桥。自动骨架模式维持原有处理；房间、门和设施的挂接逻辑保留。
- 新增 `tests/test_manual_skeleton_topology.py`，覆盖偏移交点吸附后两条线均切分、近邻但不相交线段保持断开、手绘拓扑不产生距离补桥，以及自动骨架路径不变量。
- 修改与测试已记录在提交 `381dcad`（`feat: update workspace`）。
- 后续提交 `d65778a` 更新了 `result/school_building_01_map_v9.geojson` 和 `result/floor_layout_v9_interactive.html`。

## 验证记录

- 交点与拓扑回归测试：4项通过。
- 拓扑不变量测试：4项通过。
- 路由规则一致性测试：11项通过。
- Python 编译检查及 `git diff --check` 通过。
- 曾对重建的走廊 TI 子图验证交叉口2至19路线，路线为 `2 → 9 → 14 → 19`。

## 待完成 / 注意事项

- 需要基于提交后的最终 GeoJSON，再次确认房间级路线 `F1-TR-0016 → F1-TR-0036` 经过交叉口2、9、14、19，并检查 normal、blind、wheelchair 模式。
- 完整 PDF 解析及 pipeline golden 端到端测试尚未通过验证；先前运行因环境缺少 OpenCV (`cv2`) 中止。安装依赖后应重跑，并审查 golden 差异。
- 不应把临时内存拓扑实验当作正式生成结果；验证必须使用项目正式构建流程及其输出。
- 当前工作分支为 `skeleton-intersection-topology`。尚未请求合并到 master 或创建 PR。

## 2026-10-27 完成：交点切分 + 几何连通性补齐

**孤岛根因**：交点切分把连通性完全交给了手绘几何，而手绘线在交叉口常留下厘米级缺口；
原实现又对手绘模式跳过了近邻 TI 合并、孤立 TI 挂接、TI 软桥、大孤岛补桥四道兜底。
实测 F2 最大孤岛（93 节点）的命门就是两个相距 **0.041m** 的 `intersection` 节点。

**修复**（全部在 `src/tools/import_manual_skeleton.py` 内按**几何**完成，不跨距离补桥）：

1. **路口候选归并**：相距 ≤0.3m 或吸附到同一既有 TI 的候选点视为同一路口；切分位置仍逐段记录，
   两条线照旧按真实交点切分（原「偏移交点吸附后两线均切分」的诉求不变）。
2. **缺口缝合**：节点到**另一连通分量**线段 ≤0.2m 时缝到投影点（必要时在该线段插入切点），
   逐分量迭代直到每层单连通。阈值刻意取小——>0.2m 视为真实断开，交给 QA 的分量数门禁暴露，
   绝不回到「2m 距离补桥」把交叉口2 直接跨接到 19。
3. **导入不再吸附 geojson 中已有的 TI**：手绘骨架是走廊网络的整体替换，吸附上次构建的残留节点
   会让同一 SVG 每次导入结果不同；去掉后导入成为 SVG 的纯函数（连续两次运行字节一致）。

**结果**：真实图纸上 F2 存在 0.04m 与 0.14m 两处缺口被缝合，F1/F2 均单连通；
`validate_geojson.py` **PASS**（F1 345 节点、F2 236 节点，主分量 100%，盲模式 0 房不可达），
与已验收基线在连通性指标上等价，同时保留交点切分的拓扑保真度。
结构上「交叉口2 直接跨接 19」的捷径已不可能再现：手绘模式的边**全部**来自手绘线段按交点切分，
唯一的非线段边是 ≤0.2m 的投影缝合（本次实测 0.04m、0.14m）。

**连带维护**：拓扑重建后 TI 编号整体重排，台账中 24 枚 intersection 归属失效
（R1 最大漂移 85m）→ 用项目既有 `src/tools/fix_beacon_coords.py` 做 3m 类型加权重归属，
`validate_beacon_deployment.py` 恢复 0 ERROR（工具二次运行幂等）。
其中 `BK-01-023` 最近交叉口 3.27m 超阈值，按工具策略降为 `manual_adjusted`，
原始归属与描述保留在 `originalSourceNodeId` / `originalLocationDesc`，**需人工复核该点位**。

**待人工确认**：`docs/17` 语义审计使用人工命名的「交叉口NN」；重归属后 `locationDesc`
改为「交叉口（新 TI id）」，人工命名需在审计报告侧重新对照。

## 相关文件

- `src/tools/import_manual_skeleton.py`
- `src/skeleton/pipeline.py`
- `tests/test_manual_skeleton_topology.py`
- `result/skeleton_manual_parsed.json`
- `result/school_building_01_map_v9.geojson`
- `result/floor_layout_v9_interactive.html`
