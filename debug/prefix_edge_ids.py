# -*- coding: utf-8 -*-
"""边 id 全局唯一化：把 rebuild_td_1to1 重写的裸 E 编号边加楼层前缀。

F1 的 E000001..E000214 -> F1-E000001..F1-E000214
F2 的 E000001..E000154 -> F2-E000001..F2-E000154
F1-TE-xxxx/F2-TE-xxxx（pipeline 原生）与 FX-XE-xxxx（跨层）不受影响。
"""
import json
import shutil
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
GEO = BASE / "result" / "school_building_01_map_v9.geojson"
BACKUP = BASE / "result" / "school_building_01_map_v9_before_edge_prefix.geojson"


def main():
    shutil.copy(GEO, BACKUP)
    geo = json.loads(GEO.read_text(encoding="utf-8"))

    renamed = 0
    for fk, fd in geo["floors"].items():
        for e in fd["topology"]["edges"]:
            eid = e["id"]
            if not eid.startswith("F"):  # 裸编号（E000xxx）
                e["id"] = f"F{fk}-{eid}"
                renamed += 1

    GEO.write_text(json.dumps(geo, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"边 id 加楼层前缀完成: {renamed} 条")
    # 复核
    from collections import Counter
    ids = Counter()
    for fk, fd in geo["floors"].items():
        for e in fd["topology"]["edges"]:
            ids[e["id"]] += 1
    for e in geo.get("crossFloorEdges", []):
        ids[e["id"]] += 1
    dup = {k: v for k, v in ids.items() if v > 1}
    print(f"全部边+跨层 id 唯一性: {'✅ 唯一' if not dup else '❌ 重复: ' + str(list(dup.items())[:5])}")


if __name__ == "__main__":
    main()
