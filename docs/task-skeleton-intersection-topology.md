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

## 相关文件

- `src/tools/import_manual_skeleton.py`
- `src/skeleton/pipeline.py`
- `tests/test_manual_skeleton_topology.py`
- `result/skeleton_manual_parsed.json`
- `result/school_building_01_map_v9.geojson`
- `result/floor_layout_v9_interactive.html`
