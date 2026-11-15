# -*- coding: utf-8 -*-
"""生成危险区标注网页所需的静态地图数据（tools/hazard-editor/data/map-data.js）。

页面要求「双击 index.html 即可用、不依赖构建和本地服务器」，因此把地图包里的
几何/拓扑裁剪、取整（厘米）后内联成一个普通 script 文件，浏览器用 file:// 也能加载。

用法（在仓库根目录执行）：
    python tools/hazard-editor/build_data.py
    python tools/hazard-editor/build_data.py --map backend/maps_data/school-building-01/2026-11-08.2.json
    python tools/hazard-editor/build_data.py --seed-out backend/hazards_data/school-building-01.json

重新生成后刷新页面即可；页面内的编辑草稿保存在 localStorage，不会被覆盖。
"""
from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SITE = "school-building-01"
DEFAULT_OUT = ROOT / "tools" / "hazard-editor" / "data" / "map-data.js"
DEFAULT_SEED = ROOT / "backend" / "hazards_data" / f"{DEFAULT_SITE}.json"


def _cm(v: float) -> int:
    """坐标统一转厘米整数：体积约为原始浮点的 1/3，且 1cm 精度足够标注。"""
    return int(round(float(v) * 100))


def _flat(coords: list[list[float]]) -> list[int]:
    out: list[int] = []
    for p in coords:
        out.append(_cm(p[0]))
        out.append(_cm(p[1]))
    return out


def _version_key(name: str) -> tuple:
    """2026-11-08.2 这类版本号的自然排序键。"""
    return tuple((0, int(p)) if p.isdigit() else (1, p) for p in re.split(r"(\d+)", name) if p)


def latest_map(site_id: str) -> Path:
    d = ROOT / "backend" / "maps_data" / site_id
    files = sorted((p for p in d.glob("*.json")), key=lambda p: _version_key(p.stem))
    if not files:
        raise SystemExit(f"找不到地图包：{d}")
    return files[-1]


def _floor_bounds(floors: dict) -> dict[str, list[int]]:
    """按楼层几何外接框计算视图边界（厘米）。"""
    bounds: dict[str, list[int]] = {}
    for fid, f in floors.items():
        xs: list[float] = []
        ys: list[float] = []
        geo = f.get("geometry") or {}
        for key in ("walls", "rooms", "stairs", "elevators", "columns"):
            for feat in geo.get(key) or []:
                g = feat.get("geometry") or {}
                for pt in _iter_points(g.get("coordinates")):
                    xs.append(pt[0])
                    ys.append(pt[1])
        if not xs:
            xs, ys = [0.0], [0.0]
        bounds[str(fid)] = [_cm(min(xs)), _cm(min(ys)), _cm(max(xs)), _cm(max(ys))]
    return bounds


def _iter_points(coords):
    if not coords:
        return
    if isinstance(coords[0], (int, float)):
        yield coords
        return
    for item in coords:
        yield from _iter_points(item)


