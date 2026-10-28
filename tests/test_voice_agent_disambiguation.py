# -*- coding: utf-8 -*-
"""意图解析与四级消歧测试（spec REQ-VOICE-03/04，验收 Case A~E）。

使用真实 POI 词典与真实地图地名，不虚构房间。
"""
import unittest

from src.voice_agent.schemas.llm_intent import DestinationCandidate, IntentParseResult
from src.voice_agent.schemas.spatial_context import UserSpatialContext
from src.voice_agent.services.disambiguation_engine import (
    RoutePlan,
    resolve_destination_and_act,
)
from src.voice_agent.services.dictionary_store import DictionaryStale, PoiDictionary
from src.voice_agent.services.rule_intent_resolver import resolve
from src.voice_agent.services.session_store import SessionStateStore
from testutil import CURRENT_MAP_VERSION


class FakePlanner:
    """按节点返回固定通行成本；`unreachable` 中的节点视为不可达。"""

    def __init__(self, distances: dict[str, float] | None = None, unreachable: set[str] | None = None):
        self.distances = distances or {}
        self.unreachable = unreachable or set()

    def plan(self, target_node_id: str, mode: str = "blind") -> RoutePlan:
        if target_node_id in self.unreachable:
            return RoutePlan(distance_m=0.0, reachable=False)
        return RoutePlan(distance_m=self.distances.get(target_node_id, 10.0), reachable=True)


def _ctx(floor: str = "1", motion: str = "stationary") -> UserSpatialContext:
    return UserSpatialContext(
        floor=floor,
        current_region_type="corridor",
        current_region_name="一楼中段走道",
        motion_state=motion,
        on_tactile_paving=False,
        floor_surface="普通地面",
        nearby_elements=[],
    )


class BaseCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dictionary = PoiDictionary.from_path()

    def run_turn(self, text: str, pending=(), floor: str = "1", planner=None):
        parsed = resolve(text, self.dictionary, pending)
        decision = resolve_destination_and_act(
            parsed, _ctx(floor), self.dictionary, planner or FakePlanner()
        )
        return parsed, decision


class TestDictionaryVersion(BaseCase):
    def test_version_stamp_matches_latest_map(self):
        self.assertEqual(self.dictionary.map_version, CURRENT_MAP_VERSION)

    def test_stale_version_rejected(self):
        with self.assertRaises(DictionaryStale):
            self.dictionary.ensure_version("2026-10-25.1")

    def test_aggregation(self):
        leader = self.dictionary.get("领导办公")
        assert leader is not None
        self.assertEqual(len(leader.instances), 3)
        self.assertFalse(leader.is_public_utility)
        self.assertEqual(len({i.zone_hint for i in leader.instances}), 3, "同名实例方位提示应互不相同")

        male = self.dictionary.get("男卫生间")
        assert male is not None
        self.assertTrue(male.is_public_utility)
        self.assertEqual(len(male.instances), 6)

    def test_aliases(self):
        parsed = resolve("带我去医务室", self.dictionary)
        self.assertEqual([c.standard_name for c in parsed.top_candidates], ["卫生室"])


class TestCaseAUnique(BaseCase):
    def test_medical_room_navigates(self):
        parsed, decision = self.run_turn("带我去医务室")
        self.assertEqual(parsed.intent_type, "navigate_to_destination")
        self.assertEqual(parsed.resolution_status, "resolved_unique_concept")
        self.assertEqual(decision.action, "start_navigation")
        self.assertEqual(decision.node_id, "F1-TR-0039")


class TestCaseBPublicUtility(BaseCase):
    def test_male_restroom_nearest_without_clarification(self):
        planner = FakePlanner(
            distances={
                "F1-TR-0004": 20.0,
                "F1-TR-0035": 5.0,
                "F1-TR-0041": 12.0,
                "F2-TR-0006": 3.0,  # 异层：+15m 加成后不敌同层 5m
            }
        )
        parsed, decision = self.run_turn("找一下男厕所", planner=planner)
        self.assertEqual(parsed.intent_type, "navigate_to_destination")
        self.assertEqual(decision.action, "start_navigation")
        self.assertEqual(decision.node_id, "F1-TR-0035")
        self.assertEqual(decision.pending_candidates, ())

    def test_cross_floor_when_same_floor_far(self):
        planner = FakePlanner(
            distances={
                "F2-TR-0006": 3.0,
                "F1-TR-0004": 30.0,
                "F1-TR-0035": 40.0,
                "F1-TR-0041": 50.0,
            }
        )
        _, decision = self.run_turn("去男卫生间", floor="2", planner=planner)
        self.assertEqual(decision.action, "start_navigation")
        self.assertEqual(decision.node_id, "F2-TR-0006")


