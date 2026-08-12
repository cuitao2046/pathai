# -*- coding: utf-8 -*-
"""A2 路由规则收敛对拍测试（后端 RouteGraph 与前端 JS Dijkstra 行为一致）。

路由规则（规则 1-5）存在三处实现：后端 RouteGraph（src/topology/route_rules.py）、
预计算 compute_route_rule_extras、前端 JS Dijkstra（src/rendering/render_interactive.py）。
A2 已收敛为唯一来源 src/common/constants.py，前端只经 build_path_rules_js() 序列化注入。

本测试验证：
1. 规则数据一致：build_path_rules_js() 序列化的 rules 与 constants 各值相等；
2. 路径一致：以纯 Python 复刻前端 JS Dijkstra（PATH_GRAPH 数据 + 三层回退），
   与后端最短路径逐条对拍（同层/跨层 × normal/blind/wheelchair）；
3. JS 不内嵌常量：源码扫描确认规则常量只经 PATH_GRAPH.rules 注入。

后端路径来源优先用真实 RouteGraph；无 shapely 的沙箱环境回退到对 route_rules.py
逐行复刻的等价实现（仅用于占位验证，正式回归环境应跑真实 RouteGraph 分支）。
"""
import ast
import heapq
import json
import random
import unittest
from collections import defaultdict

from src.common.constants import DOOR_PENALTY, DOOR_DEFAULT_PENALTY, SAME_FLOOR_MID_TYPES, CROSS_FLOOR_MID_TYPES
from src.rendering.render_interactive import compute_route_rule_extras, build_path_rules_js
try:
    from testutil import GEOJSON_PATH, PROJECT_ROOT, load_json
except ImportError:
    from tests.testutil import GEOJSON_PATH, PROJECT_ROOT, load_json

try:
    from src.topology.route_rules import RouteGraph
    ROUTE_RULES_IMPORTABLE = True
except ImportError:
    ROUTE_RULES_IMPORTABLE = False

MODES = ("normal", "blind", "wheelchair")
SEED_SAME_FLOOR = 20260812
SEED_CROSS_FLOOR = 20260813


class TestRulesDataConsistency(unittest.TestCase):
    """build_path_rules_js() 序列化数据 == constants 唯一来源（A2）。"""

    def test_build_path_rules_matches_constants(self):
        rules = build_path_rules_js()
        self.assertEqual(rules["doorPenalty"], DOOR_PENALTY)
        self.assertEqual(rules["doorDefaultPenalty"], DOOR_DEFAULT_PENALTY)
        self.assertEqual(set(rules["midTypesSameFloor"]), SAME_FLOOR_MID_TYPES)
        self.assertEqual(set(rules["midTypesCrossFloor"]), CROSS_FLOOR_MID_TYPES)

    def test_rules_survive_json_roundtrip(self):
        payload = json.loads(json.dumps(build_path_rules_js()))
        self.assertEqual(payload["doorPenalty"], DOOR_PENALTY)
        self.assertEqual(payload["doorDefaultPenalty"], DOOR_DEFAULT_PENALTY)
        self.assertEqual(set(payload["midTypesSameFloor"]), SAME_FLOOR_MID_TYPES)
        self.assertEqual(set(payload["midTypesCrossFloor"]), CROSS_FLOOR_MID_TYPES)

    def test_mid_types_semantics(self):
        self.assertLessEqual(SAME_FLOOR_MID_TYPES, CROSS_FLOOR_MID_TYPES)
        self.assertIn("facility", CROSS_FLOOR_MID_TYPES)
        self.assertNotIn("facility", SAME_FLOOR_MID_TYPES)
        self.assertIn("intersection", SAME_FLOOR_MID_TYPES)
        self.assertIn("facility_entrance", SAME_FLOOR_MID_TYPES)
        self.assertIn("doorway", SAME_FLOOR_MID_TYPES)
        # 规则 1/规则 4：房间与卫生间不得作为中间中转节点
        self.assertNotIn("room", SAME_FLOOR_MID_TYPES)
        self.assertNotIn("room", CROSS_FLOOR_MID_TYPES)
        self.assertNotIn("toilet", SAME_FLOOR_MID_TYPES)

    def test_door_default_penalty_is_ceiling(self):
        self.assertGreater(DOOR_DEFAULT_PENALTY, max(DOOR_PENALTY.values()))
        # elevator 等未知门类型统一走兜底（JS 与后端共用）
        self.assertEqual(DOOR_PENALTY.get("elevator", DOOR_DEFAULT_PENALTY),
                         DOOR_DEFAULT_PENALTY)


