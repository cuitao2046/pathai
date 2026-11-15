# 危险区标注工具（hazard-editor）

在室内地图上标注「对视障人士不安全」的边，设置 `blindAccessible` 与危险档位，并导出后续路由可直接消费的危险层 JSON。

- 入口页面：[index.html](./index.html)
- 数据格式：[hazard-layer.schema.json](../../specs/phase-1-indoor-navigation/hazard-layer.schema.json)
- 初始危险层：[school-building-01.json](../../backend/hazards_data/school-building-01.json)
- 地图数据：[data/map-data.js](./data/map-data.js)（由脚本生成，勿手改）

## 怎么打开

直接双击 `index.html` 即可（数据已内联成普通 script，`file://` 可用，不需要构建、不需要联网）。

如果要用本地服务器：

```powershell
python -m http.server 8899 --directory tools/hazard-editor
# 浏览器打开 http://127.0.0.1:8899/
```

推荐 Chrome / Edge。首次打开会自动载入 `backend/hazards_data/school-building-01.json` 的 23 条基线标注（地图包中 `blindAccessible=false` 的楼梯接入边/跨层楼梯边）。

## 怎么标

1. 顶部切换楼层 `1F / 2F`。
2. 选边：
   - 「浏览/平移」模式：单击选中一条边，`Shift + 单击` 多选；
   - 「框选」模式：拖出矩形，选中与矩形相交的所有边；
   - 左侧「已标注」清单点击任一条，可跳到对应楼层并定位/选中它的边。
3. 在右侧表单设置：
   - **盲人可通行（blindAccessible）**：取消勾选会自动切到 `block`；`block` 会导出 `blindAccessible=false`；
   - **危险档位**：`block` 禁行 / `avoid` 尽量避开（需填避让惩罚米数）/ `notice` 仅提示；
   - **危险类型 + 理由文案**（`reason` 必填，用于清单与后续语音播报）；
   - 「更多选项」里可设语音播报、适用人群、生效/失效时间（临时施工务必填失效时间）。
4. 点「应用到选中」。同一批边再次应用会原地更新（保留 `hazardId`）；
   若选中边里已有更严格的标注（block > avoid > notice），会弹确认，防止框选时误覆盖楼梯禁行。
5. 撤销：顶部「撤销」或 `Ctrl+Z`；`Esc` 清空选择。

图上颜色：红色实线 = `block`，橙色虚线 = `avoid`，紫色点线 = `notice`，蓝色高亮 = 当前选中；紫色菱形 = 跨层连接（楼梯/电梯），可点击选中。

## 怎么导出 / 导入

- **导出**：点顶部「导出危险层」，下载文件名 `<siteId>.json`（当前为 `school-building-01.json`）；
  「导出预览」里能看到实际 JSON 全文，可直接全选复制。
- 导出的文件符合 [schema](../../specs/phase-1-indoor-navigation/hazard-layer.schema.json)：
  `schemaVersion / hazardVersion / siteId / mapVersion / hazards[]`，
  每条危险含 `level / edgeIds / reason / blindAccessible / penaltyM / appliesTo / activeFrom / activeUntil / status / source` 等。
  作为后续路由改造的输入时，放回 `backend/hazards_data/<siteId>.json` 即可。
- **导入**：点「导入危险层」选择已有 JSON（完整危险层或 `hazards` 数组均可）。导入是整体替换，可用撤销恢复；
  `siteId`、`mapVersion` 与当前地图不一致时会在左下角提示。

编辑草稿会自动存到浏览器 localStorage（按 `siteId` 区分），刷新页面不丢；「清空」只清空内存并写入草稿，可通过撤销恢复。

## 重新生成地图数据

地图包更新后（或切换场所），重新生成内联数据：

```powershell
python tools/hazard-editor/build_data.py
# 指定地图包 / 初始危险层 / 重新生成基线
python tools/hazard-editor/build_data.py ^
  --map backend/maps_data/school-building-01/2026-11-08.2.json ^
  --initial-hazards backend/hazards_data/school-building-01.json ^
  --seed-out backend/hazards_data/school-building-01.json --force-seed
```

脚本会裁剪几何、坐标转厘米取整，当前双楼层数据约 330KB，打开与缩放不需要额外优化。

## 边界说明（本轮范围）

- 仅支持以「边」为目标的标注；schema 已为 `area / point` 预留 `targetType` 与 `geometry`，后续可加多边形绘制。
- 尚未接入后端路由与客户端播报，本轮只产出工具与危险层数据。
- 跨层楼梯/电梯只在起始楼层显示一个菱形标记，点击即可标注（`floorId` 取起始楼层）。
- 导出固定使用当前页面的 `mapVersion`；跨地图版本复用危险层前需复核 `edgeIds`。
