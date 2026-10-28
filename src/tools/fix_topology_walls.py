# -*- coding: utf-8 -*-
"""修复拓扑边穿墙（spec v0.2.6 / 决策 8）。

规则（公共空间边不得越过墙体中心线）：
  1. TI↔TI：直接删除穿墙边（骨架手绘遗漏，不能作为走廊连接）。
  2. 门（TD）↔TI：
     - 公共门（其归属房间含走廊/大厅/门厅等公共空间）→ 重挂到"同侧可见"的
       最近 TI，受距离上限约束（默认 6 m）；无候选则不连（保留房间门边）。
     - 房间间门（归属房间全为封闭空间）→ 删除其 TI 边，仅保留 TR↔TD。
  3. 设施（TF）↔TD：优先挂到"设施所在房间"的门（语义挂接）；无所在房间
     （如电梯井无面域）时退回"可见最近门"，上限见 --tf-cap-elevator/--tf-cap-stair。
  4. 出入口（TEN）↔TD/TI：重挂到可见的最近门/走廊节点，上限默认 12 m；
     室外出入口（无可见候选）不建边。

"可见"判定：连线与墙线真正相交，且交点远离起点（门口/设施本体墙放行，
默认半径 1.0 m / 2.5 m），同时远离终点（0.2 m）。

用法：
  python -m src.tools.fix_topology_walls --dry-run
  python -m src.tools.fix_topology_walls              # 原地改写（先备份 .bak）
"""
from __future__ import annotations

import argparse
import json
import math
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_GEO = ROOT / "result" / "school_building_01_map_v9.geojson"

PUBLIC_ROOM_TYPES = {"corridor", "lobby", "activity", "atrium", "elevator_lobby",
                     "stair_lobby", "entrance", "accessible_entrance"}
WALK_SPEED = 0.8
CELL = 3.0

#: 拓扑整改默认参数（CLI 与全链路回归测试共用同一来源）
DEFAULT_REPAIR_OPTS = {
    "td_cap": 6.0,
    "ten_cap": 12.0,
    "tf_cap_elevator": 3.0,
    "tf_cap_stair": 8.0,
    "door_radius": 1.5,
    "door_adjacency": 0.8,
    "ten_radius": 1.5,
    "connect_cap": 20.0,
}


# ---------------------------------------------------------------- geometry
def _cross_dists(p1, p2, a, b):
    """返回 (沿线段起点距离, 沿线段终点距离)；不真正相交返回 None。"""
    dx, dy = b[0] - a[0], b[1] - a[1]
    ex, ey = p2[0] - p1[0], p2[1] - p1[1]
    s1 = dx * (p1[1] - a[1]) - dy * (p1[0] - a[0])
    s2 = dx * (p2[1] - a[1]) - dy * (p2[0] - a[0])
    if (s1 == 0 and s2 == 0) or s1 * s2 > 0:
        return None
    det = dx * ey - dy * ex
    if abs(det) < 1e-12:
        return None
    t = (dy * (p1[0] - a[0]) - dx * (p1[1] - a[1])) / det
    u = (ex * (a[1] - p1[1]) - ey * (a[0] - p1[0])) / det
    if not (-1e-9 <= u <= 1 + 1e-9) or not (0 <= t <= 1):
        return None
    L = math.hypot(ex, ey)
    return t * L, (1 - t) * L


def _pt_seg_dist(p, a, b):
    dx, dy = b[0] - a[0], b[1] - a[1]
    L2 = dx * dx + dy * dy
    if L2 < 1e-12:
        return math.hypot(p[0] - a[0], p[1] - a[1])
    t = ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / L2
    t = max(0.0, min(1.0, t))
    return math.hypot(p[0] - (a[0] + t * dx), p[1] - (a[1] + t * dy))


