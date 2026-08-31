# PathAI 项目长期记忆（精炼版）

## 定位
初中学部 1# 教学楼 1~2 层室内导航（视障核心用户）。CAD PDF→GeoJSON→交互 HTML 全链路。设计文档 docs/（8 篇）。

## 代码（src/ 正式脚本 12 个）
- `parse_cad_pdf.py`：主解析器。OCG 矢量→标定(SCALE=0.0529 m/pt, 原点(2019.1,1154.8)pt, Y翻转, set_rotation(0))→墙体矢量化→房间识别(栅格+形态学+分水岭+标签)→门洞识别(window摆弧/DOOR_FIRE/DK笔画)→门归属→GeoJSON。
- `topology.py`+`skeleton/pipeline.py`：**实际拓扑由 pipeline.build_skeleton_topology 生成**（手动骨架优先取 `result/skeleton_manual_parsed.json` 跳过中轴），topology.build_floor_topology 仅回退；改拓扑须两处同步。
- 门节点(TD) v9 规则：同物理开口门按 center_m(0.8m)+投影坐标(1.0m)两次合并为 TD；仅归属≥1封闭房间建 TD；`validate_geojson.py` 校验「每 TR 须有 TR↔TD 边」。
- `render_interactive.py`：自包含交互 HTML(result/floor_layout_v9_interactive.html)。图层开关/缩放平移/悬停点击详情/楼层跳转/拓扑联动高亮/导出 SVG/边编辑写回/门编号 TD-xxxx/前端 Dijkstra(对齐 route_rules)。
- `render_map.py`：GeoJSON→PNG。`validate_geojson.py`：QA，核心指标"无门封闭房间数=0"。
- `route_rules.py`：`DOOR_PENALTY={swing:0,fire:0.5,opening:1}`；三层回退(最佳门→所有门→穿墙 wall_fallback)；盲模式剔盲区/禁楼梯跨层；无门卫生间虚拟穿墙边 XR-TW；`validate_wall_crossing` 豁免 doorway/facility 相邻段。
- `fix_wall_crossing.py`：栅格 A* 清洗（walkable 薄隔墙跨接，当前数据收益有限）；`fix_crossing_edges.py`：穿封闭房间/管井边绕行。
- `merge_manual_edges.py`/`generate_fingerprint_grid.py`/`export_skeleton_template.py`/`import_manual_skeleton.py`/`apply_manual_skeleton.py`/`apply_room_overrides.py`：手动边/指纹网格/手工骨架闭环/房间属性覆盖。

## 当前进展（v9, 2026-08-10）
房间 F1 81/F2 55；门 F1 132/F2 76；墙 4442 段；跨层边10(楼梯7+电梯3)；合班 F1-RM-0050 211.9m²(98.9%)；骨架 TI F1 63/F2 64；walkable F1 31/F2 16；指纹点 F1 975/F2 467。QA PASS。信标：route-mask 6m 方案 74 信标；GDOP 推荐最小高/底 0.20(保守0.25)、<0.05 禁用、退化判据 GDOP>3（docs/13，含 4.5 σ 传播链与降噪方案⏳待实施）。

## 关键约定
- 正式脚本仅 src/，调试脚本 debug/，根目录不留 .py。
- 路径基于 `__file__` 推导，不 hardcode。
- networkx 3.6 在 Py3.14 有 dataclasses bug，需 configs.py Config 补显式 `__init__`。
- ⚠️ **门不做合并（用户明确 2026-08-09）**：同一物理开口只允许一扇门，禁止 dedupe_doorways(13pt)/_merge_nearby_doors(0.8m/1.0m) 三处合并（parse_cad_pdf / skeleton/pipeline / topology）——合并混叠 rooms 归属（如 F2-TD-0010 ['F2-CR-0042','F2-RM-0005']）。
- 卫生间防火门丢弃；卫生间/楼梯间摆弧门(kind∈swing/fire 且 DK<14pt)重分类 opening(F1=4/F2=4)。
- 门洞(opening)=window 层 DK 笔画；几何优先级 ①DK<13pt 真实轴 ②50pt 内墙缝 ③吸附墙(~1.6m)。
- ⚠️ `_heban_real_polygon` 陷阱：cv2.floodFill 把 newVal 写回**图像**、mask 只置1，取填充区须 `图像==newVal`。
- ⚠️ **push 凭据（2026-08-14）**：SSH 通道 = `~/.ssh/config` Host `github-cuitao`（IdentityFile `~/.ssh/id_ed25519`，id_rsa_cuitao 已删、id_rsa 不行）；**remote 必须 `git@github-cuitao:cuitao2046/pathai.git`**；clone https 后第一件事 `git remote set-url origin` 换回 SSH。
- ⚠️ **手动骨架优先**：skeleton_manual_parsed.json 存在则跳过中轴提取；**必须 git add+commit 入库**，严禁 `git checkout <旧提交> -- 该文件` 回退（渲染缺矩形先查工作区）。重生成：`python src/tools/import_manual_skeleton.py --input "C:/.../*.svg"`。
- ⚠️ geojson 陷阱：房间类型在 **`type`** 字段（非 roomType，由中间变量映射）。
- ⚠️ **git 目录误删 bug（2026-08-14 首现，2026-08-27 复发）**：切分支/删除目录内大量文件时可能误删整个父目录，**且会连目录内 untracked 文件一并删除且不可恢复**（2026-08-27 实测：`.workbuddy/memory/` 整目录被删，15 个 tracked 日志 + 1 个 untracked 的当日 `2026-08-27.md` 全失；tracked 用 `git checkout HEAD -- <目录>` 可恢复，untracked 永久丢失）。对象库仅保护 tracked。规避：①切分支前 `git add`+`commit` 所有 memory 日志；②或 `git stash -u` 保护 untracked；③操作后**立即 `git status` 自检**，发现 deleted 立刻 `git checkout HEAD -- <目录>` 恢复；④优先 cherry-pick 而非 rebase；⑤rebase 同样触发该 bug（2026-08-28 实测），且误删范围含**任意 tracked 目录**（如 fingerprint-collector/）而非仅 memory——checkout/rebase 后须 `git checkout -- .` 全面恢复所有 tracked 文件，只恢复 .workbuddy/memory/ 不够。
- ⚠️ **远端跟踪引用不落盘（2026-08-31 实测）**：沙箱 git 的 `fetch` 能连远端（github-cuitao 可达）且报 `[new branch]`，但 `origin/master` 远端跟踪引用**写不进 `.git/refs/remotes/origin/` 与 `.git/packed-refs`**（同类文件系统脆弱性），致 `rev-parse origin/master` 报"未知引用"、误判未同步。**正确校验法：用 `git ls-remote origin <branch>` 直接问远端返回的真实 commit 哈希，与本地 `git rev-parse <branch>` 比对**——已验证 53f54bf 两端一致即确属已同步。勿依赖本地 `origin/*` 引用判定。
- `.workbuddy/memory/` 与 skeleton_manual_parsed.json 随仓库同步；result/ 其余产物为可复现渲染输出按铁律提交。⚠️ 编辑 .gitignore 后务必 git add 再 commit。

