#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
gen_beacon_at_commands.py
--------------------------
由**现行信标台账** `result/ble_deployment.json` 生成：
  1. `docs/reference/beacon_at_commands_ledger.md` —— RF-B-SR1 逐枚 AT 配置命令清单（供 nRF Connect 现场使用）
  2. `result/beacon_provisioning_plan.json` —— 机器可读配置计划（beacon-provisioner 小程序的输入契约）

设计依据 `docs/27-蓝牙信标配置工具方案.md` §5；设备语法依据
`docs/reference/RF-B-SR1_user_manual.pdf`（P9~P17）：
  AT+MODE=0                            0=iBeacon（默认；防前次误配成 Eddystone）
  AT+NAME=<名>                         设备名称，**上限 11 字节**
  AT+BEACON=<CID>,<Major4>,<Minor4>,<RSSI@1m>,<UUID32>
                                       Major/Minor 须 4 位 hex 前补零；
                                       RSSI@1m 为有符号字节，负值取补码（-59 → C5）
  AT+POWER=<dBm>                       本方案统一 -10（设备档位无 -8/-12）
  AT+ADS=,1,<间隔ms>                   模式必须=1（可连接）；设 0 将永久无法再改参数
  AT+AUTH=0 / AT+ENCRYPT=0             保持关闭（鉴权/加密开启后扫描端解析不出三元组）

关键约定（与 `docs/19`、`docs/18` TODO-9 一致）：
  - **Major/Minor 一律取台账字段**，不得按 floor 推算（floor 是判楼层的唯一权威，
    major 是部署编号空间；现行 61 枚满足 major == floor，但两者语义不同）；
  - UUID 取台账首枚的 uuid，并要求全表唯一；
  - 名称超 11 字节的按方案 A 去 `BK-` 前缀（`BK-TR-F1-001` → `TR-F1-001`），
    台账 beaconId 不变，仅设备名做映射。

用法:
  python src/tools/gen_beacon_at_commands.py                 # 默认台账 + 默认输出
  python src/tools/gen_beacon_at_commands.py --rssi-at-1m -69   # 现场标定后指定 1m 参考 RSSI
  python src/tools/gen_beacon_at_commands.py --check-only      # 只做预检（不写文件）
退出码：0 成功；2 台账预检失败（**不产出任何文件**）。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DEFAULT_LEDGER = os.path.join(ROOT, "result", "ble_deployment.json")
DEFAULT_OUT_MD = os.path.join(ROOT, "docs", "reference", "beacon_at_commands_ledger.md")
DEFAULT_OUT_PLAN = os.path.join(ROOT, "result", "beacon_provisioning_plan.json")

COMPANY_ID = "004C"           # Apple iBeacon 标准 Company ID
DEFAULT_TX_POWER = -10        # 统一档位（设备支持 -28/-20/-10/-5/-3/0/1/2/4/6）
DEFAULT_INTERVAL_MS = 300     # 台账 broadcastInterval 缺省值
DEFAULT_RSSI_AT_1M = -59      # 1m 参考 RSSI 占位值，须现场标定后替换（见 docs/28）
NAME_MAX_BYTES = 11           # 手册 P10：AT+NAME 上限
REQUIRED_FIELDS = ("beaconId", "uuid", "major", "minor", "floor", "coordinates")


class LedgerError(Exception):
    """台账预检失败（不产出文件）。"""


def rssi_hex(rssi: int) -> str:
    """有符号 8 位：负值取补码（-59 → C5）。"""
    return f"{(256 + rssi) & 0xFF if rssi < 0 else rssi & 0xFF:02X}"


def hex4(value: int) -> str:
    """2 字节、前补零 hex（1 → 0001）；手册要求前导零不可省略。"""
    return f"{value & 0xFFFF:04X}"


def device_name(beacon_id: str) -> str:
    """设备名：≤11 字节用 beaconId 原名；超长按方案 A 去 `BK-` 前缀。"""
    if len(beacon_id.encode("utf-8")) <= NAME_MAX_BYTES:
        return beacon_id
    stripped = beacon_id[3:] if beacon_id.startswith("BK-") else beacon_id
    if len(stripped.encode("utf-8")) > NAME_MAX_BYTES:
        raise LedgerError(
            f"设备名超长且无法压缩：{beacon_id} → {stripped}"
            f"（{len(stripped.encode('utf-8'))} > {NAME_MAX_BYTES} 字节）"
        )
    return stripped


def sha256_file(path: str) -> str:
    with open(path, "rb") as handle:
        return hashlib.sha256(handle.read()).hexdigest()


