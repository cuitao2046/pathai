#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
gen_beacon_at_commands.py
--------------------------
根据 result/beacon_deployment_plan_routes.json 生成 RF-B-SR1 信标的
逐信标 AT 配置命令清单（markdown）。

设备语法依据 docs/reference/RF-B-SR1_user_manual.pdf：
  AT+NAME=<名称>
  AT+BEACON=<CompanyID>,<Major>,<Minor>,<RSSI@1m>,<UUID32>
      - CompanyID 用 Apple iBeacon 标准 004C
      - Major/Minor 须 2 字节、前补零的 hex（如楼层1→0001，minor 10101→2775）
      - RSSI@1m 为有符号字节，负值取补码（如 -62 dBm -> C2）
  AT+POWER=<dBm>           本方案统一 -10（设备档位无 -8/-12）
  AT+ADS=<保留>,<模式>,<间隔ms>
      - 模式必须=1（可连接）；一旦设 0 不可连接将永久无法再改参数
      - 间隔沿用部署计划 broadcastInterval（当前 300ms）

用法:
  python src/tools/gen_beacon_at_commands.py
"""
import json
import os
import argparse

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SRC = os.path.join(ROOT, "result", "beacon_deployment_plan_routes.json")
OUT = os.path.join(ROOT, "docs", "reference", "beacon_at_commands_routes.md")

COMPANY_ID = "004C"          # Apple iBeacon 标准 Company ID
TX_POWER = -10               # 统一档位（设备支持 -28/-20/-10/-5/-3/0/1/2/4/6）
DEFAULT_RSSI_1M = -59        # 占位值：建议现场实测后替换（-> C5）


def rssi_hex(r: int) -> str:
    """有符号 8 位，负值取补码。"""
    b = (256 + r) & 0xFF if r < 0 else r & 0xFF
    return f"{b:02X}"


def hex4(z: int) -> str:
    """2 字节、前补零 hex。"""
    return f"{z & 0xFFFF:04X}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", default=SRC, help="部署计划 JSON")
    ap.add_argument("--out", default=OUT, help="输出 markdown")
    ap.add_argument("--title-tag", default="路线部署 · 48 个", help="标题标注")
    args = ap.parse_args()
    with open(args.src, encoding="utf-8") as f:
        d = json.load(f)
    beacons = d["beacons"]
    uuid32 = d["uuid"].replace("-", "").upper()
    assert len(uuid32) == 32, f"UUID 应为32位hex: {uuid32}"
    rssi_h = rssi_hex(DEFAULT_RSSI_1M)

    L = []
    L.append(f"# RF-B-SR1 信标 AT 配置命令清单（{args.title_tag}）\n")
    L.append(f"> 生成依据：`{os.path.relpath(args.src, ROOT)}`（schemaVersion {d.get('schemaVersion')}）  ")
    L.append(f"> 目标 UUID：`{d['uuid']}`  ")
    L.append(f"> 发射功率统一 **-10 dBm**（设备档位无 -8/-12，详见 docs/08 §1.3）  ")
    L.append(f"> 广播间隔：沿用部署计划 `broadcastInterval`（当前全部 300 ms）  ")
    L.append(f"> ⚠️ **RSSI@1m 为占位值 {DEFAULT_RSSI_1M} dBm（`{rssi_h}`）**，须现场实测后逐信标替换（校准方法见文末）\n")

    L.append("## 使用方式与注意事项\n")
    L.append("1. 手机装 **nRF Connect**，搜到信标（默认名 `RFstar_XXXX`）并连接。")
    L.append("2. 在 **RX 特征值**（写入通道，UUID `6E400002…`）发送以下命令；**AT 指令须大写、不带回车换行**。")
    L.append("3. 主机端 MTU 须 **≥128 字节**，否则指令无法设置。")
    L.append("4. 所有设置指令**立即生效且掉电保存**，无需 RESET/SAVE。")
    L.append("5. ⚠️ `AT+ADS` 的**模式必须保持 1（可连接）**；一旦设 0 不可连接，将永久无法再改任何参数。")
    L.append("6. 建议先 `AT+NAME=BK-xx-xxx` 命名，便于在 nRF Connect 中区分各信标。\n")

    # ---- 逐信标命令块 ----
    L.append("## 一、逐信标 AT 命令\n")
    for b in beacons:
        bid = b["beaconId"]
        floor = b["floor"]
        minor_dec = b["minor"]
        interval = b.get("broadcastInterval", 400)
        coord = b.get("coordinates") or b.get("plannedCoordinates")
        snap = b.get("snapDist_m", 0.0)
        h = b.get("installHeight")
        mt = b.get("mountType")
        desc = b.get("locationDesc", "")
        major_h = hex4(floor)
        minor_h = hex4(minor_dec)
        name = bid
        beacon_cmd = f"AT+BEACON={COMPANY_ID},{major_h},{minor_h},{rssi_h},{uuid32}"
        L.append(f"### {bid}  ·  F{floor} · {b.get('semanticTag','')}")
        L.append(f"- 位置：{desc}")
        if coord:
            L.append(f"- 坐标(pt)：({coord[0]}, {coord[1]})　吸附偏移：{snap}m　安装高度：{h}m　方式：{mt}")
        L.append("```")
        L.append(f"AT+NAME={name}")
        L.append(beacon_cmd)
        L.append(f"AT+POWER={TX_POWER}")
        L.append(f"AT+ADS=,1,{interval}")
        L.append("```")
        L.append("")

    # ---- 汇总表 ----
    L.append("## 二、命令汇总表\n")
    L.append("| # | beaconId | 楼层 | Major(hex) | Minor(dec) | Minor(hex) | RSSI@1m(hex) | 间隔(ms) | AT+BEACON 命令 |")
    L.append("|---|---|---|---|---|---|---|---|---|")
    for i, b in enumerate(beacons, 1):
        bid = b["beaconId"]
        floor = b["floor"]
        minor_dec = b["minor"]
        interval = b.get("broadcastInterval", 400)
        major_h = hex4(floor)
        minor_h = hex4(minor_dec)
        beacon_cmd = f"AT+BEACON={COMPANY_ID},{major_h},{minor_h},{rssi_h},{uuid32}"
        L.append(f"| {i} | {bid} | F{floor} | {major_h} | {minor_dec} | {minor_h} | {rssi_h} | {interval} | `{beacon_cmd}` |")

    # ---- 校准说明 ----
    L.append("\n## 三、RSSI@1m 现场校准（重要）\n")
    L.append("指纹定位以原始 RSSI 指纹库为主，RSSI@1m 仅影响帧内距离估计，因此**不校准也能用**；")
    L.append("但建议抽测校准以提升任何基于帧距离的逻辑精度。步骤：")
    L.append("1. 信标固定在 -10 dBm 后，手机置于其正下方 **1 m** 处，用 nRF Connect 读取该信标的 RSSI（如读到 -57 dBm）。")
    L.append("2. 将读到的负值转为补码 hex：字节 = (256 + RSSI) & 0xFF。例：-57 -> 0xC7；-62 -> 0xC2；-59 -> 0xC5。")
    L.append("3. 把对应信标 `AT+BEACON` 命令中的第 4 参数替换为该值（保持其余不变），重发即可。")
    L.append("4. 若全楼统一一个典型值，可直接批量替换清单中所有的 `C5`。\n")

    L.append("> 本清单由 `src/tools/gen_beacon_at_commands.py` 依据部署计划 JSON 生成，部署计划更新后重新运行即可。")

    with open(args.out, "w", encoding="utf-8") as f:
        f.write("\n".join(L))
    print(f"written: {args.out}")
    print(f"beacons={len(beacons)}  uuid32={uuid32}  rssi_hex={rssi_h}")


if __name__ == "__main__":
    main()