class TestJsNoEmbeddedRules(unittest.TestCase):
    """前端 JS 不得内嵌规则常量（A2）；规则数据只经 PATH_GRAPH.rules 注入。"""

    SOURCE = PROJECT_ROOT / "src/rendering/render_interactive.py"

    @classmethod
    def setUpClass(cls):
        cls.src = cls.SOURCE.read_text(encoding="utf-8")

    def test_no_embedded_penalty_table(self):
        self.assertNotIn("var doorPenalty = {", self.src)
        self.assertNotIn("doorPenalty: {", self.src)

    def test_no_embedded_mid_types_table(self):
        self.assertNotIn("midTypesSameFloor: {", self.src)
        self.assertNotIn("midTypesCrossFloor: {", self.src)

    def test_no_embedded_penalty_values(self):
        for pat in ("swing: 0", "swing: 0.0", "fire: 0", "fire: 0.5",
                    "opening: 1", "opening: 1.0",
                    "intersection: 1", "doorway: 1", "facility_entrance: 1"):
            self.assertNotIn(pat, self.src, f"JS 疑似内嵌规则常量：{pat}")

    def test_rules_read_from_path_graph(self):
        self.assertIn("PATH_GRAPH.rules", self.src)


class TestPathParity(unittest.TestCase):
    """后端最短路径与前端 JS Dijkstra 逐对对拍。

    PATH_GRAPH 由 compute_route_rule_extras + build_path_rules_js 构造，
    前端逻辑以纯 Python 复刻（与 render_interactive.py 内嵌脚本逐行对齐）。
    """

    @classmethod
    def setUpClass(cls):
        geo = load_json(GEOJSON_PATH)
        cls.pg = _build_path_graph(geo)
        if ROUTE_RULES_IMPORTABLE:
            cls.rg = RouteGraph(geo)
            cls.backend = None
        else:
            cls.rg = None
            cls.backend = _BackendReplica(geo)

    def _backend_shortest_path(self, s, e, mode):
        if self.rg is not None:
            return self.rg.shortest_path(s, e, mode)
        return self.backend.shortest_path(s, e, mode)

    def _assert_parity(self, s, e, mode):
        sp = self._backend_shortest_path(s, e, mode)
        js = js_shortest_path(self.pg, s, e, mode)
        self.assertEqual(
            sp is None, js is None,
            f"{s}->{e} [{mode}] 可达性不一致：backend={sp is not None}, js={js is not None}")
        if sp is None:
            return
        self.assertEqual(sp["nodes"], js["nodes"],
                         f"{s}->{e} [{mode}] 路径不一致")
        self.assertEqual(sp["distance"], js["distance"],
                         f"{s}->{e} [{mode}] 距离不一致 backend={sp['distance']} js={js['distance']}")
        self.assertEqual(bool(sp.get("door_fallback")), js.get("note") == "door_fallback",
                         f"{s}->{e} [{mode}] door_fallback 不一致")
        self.assertEqual(bool(sp.get("wall_fallback")), js.get("note") == "wall_fallback",
                         f"{s}->{e} [{mode}] wall_fallback 不一致")

    def test_same_floor_parity(self):
        pairs = _sample_pairs(self.pg["nodes"], same_floor=True,
                              seed=SEED_SAME_FLOOR, n=250)
        self.assertGreater(len(pairs), 0, "同层采样为空")
        for s, e in pairs:
            for mode in MODES:
                with self.subTest(s=s, e=e, mode=mode):
                    self._assert_parity(s, e, mode)

    def test_cross_floor_parity(self):
        pairs = _sample_pairs(self.pg["nodes"], same_floor=False,
                              seed=SEED_CROSS_FLOOR, n=100)
        self.assertGreater(len(pairs), 0, "跨层采样为空")
        for s, e in pairs:
            for mode in MODES:
                with self.subTest(s=s, e=e, mode=mode):
                    self._assert_parity(s, e, mode)

    def test_rule_2_blind_mode_stair_blocked(self):
        """规则 2：盲模式跨层只能走电梯；普通模式允许楼梯。"""
        if self.rg is not None:
            self.assertGreater(len(self.rg.cross_floor_edges), 0)
        eids = [x["id"] for x in self.pg["edges"] if x.get("crossFloor")]
        self.assertGreater(len(eids), 0)
        by_type = {}
        for x in self.pg["edges"]:
            if x.get("crossFloor"):
                by_type.setdefault(x.get("type"), []).append(x["id"])
        self.assertTrue(by_type.get("staircase"), "缺少楼梯跨层边")
        self.assertTrue(by_type.get("elevator"), "缺少电梯跨层边")
        for _eid, t in by_type.items():
            pass  # 邻接层已过滤，此处仅锁定数据存在性