class WallIndex:
    """按网格索引墙体线段，支持快速"可见性"判定。"""

    def __init__(self, floor_geo):
        self.segs = []
        for w in (floor_geo.get("geometry") or {}).get("walls") or []:
            cs = (w.get("geometry") or {}).get("coordinates") or []
            for i in range(len(cs) - 1):
                self.segs.append((cs[i], cs[i + 1]))
        self.grid = {}
        for idx, (a, b) in enumerate(self.segs):
            for cx in range(int(math.floor(min(a[0], b[0]) / CELL)),
                            int(math.floor(max(a[0], b[0]) / CELL)) + 1):
                for cy in range(int(math.floor(min(a[1], b[1]) / CELL)),
                                int(math.floor(max(a[1], b[1]) / CELL)) + 1):
                    self.grid.setdefault((cx, cy), []).append(idx)

    def _near(self, p1, p2):
        seen = set()
        for cx in range(int(math.floor(min(p1[0], p2[0]) / CELL)),
                        int(math.floor(max(p1[0], p2[0]) / CELL)) + 1):
            for cy in range(int(math.floor(min(p1[1], p2[1]) / CELL)),
                            int(math.floor(max(p1[1], p2[1]) / CELL)) + 1):
                for idx in self.grid.get((cx, cy), ()):
                    if idx not in seen:
                        seen.add(idx)
                        yield self.segs[idx]

    def blocked(self, p1, p2, start_radius, end_tol=0.2):
        """起点→终点连线是否被墙体阻挡（起点本体墙放行）。"""
        for a, b in self._near(p1, p2):
            c = _cross_dists(p1, p2, a, b)
            if not c:
                continue
            if c[0] <= start_radius or c[1] <= end_tol:
                continue
            if _pt_seg_dist(p1, a, b) <= start_radius:
                continue
            return True
        return False


def _pt_in_ring(pt, ring):
    x, y = pt
    inside = False
    n = len(ring)
    for i in range(n):
        x1, y1 = ring[i][0], ring[i][1]
        x2, y2 = ring[(i + 1) % n][0], ring[(i + 1) % n][1]
        if (y1 > y) != (y2 > y):
            xi = x1 + (y - y1) * (x2 - x1) / (y2 - y1)
            if x < xi:
                inside = not inside
    return inside


def _room_of_point(pt, rooms_geo):
    for rid, feat in rooms_geo.items():
        rings = (feat.get("geometry") or {}).get("coordinates") or []
        if rings and _pt_in_ring(pt, rings[0]):
            return rid
    return None


