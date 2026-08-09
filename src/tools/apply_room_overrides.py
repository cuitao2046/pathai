#!/usr/bin/env python3
"""将浏览器导出的 room_overrides.json 应用到 GeoJSON。

背景
----
渲染图（render_interactive.py）新增「区域标注」功能：在图上框选区域，
把落在该区域内（质心）的房间标记为指定类型（如电梯前厅/楼梯前厅），
并可「保存 GeoJSON」直接写回，或「导出标注(JSON)」下载覆盖项。

但重新运行 parse_cad_pdf.py 会从 CAD 重新生成 GeoJSON，覆盖掉手工标注。
本脚本用于把导出的覆盖项重新应用回去，使标注在重解析后可再生。

用法
----
    python apply_room_overrides.py [overrides.json] [geojson]

默认路径
--------
    overrides.json = result/room_overrides.json
    geojson        = result/school_building_01_map_v9.geojson

覆盖项格式（由渲染图「导出标注」生成）
--------------------------------------
    {"overrides": [{"floor": 1, "roomId": "F1-RM-0043", "type": "elevator_lobby"}, ...]}
"""
import json
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent.parent
DEF_OVR = BASE / "result" / "room_overrides.json"
DEF_GEO = BASE / "result" / "school_building_01_map_v9.geojson"


def main():
    ovr_path = Path(sys.argv[1]) if len(sys.argv) > 1 else DEF_OVR
    geo_path = Path(sys.argv[2]) if len(sys.argv) > 2 else DEF_GEO

    ovr = json.loads(ovr_path.read_text(encoding="utf-8"))
    items = ovr.get("overrides", [])
    if not items:
        print(f"覆盖项为空（{ovr_path}），未做改动。")
        return

    geo = json.loads(geo_path.read_text(encoding="utf-8"))
    n = 0
    for it in items:
        fk = str(it.get("floor"))
        room_id = it.get("roomId")
        new_type = it.get("type")
        if not room_id or not new_type:
            continue
        fd = geo["floors"].get(fk)
        if not fd:
            print(f"  跳过：楼层 {fk} 不存在（{room_id}）")
            continue
        # geometry.rooms：按 properties.roomId 匹配，同步 roomType 与 type
        g_rooms = (fd.get("geometry") or {}).get("rooms", [])
        target_gid = None
        for r in g_rooms:
            props = r.get("properties") or {}
            if props.get("roomId") == room_id:
                props["roomType"] = new_type
                if "type" in props:
                    props["type"] = new_type
                target_gid = r.get("id")
                n += 1
                break
        # semantic.rooms：按 geometryId 匹配并同步 type
        if target_gid:
            for s in (fd.get("semantic") or {}).get("rooms", []):
                if s.get("geometryId") == target_gid:
                    s["type"] = new_type
                    break
        if target_gid is None:
            print(f"  跳过：{fk} 层未找到 roomId={room_id}")

    geo_path.write_text(
        json.dumps(geo, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"已应用 {n} 条房间类型覆盖 -> {geo_path}")


if __name__ == "__main__":
    main()
