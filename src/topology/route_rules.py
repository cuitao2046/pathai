# -*- coding: utf-8 -*-
"""
src/route_rules.py — 导航路线生成规则（与 render_interactive.py 前端 Dijkstra 同步）

落实用户给定的三条规则：

规则 1（同层起终点）：同层路线「中间节点」禁止使用楼梯/电梯等非公共空间设施节点。
       起点或终点可以是楼梯/电梯节点，仅中间中转节点受限。

规则 2（跨层电梯）：视障(blind) 跨层导航必须走电梯跨层边，禁用楼梯跨层边；
       普通(normal) 模式跨层仍允许楼梯。

规则 3（房间经门连公共空间）：房间↔房间必须经房间附属门连接公共空间；
       门优先级 普通(swing) > 防火(fire) > 门洞(opening)；
       仅当房间完全无门（卫生间例外）才允许路径穿墙，其他场景禁止穿任何墙。

规则 4（卫生间禁中转）：任何两点之间的导航路径，中间节点不得经过卫生间；
       卫生间只能作为起点或终点（roomType=toilet 显式拦截，见 _dijkstra）。

规则 5（管井门禁路径）：归属全部为 infrastructure 的房间门（纯管井门，如
       风井/水井/排风井的门）不得出现在导航路径中——管井为设备空间，非导航
       目标；共享 TD（同时归属普通房间）不在此列（数据缺陷，另行处理）。

实现要点：
- 门节点(TD)不得作为「两个房间之间的直连通道」(room→door→room)，
  必须经过公共空间 (intersection / facility_entrance / facility)。
- 门类型通过边权惩罚实现优先级（仅在 room↔door 边施加，避免重复惩罚）。
- 穿墙几何校验：用墙体线段按厚度 buffer 后与路径公共空间段求交；
  无门卫生间(endpoint 为 doorless toilet) 例外放行。
"""
import json
import math
from collections import defaultdict

from shapely.geometry import LineString, Point
from shapely.ops import unary_union

# 门类型边权惩罚（米），越小越优先
DOOR_PENALTY = {"swing": 0.0, "fire": 0.5, "opening": 1.0}

# 同层路线允许的「中间节点」类型（不含 facility 楼梯/电梯）
SAME_FLOOR_MID_TYPES = {"intersection", "facility_entrance", "doorway"}
# 跨层路线额外允许 facility 作为中转（电梯/楼梯用于跨层）
CROSS_FLOOR_MID_TYPES = SAME_FLOOR_MID_TYPES | {"facility"}