def _extract_floor(fid: str, f: dict) -> dict:
    geo = f.get("geometry") or {}
    walls = []
    for feat in geo.get("walls") or []:
        c = (feat.get("geometry") or {}).get("coordinates") or []
        if len(c) < 2:
            continue
        props = feat.get("properties") or {}
        t = props.get("thickness")
        walls.append([_cm(c[0][0]), _cm(c[0][1]), _cm(c[-1][0]), _cm(c[-1][1]),
                      max(6, _cm(t if isinstance(t, (int, float)) else 0.2))])

    rooms = []
    for feat in geo.get("rooms") or []:
        g = feat.get("geometry") or {}
        if g.get("type") != "Polygon":
            continue
        props = feat.get("properties") or {}
        centroid = props.get("centroid") or []
        poly = _flat(g.get("coordinates", [[]])[0])
        if not centroid and len(poly) >= 2:
            centroid = [poly[0] / 100, poly[1] / 100]
        rooms.append({
            "id": feat.get("id", ""),
            "label": props.get("label") or "",
            "roomType": props.get("roomType") or "",
            "pub": bool(props.get("public")),
            "x": _cm(centroid[0]) if centroid else 0,
            "y": _cm(centroid[1]) if len(centroid) > 1 else 0,
            "poly": poly,
        })

    doors = []
    for feat in geo.get("doors") or []:
        g = feat.get("geometry") or {}
        c = g.get("coordinates") or []
        if g.get("type") == "Point" and len(c) >= 2:
            props = feat.get("properties") or {}
            doors.append([_cm(c[0]), _cm(c[1]), 1 if props.get("isGlass") else 0])

    def _polys(key: str) -> list[dict]:
        out = []
        for feat in geo.get(key) or []:
            g = feat.get("geometry") or {}
            if g.get("type") != "Polygon":
                continue
            props = feat.get("properties") or {}
            centroid = props.get("centroid") or []
            poly = _flat(g.get("coordinates", [[]])[0])
            if not centroid and len(poly) >= 2:
                centroid = [poly[0] / 100, poly[1] / 100]
            out.append({
                "id": feat.get("id", ""),
                "code": props.get("code") or props.get("label") or "",
                "x": _cm(centroid[0]) if centroid else 0,
                "y": _cm(centroid[1]) if len(centroid) > 1 else 0,
                "poly": poly,
            })
        return out

    columns = []
    for feat in geo.get("columns") or []:
        g = feat.get("geometry") or {}
        if g.get("type") == "Polygon":
            columns.append(_flat(g.get("coordinates", [[]])[0]))

    walkable = []
    for feat in ((f.get("walkable_regions") or {}).get("features") or []):
        g = feat.get("geometry") or {}
        if g.get("type") == "Polygon":
            walkable.append(_flat(g.get("coordinates", [[]])[0]))

    risk_nodes = []
    for rn in (f.get("accessibility") or {}).get("riskNodes") or []:
        c = rn.get("coordinates") or []
        if len(c) >= 2:
            risk_nodes.append({
                "id": rn.get("id", ""),
                "type": rn.get("type", ""),
                "label": rn.get("label", ""),
                "x": _cm(c[0]),
                "y": _cm(c[1]),
                "rl": rn.get("riskLevel"),
            })

    node_map = {}
    for n in (f.get("topology") or {}).get("nodes") or []:
        c = n.get("coordinates") or []
        if len(c) >= 2:
            node_map[n.get("id")] = c

    edges = []
    for e in (f.get("topology") or {}).get("edges") or []:
        a = node_map.get(e.get("from"))
        b = node_map.get(e.get("to"))
        if a is None or b is None:
            continue
        edges.append({
            "i": e.get("id", ""),
            "f": e.get("from", ""),
            "t": e.get("to", ""),
            "a": [_cm(a[0]), _cm(a[1])],
            "b": [_cm(b[0]), _cm(b[1])],
            "d": round(float(e.get("distance") or 0), 2),
            "ba": e.get("blindAccessible") is not False,
            "wa": e.get("wheelchairAccessible") is not False,
            "al": int(e.get("accessibilityLevel") or 0),
            "wl": e.get("walkable") is not False,
        })

    return {
        "displayName": f.get("displayName") or f"{fid}F",
        "level": f.get("level", fid),
        "walls": walls,
        "rooms": rooms,
        "doors": doors,
        "stairs": _polys("stairs"),
        "elevators": _polys("elevators"),
        "columns": columns,
        "walkable": walkable,
        "riskNodes": risk_nodes,
        "edges": edges,
    }


def build_map_data(map_path: Path, hazards_path: Path | None) -> dict:
    pkg = json.loads(map_path.read_text(encoding="utf-8"))
    site_id = pkg.get("siteId") or pkg.get("venueId") or DEFAULT_SITE
    map_version = pkg.get("mapVersion") or pkg.get("version") or map_path.stem
    floors_raw = pkg.get("floors") or {}
    floors = {}
    for fid, f in floors_raw.items():
        key = str(fid)
        floors[key] = _extract_floor(key, f)
    bounds = _floor_bounds(floors_raw)
    for key, box in bounds.items():
        if key in floors:
            floors[key]["bounds"] = box

    node_points = {}
    for fid, f in floors_raw.items():
        for n in (f.get("topology") or {}).get("nodes") or []:
            c = n.get("coordinates") or []
            if len(c) >= 2:
                node_points[n.get("id")] = c

    cross = []
    for e in pkg.get("crossFloorEdges") or []:
        a = node_points.get(e.get("from"))
        b = node_points.get(e.get("to"))
        if a is None or b is None:
            continue
        cross.append({
            "i": e.get("id", ""),
            "code": e.get("code") or e.get("label") or "",
            "f": e.get("from", ""),
            "t": e.get("to", ""),
            "ff": str(e.get("fromFloor") or ""),
            "tf": str(e.get("toFloor") or ""),
            "ty": e.get("type", ""),
            "d": round(float(e.get("distance") or 0), 2),
            "ba": e.get("blindAccessible") is not False,
            "wa": e.get("wheelchairAccessible") is not False,
            "a": [_cm(a[0]), _cm(a[1])],
            "b": [_cm(b[0]), _cm(b[1])],
        })

    initial = None
    if hazards_path is not None and hazards_path.exists():
        layer = json.loads(hazards_path.read_text(encoding="utf-8"))
        initial = layer.get("hazards") or []
        print(f"  [info] 初始危险层 {hazards_path.name}: {len(initial)} 条")

    return {
        "siteId": site_id,
        "venueName": pkg.get("venueName") or "",
        "mapVersion": map_version,
        "publishedAt": pkg.get("publishedAt") or "",
        "sourceFile": str(map_path.relative_to(ROOT)).replace("\\", "/"),
        "generatedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "floors": floors,
        "crossFloorEdges": cross,
        "initialHazards": initial or [],
        "initialHazardMeta": {
            "file": str(hazards_path.relative_to(ROOT)).replace("\\", "/") if hazards_path else None,
            "count": len(initial or []),
        },
    }


