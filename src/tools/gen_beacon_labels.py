# -*- coding: utf-8 -*-
"""
信标物理标签页生成器（A4 可打印 HTML，含 beaconId 二维码）。

数据源：`backend/maps_data/<siteId>/<version>.json` 的 `beacons`
（与后续 `GET /api/v1/sites/{siteId}/beacons` 同源，保证标签与台账一致）。

标签内容（每枚一张）：
  - 短码 `deviceName`（≤7 字符，规则 `F{floor}-{minor 后三位}`，如 `F1-101`）
  - `beaconId`（如 `BK-01-001`）
  - 二维码：内容 = `beaconId` 明文（App 扫码定位台账，见 docs/27 §6.11）
  - 楼层 + 位置简述

预检（任一不过 → 不产出文件、exit 1）：
  - `beaconId` 匹配 `^[A-Za-z0-9-]{1,20}$` 且全表唯一
  - 全表 UUID 统一为系统预定义常量；`(major, minor)` 唯一
  - `deviceName` 全表唯一且长度 ≤7

用法（仓库根目录）：
  python src/tools/gen_beacon_labels.py
  python src/tools/gen_beacon_labels.py --site school-building-01 --version 2026-10-29.1
  python src/tools/gen_beacon_labels.py --out result/beacon_labels_2026-10-29.1.html
"""
from __future__ import annotations

import argparse
import html
import json
import re
import sys
from pathlib import Path

import segno

BASE_DIR = Path(__file__).resolve().parents[2]
MAP_DIR = BASE_DIR / "backend" / "maps_data"
RESULT_DIR = BASE_DIR / "result"

UUID_CONST = "B9407F30-F5F8-466E-AFF9-25556B57FE6D"
BEACON_ID_RE = re.compile(r"^[A-Za-z0-9-]{1,20}$")
MAX_NAME_LEN = 7
COLS, ROWS = 4, 8
PER_PAGE = COLS * ROWS
LOCATION_MAX_CHARS = 26


def _fail(msg: str) -> None:
    print(f"[ERROR] {msg}", file=sys.stderr)
    raise SystemExit(1)


def load_site(site: str, version: str) -> tuple[str, dict]:
    site_dir = MAP_DIR / site
    if not site_dir.is_dir():
        _fail(f"场所地图目录不存在：{site_dir}")
    if version == "latest":
        candidates = []
        for file in sorted(site_dir.glob("*.json")):
            data = json.loads(file.read_text(encoding="utf-8"))
            candidates.append(((data.get("publishedAt", ""), data.get("mapVersion", file.stem)), data))
        if not candidates:
            _fail(f"场所无已发布地图包：{site_dir}")
        return max(candidates, key=lambda item: item[0])[1]
    file = site_dir / f"{version}.json"
    if not file.is_file():
        _fail(f"地图包不存在：{file}")
    return json.loads(file.read_text(encoding="utf-8"))


def device_name(beacon: dict) -> str:
    return f"F{beacon['floorId']}-{int(beacon['minor']) % 1000:03d}"


def precheck(beacons: list[dict]) -> None:
    if not beacons:
        _fail("地图包 beacons 为空")
    ids, triplets, names = set(), set(), set()
    for beacon in beacons:
        beacon_id = beacon.get("beaconId")
        if not isinstance(beacon_id, str) or not BEACON_ID_RE.match(beacon_id):
            _fail(f"beaconId 非法：{beacon_id!r}")
        if beacon_id in ids:
            _fail(f"beaconId 重复：{beacon_id}")
        ids.add(beacon_id)
        for key in ("floorId", "major", "minor"):
            if beacon.get(key) in (None, ""):
                _fail(f"{beacon_id} 缺少字段 {key}")
        if beacon.get("uuid") != UUID_CONST:
            _fail(f"{beacon_id} UUID 与系统预定义值不一致：{beacon.get('uuid')}")
        triplet = (int(beacon["major"]), int(beacon["minor"]))
        if triplet in triplets:
            _fail(f"(major, minor) 重复：{triplet}（{beacon_id}）")
        triplets.add(triplet)
        name = device_name(beacon)
        if len(name) > MAX_NAME_LEN:
            _fail(f"{beacon_id} 短码超长（{len(name)} > {MAX_NAME_LEN}）：{name}")
        if name in names:
            _fail(f"短码重复：{name}（{beacon_id}）")
        names.add(name)