class RouteGraph:
    """从 GeoJSON 构建带规则的导航图。"""

    def __init__(self, geo: dict):
        self.geo = geo
        self.nodes = {}          # id -> node dict
        self.edges = []          # normalized edges
        self.walls = []          # buffered wall geometries
        self.wall_lines = []     # zero-width wall centerlines (穿墙判定用)
        self.wall_bounds = []    # 墙 bounding box，用于快速预筛
        self.wall_floors = []    # 每根墙线所在楼层（int），穿墙判定须按楼层隔离
        self.rooms_with_doors = set()  # semantic roomId with >=1 affiliated door
        self._build()

    # ------------------------------------------------------------------ #
    # 构建
    # ------------------------------------------------------------------ #
    def _build(self):
        for fk, fd in self.geo.get("floors", {}).items():
            topo = fd.get("topology", {}) or {}
            for n in topo.get("nodes", []):
                self.nodes[n["id"]] = {
                    "id": n["id"],
                    "type": n.get("type"),
                    "floor": int(fk),
                    "facilityType": n.get("facilityType"),
                    "roomType": n.get("roomType"),
                    "roomId": n.get("roomId"),
                    "label": n.get("label"),
                    "doorType": n.get("doorType"),
                    "rooms": n.get("rooms") or [],
                    "blindAccessible": n.get("blindAccessible", True),
                    "coords": n.get("coordinates"),
                }
            for e in topo.get("edges", []):
                self.edges.append(self._norm_edge(e, fk))
            # 墙体（按厚度 buffer 用于穿墙检测）
            for w in (fd.get("geometry", {}) or {}).get("walls", []):
                g = w.get("geometry", {})
                if g.get("type") == "LineString":
                    t = (w.get("properties") or {}).get("thickness") or 0.2
                    ls = LineString(g["coordinates"])
                    if ls.length < 1e-6:
                        continue  # 退化墙体不参与穿墙判定
                    self.walls.append(ls.buffer(t / 2.0 + 0.02))
                    self.wall_lines.append(ls)
                    self.wall_bounds.append(ls.bounds)
                    self.wall_floors.append(int(fk))
        # 跨层边
        for e in self.geo.get("crossFloorEdges", []):
            self.edges.append(self._norm_edge(e, None, cross=True))
        self.wall_union = unary_union(self.walls) if self.walls else None
        # 规则 3：穿墙走廊边集合（两端均为 intersection 且直线段真正穿墙）。
        # 路由时在邻接图中剔除这些边（绕行），仅在无其他连通路径时回退保留，
        # 从而保证「导航路径不穿墙」且尽量保持连通。
        self.wall_crossing_titi = set()
        for e in self.edges:
            a = self.nodes.get(e["from"])
            b = self.nodes.get(e["to"])
            if not a or not b:
                continue
            if a["type"] == "intersection" and b["type"] == "intersection":
                if self._seg_crosses_any_wall(a["coords"], b["coords"], a["floor"]):
                    self.wall_crossing_titi.add(e["id"])
        # 收集有门的房间（语义 roomId）
        for nid, n in self.nodes.items():
            if n["type"] == "doorway":
                for rid in n["rooms"]:
                    self.rooms_with_doors.add(rid)
        # 规则 3：每间房的最高优先级门类型（swing>fire>opening，惩罚越小越优先）。
        # 房间只可使用「优先级不低于自身最佳门」的门（共享门若为更高优先级亦可用）。
        best_door = {}
        for n in self.nodes.values():
            if n["type"] == "doorway":
                for rid in n["rooms"]:
                    t = n["doorType"]
                    if rid not in best_door or \
                       DOOR_PENALTY.get(t, 9) < DOOR_PENALTY.get(best_door[rid], 9):
                        best_door[rid] = t
        for n in self.nodes.values():
            if n["type"] == "room":
                n["best_door_type"] = best_door.get(n["roomId"])
        # 规则 3 例外：无门卫生间（门洞例外）需经穿墙连接到公共空间，
        # 否则在拓扑中孤立、规则无法生效。为其补一条「穿墙」虚拟边到最近公共节点。
        self._add_doorless_toilet_links()
        # 规则 5：归属全为 infrastructure 的门（纯管井门，如风井/水井/排风井的门）
        # 不得出现在导航路径中——管井为设备空间非导航目标，路径不应经过其门。
        # 共享 TD（同时归属普通房间/卫生间等）不在此列，属数据缺陷另行处理。
        room_id_to_type = {}
        for nid, n in self.nodes.items():
            if n["type"] == "room":
                room_id_to_type[n.get("roomId") or nid] = n.get("roomType")
        self.infra_doorway_ids = set()
        for nid, n in self.nodes.items():
            if n["type"] != "doorway":
                continue
            rids = n.get("rooms") or []
            if not rids:
                continue
            if all(room_id_to_type.get(r) == "infrastructure" for r in rids):
                self.infra_doorway_ids.add(nid)

    def _add_doorless_toilet_links(self, radius=20.0):
        import math as _m
        cand_types = {"intersection", "facility_entrance", "doorway", "facility"}
        seq = 0
        for n in self.nodes.values():
            if not self.is_doorless_toilet(n["id"]):
                continue
            cx, cy = n["coords"]
            best = None
            best_d = radius
            for m in self.nodes.values():
                if m["id"] == n["id"] or m["type"] not in cand_types:
                    continue
                if m["floor"] != n["floor"]:
                    continue
                d = _m.hypot(cx - m["coords"][0], cy - m["coords"][1])
                if d < best_d:
                    best_d, best = d, m
            if best is None:
                continue
            seq += 1
            self.edges.append({
                "id": "XR-TW-%04d" % seq,
                "from": n["id"], "to": best["id"],
                "distance": round(best_d, 3),
                "accessibilityLevel": 0,
                "blindAccessible": True,
                "wheelchairAccessible": True,
                "crossFloor": False,
                "type": "toilet_wall_link",
                "doorType": None,
                "floor": n["floor"],
                "through_wall": True,
            })

    @staticmethod
    def _norm_edge(e, fk, cross=False):
        return {
            "id": e.get("id"),
            "from": e.get("from"),
            "to": e.get("to"),
            "distance": float(e.get("distance") or 0),
            "accessibilityLevel": e.get("accessibilityLevel", 0),
            "blindAccessible": e.get("blindAccessible", True),
            "wheelchairAccessible": e.get("wheelchairAccessible", True),
            "crossFloor": bool(cross or e.get("crossFloor")),
            "type": e.get("type"),
            "doorType": e.get("doorType"),
            "floor": (int(fk) if fk is not None else None),
        }

    # ------------------------------------------------------------------ #
    # 规则辅助
    # ------------------------------------------------------------------ #
    def edge_allowed(self, e: dict, mode: str) -> bool:
        """规则 2：模式约束 + 盲模式剔除楼梯跨层边。"""
        if mode == "blind":
            if e["blindAccessible"] is False:
                return False
            if int(e["accessibilityLevel"]) == 999:
                return False
            # 盲模式跨层必须走电梯，禁用楼梯跨层边
            if e["crossFloor"] and e.get("type") == "staircase":
                return False
        elif mode == "wheelchair":
            if e["wheelchairAccessible"] is False:
                return False
            if int(e["accessibilityLevel"]) == 999:
                return False
        return True

    def _seg_crosses_any_wall(self, p1, p2, floor):
        """p1->p2 是否真正穿墙（遍历【同楼层】bbox 内所有墙线段，与 validate_wall_crossing 同源）。

        floor 为 int（节点楼层）：F1/F2 投影坐标重叠，若不过滤楼层会把本层走廊骨架
        误判为穿过另一层楼的墙，必须按楼层隔离。
        """
        minx, miny = min(p1[0], p2[0]), min(p1[1], p2[1])
        maxx, maxy = max(p1[0], p2[0]), max(p1[1], p2[1])
        for wl, wb, wf in zip(self.wall_lines, self.wall_bounds, self.wall_floors):
            if wf != floor:
                continue
            if wb[0] > maxx or wb[2] < minx or wb[1] > maxy or wb[3] < miny:
                continue
            if self._segment_crosses_wall(p1, p2, wl.coords[0], wl.coords[-1]):
                return True
        return False

    def _edge_door_type(self, e: dict):
        """门类型存于门节点(非边)，从 doorway 端点推导。"""
        a = self.nodes.get(e["from"])
        b = self.nodes.get(e["to"])
        if a and a["type"] == "doorway":
            return a.get("doorType")
        if b and b["type"] == "doorway":
            return b.get("doorType")
        return None

    def _edge_weight(self, e: dict) -> float:
        w = e["distance"]
        dt = self._edge_door_type(e)
        if dt:
            a = self.nodes.get(e["from"])
            b = self.nodes.get(e["to"])
            # 仅在 room↔door 边施加门类型惩罚（每扇门只惩罚一次）
            if (a and a["type"] == "room") or (b and b["type"] == "room"):
                w += DOOR_PENALTY.get(dt, 0.0)
        return w

    def _mid_types(self, start_id, end_id):
        """规则 1：同层禁 facility 中转；跨层允许 facility 中转。"""
        s = self.nodes.get(start_id)
        e = self.nodes.get(end_id)
        same_floor = (s and e and s["floor"] == e["floor"])
        return SAME_FLOOR_MID_TYPES if same_floor else CROSS_FLOOR_MID_TYPES

    def _door_pass_through_blocked(self, u_id, prev_id, nb_id):
        """规则 3：禁止 room→door→room 穿透（门必须连公共空间）。"""
        u = self.nodes.get(u_id)
        if not u or u["type"] != "doorway":
            return False
        if u_id == prev_id or u_id == nb_id:
            return False
        prev = self.nodes.get(prev_id)
        nb = self.nodes.get(nb_id)
        if prev and nb and prev["type"] == "room" and nb["type"] == "room":
            return True
        return False

    # ------------------------------------------------------------------ #
    # 受限 Dijkstra
    # ------------------------------------------------------------------ #
    def _build_adjacency(self, mode, door_filter, allow_wall=False):
        adj = defaultdict(list)
        for e in self.edges:
            if not self.edge_allowed(e, mode):
                continue
            # 规则 3：剔除穿墙走廊边（TI↔TI），避免导航路径穿墙；
            # 仅在回退（无替代连通路径的桥边）时 allow_wall=True 重新纳入。
            if not allow_wall and e["id"] in self.wall_crossing_titi:
                continue
            a, b = e["from"], e["to"]
            if a not in self.nodes or b not in self.nodes:
                continue
            # 规则 5：剔除连接「纯管井门」的边（导航路径不经过风井/水井门）
            if a in self.infra_doorway_ids or b in self.infra_doorway_ids:
                continue
            # 规则 3：房间只可使用优先级不低于自身最佳门的门
            if door_filter:
                ta, tb = self.nodes[a]["type"], self.nodes[b]["type"]
                e_dt = self._edge_door_type(e)
                if ta == "room" and tb == "doorway":
                    best = self.nodes[a].get("best_door_type")
                    if best is not None and \
                       DOOR_PENALTY.get(e_dt, 9) > DOOR_PENALTY.get(best, 9):
                        continue
                if tb == "room" and ta == "doorway":
                    best = self.nodes[b].get("best_door_type")
                    if best is not None and \
                       DOOR_PENALTY.get(e_dt, 9) > DOOR_PENALTY.get(best, 9):
                        continue
            w = self._edge_weight(e)
            adj[a].append((b, w, e["id"]))
            adj[b].append((a, w, e["id"]))
        return adj

    def shortest_path(self, start_id, end_id, mode="normal"):
        if start_id not in self.nodes or end_id not in self.nodes:
            return None
        # 优先按规则 3（仅用最佳门 + 不穿墙走廊边）寻路；
        # 若不可达（最佳门未接入路网），回退允许所有门；
        # 仍不可达（被剔除的穿墙走廊边是桥边）则回退纳入穿墙边以保持连通。
        adj = self._build_adjacency(mode, door_filter=True)
        sp = self._dijkstra(start_id, end_id, mode, adj)
        if sp is None:
            adj = self._build_adjacency(mode, door_filter=False)
            sp = self._dijkstra(start_id, end_id, mode, adj)
            if sp is not None:
                sp["door_fallback"] = True
        if sp is None:
            adj = self._build_adjacency(mode, door_filter=True, allow_wall=True)
            sp = self._dijkstra(start_id, end_id, mode, adj)
            if sp is not None:
                sp["wall_fallback"] = True
        return sp

    def _dijkstra(self, start_id, end_id, mode, adj):
        mid_types = self._mid_types(start_id, end_id)
        dist = {start_id: 0.0}
        prev = {}
        prev_edge = {}
        visited = set()
        # 简易优先队列（列表 + 排序）
        pq = [(0.0, start_id)]
        while pq:
            pq.sort(key=lambda x: x[0])
            d, u = pq.pop(0)
            if u in visited:
                continue
            visited.add(u)
            if u == end_id:
                break
            # 中间节点类型白名单（起终点豁免）
            if u != start_id and u != end_id:
                # 规则 4：卫生间禁止作为中间节点（只能作起终点）
                if self.nodes[u].get("roomType") == "toilet":
                    continue
                if self.nodes[u]["type"] not in mid_types:
                    continue
            for nb, w, eid in adj[u]:
                if nb in visited:
                    continue
                # 规则 3：门不得作为两房间直连通道
                if self._door_pass_through_blocked(u, prev.get(u, u), nb):
                    continue
                nd = d + w
                if nd < dist.get(nb, float("inf")):
                    dist[nb] = nd
                    prev[nb] = u
                    prev_edge[nb] = eid
                    pq.append((nd, nb))

        if end_id not in dist:
            return None

        # 回溯
        path = []
        edge_ids = []
        at = end_id
        while at:
            path.append(at)
            if at in prev_edge:
                edge_ids.append(prev_edge[at])
            at = prev.get(at)
            if at == start_id:
                path.append(start_id)
                break
        path.reverse()
        edge_ids.reverse()

        # 统计跨层信息
        cross_edges = [eid for eid in edge_ids
                       if self._edge_by_id(eid) and self._edge_by_id(eid)["crossFloor"]]
        used_elevator = any(
            self._edge_by_id(eid) and self._edge_by_id(eid).get("type") == "elevator"
            for eid in cross_edges)
        used_stair = any(
            self._edge_by_id(eid) and self._edge_by_id(eid).get("type") == "staircase"
            for eid in cross_edges)

        return {
            "path": path,
            "edges": edge_ids,
            "distance": round(dist[end_id], 2),
            "cross_floor": bool(cross_edges),
            "used_elevator": used_elevator,
            "used_stair": used_stair,
            "mid_nodes": path[1:-1],
        }

    def _edge_by_id(self, eid):
        for e in self.edges:
            if e["id"] == eid:
                return e
        return None

    # ------------------------------------------------------------------ #
    # 规则 3：穿墙几何校验（无门卫生间例外）
    # ------------------------------------------------------------------ #
    def is_doorless_toilet(self, node_id) -> bool:
        n = self.nodes.get(node_id)
        if not n:
            return False
        if n["roomType"] != "toilet":
            return False
        return (n.get("roomId") or n["id"]) not in self.rooms_with_doors

    def _segment_crosses_wall(self, p1, p2, A, B):
        """路径段 p1->p2 是否真正「穿透」墙体线段 A-B。

        判定：两端点位于墙线两侧(opposite sides)且交点落在线段内。
        共线/同侧(沿墙并行)不算穿墙。
        """
        ax, ay = A[0], A[1]
        bx, by = B[0], B[1]
        px, py = p1[0], p1[1]
        qx, qy = p2[0], p2[1]
        dx, dy = bx - ax, by - ay

        def side(x, y):
            return (bx - ax) * (y - ay) - (by - ay) * (x - ax)

        s1 = side(px, py)
        s2 = side(qx, qy)
        if s1 == 0 and s2 == 0:
            return False  # 共线：沿墙，非穿透
        if s1 * s2 > 0:
            return False  # 同侧：沿墙并行，非穿透
        if abs(dx) < 1e-12 and abs(dy) < 1e-12:
            return False  # 退化墙线
        # 异侧：求交点参数
        ex, ey = qx - px, qy - py
        det = dx * ey - dy * ex
        if abs(det) < 1e-12:
            return False
        u = (ex * (ay - py) - ey * (ax - px)) / det  # 沿墙 A->B 参数
        t = (dy * (px - ax) - dx * (py - ay)) / det  # 沿路径 p1->p2 参数
        return (0.0 - 1e-9) <= t <= (1.0 + 1e-9) and (0.0 - 1e-9) <= u <= (1.0 + 1e-9)

    def validate_wall_crossing(self, path, mode="normal"):
        """对路径的公共空间段做穿墙检测。

        返回 dict: {crossings: [...], allowed_by_toilet_exception: bool, ok: bool}
        - 跳过 room↔door 段（房内心到门属正常，非穿墙）
        - 其余段与墙体 union 求交
        - 起/终点含无门卫生间则整条路径允许穿墙
        """
        crossings = []
        if not path or len(path) < 2 or self.wall_union is None:
            return {"crossings": [], "allowed_by_toilet_exception": False, "ok": True}

        # 无门卫生间例外（只看起/终点房间）
        allow_toilet = (
            self.is_doorless_toilet(path[0]) or self.is_doorless_toilet(path[-1]))

        seg_pts = []
        for i in range(len(path) - 1):
            a = self.nodes[path[i]]
            b = self.nodes[path[i + 1]]
            # 跳过 room↔door 段（房内心到门属正常，非穿墙）
            if {a["type"], b["type"]} == {"room", "doorway"}:
                continue
            # 跳过与门/设施节点相邻的段：门洞、设施入口、设施中心都位于墙体的
            # 合法开口（门口/电梯门/管井门）处，门→走廊段必然贴开口，属合法
            # 穿过自身墙面而非非法穿墙，不计入穿墙违例。仅检查「纯走道段」
            # （两端均为 intersection 的走廊↔走廊段）。
            if a["type"] in ("doorway", "facility", "facility_entrance") or \
               b["type"] in ("doorway", "facility", "facility_entrance"):
                continue
            if a["coords"] is None or b["coords"] is None:
                continue
            # 仅「真正穿墙」(两端在墙线异侧且交点在线段内)才算，沿墙并行排除。
            if self._seg_crosses_any_wall(a["coords"], b["coords"], a["floor"]):
                hit = True
            else:
                hit = False
            if hit:
                crossings.append({
                    "from": path[i], "to": path[i + 1],
                    "from_label": a.get("label"), "to_label": b.get("label"),
                })

        return {
            "crossings": crossings,
            "allowed_by_toilet_exception": allow_toilet,
            "ok": (len(crossings) == 0) or allow_toilet,
        }

    def generate_route(self, start_id, end_id, mode="normal"):
        sp = self.shortest_path(start_id, end_id, mode)
        if sp is None:
            return {"reachable": False}
        wall = self.validate_wall_crossing(sp["path"], mode)
        return {
            "reachable": True,
            "mode": mode,
            "path": sp["path"],
            "edges": sp["edges"],
            "distance": sp["distance"],
            "cross_floor": sp["cross_floor"],
            "used_elevator": sp["used_elevator"],
            "used_stair": sp["used_stair"],
            "mid_nodes": sp["mid_nodes"],
            "wall": wall,
            "valid": wall["ok"],
        }


# --------------------------- 便捷函数 --------------------------- #
def load_geojson(path: str) -> "RouteGraph":
    with open(path, "r", encoding="utf-8") as f:
        return RouteGraph(json.load(f))


if __name__ == "__main__":
    import sys
    g = load_geojson(sys.argv[1] if len(sys.argv) > 1 else
                    "result/school_building_01_map_v9.geojson")
    print(f"nodes={len(g.nodes)} edges={len(g.edges)} walls={len(g.walls)}")
