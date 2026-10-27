# -*- coding: utf-8 -*-
"""信标 AT 配置清单生成器回归测试（src/tools/gen_beacon_at_commands.py）。

覆盖两类风险（docs/27 §2 的 G1~G5）：
1. **数值来源**：Major/Minor 必须取台账字段、UUID 必须取自台账且全表唯一、
   1m 参考 RSSI 按补码编码——避免"按 floor 推算 major"与"照抄 -59"这类错配；
2. **预检拦截**：台账不自洽（major≠floor、三元组重复、缺字段…）时必须 **非零退出且不产出文件**，
   把错误拦在离线阶段，而不是让现场写入错误参数。

数据来源：真实台账 `result/ble_deployment.json`（61 枚，项目约定 mock 不得虚构数据）。
"""
import copy
import json
import os
import subprocess
import sys
import tempfile
import unittest

from tests.testutil import PROJECT_ROOT, RESULT_DIR, load_json
from src.tools.gen_beacon_at_commands import (
    DEFAULT_RSSI_AT_1M,
    NAME_MAX_BYTES,
    LedgerError,
    build_plan,
    device_name,
    hex4,
    render_markdown,
    rssi_hex,
    validate_ledger,
)

LEDGER_PATH = RESULT_DIR / "ble_deployment.json"
SCRIPT = PROJECT_ROOT / "src" / "tools" / "gen_beacon_at_commands.py"


def real_ledger() -> dict:
    return load_json(LEDGER_PATH)


class TestEncoding(unittest.TestCase):
    def test_hex4_pads_leading_zero(self):
        # 手册 P13 备注：前导零不可省略（major=1 必须写 0001，否则设备返回 ERROR）
        self.assertEqual(hex4(1), "0001")
        self.assertEqual(hex4(2), "0002")
        self.assertEqual(hex4(10101), "2775")
        self.assertEqual(hex4(20001), "4E21")
        self.assertEqual(hex4(0xFFFF), "FFFF")

    def test_rssi_hex_twos_complement(self):
        # 手册 P13：RSSI@1m 为有符号字节，负值取补码
        self.assertEqual(rssi_hex(-57), "C7")
        self.assertEqual(rssi_hex(-62), "C2")
        self.assertEqual(rssi_hex(DEFAULT_RSSI_AT_1M), "C5")
        self.assertEqual(rssi_hex(-69), "BB")
        self.assertEqual(rssi_hex(-1), "FF")
        self.assertEqual(rssi_hex(0), "00")


class TestDeviceName(unittest.TestCase):
    def test_short_id_kept_as_is(self):
        self.assertEqual(device_name("BK-01-001"), "BK-01-001")  # 9 字节

    def test_overlong_id_uses_option_a(self):
        # docs/27 §5.2 已定方案 A：去 `BK-` 前缀
        self.assertEqual(device_name("BK-TR-F1-001"), "TR-F1-001")
        self.assertLessEqual(len(device_name("BK-TR-F2-003").encode()), NAME_MAX_BYTES)

    def test_unshrinkable_id_rejected(self):
        with self.assertRaises(LedgerError):
            device_name("ABCDEFGHIJKL")  # 12 字节且无 BK- 前缀