class TestCaseCAmbiguity(BaseCase):
    def test_physics_lab_asks_then_resolves(self):
        parsed, decision = self.run_turn("我要去物理实验室")
        self.assertEqual(parsed.resolution_status, "ambiguous_concepts")
        self.assertEqual(decision.action, "speak")
        self.assertIn("物理", decision.text or "")
        self.assertEqual(len(decision.pending_candidates), 3)
        names = {c["standard_name"] for c in decision.pending_candidates}
        self.assertEqual(names, {"物理（声光电）", "物理（热）", "物理工作室"})

        _, second = self.run_turn("去热学那个", pending=decision.pending_candidates)
        self.assertEqual(second.action, "start_navigation")
        self.assertEqual(second.target, "2F物理（热）")
        self.assertEqual(second.node_id, "F2-TR-0021")

    def test_principal_office_multi_instance(self):
        parsed, decision = self.run_turn("我要去校长办公室")
        self.assertEqual(parsed.resolution_status, "resolved_unique_concept")
        self.assertEqual(parsed.top_candidates[0].standard_name, "领导办公")
        self.assertEqual(decision.action, "speak")
        self.assertIn("共有3间领导办公", decision.text or "")
        self.assertIn("第1间", decision.text or "")
        self.assertEqual(len(decision.pending_candidates), 3)

        _, second = self.run_turn("第一个", pending=decision.pending_candidates)
        self.assertEqual(second.action, "start_navigation")
        self.assertEqual(second.node_id, "F1-TR-0020")

    def test_nearest_reference_in_pending(self):
        _, decision = self.run_turn("我要去校长办公室")
        planner = FakePlanner(distances={"F1-TR-0020": 30.0, "F1-TR-0022": 8.0, "F1-TR-0024": 15.0})
        _, second = self.run_turn("离我最近的那个", pending=decision.pending_candidates, planner=planner)
        self.assertEqual(second.action, "start_navigation")
        self.assertEqual(second.node_id, "F1-TR-0022")


class TestCaseDReply(BaseCase):
    def test_where_am_i(self):
        parsed, _ = self.run_turn("我现在在哪儿")
        self.assertEqual(parsed.intent_type, "query_where_am_i")

    def test_hazard_question(self):
        parsed, _ = self.run_turn("前面门开着吗")
        self.assertEqual(parsed.intent_type, "check_hazard_or_door")


class TestCaseEFallback(BaseCase):
    def test_canteen_falls_back_to_water(self):
        parsed, decision = self.run_turn("去食堂")
        self.assertEqual(parsed.resolution_status, "not_found_in_building")
        self.assertEqual(decision.action, "speak")
        self.assertIn("饮水", decision.text or "")
        self.assertEqual(len(decision.pending_candidates), 1)

    def test_unknown_place_refused(self):
        _, decision = self.run_turn("带我去火星大厅")
        self.assertEqual(decision.action, "speak")
        self.assertIn("火星大厅", decision.text or "")
        self.assertIn("没有找到", decision.text or "")


class TestDecisionMatrixBoundaries(BaseCase):
    def test_low_confidence_refused(self):
        parsed = IntentParseResult(
            intent_type="navigate_to_destination",
            raw_target_mention="某处",
            resolution_status="resolved_unique_concept",
            top_candidates=[
                DestinationCandidate(
                    standard_name="卫生室",
                    match_type="exact_or_synonym",
                    confidence=0.49,
                    reasoning="低置信",
                )
            ],
        )
        decision = resolve_destination_and_act(parsed, _ctx(), self.dictionary, FakePlanner())
        self.assertEqual(decision.action, "speak")
        self.assertIn("没有找到", decision.text or "")

    def test_small_confidence_gap_asks(self):
        parsed = IntentParseResult(
            intent_type="navigate_to_destination",
            resolution_status="ambiguous_concepts",
            top_candidates=[
                DestinationCandidate(
                    standard_name="物理（声光电）",
                    match_type="category_submatch",
                    confidence=0.7,
                    reasoning="a",
                ),
                DestinationCandidate(
                    standard_name="物理（热）",
                    match_type="category_submatch",
                    confidence=0.6,
                    reasoning="b",
                ),
            ],
            clarification_question="请问是声光电还是热学？",
        )
        decision = resolve_destination_and_act(parsed, _ctx(), self.dictionary, FakePlanner())
        self.assertEqual(decision.action, "speak")
        self.assertEqual(len(decision.pending_candidates), 2)

    def test_public_utility_all_unreachable(self):
        planner = FakePlanner(
            unreachable={
                "F1-TR-0004",
                "F1-TR-0035",
                "F1-TR-0041",
                "F2-TR-0006",
                "F2-TR-0026",
                "F2-TR-0032",
            }
        )
        _, decision = self.run_turn("去男卫生间", planner=planner)
        self.assertEqual(decision.action, "speak")
        self.assertIn("没有无障碍路线", decision.text or "")

    def test_contextual_node_skips_expansion(self):
        parsed = IntentParseResult(
            intent_type="navigate_to_destination",
            resolution_status="resolved_unique_concept",
            top_candidates=[
                DestinationCandidate(
                    standard_name="领导办公",
                    match_type="contextual_selection",
                    confidence=1.0,
                    reasoning="用户选择第 2 间",
                    node_id="F1-TR-0022",
                )
            ],
        )
        decision = resolve_destination_and_act(parsed, _ctx(), self.dictionary, FakePlanner())
        self.assertEqual(decision.action, "start_navigation")
        self.assertEqual(decision.node_id, "F1-TR-0022")


class TestSessionStore(unittest.TestCase):
    def test_ttl_expiry(self):
        now = [1000.0]
        store = SessionStateStore(ttl_seconds=180, clock=lambda: now[0])
        store.put("u1", [{"standard_name": "领导办公"}])
        self.assertEqual(len(store.get("u1")), 1)
        now[0] += 179.9
        self.assertEqual(len(store.get("u1")), 1)
        now[0] += 0.2
        self.assertEqual(store.get("u1"), ())

    def test_clear(self):
        store = SessionStateStore()
        store.put("u1", [{"standard_name": "领导办公"}])
        store.clear("u1")
        self.assertEqual(store.get("u1"), ())


if __name__ == "__main__":
    unittest.main()
