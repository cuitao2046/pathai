# PathAI 项目长期记忆（精炼版）

## 定位
初中学部 1# 教学楼 1~2 层室内导航（视障核心用户）。CAD PDF→GeoJSON→交互 HTML 全链路。设计文档 docs/（21+ 篇，01~21 + CAD 根文档）。

## 代码（src/ 模块化，61 个 .py）
结构：common/（constants.py 单一真值源：SCALE=0.0529、DOOR_PENALTY={swing:0,fire:0.5,opening:1}、DOOR_DEFAULT_PENALTY=9）/ parsing / geometry / semantics / skeleton / topology / rendering / qa / io / tools（~26 脚本）。
- 解析主链：OCG 矢量→标定(SCALE=0.0529 m/pt, 原点(2019.1,1154.8)pt, Y翻转)→墙体矢量化→房间识别→门洞识别→门归属→GeoJSON。
- **实际拓扑由 skeleton/pipeline.build_skeleton_topology 生成**（手动骨架优先取 result/skeleton_manual_parsed.json 跳过中轴），topology.build_floor_topology 仅回退；改拓扑须两处同步。
- 门节点(TD) v9：同物理开口门按 center_m(0.8m)+投影坐标(1.0m) 两次合并；仅归属≥1封闭房间建 TD；validate_geojson 校验「每 TR 须有 TR↔TD 边」。
- route_rules.py：受限 Dijkstra+三层回退(最佳门→所有门→wall_fallback)；盲模式剔盲区/禁楼梯跨层；validate_wall_crossing 豁免 doorway/facility 相邻段。
- render_interactive.py 自包含交互 HTML；render_map.py→PNG；validate_geojson.py QA（核心指标无门封闭房间=0）。

## 当前进展（v9.0.0, 2026-09-21 实测）
房间 136（F1 81/F2 55）；门 208；墙 4442；柱 220；拓扑节点 465、边 592；跨层边 10(楼梯7+电梯3)；骨架线 F1 59/F2 48；walkable F1 31/F2 16；指纹网格 1434 全楼 / 647 路线（F1 611/F2 36）；riskNodes F1 68/F2 41。QA PASS。信标现行台账 61 枚（F1 52/F2 9，wall 54/door_frame 7；−10dBm/300ms/CR2477），647 路线点 ≥3 可见覆盖 97.7%（F1 97.55/F2 100）；纯三点退化点 21.3%（138/647）。74 枚 route-mask/GDOP 系方案态（docs/13）。两条基准路线：1F 音乐教室→组织办公 140.4m（同层）、教师办公室 F1→历史教室 F2 146.3m（跨层经电梯）。

