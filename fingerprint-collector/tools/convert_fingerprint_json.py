# -*- coding: utf-8 -*-
"""将指纹采集小程序导出的 JSON 转换为可直接入库的原始数据。

输入：微信小程序 tools/... 导出的 JSON（type=ble_fingerprint_collection，schemaVersion 1.1.0）
输出：
  - CSV  (默认): 一行 = 一个「指纹样本中的一个信标观测」，可直接 COPY/LOAD DATA 入库
  - SQL  : CREATE TABLE IF NOT EXISTS + INSERT 语句（SQLite / MySQL 通用语法）

行级字段（fingerprint_samples）：
  fp_id, floor, x, y, region_type, capture_index, collected_at,
  uuid, major, minor, rssi, tx_power, accuracy,
  operator, venue_id, dataset,
  zone_id, zone_name, anchor_id, anchor_type, anchor_x, anchor_y, rel_x, rel_y

其中 zone_id/zone_name/anchor_*/rel_* 来自分区采集（schemaVersion>=1.1.0）；
无分区的记录这些列为空。x,y 为绝对坐标；rel_x,rel_y 为相对锚点坐标（可空）。

用法：
  python convert_fingerprint_json.py --in export.json --out samples.csv
  python convert_fingerprint_json.py --in export.json --format sql --out samples.sql
"""
import argparse
import csv
import json
import sys


def load(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def iter_rows(doc):
    """把导出的 records 展开为行级元组。"""
    operator = doc.get("operator", "") or ""
    venue_id = doc.get("venueId", "") or ""
    dataset = doc.get("dataset", "") or ""
    for rec in doc.get("records", []):
        fp_id = rec.get("fpId")
        floor = rec.get("floor")
        coords = rec.get("coordinates") or [None, None]
        x, y = (coords + [None, None])[:2]
        region = rec.get("regionType", "") or ""
        cap = rec.get("captureIndex")
        ts = rec.get("collectedAt", "") or ""
        # 分区字段（schemaVersion>=1.1.0；无分区则空）
        zone_id = rec.get("zoneId")
        zone_name = rec.get("zoneName", "") or ""
        anchor = rec.get("anchor") or {}
        anchor_id = anchor.get("anchorId")
        anchor_type = anchor.get("anchorType")
        anchor_abs = anchor.get("abs") or [None, None]
        anchor_x, anchor_y = (anchor_abs + [None, None])[:2]
        rel = rec.get("relCoordinates") or [None, None]
        rel_x, rel_y = (rel + [None, None])[:2]
        for b in rec.get("beacons", []):
            yield (
                fp_id, floor, x, y, region, cap, ts,
                b.get("uuid"), b.get("major"), b.get("minor"),
                b.get("rssi"), b.get("txPower"), b.get("accuracy"),
                operator, venue_id, dataset,
                zone_id, zone_name, anchor_id, anchor_type, anchor_x, anchor_y, rel_x, rel_y,
            )


COLUMNS = [
    "fp_id", "floor", "x", "y", "region_type", "capture_index", "collected_at",
    "uuid", "major", "minor", "rssi", "tx_power", "accuracy",
    "operator", "venue_id", "dataset",
    "zone_id", "zone_name", "anchor_id", "anchor_type", "anchor_x", "anchor_y", "rel_x", "rel_y",
]


def to_csv(rows, out_path):
    with open(out_path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(COLUMNS)
        for r in rows:
            w.writerow(["" if v is None else v for v in r])


def sql_str(v):
    if v is None:
        return "NULL"
    s = str(v).replace("'", "''")
    return "'" + s + "'"


def to_sql(rows, out_path):
    lines = []
    lines.append("-- 指纹采集导出 -> 数据库原始数据（自动生成）")
    lines.append("CREATE TABLE IF NOT EXISTS fingerprint_samples (")
    lines.append("  id            INTEGER PRIMARY KEY AUTOINCREMENT,")
    lines.append("  fp_id         TEXT    NOT NULL,")
    lines.append("  floor         INTEGER,")
    lines.append("  x             REAL,")
    lines.append("  y             REAL,")
    lines.append("  region_type   TEXT,")
    lines.append("  capture_index INTEGER,")
    lines.append("  collected_at  TEXT,")
    lines.append("  uuid          TEXT,")
    lines.append("  major         INTEGER,")
    lines.append("  minor         INTEGER,")
    lines.append("  rssi          INTEGER,")
    lines.append("  tx_power      INTEGER,")
    lines.append("  accuracy      REAL,")
    lines.append("  operator      TEXT,")
    lines.append("  venue_id      TEXT,")
    lines.append("  dataset       TEXT,")
    lines.append("  zone_id       TEXT,")
    lines.append("  zone_name     TEXT,")
    lines.append("  anchor_id     TEXT,")
    lines.append("  anchor_type   TEXT,")
    lines.append("  anchor_x      REAL,")
    lines.append("  anchor_y      REAL,")
    lines.append("  rel_x         REAL,")
    lines.append("  rel_y         REAL")
    lines.append(");")
    lines.append("")
    for r in rows:
        vals = ", ".join(sql_str(v) for v in r)
        lines.append(f"INSERT INTO fingerprint_samples ({', '.join(COLUMNS)}) VALUES ({vals});")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def main():
    ap = argparse.ArgumentParser(description="指纹采集 JSON -> 数据库原始数据")
    ap.add_argument("--in", dest="inp", required=True, help="小程序导出的 JSON 文件")
    ap.add_argument("--out", default="fingerprint_samples.csv", help="输出文件")
    ap.add_argument("--format", choices=("csv", "sql"), default="csv", help="输出格式")
    args = ap.parse_args()

    doc = load(args.inp)
    if doc.get("type") != "ble_fingerprint_collection":
        print(f"[warn] 未知 type={doc.get('type')}，仍尝试解析", file=sys.stderr)
    rows = list(iter_rows(doc))
    if args.format == "sql":
        to_sql(rows, args.out)
    else:
        to_csv(rows, args.out)

    # 统计
    fps = set()
    for r in rows:
        fps.add(r[0])
    print(f"  解析记录数(record): {len(doc.get('records', []))}")
    print(f"  展开行级观测数(row): {len(rows)}")
    print(f"  覆盖采集点(fp):      {len(fps)}")
    zones_meta = doc.get("zones") or []
    print(f"  导出范围(exportScope): {doc.get('exportScope', 'all')} · 含分区元信息 {len(zones_meta)} 个")
    if zones_meta:
        for z in zones_meta:
            a = z.get("anchor") or {}
            print(f"    - {z.get('zoneId')} {z.get('name')} (F{z.get('floor')}) "
                  f"锚点={a.get('anchorId')} ({a.get('anchorType')}) abs=({a.get('abs')})")
    print(f"  输出 -> {args.out} ({args.format})")


if __name__ == "__main__":
    main()
