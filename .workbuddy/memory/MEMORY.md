# PathAI 项目长期记忆（精炼版）

## 定位
初中学部 1# 教学楼 1~2 层室内导航（视障核心用户）。平面图 PDF：`A20-002/003-II-...首层/二层平面图-A0_BIAD-无签名.pdf`。设计文档在 `docs/`（8 篇）。

## 代码（src/ 正式脚本，共 6 个）
- `parse_cad_pdf.py`：主解析器。PyMuPDF 按 OCG 提取矢量→坐标标定(SCALE=0.0529 m/pt, 原点(2019.1,1154.8)pt, Y翻转, set_rotation(0) 处理270°旋转)→墙体矢量化→房间识别(OpenCV栅格化+形态学+分水岭+标签探测)→门洞识别(window摆弧/DOOR_FIRE/DK笔画)→门归属链→GeoJSON。需带 fitz 的 Python。
- `topology.py`：导航拓扑（节点5类 room/doorway/intersection/facility/facility_entrance；边含 distance/estimatedTime(0.8m/s)/accessibilityLevel/riskLevel；开放空间建 intersection 型 circulation 节点）。
- `render_map.py`：GeoJSON→PNG（需 matplotlib/shapely venv）。
- `validate_geojson.py`：QA，核心指标"无门封闭房间数=0"。
- `render_interactive.py`：自包含交互式 HTML(result/floor_layout_v9_interactive.html)。图层开关、缩放/平移(含触控板双指手势)、悬停/点击详情、楼层跳转、拓扑联动高亮、导出所选图层 SVG、拓扑边编辑(双击加边/选中删除/浏览器直写 GeoJSON)、「交叉口连接边 TI↔TI」图层开关。
- `merge_manual_edges.py`：把渲染图中手动编辑/添加的拓扑边合并回 GeoJSON。

## 当前进展(v9, 2026-08-06)
房间 F1 72/F2 55；门 F1 129(swing68/fire53/opening8)/F2 75(swing36/fire32/opening7)；跨层边10(楼梯7+电梯3, matchedBy:code)；DK F1 26/F2 31(旋转无关4方向)；合班 F1-RM-0050 真实多边形 190.4m²(16.5×13.0m, 9顶点, classroom)，由 `_heban_real_polygon` 局部泛洪+形态学闭运算重建，替换3m占位，零退化；骨架模式 TI F1 61段/498节点/323边、F2 33段/310节点/199边；walkable F1 22/F2 16。QA PASS(无门封闭房间=0；模块豁免 F1=R1027/R1028/R1031/R1032/R1033、F2=R2016/R2017/R2020/R2021)。