def load_ledger(path: str) -> dict:
    if not os.path.isfile(path):
        raise LedgerError(f"台账不存在：{path}")
    with open(path, encoding="utf-8") as handle:
        ledger = json.load(handle)
    if not isinstance(ledger, dict) or not isinstance(ledger.get("beacons"), list):
        raise LedgerError(f"台账结构异常（缺 beacons 数组）：{path}")
    return ledger


def validate_ledger(ledger: dict) -> list[str]:
    """预检台账；返回错误列表（空列表 = 通过）。"""
    errors: list[str] = []
    beacons = ledger["beacons"]
    if not beacons:
        errors.append("台账 beacons 为空")
        return errors

    uuids = {b.get("uuid") for b in beacons}
    if len(uuids) != 1:
        errors.append(f"uuid 不唯一（{len(uuids)} 种）：{sorted(str(u) for u in uuids)}")

    seen_ids: set[str] = set()
    seen_triplets: dict[tuple, str] = {}
    for beacon in beacons:
        bid = beacon.get("beaconId") or "?"
        for field in REQUIRED_FIELDS:
            if beacon.get(field) is None or beacon.get(field) == "":
                errors.append(f"{bid}：缺少必填字段 {field}")
        if bid in seen_ids:
            errors.append(f"beaconId 重复：{bid}")
        seen_ids.add(bid)

        major, minor = beacon.get("major"), beacon.get("minor")
        if isinstance(major, int) and isinstance(minor, int):
            if not 0 <= major <= 0xFFFF:
                errors.append(f"{bid}：major 超出 [0,65535]：{major}")
            if not 0 <= minor <= 0xFFFF:
                errors.append(f"{bid}：minor 超出 [0,65535]：{minor}")
            # major 必须等于 floor（与校验器 R7、docs/18 TODO-9 同口径）：
            # 否则配置工具按 floor 写出的参数会与地图包/客户端三元组解析不一致
            floor = beacon.get("floor")
            if isinstance(floor, int) and major != floor:
                errors.append(f"{bid}：major={major} ≠ floor={floor}（须一致，见 docs/18 TODO-9）")
            triplet = (beacon.get("uuid"), major, minor)
            if triplet in seen_triplets:
                errors.append(
                    f"iBeacon 三元组重复：{bid} 与 {seen_triplets[triplet]}"
                    f"（uuid/major/minor 相同，扫描端无法区分）"
                )
            else:
                seen_triplets[triplet] = bid
        else:
            errors.append(f"{bid}：major/minor 须为整数")

        coords = beacon.get("coordinates")
        if not (isinstance(coords, list) and len(coords) == 2):
            errors.append(f"{bid}：coordinates 须为 [x, y]")

        try:
            device_name(bid)
        except LedgerError as exc:
            errors.append(str(exc))
    return errors


def build_plan(ledger: dict, rssi_at_1m: int, ledger_path: str = DEFAULT_LEDGER) -> dict:
    """构造机器可读配置计划（beacon-provisioner 的输入契约，docs/27 §5.3）。"""
    uuid32 = ledger["beacons"][0]["uuid"].replace("-", "").upper()
    if len(uuid32) != 32:
        raise LedgerError(f"UUID 应为 32 位 hex：{uuid32}")
    uuid_dashed = ledger["beacons"][0]["uuid"]
    beacons = []
    for beacon in ledger["beacons"]:
        bid = beacon["beaconId"]
        name = device_name(bid)
        major = beacon["major"]
        minor = beacon["minor"]
        tx_power = beacon.get("txPower", DEFAULT_TX_POWER)
        interval = beacon.get("broadcastInterval", DEFAULT_INTERVAL_MS)
        beacons.append(
            {
                "beaconId": bid,
                "deviceName": name,
                "floor": beacon["floor"],
                "major": major,
                "minor": minor,
                "txPower": tx_power,
                "interval": interval,
                "rssiAt1m": None,  # 现场实测后回填（docs/28）
                "locationDesc": beacon.get("locationDesc", ""),
                "coordinates": beacon["coordinates"],
                "mountType": beacon.get("mountType"),
                "installHeight": beacon.get("installHeight"),
                "semanticTag": beacon.get("semanticTag"),
                "atCommands": [
                    "AT+MODE=0",
                    f"AT+NAME={name}",
                    f"AT+BEACON={COMPANY_ID},{hex4(major)},{hex4(minor)},"
                    f"{rssi_hex(rssi_at_1m)},{uuid32}",
                    f"AT+POWER={tx_power}",
                    f"AT+ADS=,1,{interval}",
                    "AT+AUTH=0",
                    "AT+ENCRYPT=0",
                ],
            }
        )
    return {
        "schemaVersion": "1.0",
        "generatedAt": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "generator": "src/tools/gen_beacon_at_commands.py",
        "sourceLedger": os.path.relpath(ledger_path, ROOT).replace(os.sep, "/"),
        "sourceLedgerSha256": sha256_file(ledger_path),
        "defaults": {
            "companyId": COMPANY_ID,
            "uuid": uuid_dashed,
            "txPower": DEFAULT_TX_POWER,
            "interval": DEFAULT_INTERVAL_MS,
            "rssiAt1mPlaceholder": rssi_at_1m,
            "atNameMaxBytes": NAME_MAX_BYTES,
        },
        "beacons": beacons,
    }