class TestPlanFromRealLedger(unittest.TestCase):
    """真实台账（61 枚）驱动：预检通过 + 关键取值正确。"""

    @classmethod
    def setUpClass(cls):
        cls.ledger = real_ledger()
        cls.plan = build_plan(cls.ledger, DEFAULT_RSSI_AT_1M, str(LEDGER_PATH))
        cls.by_id = {b["beaconId"]: b for b in cls.plan["beacons"]}

    def test_preflight_passes_on_real_ledger(self):
        self.assertEqual(validate_ledger(self.ledger), [])

    def test_covers_every_ledger_beacon(self):
        self.assertEqual(len(self.plan["beacons"]), len(self.ledger["beacons"]))
        self.assertEqual(len(self.plan["beacons"]), 61)
        # G1：8 枚 BK-M-* 人工补点曾完全缺失于历史清单
        self.assertTrue(all(f"BK-M-{n}" in "".join(self.by_id) for n in ("1-001", "2-001")))

    def test_major_minor_taken_from_ledger_fields(self):
        # BK-M-2-001 在 2026-10-27 修正后为 floor=2 / major=2 / minor=20001
        beacon = self.by_id["BK-M-2-001"]
        self.assertEqual(beacon["major"], 2)
        self.assertEqual(beacon["minor"], 20001)
        cmd = next(c for c in beacon["atCommands"] if c.startswith("AT+BEACON="))
        self.assertEqual(cmd.split(",")[1], "0002")
        self.assertEqual(cmd.split(",")[2], "4E21")

    def test_at_name_all_within_manual_limit(self):
        # G5：历史清单有 18 行 AT+NAME 超 11 字节（设备必返 ERROR）
        for beacon in self.plan["beacons"]:
            self.assertLessEqual(len(beacon["deviceName"].encode("utf-8")), NAME_MAX_BYTES)
            self.assertIn(f"AT+NAME={beacon['deviceName']}", beacon["atCommands"])
        renamed = [b for b in self.plan["beacons"] if b["deviceName"] != b["beaconId"]]
        self.assertEqual(len(renamed), 10)  # 10 枚 BK-TR-Fx-00x 超长

    def test_command_set_and_order(self):
        for beacon in self.plan["beacons"]:
            self.assertEqual(len(beacon["atCommands"]), 7)
            self.assertEqual(beacon["atCommands"][0], "AT+MODE=0")
            self.assertTrue(beacon["atCommands"][1].startswith("AT+NAME="))
            self.assertTrue(beacon["atCommands"][2].startswith("AT+BEACON="))
            self.assertEqual(beacon["atCommands"][3], "AT+POWER=-10")
            # 模式位恒为 1：设 0 后设备永久不可连接（手册 P11）
            self.assertTrue(beacon["atCommands"][4].startswith("AT+ADS=,1,"))
            self.assertEqual(beacon["atCommands"][5], "AT+AUTH=0")
            self.assertEqual(beacon["atCommands"][6], "AT+ENCRYPT=0")

    def test_uuid_and_rssi_encoding(self):
        uuid32 = self.plan["defaults"]["uuid"].replace("-", "").upper()
        self.assertEqual(len(uuid32), 32)
        for beacon in self.plan["beacons"]:
            cmd = next(c for c in beacon["atCommands"] if c.startswith("AT+BEACON="))
            parts = cmd.split(",")
            self.assertEqual(parts[0], "AT+BEACON=004C")
            self.assertEqual(parts[3], "C5")  # -59 占位（待现场标定替换）
            self.assertEqual(parts[4], uuid32)

    def test_plan_contract_fields(self):
        self.assertEqual(self.plan["schemaVersion"], "1.0")
        self.assertEqual(self.plan["sourceLedger"], "result/ble_deployment.json")
        self.assertEqual(len(self.plan["sourceLedgerSha256"]), 64)
        for beacon in self.plan["beacons"]:
            self.assertIsNone(beacon["rssiAt1m"])  # 现场实测后回填
            self.assertIn("locationDesc", beacon)
            self.assertEqual(len(beacon["coordinates"]), 2)