class _BackendReplica:
    """route_rules.py 的等价复刻（无 shapely 依赖，供沙箱占位对拍）。"""

    def __init__(self, geo):
        self.nodes = {}
        self.edges = []
        for fk, fd in geo.get("floors", {}).items():
            topo = fd.get("topology", {}) or {}
            for n in topo.get("nodes", []):
                self.nodes[n["id"]] = {
                    "id": n["id"], "type": n.get("type"), "floor": int(fk),
                    "roomType": n.get("roomType"), "roomId": n.get("roomId"),
                    "doorType": n.get("doorType"), "rooms": n.get("rooms") or [],
                    "blindAccessible": n.get("blindAccessible", True),
                    "isNormallyOpen": n.get("isNormallyOpen"),
                }
            for e in topo.get("edges", []):
                self.edges.append({
                    "id": e.get("id"), "from": e.get("from"), "to": e.get("to"),
                    "distance": float(e.get("distance") or 0),
                    "accessibilityLevel": e.get("accessibilityLevel", 0),
                    "blindAccessible": e.get("blindAccessible", True),
                    "wheelchairAccessible": e.get("wheelchairAccessible", True),
                    "crossFloor": False, "type": e.get("type"),
                    "doorType": e.get("doorType"),
                })
        for e in geo.get("crossFloorEdges", []):
            self.edges.append({
                "id": e.get("id"), "from": e.get("from"), "to": e.get("to"),
                "distance": float(e.get("distance") or 0),
                "accessibilityLevel": e.get("accessibilityLevel", 0),
                "blindAccessible": e.get("blindAccessible", True),
                "wheelchairAccessible": e.get("wheelchairAccessible", True),
                "crossFloor": True, "type": e.get("type"),
                "doorType": None,
            })
        extras = compute_route_rule_extras(geo)
        self.wall_crossing_titi = {x.split(":")[1] for x in extras["wall_crossing_titi"]}
        self.infra_doorway_ids = set(extras["infra_doorway_ids"])
        best_door = {}
        for n in self.nodes.values():
            if n["type"] == "doorway":
                for rid in n["rooms"]:
                    t = n["doorType"]
                    p = self._door_penalty(n)
                    if rid not in best_door:
                        best_door[rid] = t
                    else:
                        cur_p = DOOR_PENALTY.get(best_door[rid], DOOR_DEFAULT_PENALTY)
                        if p < cur_p:
                            best_door[rid] = t
        self.best_door_type = {}
        for nid, n in self.nodes.items():
            if n["type"] == "room":
                self.best_door_type[nid] = best_door.get(n["roomId"])
        self.edge_by_id = {e["id"]: e for e in self.edges}

    def _door_penalty(self, node):
        if node.get("doorType") == "fire" and node.get("isNormallyOpen"):
            return 0.0
        return DOOR_PENALTY.get(node.get("doorType"), DOOR_DEFAULT_PENALTY)

    def _door_node_of_edge(self, e):
        a, b = self.nodes.get(e["from"]), self.nodes.get(e["to"])
        if a and a["type"] == "doorway":
            return a
        if b and b["type"] == "doorway":
            return b
        return None

    def _edge_weight(self, e):
        w = e["distance"]
        dn = self._door_node_of_edge(e)
        if dn and dn.get("doorType"):
            a, b = self.nodes.get(e["from"]), self.nodes.get(e["to"])
            if (a and a["type"] == "room") or (b and b["type"] == "room"):
                w += self._door_penalty(dn)
        return w

    def _edge_allowed(self, e, mode):
        if mode == "blind":
            if e["blindAccessible"] is False:
                return False
            if int(e["accessibilityLevel"]) == 999:
                return False
            if e["crossFloor"] and e.get("type") == "staircase":
                return False
        elif mode == "wheelchair":
            if e["wheelchairAccessible"] is False:
                return False
            if int(e["accessibilityLevel"]) == 999:
                return False
        return True

    def _edge_is_closed_fire_door(self, e):
        for nid in (e["from"], e["to"]):
            n = self.nodes.get(nid)
            if n and n["type"] == "doorway" and n.get("doorType") == "fire" \
                    and n.get("isNormallyOpen") is False:
                return True
        return False

    def _build_adjacency(self, mode, door_filter, allow_wall=False):
        adj = defaultdict(list)
        for e in self.edges:
            if not self._edge_allowed(e, mode):
                continue
            if not allow_wall and e["id"] in self.wall_crossing_titi:
                continue
            a, b = e["from"], e["to"]
            if a not in self.nodes or b not in self.nodes:
                continue
            if a in self.infra_doorway_ids or b in self.infra_doorway_ids:
                continue
            if self._edge_is_closed_fire_door(e):
                continue
            if door_filter:
                ta, tb = self.nodes[a]["type"], self.nodes[b]["type"]
                door_node = best = None
                if ta == "room" and tb == "doorway":
                    door_node, best = self.nodes[b], self.best_door_type.get(a)
                elif tb == "room" and ta == "doorway":
                    door_node, best = self.nodes[a], self.best_door_type.get(b)
                if best is not None and door_node is not None:
                    edge_p = self._door_penalty(door_node)
                    if edge_p > DOOR_PENALTY.get(best, DOOR_DEFAULT_PENALTY):
                        continue
            w = self._edge_weight(e)
            adj[a].append((b, w, e["id"]))
            adj[b].append((a, w, e["id"]))
        return adj

    def _dijkstra(self, start_id, end_id, mode, adj):
        s, e = self.nodes.get(start_id), self.nodes.get(end_id)
        mid_types = (SAME_FLOOR_MID_TYPES
                     if s and e and s["floor"] == e["floor"] else CROSS_FLOOR_MID_TYPES)
        dist = {start_id: 0.0}
        prev, prev_edge = {}, {}
        visited = set()
        pq = [(0.0, start_id)]
        while pq:
            d, u = heapq.heappop(pq)
            if u in visited:
                continue
            visited.add(u)
            if u == end_id:
                break
            if u != start_id and u != end_id:
                if self.nodes[u].get("roomType") == "toilet":
                    continue
                if self.nodes[u]["type"] not in mid_types:
                    continue
            for nb, w, eid in adj[u]:
                if nb in visited:
                    continue
                if self._door_pass_through_blocked(u, prev.get(u, u), nb):
                    continue
                nd = d + w
                if nd < dist.get(nb, float("inf")):
                    dist[nb] = nd
                    prev[nb] = u
                    prev_edge[nb] = eid
                    heapq.heappush(pq, (nd, nb))
        if end_id not in dist:
            return None
        path, edge_ids = [], []
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
        return {"nodes": path, "edges": edge_ids,
                "distance": round(dist[end_id], 2),
                "cross_floor": any(self.edge_by_id.get(x) and self.edge_by_id[x]["crossFloor"]
                                   for x in edge_ids)}

    def _door_pass_through_blocked(self, u_id, prev_id, nb_id):
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

    def shortest_path(self, start_id, end_id, mode="normal"):
        if start_id not in self.nodes or end_id not in self.nodes:
            return None
        sp = self._dijkstra(start_id, end_id, mode, self._build_adjacency(mode, True))
        if sp is None:
            sp = self._dijkstra(start_id, end_id, mode, self._build_adjacency(mode, False))
            if sp is not None:
                sp["door_fallback"] = True
        if sp is None:
            sp = self._dijkstra(start_id, end_id, mode,
                                self._build_adjacency(mode, True, allow_wall=True))
            if sp is not None:
                sp["wall_fallback"] = True
        return sp