def render_markdown(plan: dict, title_tag: str) -> str:
    beacons = plan["beacons"]
    renamed = [b for b in beacons if b["deviceName"] != b["beaconId"]]
    rssi = plan["defaults"]["rssiAt1mPlaceholder"]
    lines: list[str] = []
    lines.append(f"# RF-B-SR1 信标 AT 配置命令清单（{title_tag}）\n")
    lines.append(f"> 生成依据：现行台账 `{plan['sourceLedger']}`（{len(beacons)} 枚）  ")
    lines.append(f"> 目标 UUID：`{plan['defaults']['uuid']}`  ")
    lines.append(f"> 发射功率统一 **{plan['defaults']['txPower']} dBm**（设备档位无 -8/-12，见 docs/08 §1.3）  ")
    lines.append(f"> 广播间隔：取台账 `broadcastInterval`（当前全部 {plan['defaults']['interval']} ms）  ")
    lines.append(f"> ⚠️ **RSSI@1m 为占位值 {rssi} dBm（`{rssi_hex(rssi)}`）**，须现场标定后替换"
                 "（标定流程见 `docs/28-定位参数现场标定方案.md`）\n")
    lines.append("## 使用方式与注意事项\n")
    lines.append("1. 手机装 **nRF Connect**，搜到信标（默认名 `RFstar_XXXX`）并连接。")
    lines.append("2. 在 **RX 特征值**（写入通道，UUID `6E400002…`）逐条发送下列命令；"
                 "**AT 指令须大写、不带回车换行**。")
    lines.append("3. 主机端 MTU 须 **≥128 字节**，否则指令无法设置。")
    lines.append("4. 每条指令回 `OK` 才算成功；`FAIL`/`ERROR`/`BUSY` 需重试（`BUSY` 为上一操作未完成）。")
    lines.append("5. 所有设置指令**立即生效且掉电保存**，无需 RESET/SAVE。")
    lines.append("6. ⚠️ `AT+ADS` 的**模式必须保持 1（可连接）**；一旦设 0 不可连接，"
                 "将**永久无法再改任何参数**（整台报废）。")
    lines.append("7. 建议先执行 `AT+NAME` 命名，便于在 nRF Connect 中区分各信标。")
    lines.append("8. `AT+AUTH=0` / `AT+ENCRYPT=0` 为幂等确认：鉴权或加密一旦开启，"
                 "扫描端将解析不出 UUID/Major/Minor。\n")
    lines.append("## 一、逐信标 AT 命令\n")
    for beacon in beacons:
        head = f"### {beacon['beaconId']}  ·  F{beacon['floor']} · {beacon.get('semanticTag') or ''}"
        lines.append(head)
        if beacon["deviceName"] != beacon["beaconId"]:
            lines.append(f"- 设备名：**{beacon['deviceName']}**（原名 {beacon['beaconId']} 超 "
                         f"{NAME_MAX_BYTES} 字节，按 docs/27 方案 A 去 `BK-` 前缀）")
        lines.append(f"- 位置：{beacon.get('locationDesc') or '-'}")
        coord = beacon.get("coordinates")
        if coord:
            lines.append(
                f"- 坐标(米)：({coord[0]}, {coord[1]})　安装高度：{beacon.get('installHeight')} m"
                f"　方式：{beacon.get('mountType')}"
            )
        lines.append("```")
        lines.extend(beacon["atCommands"])
        lines.append("```")
        lines.append("")

    lines.append("## 二、命令汇总表\n")
    lines.append("| # | beaconId | 设备名 | 楼层 | Major(hex) | Minor(dec) | Minor(hex) | "
                 "RSSI@1m(hex) | 间隔(ms) | AT+BEACON 命令 |")
    lines.append("|---|---|---|---|---|---|---|---|---|---|")
    rssi_h = rssi_hex(rssi)
    for index, beacon in enumerate(beacons, 1):
        beacon_cmd = next(c for c in beacon["atCommands"] if c.startswith("AT+BEACON="))
        lines.append(
            f"| {index} | {beacon['beaconId']} | {beacon['deviceName']} | F{beacon['floor']} | "
            f"{hex4(beacon['major'])} | {beacon['minor']} | {hex4(beacon['minor'])} | {rssi_h} | "
            f"{beacon['interval']} | `{beacon_cmd}` |"
        )

    lines.append(f"\n## 三、设备名映射（{len(renamed)} 枚，超 11 字节）\n")
    if renamed:
        lines.append("| beaconId（台账） | 写入设备名 | 字节数 |")
        lines.append("|---|---|---|")
        for beacon in renamed:
            lines.append(f"| {beacon['beaconId']} | {beacon['deviceName']} | "
                         f"{len(beacon['deviceName'].encode('utf-8'))} |")
    else:
        lines.append("（无：全部 beaconId 均 ≤ 11 字节）")

    lines.append("\n## 四、RSSI@1m 现场标定（替换清单中的占位值）\n")
    lines.append("`RSSI@1m` 是 **1 米处的参考 RSSI**（measured power），与发射功率 `AT+POWER` 是两个不同的量：")
    lines.append("本方案把发射功率降到 -10 dBm，1m 参考值会同步下降约 10 dB，"
                 "因此**不能照抄 -59**（该值是 0 dBm 档设备的典型值），必须现场实测。\n")
    lines.append("步骤（完整流程见 `docs/28`）：")
    lines.append("1. 信标按本清单配置并固定到位后，手机置于其正下方 **1 m** 处；")
    lines.append("2. 用扫描工具读该信标 RSSI，多次采样取中位数（避免手抖/遮挡）；")
    lines.append("3. 负值转补码 hex：`字节 = (256 + RSSI) & 0xFF`（-57 → C7、-62 → C2、-69 → BB）；")
    lines.append("4. 把该值替换进对应信标的 `AT+BEACON` 第 4 参数后重发（其余参数不变）；")
    lines.append("5. 全楼统一典型值时，可批量替换清单中所有占位值。\n")

    lines.append(f"> 本清单由 `src/tools/gen_beacon_at_commands.py` 依据 `{plan['sourceLedger']}` 生成；"
                 "台账更新后重新运行即可（生成器另产出 `result/beacon_provisioning_plan.json`）。")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="由现行台账生成 RF-B-SR1 信标 AT 配置清单与配置计划")
    parser.add_argument("--ledger", default=DEFAULT_LEDGER, help="信标台账 JSON（默认 result/ble_deployment.json）")
    parser.add_argument("--out", default=DEFAULT_OUT_MD, help="输出 markdown 清单")
    parser.add_argument("--plan", default=DEFAULT_OUT_PLAN, help="输出机器可读配置计划 JSON")
    parser.add_argument("--rssi-at-1m", type=int, default=DEFAULT_RSSI_AT_1M,
                        help=f"1m 参考 RSSI 占位值（默认 {DEFAULT_RSSI_AT_1M}，现场标定后指定）")
    parser.add_argument("--title-tag", default="现行台账 · 61 枚", help="清单标题标注")
    parser.add_argument("--check-only", action="store_true", help="只做预检，不写文件")
    args = parser.parse_args()

    try:
        ledger = load_ledger(args.ledger)
        errors = validate_ledger(ledger)
    except LedgerError as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 2

    if errors:
        print(f"[ERROR] 台账预检未通过（{len(errors)} 项），不产出任何文件：", file=sys.stderr)
        for item in errors:
            print(f"  - {item}", file=sys.stderr)
        return 2

    plan = build_plan(ledger, args.rssi_at_1m, args.ledger)
    print(f"预检通过：{len(plan['beacons'])} 枚信标，uuid={plan['defaults']['uuid']}，"
          f"rssi@1m 占位={args.rssi_at_1m}（{rssi_hex(args.rssi_at_1m)}）")
    if args.check_only:
        return 0

    with open(args.out, "w", encoding="utf-8") as handle:
        handle.write(render_markdown(plan, args.title_tag))
    with open(args.plan, "w", encoding="utf-8") as handle:
        json.dump(plan, handle, ensure_ascii=False, indent=2)
    print(f"written: {os.path.relpath(args.out, ROOT)}")
    print(f"written: {os.path.relpath(args.plan, ROOT)}")
    print(f"  ledgerSha256={plan['sourceLedgerSha256']}")
    renamed = sum(1 for b in plan["beacons"] if b["deviceName"] != b["beaconId"])
    print(f"  设备名超长映射：{renamed} 枚")
    return 0


if __name__ == "__main__":
    sys.exit(main())