class TestPreflightRejects(unittest.TestCase):
    """台账不自洽时必须报错（离线拦截，避免现场写错参数）。"""

    def mutate(self, fn) -> list:
        ledger = copy.deepcopy(real_ledger())
        fn(ledger)
        return validate_ledger(ledger)

    def test_major_floor_mismatch_rejected(self):
        # docs/18 TODO-9 + 校验器 R7：major 必须等于 floor
        def break_major(ledger):
            for beacon in ledger["beacons"]:
                if beacon["beaconId"] == "BK-M-2-001":
                    beacon["major"] = 1

        errors = self.mutate(break_major)
        self.assertTrue(any("≠ floor" in e for e in errors), errors)

    def test_duplicate_triplet_rejected(self):
        # 两台配置成同一 (uuid,major,minor) 时扫描端无法区分
        def dup(ledger):
            first, second = ledger["beacons"][0], ledger["beacons"][1]
            second["major"], second["minor"] = first["major"], first["minor"]

        errors = self.mutate(dup)
        self.assertTrue(any("三元组重复" in e for e in errors), errors)

    def test_multiple_uuids_rejected(self):
        def break_uuid(ledger):
            ledger["beacons"][1]["uuid"] = "00000000-0000-0000-0000-000000000000"

        errors = self.mutate(break_uuid)
        self.assertTrue(any("uuid 不唯一" in e for e in errors), errors)

    def test_missing_required_field_rejected(self):
        def drop(ledger):
            ledger["beacons"][3].pop("coordinates", None)

        errors = self.mutate(drop)
        self.assertTrue(any("coordinates" in e for e in errors), errors)

    def test_out_of_range_minor_rejected(self):
        def over(ledger):
            ledger["beacons"][2]["minor"] = 70000

        errors = self.mutate(over)
        self.assertTrue(any("超出" in e for e in errors), errors)

    def test_duplicate_beacon_id_rejected(self):
        def dup_id(ledger):
            ledger["beacons"][4]["beaconId"] = ledger["beacons"][0]["beaconId"]

        errors = self.mutate(dup_id)
        self.assertTrue(any("beaconId 重复" in e for e in errors), errors)

    def test_unshrinkable_device_name_rejected(self):
        def long_id(ledger):
            ledger["beacons"][5]["beaconId"] = "ABCDEFGHIJKL"

        errors = self.mutate(long_id)
        self.assertTrue(any("超长" in e for e in errors), errors)

    def test_empty_ledger_rejected(self):
        self.assertTrue(validate_ledger({"beacons": []}))


class TestMarkdown(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.md = render_markdown(build_plan(real_ledger(), DEFAULT_RSSI_AT_1M), "单元测试")

    def test_one_block_per_beacon(self):
        self.assertEqual(self.md.count("\n### "), 61)

    def test_summary_table_row_per_beacon(self):
        rows = [ln for ln in self.md.splitlines() if ln.startswith("| ") and "`AT+BEACON=" in ln]
        self.assertEqual(len(rows), 61)

    def test_contains_safety_notes(self):
        # 模式位 0 的不可逆后果必须显著提示
        self.assertIn("永久无法再改任何参数", self.md)
        self.assertIn("MTU 须 **≥128 字节**", self.md)

    def test_lists_device_name_mapping(self):
        self.assertIn("设备名映射（10 枚", self.md)
        self.assertIn("BK-TR-F1-001", self.md)
        self.assertIn("TR-F1-001", self.md)


class TestCli(unittest.TestCase):
    """CLI 行为：成功路径不报错；预检失败 **非零退出且不产出文件**。"""

    def run_cli(self, *args, cwd=None) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(SCRIPT), *args],
            cwd=str(cwd or PROJECT_ROOT),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )

    def test_check_only_passes_on_real_ledger(self):
        result = self.run_cli("--check-only")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("预检通过", result.stdout)

    def test_bad_ledger_exits_nonzero_and_writes_nothing(self):
        ledger = copy.deepcopy(real_ledger())
        for beacon in ledger["beacons"]:
            if beacon["beaconId"] == "BK-M-2-001":
                beacon["major"] = 1  # major≠floor
        with tempfile.TemporaryDirectory() as tmp:
            bad_ledger = os.path.join(tmp, "bad_ledger.json")
            with open(bad_ledger, "w", encoding="utf-8") as handle:
                json.dump(ledger, handle, ensure_ascii=False)
            out_md = os.path.join(tmp, "out.md")
            out_plan = os.path.join(tmp, "out.json")
            result = self.run_cli(
                "--ledger", bad_ledger, "--out", out_md, "--plan", out_plan
            )
            self.assertEqual(result.returncode, 2, result.stdout)
            self.assertIn("预检未通过", result.stderr)
            self.assertFalse(os.path.exists(out_md))
            self.assertFalse(os.path.exists(out_plan))


if __name__ == "__main__":
    unittest.main()