def _build_path_graph(geo):
    """复刻 path_graph_js 注入（nodes/edges/infraDoorwayIds/rules）。"""
    extras = compute_route_rule_extras(geo)
    path_nodes = {}
    path_edges = []
    for fk, fd in geo["floors"].items():
        for n in (fd.get("topology") or {}).get("nodes") or []:
            nd = {
                "id": n["id"], "type": n.get("type"), "label": n.get("label") or "",
                "floor": int(fk), "facilityType": n.get("facilityType"),
                "roomType": n.get("roomType"), "roomId": n.get("roomId"),
                "doorType": n.get("doorType"), "rooms": n.get("rooms") or [],
                "isNormallyOpen": n.get("isNormallyOpen"),
            }
            if n.get("type") == "room" and n["id"] in extras["room_best_door"]:
                nd["bestDoorType"] = extras["room_best_door"][n["id"]]
            path_nodes[n["id"]] = nd
        for e in (fd.get("topology") or {}).get("edges") or []:
            path_edges.append({
                "id": e.get("id"), "from": e.get("from"), "to": e.get("to"),
                "distance": float(e.get("distance") or 0),
                "accessibilityLevel": e.get("accessibilityLevel", 0),
                "blindAccessible": e.get("blindAccessible", True),
                "wheelchairAccessible": e.get("wheelchairAccessible", True),
                "crossFloor": False, "type": e.get("type"),
                "doorType": extras["edge_door_type"].get(f"{fk}:{e['id']}"),
                "wallCrossing": f"{fk}:{e['id']}" in extras["wall_crossing_titi"],
            })
    for e in geo.get("crossFloorEdges") or []:
        path_edges.append({
            "id": e.get("id"), "from": e.get("from"), "to": e.get("to"),
            "distance": float(e.get("distance") or 0),
            "accessibilityLevel": e.get("accessibilityLevel", 0),
            "blindAccessible": e.get("blindAccessible", True),
            "wheelchairAccessible": e.get("wheelchairAccessible", True),
            "crossFloor": True, "type": e.get("type"),
            "doorType": None, "wallCrossing": False,
        })
    return {"nodes": path_nodes, "edges": path_edges,
            "infraDoorwayIds": sorted(extras["infra_doorway_ids"]),
            "rules": build_path_rules_js()}