## 关键约定
- 目录：正式脚本仅 src/(6个)；调试脚本 debug/；根目录不留.py。探索性副本(src/optimize*/src/adjcent/src/fix/src/pathai_src/debug/conn/debug/heban)已 .gitignore 取消跟踪(本地保留)。
- 路径基于 `__file__` 推导，不 hardcode E:\code\pathai。
- 骨架模式依赖 scikit-image + networkx；缺则静默回退质心拓扑致骨架图层空。networkx 3.6 在 Py3.14 有 dataclasses bug，需给 configs.py 的 Config 补显式 `__init__`。
- 门洞(opening)=window层带 DK 矢量笔画。DK 旋转无关4方向。几何优先级：① DK距window组<13pt用其真实轴/宽并移除该window；② 否则复用50pt内最近墙缝；③ 否则吸附最近墙(轴⊥墙,宽=30pt≈1.6m)。`dedupe_doorways` 合并中心距<13pt同洞口。摆弧门避让：距摆弧门中心<DK_NEAR_ARC_PT 的 DK 跳过。
- 卫生间防火门直接丢弃(用户"不用考虑")；卫生间/楼梯间摆弧门(kind∈swing/fire 且有DK<14pt)重分类为 opening(F1=4/F2=4)。
- 核心内部门过滤(v9.1 收窄仅 swing/fire)：opening 首行豁免；判定 所属核心边界<30pt/公共面向<19pt/对侧核心<51pt。
- 验证器模块豁免：零门封闭房间检查仅对未与任何房间/公共空间相邻的孤立核心房间失败；核心房间1.5m内与任一其他房间相邻则豁免。
- detect_doors 摆弧门去重加中心距守卫(DOOR_CLUSTER_CENTER_PT=14)。DOOR_FIRE 只处理 arc based 门。
- 图层：家具层仅 A-METAL-S；A-TECH-SANT 整层剔除。跨层配对按井道编号(II-xx#ST/EL)，几何<3.5m兜底。
- ⚠️ 合班 `_heban_real_polygon` 陷阱：`cv2.floodFill` 把 newVal 写回**图像**、mask 只置1，取填充区须 `图像==newVal`。
- git：仅 `.workbuddy/memory/` 随仓库同步。⚠️ 编辑 .gitignore 后务必 `git add` 再 commit，否则 merge 中静默丢失。

## 提交工作流
当前仅 master 分支(feature 分支已删并合入 master)。阶段性改动直接 commit 并 push origin master(fast-forward，禁 --force)。范围 src/代码、debug/脚本、result/产物、.workbuddy/memory/更新；临时脚本先删再提交。

## 已知限制
1. 少量标签未匹配多边形(音乐/书法/美术教室等孤儿门)；卫生间多边形只覆盖盥洗走道区。
2. 走廊骨架为简化模型，未重建 v7 式700边中轴路网。
3. 门仅 Point+width_m，无铰链/朝向。
4. DK 若笔画被遮挡或 window 组被贴墙过滤误删(F1剔49/F2剔26)仍漏检。
5. 孤儿门 F1 61/F2 17，validate 仅信息项。
6. CAD 文字标签包围盒误识别为房间多边形；render_interactive 用 `_is_label_bbox`(面积<6m²且长宽比≥1.5)过滤。
7. 开放/封闭空间分治(OPEN_SPACE_TYPES={corridor,lobby,activity,atrium})，开放空间建 intersection 节点，不得合并处理。
8. ⚠️ geojson 字段陷阱：最终 `semantic.rooms` 中房间类型写在 **`type`** 字段（如 type="elevator_lobby"），**不是** `roomType`；`classify_elevator_stair_lobby` 等函数内部用 `roomType` 中间变量，序列化时映射到 `type`。排查时勿误查 `roomType` 导致全 None（已踩坑）。
9. 电梯前室/楼梯前室识别：`classify_elevator_stair_lobby`（parse_cad_pdf.py 第1862行）在 build_geojson 的设施 reconcile 之后执行；对 corridor/lobby 开放空间按"到井道几何最短距离 + 面积阈值"细分——电梯前室(d_elev<1.5m 且 ≤150m²)、楼梯前室(d_stair<3.0m 且 ≤60m²)，同时贴近时优先电梯前室。当前 v9 geojson：elevator_lobby=9、stair_lobby=6。

## 方案迭代历史

> 每次方案优化在此追加一条，方便回溯迭代路径。格式：`YYYY-MM-DD / 简要标题 / 问题→方案→影响`。

| 日期 | 标题 | 问题 | 方案 | 影响 |
|---|---|---|---|---|
| 2026-08-07 | classify 后移至 T1.5 裁剪之后 | F1-CR-0043 判定面积(裁剪前≈60⁺)≠输出面积(裁剪后 59.11)，导致边界漏判楼梯前室 | 将 `classify_elevator_stair_lobby` 和 `generate_walkable_polygons` 调用从 T1.5「沿建筑外轮廓裁剪」之前移到之后 | F1-CR-0043 成功判为 stair_lobby；F1-CR-0061 裁剪后面积<8 自然退出；总前室 15 不变；walkable 基于裁剪后 poly 生成，不再需二次裁剪 |
| 2026-08-07 | 空间分解模块 v1 | 可通行区为整块多边形，走道/前室/门厅混在一起，难以按几何特征独立标注 | 新建 `src/spatial_decompose.py`：基于中轴骨架(skeleton lines)的垂直截线法，每个骨架段两端做垂直截线→四边形→polygon clip→近似矩形/梯形。集成到 parse_cad_pdf.py floor_block，写入 geojson["spatial_blocks"] | F1 195 块 / F2 92 块 / 总计 287 块。分类器 v1 偏粗糙(stair_lobby=110 偏多，根因：贴楼梯的每个小块都独立判为前室，缺连接度/袋形约束)。525 条中轴段因长度<0.3m 跳过(骨架边缘碎枝) |
| 2026-08-07 | 分类器 v2：端点连接度 | v1 分类器把贴楼梯的每个小块都判为 stair_lobby(110个 vs 预期~6)，因为只依赖距离+面积，不知道块是穿越型还是服务型 | 给每个块增加 `endpoint_types` 属性（通过匹配 skeleton 端点与 junctions/terminals）。分类规则改为"前室 = 贴近井道 **且** 至少一端为 terminal(死胡同)"，穿越型(junction-junction)块保持 corridor | stair_lobby 110→50(-55%)；elevator_lobby 16→3(F2 归零，因电梯区域全为穿越型)；corridor 151→223。端点类型验证：107个 junction-junction 块中 97 个正确判 corridor |
| 2026-08-07 | 渲染集成：空间块图层 | 空间分解数据仅存在于 geojson，无法直观验证分类正确性 | render_interactive.py 新增 `layer_spatial_block` 图层：从 geojson["floors"]["N"]["spatial_blocks"] 读取，按 space_type 颜色编码（灰=corridor/橙=elevator_lobby/棕=stair_lobby/黄=lobby），默认关闭避免遮挡，在图层开关中勾选"空间块"查看 | F1 198 / F2 92 个多边形渲染成功 |
| 2026-08-07 | 空间分解 v2：polygonize 分区 | v1 独立垂直截线导致块重叠(~5m² F2)且公共区覆盖不全，不符合"近似矩形+三角形"目标 | 重写 `spatial_decompose.py`：① 聚类骨架端点识别 junction(≥2段)/terminal(1段)；② junction 处用角平分线作为**共享分界线**替代 v1 的各自垂直截线；③ 全部 cuts + walkable boundary → `shapely.ops.polygonize` → 无重叠完全覆盖分区；④ 全局逐块去重叠(Difference)清扫残留 sliver；⑤ 每 polygon 组件独立处理避免跨组件分界污染 | F1 180 块 / F2 89 块；overlap 0.00%；形状 82% rect + 18% trapezoid；中间产物 0 WARN/0 ERROR 全流程干净 |
| 2026-08-05 | 合班教室真实闭合墙体识别 | 合班教室走廊侧大开口未被识别成门→自由空间漏进走廊→build_rooms 不产闭合物→3m 占位方块 | `_heban_real_polygon`：局部 18m×18m 墙图，多档椭圆核(1.2~6.0m)MORPH_CLOSE 桥合开口→标签点 floodFill→轮廓→approxPolyDP。完全局部，不修改全局 all_segs | F1-RM-0050 得 190.4m²(16.5×13.0m, 9 顶点, classroom)，替换 3m 占位。⚠️ 陷阱：cv2.floodFill 写图像非 mask |

## 失败实验速记
虚线墙=短段+大间隙→无条件30pt桥接；真墙=2px单线→不能开运算去薄墙；LABEL_SKIP_RE 不含"出入口"；arc_mid 非万能(存在外开门)；DK 是 window 层矢量笔画非文本层。