## 关键约定
- 正式脚本仅 src/，调试 debug/，根目录不留 .py；路径基于 __file__ 推导。
- ⚠️ **门不做合并（2026-08-09 用户明确）**：同一物理开口只允许一扇门，禁止三处合并逻辑（parse_cad_pdf / skeleton/pipeline / topology）。
- 卫生间防火门丢弃；卫生间/楼梯间摆弧门(DK<14pt)重分类 opening(F1=4/F2=4)。门洞=window 层 DK 笔画，优先级 ①DK<13pt 真实轴 ②50pt 内墙缝 ③吸附墙(~1.6m)。
- ⚠️ cv2.floodFill 陷阱：newVal 写回图像、mask 只置1，取填充区须 图像==newVal。
- ⚠️ push 凭据：SSH = ~/.ssh/config Host github-cuitao（id_ed25519）；remote 必须 git@github-cuitao:cuitao2046/pathai.git。
- ⚠️ 手动骨架优先：skeleton_manual_parsed.json 存在则跳过中轴；必须入库，严禁 checkout 旧提交回退；重生成用 src/tools/import_manual_skeleton.py。
- ⚠️ geojson 房间类型两层：geometry.rooms[].properties.type=粗粒度(room/corridor/…)，roomType/roomSubType=细粒度同值；semantic.rooms[] 只有 type+roomSubType（无 roomType）。
- ⚠️ git 目录误删 bug（checkout/rebase 触发，连 untracked 一并删且不可恢复）：操作后立即 git status 自检、deleted 用 git checkout HEAD -- 恢复（须 checkout -- . 全面恢复）；优先 cherry-pick；切分支前 commit 或 stash -u 保护。
- ⚠️ 远端跟踪引用不落盘：校验同步用 git ls-remote origin <branch> 比对哈希，勿依赖 origin/*。
- .workbuddy/memory/ 与 skeleton_manual_parsed.json 随仓库同步；编辑 .gitignore 后务必 add+commit。
- ⚠️ 本机执行环境：Bash PATH 间歇损坏→切 Python；PowerShell stdout 不回显、Remove-Item 静默失败；跑脚本/回归用 venv `…binaries/python/envs/default/Scripts/python.exe`（含 shapely/nx；托管 3.13.12 无 shapely，跑回归会假失败）；看输出让脚本写 UTF-8 文件再 Read；删文件用 python -c os.remove。
- ⚠️ **并行 Edit 同一文件会竞态（2026-09-21 实测）**：同一消息里对同一文件的多个 Edit 调用并行执行、last-writer-wins，只有最后 1 个生效（其余静默丢失但各返回成功）。**同一文件的多处修改必须串行（分多条消息）或用带命中断言的 Python 脚本一次完成**；批量文档修改一律走断言脚本。

## 提交工作流（铁律）
- 铁律0：禁 master 直接开发。分支→commit→push→人工校验→FF 合入→推 master→删分支（本地 -d + 远端 --delete）。
- 分支名禁斜杠（unborn 分支陷阱）；铁律0b 一分支一需求；铁律0c 禁顺带改无关代码，提交前 git diff --stat 自查。
- ✅ 堆叠分支（2026-09-21 采纳）：多需求积压时分支1基于 master、分支2基于分支1 tip…按序 FF，零 rebase；checkout -b 前后 status --porcelain 必须完全一致；暂存校验用路径前缀归属（目录条目展开多文件，不能数数）。
- ✅ 未跟踪文件 FF 前置（2026-09-21 实测）：仓库外备份→路径写入 .git/info/exclude 让 status 变空→safe-merge→finally 还原 exclude→事后比对大小。勿用移出搬回。坑：copy2 覆盖只读先 chmod S_IWRITE；git 路径 / 与 Windows \ 须统一 replace 后比对；工具会重试执行同一脚本——判读以 git log/reflog/status 为准，不看退出码；git ls-files 默认 quotepath=true 会给含空格/中文文件名加引号+八进制转义（备份脚本拼路径必炸），须加 -c core.quotepath=false。
- master 现状（2026-09-22 上午）：`fd4358f`（==远端，ls-remote 校验），远端仅 master。09-21 下午至 09-22 合入序：…→1fefb32→f989a5b→aa7ef31→e9c8d08→fd4358f（A4 三脚架改云腾 VT-7008，预算联动：A 614/固定项 794/档1 方案I 3,989）。

## 已知限制
孤儿门标签未匹配(F1 61/F2 17)；卫生间多边形只覆盖盥洗区；骨架为简化模型；门无铰链/朝向；DK 漏检；CAD 标签包围盒误识别(render 用 _is_label_bbox 过滤)；OPEN_SPACE_TYPES 开放空间建 intersection 不合并；穿墙均为桥边回退（数据质量，独立修复）。

## 失败实验速记
虚线墙=短段+大间隙→30pt桥接；真墙=2px单线不能开运算去薄墙；LABEL_SKIP_RE 不含"出入口"；arc_mid 非万能(外开门)；DK 是 window 层矢量笔画；shapely 宽可见图 O(n²)→numpy 栅格 A*。

## 方案迭代历史
详见各日 .md（合班射线投票 v2、手动骨架+路由三层、route-mask+GDOP 等）。