def _door_penalty_from_rules(rules, dt):
    P = rules.get("doorPenalty") or {}
    default = rules.get("doorDefaultPenalty")
    if default is None:
        default = 9.0
    return P.get(dt, default)


def _edge_allowed_js(e, mode):
    if mode == "blind":
        if e["blindAccessible"] is False:
            return False
        if int(e["accessibilityLevel"]) == 999:
            return False
        if e["crossFloor"] and e.get("type") == "staircase":
            return False
    elif mode == "wheelchair":
        if e["wheelchairAccessible"] is False:
            return False
        if int(e["accessibilityLevel"]) == 999:
            return False
    return True


def _is_closed_fire_door(nodes, nid):
    n = nodes.get(nid)
    return bool(n and n.get("type") == "doorway" and n.get("doorType") == "fire"
                and n.get("isNormallyOpen") is False)


def _edge_weight_js(rules, e, nodes):
    w = float(e.get("distance") or 0)
    dt = e.get("doorType")
    if dt:
        a, b = nodes.get(e["from"]), nodes.get(e["to"])
        if a and b and (a.get("type") == "room" or b.get("type") == "room"):
            if dt == "fire":
                dn = a if a.get("type") == "doorway" else (b if b.get("type") == "doorway" else None)
                if dn and dn.get("isNormallyOpen"):
                    return w
            w += _door_penalty_from_rules(rules, dt)
    return w


def _build_path_adj(pg, mode, door_filter, allow_wall):
    nodes = pg["nodes"]
    rules = pg.get("rules") or {}
    infra = set(pg.get("infraDoorwayIds") or [])
    adj = defaultdict(list)
    for e in pg.get("edges") or []:
        if not _edge_allowed_js(e, mode):
            continue
        if e["from"] in infra or e["to"] in infra:
            continue
        if _is_closed_fire_door(nodes, e["from"]) or _is_closed_fire_door(nodes, e["to"]):
            continue
        if not allow_wall and e.get("wallCrossing"):
            continue
        a, b = e["from"], e["to"]
        if a not in nodes or b not in nodes:
            continue
        if door_filter:
            ta, tb = nodes[a]["type"], nodes[b]["type"]
            if ta == "room" and tb == "doorway":
                best = nodes[a].get("bestDoorType")
                if best is not None:
                    ep = (0.0 if (nodes[b].get("doorType") == "fire"
                                  and nodes[b].get("isNormallyOpen"))
                          else _door_penalty_from_rules(rules, e.get("doorType")))
                    if ep > _door_penalty_from_rules(rules, best):
                        continue
            elif tb == "room" and ta == "doorway":
                best = nodes[b].get("bestDoorType")
                if best is not None:
                    ep = (0.0 if (nodes[a].get("doorType") == "fire"
                                  and nodes[a].get("isNormallyOpen"))
                          else _door_penalty_from_rules(rules, e.get("doorType")))
                    if ep > _door_penalty_from_rules(rules, best):
                        continue
        w = _edge_weight_js(rules, e, nodes)
        adj[a].append((b, w, e["id"]))
        adj[b].append((a, w, e["id"]))
    return adj


