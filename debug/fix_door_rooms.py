# -*- coding: utf-8 -*-
"""修复 geometry.doors 的 rooms 字段漂移（需求⑩补充）。

判据（贴墙权威）：
- 对每扇 swing/fire 门，rooms 权威 = 贴墙(<0.6m) 的封闭房间（不含开放空间/楼梯间/管井）
- 漂移 = 标注了但距墙 >=5m 的封闭房间 → 从 rooms 移除
- 悬空引用 = 标注了已删除房间（RM-0007/CR-0045 等不在 geometry.rooms）→ 移除
- 近邻（<5m）保留：可能是合法套间/相邻房间共用墙门，语义误差小
- 缺失（贴墙但未标注）→ 补入 rooms（保证完整性）

注意：geometry.doors.rooms 是信息字段，拓扑 TD.rooms 已按贴墙权威生成（rebuild 时），
本修复保持两者一致方向，不重建拓扑边。
"""
import json
import shutil
from pathlib import Path
from shapely.geometry import shape, Point

BASE = Path(__file__).resolve().parent.parent
GEO = BASE / "result" / "school_building_01_map_v9.geojson"
BACKUP = BASE / "result" / "school_building_01_map_v9_before_door_rooms_fix.geojson"

WALL_TOL = 0.6       # 贴墙判据
KEEP_NEAR = 5.0      # <5m 视为近邻合法，不移除
OPEN = {"corridor", "lobby", "activity", "atrium", "elevator_lobby",
        "stair_lobby", "staircase", "infrastructure", "elevator_hall"}


def main():
    shutil.copy(GEO, BACKUP)
    geo = json.loads(GEO.read_text(encoding="utf-8"))

    stats = {"removed": 0, "dangling": 0, "added": 0}
    for fk, fd in geo["floors"].items():
        room_poly = {}
        room_type = {}
        for r in fd["geometry"].get("rooms", []):
            try:
                room_poly[r["id"]] = shape(r["geometry"])
                room_type[r["id"]] = r.get("properties", {}).get("roomType")
            except Exception:
                pass
        for d in fd["geometry"].get("doors", []):
            pr = d.get("properties", {})
            dt = pr.get("doorType")
            if dt not in ("swing", "fire"):
                continue
            coords = d.get("geometry", {}).get("coordinates")
            if not coords:
                continue
            pt = Point(coords)
            marked = set(pr.get("rooms") or [])

            # 贴墙封闭房间（权威归属）
            wall = {rid for rid, rp in room_poly.items()
                    if rp.boundary.distance(pt) < WALL_TOL and room_type.get(rid) not in OPEN}

            # 1) 悬空引用：标注了已删除房间
            dangling = {r for r in marked if r not in room_poly}
            # 2) 远漂移：标注了但距墙 >=5m 的封闭房间
            far_drift = {r for r in (marked - wall) if r in room_poly
                         and room_poly[r].boundary.distance(pt) >= KEEP_NEAR
                         and room_type.get(r) not in OPEN}

            # 3) 缺失：贴墙但未标注
            miss = {r for r in wall if r not in marked}

            if dangling or far_drift or miss:
                new_rooms = sorted((marked - dangling - far_drift) | miss)
                # 用内容比较（删漂移+补缺失可能长度不变，如 1→1）
                if new_rooms != sorted(marked):
                    stats["removed"] += len(dangling) + len(far_drift)
                    stats["dangling"] += len(dangling)
                    stats["added"] += len(miss)
                    pr["rooms"] = new_rooms
                    print(f"F{fk} {d['id']} ({dt}): 移除漂移={sorted(far_drift)} "
                          f"悬空={sorted(dangling)} 补缺失={sorted(miss)} -> rooms={new_rooms}")

    GEO.write_text(json.dumps(geo, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n=== 完成: 移除漂移/悬空共 {stats['removed']} 项, 悬空 {stats['dangling']} 处, 补缺失 {stats['added']} 项 ===")


if __name__ == "__main__":
    main()