## 提交工作流（铁律）
- **铁律 0（2026-08-09 起）**：禁止直接在 master 工作。流程：最新 master 切分支→开发+当日日志→commit(禁--force)→push→人工校验→FF 合入 master→推 master→**默认删已合入分支**（本地 branch -d + 远端 push --delete + fetch --prune）。
- ⚠️ **分支名禁含斜杠/反斜杠（无条件）**：沙箱 .git/refs/heads/ 写入带斜杠名会静默失败成 unborn 分支、git add 误暂存全树。一律连字符：feature-xxx。禁止 mkdir -p hack。
- **铁律 0b（2026-08-10 起）**：一个分支只含一个需求，无关改动必须另开分支；误堆叠用 reset --hard 摘除 + 新分支 cherry-pick 迁走。
- **铁律 0c（2026-08-20 用户明确）**：**开发严禁顺带修改不相关代码**。一个提交/分支只动与本次需求直接相关的文件与逻辑；**渲染产物（HTML/PNG 等）由对应生成脚本重新生成属正常再生、不算越界，但不得在生成脚本或源文件里夹带无关改动**；发现需要改的不相关代码→必须另开分支/提交，绝不混入当前需求。每次提交前自查 `git diff --stat` 确认无无关文件入栈。
- 每次功能/优化完成必做：①成果追加 .workbuddy/memory/YYYY-MM-DD.md（append-only）②push（沙箱无凭据时明确告知"待 push"+commit 清单，不得静默跳过）。
- ✅ 用户已采纳（2026-08-12）：**禁止双会话并行直推 master**（触发反复 rebase 冲突）。

## 已知限制
1. 少量标签未匹配多边形(音乐/书法/美术等孤儿门 F1 61/F2 17)；卫生间多边形只覆盖盥洗走道区。
2. 走廊骨架为简化模型，未重建 v7 式 700 边中轴路网。
3. 门仅 Point+width_m，无铰链/朝向。
4. DK 遮挡/贴墙过滤误删(F1剔49/F2剔26)仍漏检。
5. CAD 标签包围盒误识别为房间；render 用 `_is_label_bbox`(面积<6m²且长宽比≥1.5)过滤。
6. 开放/封闭空间分治(OPEN_SPACE_TYPES={corridor,lobby,activity,atrium})，开放空间建 intersection 节点，不得合并。
7. 穿墙均为 walkable 薄隔墙跨接「桥边回退」（数据质量问题，独立修复），路由层已保证 0 可避免穿墙。

## 方案迭代历史（详见各日 .md）
| 日期 | 标题 | 要点 |
|---|---|---|
| 2026-08-10 | 合班射线投票 v2 | `_heban_real_polygon_v2` 语义种子+射线投票→211.9m²(98.9%)，零重叠、正确门关联 |
| 2026-08-09 | 手动骨架入库+路由三层落地 | pipeline 手动骨架优先；新建 route_rules.py 三规则+三层回退；前端 Dijkstra 对齐；验证全绿 |
| 2026-08-08 | 骨架手动标注闭环+导航绕行 | export/import/apply 骨架；fix_crossing_edges 绕行；门编号 TD-xxxx；src 扩至 12 |
| 2026-08-07 | 走廊/教室重叠修复+空间块回滚 | `_resolve_open_closed_overlaps` 取差 F1-CR-0048 565.5→192.6m²；空间分解/分类/图层回滚 |
| 2026-08-14 | route-mask 路线掩码+GDOP 分析 | 74 信标(95→74)，[7]违规 21→0；GDOP×高/底：最小 0.20(保守0.25)，<0.05 禁用；docs/13 落地 4.5 σ 传播链+降噪方案 |

## 失败实验速记
虚线墙=短段+大间隙→无条件30pt桥接；真墙=2px单线→不能开运算去薄墙；LABEL_SKIP_RE 不含"出入口"；arc_mid 非万能(外开门)；DK 是 window 层矢量笔画非文本层；shapely 宽可见图 O(n²) 大数据错误→numpy 栅格 A*。