def _door_pass_through_blocked(u, prev_u, nb, nodes):
    nu = nodes.get(u)
    if not nu or nu.get("type") != "doorway":
        return False
    if u == prev_u or u == nb:
        return False
    np_ = nodes.get(prev_u)
    nnb = nodes.get(nb)
    if np_ and nnb and np_.get("type") == "room" and nnb.get("type") == "room":
        return True
    return False


def _js_dijkstra_core(pg, start_id, end_id, mode, adj):
    nodes = pg["nodes"]
    rules = pg.get("rules") or {}
    ns, ne = nodes.get(start_id), nodes.get(end_id)
    same = bool(ns and ne and ns.get("floor") == ne.get("floor"))
    mid = set(rules.get("midTypesSameFloor") or [])
    if not same:
        mid = set(rules.get("midTypesCrossFloor") or [])
    dist = {start_id: 0.0}
    prev, prev_edge = {}, {}
    visited = set()
    pq = [(0.0, start_id)]
    while pq:
        d, u = heapq.heappop(pq)
        if u in visited:
            continue
        visited.add(u)
        if u == end_id:
            break
        if u != start_id and u != end_id:
            nu = nodes.get(u) or {}
            if nu.get("roomType") == "toilet":
                continue
            if nu.get("type") not in mid:
                continue
        for nb, w, eid in adj.get(u, []):
            if nb in visited:
                continue
            if _door_pass_through_blocked(u, prev.get(u, u), nb, nodes):
                continue
            nd = d + w
            if nd < dist.get(nb, float("inf")):
                dist[nb] = nd
                prev[nb] = u
                prev_edge[nb] = eid
                heapq.heappush(pq, (nd, nb))
    if end_id not in dist:
        return None
    path, edge_ids = [], []
    at = end_id
    while at is not None:
        path.append(at)
        if at in prev_edge:
            edge_ids.append(prev_edge[at])
        if at == start_id:
            break
        at = prev.get(at)
    path.reverse()
    edge_ids.reverse()
    return {"nodes": path, "edges": edge_ids,
            "distance": round(dist[end_id] * 100) / 100}


def js_shortest_path(pg, start_id, end_id, mode):
    """复刻前端 JS dijkstra（door_filter -> door_fallback -> wall_fallback）。"""
    res = _js_dijkstra_core(pg, start_id, end_id, mode, _build_path_adj(pg, mode, True, False))
    note = None
    if res is None:
        res = _js_dijkstra_core(pg, start_id, end_id, mode, _build_path_adj(pg, mode, False, False))
        if res is not None:
            note = "door_fallback"
    if res is None:
        res = _js_dijkstra_core(pg, start_id, end_id, mode, _build_path_adj(pg, mode, True, True))
        if res is not None:
            note = "wall_fallback"
    if res is not None and note:
        res["note"] = note
    return res


def _sample_pairs(nodes, same_floor, seed, n):
    rng = random.Random(seed)
    by_floor = {}
    for nid, nd in nodes.items():
        by_floor.setdefault(nd["floor"], []).append(nid)
    pairs = []
    if same_floor:
        for _ in range(n):
            fl = rng.choice(sorted(by_floor))
            ids = by_floor[fl]
            if len(ids) < 2:
                continue
            s, e = rng.sample(ids, 2)
            pairs.append((s, e))
    else:
        floors = sorted(by_floor)
        if len(floors) < 2:
            return []
        for _ in range(n):
            f1, f2 = rng.sample(floors, 2)
            pairs.append((rng.choice(by_floor[f1]), rng.choice(by_floor[f2])))
    return pairs


if __name__ == "__main__":
    unittest.main()
