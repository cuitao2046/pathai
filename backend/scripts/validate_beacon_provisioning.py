# -*- coding: utf-8 -*-
"""
信标入库离线一致性校验器（docs/27 §7 / P2）。

三方比对：
  ① 真实部署台账（SQLite beacon_deployment_records）
  ② 规划台账 result/ble_deployment.json
  ③ 地图包 backend/maps_data/<site>/<version>.json 的 beacons

规则 P-R1 ~ P-R11；ERROR 非零退出，可作为发布前检查（P3）。

用法（仓库根目录）：
  python backend/scripts/validate_beacon_provisioning.py
  python backend/scripts/validate_beacon_provisioning.py --site school-building-01 \
      --db backend/.local/pathai.db --scan result/scan_snapshot.json

扫描快照（可选，用于 P-R7/P-R8）：
  {"triplets": [{"uuid": "...", "major": 1, "minor": 10101}, ...]}
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[2]
PLANNED_JSON = BASE_DIR / "result" / "ble_deployment.json"
MAPS_DIR = BASE_DIR / "backend" / "maps_data"
DEFAULT_DB = BASE_DIR / "backend" / ".local" / "pathai.db"
DEFAULT_OUT = BASE_DIR / "result" / "beacon_provisioning_report.json"


def derive_device_name(floor_id, minor) -> str:
    return f"F{int(floor_id)}-{int(minor) % 1000:03d}"


def latest_map_path(site: str) -> Path:
    files = sorted(MAPS_DIR.joinpath(site).glob("*.json"))
    if not files:
        raise SystemExit(f"地图包不存在：{MAPS_DIR / site}")
    return files[-1]


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def load_records(db: Path, site: str) -> list[dict]:
    if not db.exists():
        raise SystemExit(f"真实部署台账数据库不存在：{db}（先启动后端并完成入库）")
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT beacon_id, map_version, device_mac, device_name, name_write, "
            "written_json, verify_json, source FROM beacon_deployment_records "
            "WHERE site_id = ? ORDER BY beacon_id",
            (site,),
        ).fetchall()
    finally:
        conn.close()

    records = []
    for row in rows:
        records.append(
            {
                "beaconId": row["beacon_id"],
                "mapVersion": row["map_version"],
                "deviceMac": row["device_mac"],
                "deviceName": row["device_name"],
                "nameWrite": row["name_write"],
                "written": json.loads(row["written_json"]),
                "verify": json.loads(row["verify_json"]),
                "source": row["source"],
            }
        )
    return records


def add(issues: list[dict], rule: str, level: str, beacon_id: str | None, message: str) -> None:
    issues.append(
        {"rule": rule, "level": level, "beaconId": beacon_id, "message": message}
    )


def check_duplicates(issues: list[dict], source: str, items: list[tuple[str, int, int]]) -> None:
    seen: dict[tuple[int, int], str] = {}
    for beacon_id, major, minor in items:
        key = (int(major), int(minor))
        if key in seen:
            add(
                issues,
                "P-R4",
                "ERROR",
                beacon_id,
                f"{source} 内 (major,minor)=({major},{minor}) 重复：{seen[key]} ↔ {beacon_id}",
            )
        else:
            seen[key] = beacon_id


def validate(
    site: str,
    planned_path: Path,
    map_path: Path,
    db_path: Path,
    scan_path: Path | None,
) -> dict:
    planned = load_json(planned_path)["beacons"]
    package = load_json(map_path)
    map_beacons = {b["beaconId"]: b for b in package.get("beacons", [])}
    records = load_records(db_path, site)
    by_id = {r["beaconId"]: r for r in records}
    issues: list[dict] = []

    planned_by_id = {b["beaconId"]: b for b in planned}

    # P-R1 / P-R2 / P-R3 / P-R6 / P-R9 / P-R10 / P-R11
    for beacon in planned:
        beacon_id = beacon["beaconId"]
        record = by_id.get(beacon_id)
        if record is None:
            add(issues, "P-R1", "ERROR", beacon_id, "规划台账有该信标，但真实部署台账缺失（未入库）")
            continue

        if not record["verify"].get("passed", False):
            add(issues, "P-R2", "ERROR", beacon_id, "verify.passed != true（未通过全量读回校验）")

        written = record["written"]
        planned_key = (
            beacon["uuid"].lower(),
            int(beacon["major"]),
            int(beacon["minor"]),
        )
        written_key = (
            str(written.get("uuid", "")).lower(),
            int(written.get("major", -1)),
            int(written.get("minor", -1)),
        )
        if written_key != planned_key:
            add(
                issues,
                "P-R3",
                "ERROR",
                beacon_id,
                f"三元组与规划不一致：写入 {written_key}，规划 {planned_key}",
            )

        if beacon.get("txPower") is not None and abs(
            float(written.get("txPower", 0)) - float(beacon["txPower"])
        ) > 0.01:
            add(
                issues,
                "P-R6",
                "WARN",
                beacon_id,
                f"发射功率与规划不一致：写入 {written.get('txPower')} dBm，规划 {beacon['txPower']} dBm"
                f"（source={record['source']}）",
            )

        if beacon.get("broadcastInterval") is not None and int(
            written.get("interval", -1)
        ) != int(beacon["broadcastInterval"]):
            add(
                issues,
                "P-R9",
                "WARN",
                beacon_id,
                f"广播间隔与规划不一致：写入 {written.get('interval')} ms，规划 {beacon['broadcastInterval']} ms"
                f"（source={record['source']}）",
            )

        if not record.get("deviceMac"):
            add(issues, "P-R10", "WARN", beacon_id, "真实部署台账缺少 deviceMac（审计信息缺失）")

        map_item = map_beacons.get(beacon_id)
        expected_name = (
            map_item.get("deviceName")
            if map_item is not None
            else None
        ) or derive_device_name(beacon.get("floor", 0), beacon.get("minor", 0))
        written_name = written.get("name")
        if written_name != expected_name:
            add(
                issues,
                "P-R11",
                "WARN",
                beacon_id,
                f"写入设备名与 deviceName 不一致：写入 {written_name!r}，期望 {expected_name!r}",
            )

    # P-R4：各来源内三元组重复
    check_duplicates(
        issues,
        "规划台账",
        [(b["beaconId"], b["major"], b["minor"]) for b in planned],
    )
    check_duplicates(
        issues,
        "地图包",
        [(b["beaconId"], b["major"], b["minor"]) for b in map_beacons.values()],
    )
    check_duplicates(
        issues,
        "真实部署台账",
        [
            (
                r["beaconId"],
                r["written"].get("major", -1),
                r["written"].get("minor", -1),
            )
            for r in records
        ],
    )

    # P-R5：规划 major 与地图包不一致
    for beacon_id, beacon in planned_by_id.items():
        map_item = map_beacons.get(beacon_id)
        if map_item is None:
            add(issues, "P-R5", "ERROR", beacon_id, "地图包缺少该信标")
        elif int(beacon["major"]) != int(map_item["major"]):
            add(
                issues,
                "P-R5",
                "ERROR",
                beacon_id,
                f"major 不一致：规划 {beacon['major']}，地图包 {map_item['major']}",
            )

    # P-R7 / P-R8：需要现场扫描快照
    if scan_path is not None and scan_path.exists():
        scan = load_json(scan_path)
        known = {
            (b["uuid"].lower(), int(b["major"]), int(b["minor"]))
            for b in planned
        } | {
            (
                str(r["written"].get("uuid", "")).lower(),
                int(r["written"].get("major", -1)),
                int(r["written"].get("minor", -1)),
            )
            for r in records
        }
        for item in scan.get("triplets", []):
            key = (
                str(item.get("uuid", "")).lower(),
                int(item.get("major", -1)),
                int(item.get("minor", -1)),
            )
            if key not in known:
                add(
                    issues,
                    "P-R7",
                    "WARN",
                    item.get("beaconId"),
                    f"扫描到陌生 iBeacon：{key}",
                )
    else:
        add(
            issues,
            "P-R8",
            "WARN",
            None,
            "未提供 --scan 扫描快照，P-R7/P-R8 跳过（规划内未部署项仅按 P-R1 报缺失记录）",
        )

    errors = sum(1 for i in issues if i["level"] == "ERROR")
    warnings = sum(1 for i in issues if i["level"] == "WARN")
    return {
        "siteId": site,
        "mapVersion": package.get("version") or package.get("mapVersion"),
        "generatedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "sources": {
            "planned": str(planned_path),
            "map": str(map_path),
            "db": str(db_path),
            "scan": str(scan_path) if scan_path else None,
        },
        "summary": {
            "planned": len(planned),
            "recorded": len(records),
            "verified": sum(1 for r in records if r["verify"].get("passed")),
            "errors": errors,
            "warnings": warnings,
        },
        "issues": issues,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="信标入库离线一致性校验（P-R1~P-R11）")
    parser.add_argument("--site", default="school-building-01")
    parser.add_argument("--planned", type=Path, default=PLANNED_JSON)
    parser.add_argument("--map", type=Path, default=None)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--scan", type=Path, default=None)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()

    map_path = args.map or latest_map_path(args.site)
    report = validate(args.site, args.planned, map_path, args.db, args.scan)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    summary = report["summary"]
    print(
        f"[beacon-provisioning] site={report['siteId']} map={report['mapVersion']} "
        f"planned={summary['planned']} recorded={summary['recorded']} "
        f"verified={summary['verified']} ERROR={summary['errors']} WARN={summary['warnings']}"
    )
    for issue in report["issues"]:
        if issue["level"] == "ERROR":
            print(f"  [{issue['rule']}][ERROR] {issue['beaconId'] or '-'}: {issue['message']}")
    if summary["warnings"]:
        print(f"  （{summary['warnings']} 条 WARN 详见 {args.out}）")
    print(f"报告：{args.out}")
    return 1 if summary["errors"] else 0


if __name__ == "__main__":
    sys.exit(main())