# ---------------------------------------------------------------- repair
class FloorRepair:
    def __init__(self, fk, floor_geo, opts):
        self.fk = fk
        self.geo = floor_geo
        self.opts = opts
        self.walls = WallIndex(floor_geo)
        self.nodes = {n["id"]: n for n in floor_geo["topology"]["nodes"]}
        self.sem = {r["id"]: r for r in (floor_geo.get("semantic") or {}).get("rooms") or []}
        self.rooms_geo = {f["id"]: f for f in (floor_geo.get("geometry") or {}).get("rooms") or []}
        self.edges = list(floor_geo["topology"]["edges"])
        max_seq = 0
        for e in self.edges:
            tail = str(e.get("id") or "").rsplit("-", 1)[-1]
            if tail.isdigit():
                max_seq = max(max_seq, int(tail))
        self.seq = max_seq
        self._grid = None  # 按需构建的可通行区栅格（A* 兜底用）
        self.tww_seq = 0
        self.stats = {"titi_removed": [], "door_ti_replaced": 0, "door_ti_dropped": [],
                      "interior_door_ti_removed": 0, "tf_linked": 0, "tf_dropped": [],
                      "ten_linked": 0, "ten_dropped": [],
                      "connect_added": [], "connect_failed": []}

    # -- helpers
    def _dist(self, p, q):
        return math.hypot(p[0] - q[0], p[1] - q[1])

    def _new_edge(self, frm, to, dist, attrs):
        self.seq += 1
        return {
            "id": f"{self.fk}-TE-{self.seq:04d}",
            "from": frm, "to": to,
            "distance": round(dist, 2),
            "estimatedTime": round(dist / WALK_SPEED, 1),
            **attrs,
        }

    @staticmethod
    def _default_attrs(r_level=0.5):
        return {"accessibilityLevel": 0, "riskLevel": r_level, "walkable": True,
                "wheelchairAccessible": True, "blindAccessible": True}

    def _is_public_door(self, td):
        """门是否面向公共空间：归属房间含公共空间，或门点贴着公共空间面域（≤0.8m）。"""
        if any(self.sem.get(rid, {}).get("type") in PUBLIC_ROOM_TYPES
               for rid in (td.get("rooms") or [])):
            return True
        p = td["coordinates"]
        for rid, feat in self.rooms_geo.items():
            if self.sem.get(rid, {}).get("type") not in PUBLIC_ROOM_TYPES:
                continue
            rings = (feat.get("geometry") or {}).get("coordinates") or []
            for ring in rings:
                for i in range(len(ring) - 1):
                    if _pt_seg_dist(p, ring[i], ring[i + 1]) <= self.opts.door_adjacency:
                        return True
        return False

    def _visible(self, p1, p2, start_radius, end_tol=0.2):
        return not self.walls.blocked(p1, p2, start_radius, end_tol)

    # -- steps
    def fix_titi(self):
        kept = []
        for e in self.edges:
            a, b = self.nodes.get(e["from"]), self.nodes.get(e["to"])
            if (a and b and a["type"] == "intersection" and b["type"] == "intersection"
                    and not self._visible(a["coordinates"], b["coordinates"], 0.1, 0.1)):
                self.stats["titi_removed"].append(e["id"])
                continue
            kept.append(e)
        self.edges = kept

    def fix_facilities(self):
        tfs = [n for n in self.nodes.values() if n["type"] == "facility"]
        tds = [n for n in self.nodes.values() if n["type"] == "doorway"]
        kept = [e for e in self.edges
                if not ({self.nodes.get(e["from"], {}).get("type"),
                         self.nodes.get(e["to"], {}).get("type")} == {"facility", "doorway"})]
        self.edges = kept
        for tf in tfs:
            rid = _room_of_point(tf["coordinates"], self.rooms_geo)
            targets = []
            if rid:
                targets = [td for td in tds if rid in (td.get("rooms") or [])]
                anchor_radius = 2.5
            if not targets:
                cap = (self.opts.tf_cap_elevator if tf.get("facilityType") == "elevator"
                       else self.opts.tf_cap_stair)
                anchor_radius = 2.5 if not rid else 1.0
                cand = sorted(((self._dist(tf["coordinates"], td["coordinates"]), td)
                               for td in tds), key=lambda x: x[0])
                for d, td in cand:
                    if tf.get("facilityType") == "elevator" and td.get("doorType") == "fire":
                        continue
                    if d > cap:
                        break
                    if self._visible(tf["coordinates"], td["coordinates"], anchor_radius):
                        targets = [td]
                        break
            if not targets:
                self.stats["tf_dropped"].append(tf["id"])
                continue
            is_stair = tf.get("facilityType") == "staircase"
            attrs = {"accessibilityLevel": 999 if is_stair else 0,
                     "riskLevel": 10 if is_stair else 1.0,
                     "walkable": True,
                     "wheelchairAccessible": not is_stair,
                     "blindAccessible": not is_stair}
            for td in targets:
                d = self._dist(tf["coordinates"], td["coordinates"])
                self.edges.append(self._new_edge(tf["id"], td["id"], d, attrs))
            self.stats["tf_linked"] += 1

    def fix_doors(self):
        tds = [n for n in self.nodes.values() if n["type"] == "doorway"]
        tis = [n for n in self.nodes.values() if n["type"] == "intersection"]
        kept = []
        for e in self.edges:
            a, b = self.nodes.get(e["from"]), self.nodes.get(e["to"])
            types = {a.get("type"), b.get("type")} if a and b else set()
            if types == {"doorway", "intersection"}:
                self.stats["door_ti_replaced"] += 1
                continue
            kept.append(e)
        self.edges = kept

        for td in tds:
            if not self._is_public_door(td):
                continue  # 房间间门：不连走廊
            cand = sorted(((self._dist(td["coordinates"], ti["coordinates"]), ti)
                           for ti in tis), key=lambda x: x[0])
            picked = None
            for d, ti in cand:
                if d > self.opts.td_cap:
                    break
                if self._visible(td["coordinates"], ti["coordinates"], self.opts.door_radius):
                    picked = (d, ti)
                    break
            if picked is None:
                self.stats["door_ti_dropped"].append(td["id"])
                continue
            d, ti = picked
            self.edges.append(self._new_edge(td["id"], ti["id"], d, self._default_attrs()))

    def fix_entrances(self):
        tens = [n for n in self.nodes.values() if n["type"] == "facility_entrance"]
        tds = [n for n in self.nodes.values() if n["type"] == "doorway"]
        tis = [n for n in self.nodes.values() if n["type"] == "intersection"]
        kept = []
        for e in self.edges:
            a, b = self.nodes.get(e["from"]), self.nodes.get(e["to"])
            types = {a.get("type"), b.get("type")} if a and b else set()
            if "facility_entrance" in types and (types & {"doorway", "intersection"}):
                continue
            kept.append(e)
        self.edges = kept
        for ten in tens:
            cands = []
            for td in tds:
                d = self._dist(ten["coordinates"], td["coordinates"])
                if d <= self.opts.ten_cap and self._visible(
                        ten["coordinates"], td["coordinates"], self.opts.ten_radius):
                    cands.append((d, "TD", td["id"]))
            for ti in tis:
                d = self._dist(ten["coordinates"], ti["coordinates"])
                if d <= self.opts.ten_cap and self._visible(
                        ten["coordinates"], ti["coordinates"], self.opts.ten_radius):
                    cands.append((d, "TI", ti["id"]))
            if not cands:
                self.stats["ten_dropped"].append(ten["id"])
                continue
            cands.sort(key=lambda x: (x[0], 0 if x[1] == "TD" else 1))
            d, _, nid = cands[0]
            self.edges.append(self._new_edge(ten["id"], nid, d,
                                             self._default_attrs(r_level=5)))
            self.stats["ten_linked"] += 1

    def _components(self):
        adj = {}
        for e in self.edges:
            adj.setdefault(e["from"], set()).add(e["to"])
            adj.setdefault(e["to"], set()).add(e["from"])
        seen, comps = set(), []
        for nid in adj:
            if nid in seen:
                continue
            stack, comp = [nid], []
            seen.add(nid)
            while stack:
                cur = stack.pop()
                comp.append(cur)
                for nb in adj.get(cur, ()):
                    if nb not in seen:
                        seen.add(nb)
                        stack.append(nb)
            comps.append(set(comp))
        return comps

    def _astar_chain(self, start_node, ti):
        """可通行区 A* 折线：返回门→TI 的途径点链（不含首尾），失败返回 None。"""
        from src.tools.fix_crossing_edges import collect_walkable
        from src.tools.fix_wall_crossing import WalkGrid, string_pull

        if self._grid is None:
            W, _, _ = collect_walkable(self.geo)
            if W is not None and hasattr(W, "buffer"):
                # 可通行面域是按空间分片的，门洞处存在缝隙；小幅膨胀以打通门洞，
                # 使 A* 能跨空间绕行（最终链段仍逐段做穿墙校验）。
                W = W.buffer(0.35)
            self._grid = WalkGrid(W, 0.5) if W is not None else False
        if not self._grid:
            return None
        path = self._grid.astar(start_node["coordinates"], ti["coordinates"])
        if not path or len(path) < 2:
            return None
        pts = [list(start_node["coordinates"])] + [list(p) for p in path] + \
              [list(ti["coordinates"])]
        merged = [pts[0]]
        for p in pts[1:]:
            if self._dist(p, merged[-1]) > 1e-3:
                merged.append(p)
        simplified = string_pull(merged, lambda p, q: self.walls.blocked(p, q, 0.05, 0.05))
        if len(simplified) < 2:
            return None
        # 首段（门→首个途径点）按门口半径复核；其余段须严格不穿墙
        if self.walls.blocked(simplified[0], simplified[1],
                              self.opts.door_radius, 0.2):
            return None
        for i in range(1, len(simplified) - 1):
            if self.walls.blocked(simplified[i], simplified[i + 1], 0.05, 0.05):
                return None
        return simplified[1:-1]

    def ensure_connectivity(self):
        """兜底：把断连的门/房间簇接回主走廊图。

        优先直连"可见 TI"；直线不可见时用可通行区 A* 折线绕行（TWW 途径点），
        两种方式都不产生穿墙边。
        """
        tis = [n for n in self.nodes.values() if n["type"] == "intersection"]
        if not tis:
            return
        main = None
        for comp in self._components():
            if any(self.nodes.get(nid, {}).get("type") == "intersection" for nid in comp):
                if main is None or len(comp) > len(main):
                    main = comp
        if not main:
            return
        for _ in range(4):
            comps = [c for c in self._components() if not c <= main]
            if not comps:
                break
            for comp in comps:
                cand_nodes = [self.nodes[nid] for nid in comp
                              if self.nodes.get(nid, {}).get("type") in
                              ("doorway", "facility", "intersection")]
                cand_nodes.sort(key=lambda n: 0 if n["type"] == "doorway" else
                                1 if n["type"] == "intersection" else 2)
                attached = False
                for cn in cand_nodes:
                    radius = {"doorway": self.opts.door_radius,
                              "facility_entrance": self.opts.ten_radius,
                              "facility": 2.5, "intersection": 0.1}[cn["type"]]
                    # 目标 TI 必须不在当前分量（否则是自连通）
                    targets = [ti for ti in tis
                               if ti["id"] not in comp and ti["id"] in main]
                    targets.sort(key=lambda t: self._dist(cn["coordinates"],
                                                          t["coordinates"]))
                    direct = None
                    for ti in targets:
                        d = self._dist(cn["coordinates"], ti["coordinates"])
                        if d <= self.opts.connect_cap and \
                           self._visible(cn["coordinates"], ti["coordinates"], radius):
                            direct = (d, ti)
                            break
                    if direct:
                        d, ti = direct
                        self.edges.append(self._new_edge(cn["id"], ti["id"], d,
                                                         self._default_attrs()))
                        self.stats["connect_added"].append((cn["id"], ti["id"], round(d, 2)))
                        main |= comp
                        attached = True
                        break
                    # A* 绕行
                    for ti in targets[:6]:
                        pts = self._astar_chain(cn, ti)
                        if not pts:
                            continue
                        prev, prev_p = cn["id"], list(cn["coordinates"])
                        for k, wp in enumerate(pts):
                            self.tww_seq += 1
                            nid = f"{self.fk}-TWW-{self.tww_seq:04d}"
                            self.nodes[nid] = {
                                "id": nid, "type": "intersection", "floor": int(self.fk),
                                "coordinates": [round(wp[0], 3), round(wp[1], 3)],
                                "public": True, "accessible": True, "riskLevel": 1,
                            }
                            d = self._dist(prev_p, wp)
                            self.edges.append(self._new_edge(prev, nid, d,
                                                             self._default_attrs()))
                            prev, prev_p = nid, wp
                        d = self._dist(prev_p, ti["coordinates"])
                        self.edges.append(self._new_edge(prev, ti["id"], d,
                                                         self._default_attrs()))
                        self.stats["connect_added"].append(
                            (cn["id"], ti["id"], f"A* {len(pts)} 途径点"))
                        main |= comp
                        attached = True
                        break
                    if attached:
                        break
                if not attached:
                    self.stats["connect_failed"].append(sorted(comp)[:6])
                    main |= comp  # 避免重复报告

    def run(self):
        self.fix_titi()
        self.fix_facilities()
        self.fix_doors()
        self.fix_entrances()
        self.ensure_connectivity()
        # 去重（同端点只保留最短一条）
        dedup = {}
        for e in self.edges:
            key = tuple(sorted((e["from"], e["to"])))
            if key not in dedup or e["distance"] < dedup[key]["distance"]:
                dedup[key] = e
        self.edges = list(dedup.values())
        self.geo["topology"]["edges"] = self.edges
        return self.stats


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--geo", default=str(DEFAULT_GEO))
    ap.add_argument("--out", default=None, help="输出路径（默认原地改写并备份 .bak）")
    ap.add_argument("--dry-run", action="store_true", help="只统计不写文件")
    ap.add_argument("--td-cap", type=float, default=DEFAULT_REPAIR_OPTS["td_cap"],
                    help="公共门→TI 距离上限（m）")
    ap.add_argument("--ten-cap", type=float, default=DEFAULT_REPAIR_OPTS["ten_cap"],
                    help="出入口→门/走廊上限（m）")
    ap.add_argument("--tf-cap-elevator", type=float, default=DEFAULT_REPAIR_OPTS["tf_cap_elevator"])
    ap.add_argument("--tf-cap-stair", type=float, default=DEFAULT_REPAIR_OPTS["tf_cap_stair"])
    ap.add_argument("--door-radius", type=float, default=DEFAULT_REPAIR_OPTS["door_radius"],
                    help="门口本体墙放行半径（m，覆盖门框/双线墙/门凹槽）")
    ap.add_argument("--door-adjacency", type=float, default=DEFAULT_REPAIR_OPTS["door_adjacency"],
                    help="门点距公共空间面域边界的判定半径（m）")
    ap.add_argument("--ten-radius", type=float, default=DEFAULT_REPAIR_OPTS["ten_radius"])
    ap.add_argument("--connect-cap", type=float, default=DEFAULT_REPAIR_OPTS["connect_cap"],
                    help="断连兜底挂接搜索半径（m）")
    args = ap.parse_args()

    geo = json.loads(Path(args.geo).read_text(encoding="utf-8"))
    total = {"titi": 0, "door": 0, "interior": 0, "tf": 0, "ten": 0,
             "dropped_door": [], "dropped_tf": [], "dropped_ten": []}
    for fk in sorted(geo["floors"]):
        rep = FloorRepair(fk, geo["floors"][fk], args)
        st = rep.run()
        total["titi"] += len(st["titi_removed"])
        total["door"] += st["door_ti_replaced"]
        total["tf"] += st["tf_linked"]
        total["ten"] += st["ten_linked"]
        total["dropped_door"] += st["door_ti_dropped"]
        total["dropped_tf"] += st["tf_dropped"]
        total["dropped_ten"] += st["ten_dropped"]
        print(f"[F{fk}] 删除跨墙 TI-TI {len(st['titi_removed'])} 条 {st['titi_removed']}；"
              f"重挂门→TI {st['door_ti_replaced']} 组；"
              f"设施重挂 {st['tf_linked']}；出入口重挂 {st['ten_linked']}")
        if st["door_ti_dropped"]:
            print(f"      公共门无 ≤{args.td_cap}m 可见 TI（不连走廊）：{st['door_ti_dropped']}")
        if st["tf_dropped"]:
            print(f"      设施无合法门（不建边）：{st['tf_dropped']}")
        if st["ten_dropped"]:
            print(f"      出入口无 ≤{args.ten_cap}m 可见目标（不建边）：{st['ten_dropped']}")
        if st["connect_added"]:
            print(f"      断连兜底挂接 {len(st['connect_added'])} 条：{st['connect_added']}")
        if st["connect_failed"]:
            print(f"      仍无法挂接的分量：{st['connect_failed']}")

    print(
        f"合计：删除跨界 TI-TI {total['titi']}；门→TI 重挂 {total['door']}；"
        f"设施重挂 {total['tf']}；出入口重挂 {total['ten']}；"
        f"丢弃 门{len(total['dropped_door'])}/设施{len(total['dropped_tf'])}/"
        f"出入口{len(total['dropped_ten'])}"
    )

    if args.dry_run:
        print("（dry-run，未写文件）")
        return
    out = Path(args.out) if args.out else Path(args.geo)
    if out == Path(args.geo):
        bak = out.with_suffix(out.suffix + ".bak")
        if not bak.exists():
            shutil.copy2(out, bak)
            print(f"已备份 {bak}")
    out.write_text(json.dumps(geo, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"已写入 {out}")


if __name__ == "__main__":
    main()