def render_label(beacon: dict) -> str:
    beacon_id = beacon["beaconId"]
    name = device_name(beacon)
    location = f"{beacon['floorId']}F · {beacon.get('locationDesc', '')}".strip()
    if len(location) > LOCATION_MAX_CHARS:
        location = location[: LOCATION_MAX_CHARS - 1] + "…"
    qr = segno.make_qr(beacon_id, error="m").svg_inline(scale=1, border=0, dark="#000", light="#fff")
    return (
        '<div class="label">'
        '<div class="info">'
        f'<div class="code">{html.escape(name)}</div>'
        f'<div class="bid">{html.escape(beacon_id)}</div>'
        f'<div class="meta">{html.escape(location)}</div>'
        "</div>"
        f'<div class="qr">{qr}</div>'
        "</div>"
    )


def build_html(pkg: dict, beacons: list[dict]) -> str:
    beacons = sorted(beacons, key=lambda b: b["beaconId"])
    pages = []
    for start in range(0, len(beacons), PER_PAGE):
        chunk = beacons[start : start + PER_PAGE]
        labels = "".join(render_label(b) for b in chunk)
        pages.append(
            f'<section class="page"><div class="grid">{labels}</div>'
            f'<div class="footer">{html.escape(pkg["siteId"])} · {html.escape(pkg["mapVersion"])}'
            f" · {start + 1}-{start + len(chunk)} / {len(beacons)}</div></section>"
        )
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>信标标签 · {html.escape(pkg["siteId"])} · {html.escape(pkg["mapVersion"])}</title>
<style>
@page {{ size: A4; margin: 8mm; }}
* {{ box-sizing: border-box; }}
body {{ margin: 0; font-family: "Microsoft YaHei", "PingFang SC", sans-serif; color: #000; }}
.page {{ width: 194mm; height: 281mm; display: flex; flex-direction: column; page-break-after: always; }}
.page:last-of-type {{ page-break-after: auto; }}
.grid {{ flex: 1; display: grid; grid-template-columns: repeat({COLS}, 1fr); grid-auto-rows: 33mm; gap: 1.5mm; }}
.label {{ display: flex; align-items: center; gap: 1.5mm; padding: 1.5mm 2mm;
          border: 0.2mm dashed #999; overflow: hidden; }}
.info {{ flex: 1; min-width: 0; }}
.code {{ font-size: 15pt; font-weight: 700; letter-spacing: 0.3pt; line-height: 1.1; }}
.bid {{ font-size: 8pt; font-weight: 600; line-height: 1.3; }}
.meta {{ font-size: 6pt; line-height: 1.25; color: #333; }}
.qr {{ width: 24mm; height: 24mm; flex: 0 0 24mm; }}
.qr svg {{ width: 100%; height: 100%; display: block; }}
.footer {{ height: 6mm; font-size: 7pt; color: #666; text-align: right; padding-top: 1mm; }}
</style>
</head>
<body>
{"".join(pages)}
</body>
</html>
"""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="生成信标物理标签页（A4，含 beaconId 二维码）")
    parser.add_argument("--site", default="school-building-01", help="场所 ID（默认 school-building-01）")
    parser.add_argument("--version", default="latest", help="地图版本（默认 latest）")
    parser.add_argument("--out", default="", help="输出 HTML 路径（默认 result/beacon_labels_<version>.html）")
    args = parser.parse_args(argv)

    pkg = load_site(args.site, args.version)
    beacons = pkg.get("beacons") or []
    precheck(beacons)
    html_text = build_html(pkg, beacons)

    out = Path(args.out) if args.out else RESULT_DIR / f"beacon_labels_{pkg['mapVersion']}.html"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html_text, encoding="utf-8")

    pages = (len(beacons) + PER_PAGE - 1) // PER_PAGE
    print(f"信标标签：{len(beacons)} 张 / {pages} 页（{args.site} · {pkg['mapVersion']}）")
    print(f"输出：{out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