def seed_hazards(map_path: Path) -> dict:
    """把地图包里已有的 blindAccessible=false 边导入成危险层基线（结构风险镜像）。

    目的：让危险层从第一版起就覆盖「视障不可通行」的楼梯接入边，后续新增运营危险
    直接在网页上叠加即可；这些条目的 source=map_package，便于与运营标注区分。
    """
    pkg = json.loads(map_path.read_text(encoding="utf-8"))
    site_id = pkg.get("siteId") or pkg.get("venueId") or DEFAULT_SITE
    map_version = pkg.get("mapVersion") or pkg.get("version") or map_path.stem
    now = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    day = now[:10].replace("-", "")
    hazards = []
    seq = 0

    def _add(floor_id: str, edge_id: str, htype: str, note: str) -> None:
        nonlocal seq
        seq += 1
        hazards.append({
            "hazardId": f"HZ-{day}-{seq:04d}",
            "siteId": site_id,
            "floorId": floor_id,
            "targetType": "edge",
            "edgeIds": [edge_id],
            "type": htype,
            "level": "block",
            "appliesTo": ["blind"],
            "reason": "楼梯口，视障不可通行（地图包基线）",
            "spokenText": "前方为楼梯，已为您绕开。",
            "blindAccessible": False,
            "activeFrom": None,
            "activeUntil": None,
            "status": "active",
            "source": "map_package",
            "reportedBy": None,
            "note": note,
            "createdAt": now,
            "updatedAt": now,
            "revision": 1,
        })

    for fid, f in (pkg.get("floors") or {}).items():
        for e in (f.get("topology") or {}).get("edges") or []:
            if e.get("blindAccessible") is False:
                _add(str(fid), e.get("id", ""), "stairs",
                     "由地图包 blindAccessible=false 导入；结构风险仍以地图包为准")

    for e in pkg.get("crossFloorEdges") or []:
        if e.get("blindAccessible") is False:
            htype = "stairs" if e.get("type") == "staircase" else "obstacle"
            _add(str(e.get("fromFloor") or ""), e.get("id", ""), htype,
                 "由地图包跨层边 blindAccessible=false 导入；结构风险仍以地图包为准")

    return {
        "schemaVersion": "1.0.0",
        "hazardVersion": f"{now}/{len(hazards)}",
        "siteId": site_id,
        "mapVersion": map_version,
        "generatedAt": now,
        "updatedAt": now,
        "generator": "tools/hazard-editor/build_data.py (seed)",
        "notes": "初始危险层：镜像地图包 blindAccessible=false 的楼梯接入边/跨层楼梯边。",
        "hazards": hazards,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description="生成危险区标注页面的静态数据")
    ap.add_argument("--map", default=None, help="地图包 JSON；缺省取该 site 最新发布版本")
    ap.add_argument("--site", default=DEFAULT_SITE, help="场所 ID（用于定位默认地图包）")
    ap.add_argument("--out", default=str(DEFAULT_OUT), help="输出 map-data.js")
    ap.add_argument("--initial-hazards", default=str(DEFAULT_SEED),
                    help="作为页面初始数据的危险层 JSON（不存在则忽略）")
    ap.add_argument("--seed-out", default=None,
                    help="按地图包 blindAccessible=false 生成初始危险层到该路径（已存在则跳过）")
    ap.add_argument("--force-seed", action="store_true", help="覆盖 --seed-out 已有文件")
    args = ap.parse_args()

    map_path = Path(args.map) if args.map else latest_map(args.site)
    if not map_path.is_absolute():
        map_path = ROOT / map_path
    if not map_path.exists():
        raise SystemExit(f"地图包不存在：{map_path}")

    if args.seed_out:
        seed_path = Path(args.seed_out)
        if not seed_path.is_absolute():
            seed_path = ROOT / seed_path
        if seed_path.exists() and not args.force_seed:
            print(f"  [skip] 危险层已存在：{seed_path.relative_to(ROOT)}")
        else:
            seed_path.parent.mkdir(parents=True, exist_ok=True)
            seed = seed_hazards(map_path)
            seed_path.write_text(json.dumps(seed, ensure_ascii=False, indent=2) + "\n",
                                 encoding="utf-8")
            print(f"  [ok] 生成初始危险层 {seed_path.relative_to(ROOT)}: {len(seed['hazards'])} 条")

    hazards_path = Path(args.initial_hazards) if args.initial_hazards else None
    if hazards_path is not None and not hazards_path.is_absolute():
        hazards_path = ROOT / hazards_path

    data = build_map_data(map_path, hazards_path)
    out_path = Path(args.out)
    if not out_path.is_absolute():
        out_path = ROOT / out_path
    out_path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    out_path.write_text(
        "/* 由 tools/hazard-editor/build_data.py 生成，请勿手工编辑。 */\n"
        "window.HAZARD_MAP_DATA = " + payload + ";\n",
        encoding="utf-8",
    )
    size_kb = out_path.stat().st_size / 1024
    edges = sum(len(f["edges"]) for f in data["floors"].values())
    print(f"  [ok] {out_path.relative_to(ROOT)}: {size_kb:.0f} KB, "
          f"{len(data['floors'])} 层, {edges} 条边, 跨层 {len(data['crossFloorEdges'])} 条")


if __name__ == "__main__":
    main()
