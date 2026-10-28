# -*- coding: utf-8 -*-
"""M3 LLM 意图解析测试（spec REQ-VOICE-06 / 验收 G~K）。

全部走注入的假 transport 或假客户端，不访问网络；词典与地名为真实数据。
"""
import json
import unittest

import httpx

from src.voice_agent.config.prompts import (
    DICT_HEADER,
    build_intent_messages,
    compact_dictionary_text,
    intent_json_schema,
    intent_system_prompt,
    prompt_cache_key,
)
from src.voice_agent.schemas.spatial_context import NearbyElement, UserSpatialContext
from src.voice_agent.services.dictionary_store import PoiDictionary
from src.voice_agent.services.intent_parser import IntentParser, ParseOutcome
from src.voice_agent.services.llm_client import LlmClient, LlmConfig, LlmError, LlmResult


class FakeLlm:
    """返回固定 JSON 的假客户端，同时记录调用次数与 messages。"""

    def __init__(self, payload: dict, ttft_ms: int = 120):
        self.payload = payload
        self.ttft_ms = ttft_ms
        self.calls: list[dict] = []

    def chat(self, messages, response_schema=None, schema_name="voice_agent_output") -> LlmResult:
        self.calls.append({"messages": messages, "schema": response_schema, "name": schema_name})
        return LlmResult(
            text=json.dumps(self.payload, ensure_ascii=False),
            ttft_ms=self.ttft_ms,
            total_ms=self.ttft_ms + 80,
        )


def _sse_response(*chunks: str) -> bytes:
    lines = [
        "data: "
        + json.dumps({"choices": [{"delta": {"content": chunk}}]}, ensure_ascii=False)
        + "\n\n"
        for chunk in chunks
    ]
    return ("".join(lines) + "data: [DONE]\n\n").encode("utf-8")


def _ctx(motion: str = "stationary", tactile: bool = False) -> UserSpatialContext:
    return UserSpatialContext(
        floor="1",
        current_region_type="corridor",
        current_region_name="一楼中段走道",
        motion_state=motion,
        on_tactile_paving=tactile,
        floor_surface="普通地面",
        nearby_elements=[
            NearbyElement(
                name="领导办公",
                element_type="fire_door_normally_closed",
                clock_direction=12,
                distance_meters=2.0,
                distance_steps=3,
                accessibility_note="常闭防火门，可能需要推开",
            )
        ],
    )


class LlmBase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dictionary = PoiDictionary.from_path()

    @staticmethod
    def _config(mode: str = "json_object") -> LlmConfig:
        return LlmConfig(
            base_url="https://llm.test/v1",
            api_key="secret-key",
            model="fake-model",
            structured_mode=mode,
        )


class TestLlmClient(LlmBase):
    def test_stream_aggregation_and_ttft(self):
        captured: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["payload"] = json.loads(request.content.decode("utf-8"))
            captured["auth"] = request.headers.get("authorization")
            return httpx.Response(
                200,
                headers={"content-type": "text/event-stream"},
                content=_sse_response('{"intent_type":', ' "query_where_am_i"}'),
            )

        client = LlmClient(self._config(), transport=httpx.MockTransport(handler))
        result = client.chat([{"role": "user", "content": "我在哪"}])

        self.assertEqual(result.text, '{"intent_type": "query_where_am_i"}')
        self.assertGreaterEqual(result.ttft_ms, 0)
        self.assertGreaterEqual(result.total_ms, result.ttft_ms)
        self.assertEqual(captured["payload"]["model"], "fake-model")
        self.assertTrue(captured["payload"]["stream"])
        self.assertEqual(captured["payload"]["response_format"], {"type": "json_object"})
        self.assertEqual(captured["auth"], "Bearer secret-key")

    def test_json_schema_mode_payload(self):
        captured: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["payload"] = json.loads(request.content.decode("utf-8"))
            return httpx.Response(
                200, headers={"content-type": "text/event-stream"},
                content=_sse_response("{}"),
            )

        client = LlmClient(self._config("json_schema"), transport=httpx.MockTransport(handler))
        client.chat([{"role": "user", "content": "x"}], response_schema={"type": "object"})

        fmt = captured["payload"]["response_format"]
        self.assertEqual(fmt["type"], "json_schema")
        self.assertTrue(fmt["json_schema"]["strict"])
        self.assertEqual(fmt["json_schema"]["schema"], {"type": "object"})

    def test_http_error_raises(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(500, content=b'{"error":"boom"}')

        client = LlmClient(self._config(), transport=httpx.MockTransport(handler))
        with self.assertRaises(LlmError):
            client.chat([{"role": "user", "content": "x"}])

    def test_timeout_raises(self):
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ReadTimeout("timeout", request=request)

        client = LlmClient(self._config(), transport=httpx.MockTransport(handler))
        with self.assertRaises(LlmError):
            client.chat([{"role": "user", "content": "x"}])

    def test_not_configured_raises(self):
        with self.assertRaises(LlmError):
            LlmClient(LlmConfig(base_url="", api_key="", model=""))

    def test_key_not_in_repr(self):
        self.assertNotIn("secret-key", repr(self._config()))


class TestPrompts(LlmBase):
    def test_dictionary_is_static_prefix(self):
        messages, cache_key = build_intent_messages(
            self.dictionary, "带我去医务室", _ctx(), pending=[{"standard_name": "领导办公"}]
        )
        system = messages[0]["content"]
        user = messages[1]["content"]
        dict_text = compact_dictionary_text(self.dictionary)

        self.assertTrue(system.startswith(DICT_HEADER))
        self.assertIn(dict_text, system)
        self.assertLess(system.index(dict_text), system.index("### 二、"))
        # 逐请求数据不得进入缓存前缀
        self.assertNotIn("一楼中段走道", system)
        self.assertNotIn("带我去医务室", system)
        self.assertIn("实时自我中心空间上下文", user)
        self.assertIn("当前待澄清会话状态", user)
        self.assertIn("带我去医务室", user)
        self.assertEqual(cache_key, prompt_cache_key(self.dictionary))
        self.assertIn(self.dictionary.map_version, cache_key)

    def test_schema_contains_core_enums(self):
        schema = json.dumps(intent_json_schema(), ensure_ascii=False)
        for token in ("navigate_to_destination", "ambiguous_concepts", "standard_name", "direct_response_speech"):
            self.assertIn(token, schema)

    def test_fallbacks_and_accessibility_facts_in_prefix(self):
        system = intent_system_prompt(self.dictionary)
        self.assertIn("食堂→饮水", system)
        self.assertIn("未铺设盲道", system)


class TestParserLlmCases(LlmBase):
    def test_case_a_unique_destination(self):
        llm = FakeLlm(
            {
                "intent_type": "navigate_to_destination",
                "raw_target_mention": "医务室",
                "resolution_status": "resolved_unique_concept",
                "top_candidates": [
                    {
                        "standard_name": "卫生室",
                        "match_type": "exact_or_synonym",
                        "confidence": 0.96,
                        "reasoning": "医务室是卫生室的常用别名",
                    }
                ],
            }
        )
        outcome = IntentParser(self.dictionary, llm).parse("带我去医务室", _ctx())
        self.assertEqual(outcome.source, "llm")
        self.assertFalse(outcome.degraded)
        self.assertEqual(outcome.result.top_candidates[0].standard_name, "卫生室")
        self.assertGreater(outcome.llm_ttft_ms or 0, 0)
        self.assertEqual(len(llm.calls), 1)
        self.assertIsNotNone(llm.calls[0]["schema"])

    def test_case_c_ambiguous(self):
        llm = FakeLlm(
            {
                "intent_type": "navigate_to_destination",
                "resolution_status": "ambiguous_concepts",
                "raw_target_mention": "物理实验室",
                "top_candidates": [
                    {"standard_name": "物理（声光电）", "match_type": "category_submatch", "confidence": 0.72, "reasoning": "a"},
                    {"standard_name": "物理（热）", "match_type": "category_submatch", "confidence": 0.68, "reasoning": "b"},
                    {"standard_name": "物理工作室", "match_type": "category_submatch", "confidence": 0.62, "reasoning": "c"},
                ],
                "clarification_question": "二楼有物理（声光电）、物理（热）、物理工作室，请问您要去哪一个？",
            }
        )
        outcome = IntentParser(self.dictionary, llm).parse("我要去物理实验室", _ctx())
        self.assertEqual(outcome.source, "llm")
        self.assertEqual(outcome.result.resolution_status, "ambiguous_concepts")
        self.assertEqual(len(outcome.result.top_candidates), 3)
        self.assertIn("请问", outcome.result.clarification_question or "")

    def test_case_d_direct_answer_is_clamped(self):
        long_speech = "你现在位于一楼中段走道，脚下未铺设盲道，" * 6
        llm = FakeLlm(
            {
                "intent_type": "query_where_am_i",
                "direct_response_speech": long_speech,
            }
        )
        outcome = IntentParser(self.dictionary, llm).parse("我周围有什么", _ctx("stationary"))
        self.assertEqual(outcome.source, "llm")
        speech = outcome.result.direct_response_speech or ""
        self.assertLessEqual(len(speech), 65)
        self.assertNotIn(speech[-1], "，；、")

        walking = IntentParser(self.dictionary, llm).parse("我周围有什么", _ctx("walking"))
        walking_speech = walking.result.direct_response_speech or ""
        self.assertLessEqual(len(walking_speech), 20)
        self.assertNotIn(walking_speech[-1], "，；、")

    def test_direct_answer_filled_from_context_when_missing(self):
        llm = FakeLlm({"intent_type": "check_hazard_or_door"})
        outcome = IntentParser(self.dictionary, llm).parse("前面门开着吗", _ctx())
        speech = outcome.result.direct_response_speech or ""
        self.assertIn("一楼中段走道", speech)
        self.assertIn("防火门", speech)
        self.assertNotIn("F1-", speech)

    def test_candidates_truncated_and_filtered(self):
        llm = FakeLlm(
            {
                "intent_type": "navigate_to_destination",
                "resolution_status": "ambiguous_concepts",
                "top_candidates": [
                    {"standard_name": "卫生室", "match_type": "category_submatch", "confidence": 0.6, "reasoning": "a"},
                    {"standard_name": "火星大厅", "match_type": "category_submatch", "confidence": 0.6, "reasoning": "x"},
                    {"standard_name": "传达室", "match_type": "category_submatch", "confidence": 0.5, "reasoning": "b"},
                    {"standard_name": "饮水", "match_type": "category_submatch", "confidence": 0.5, "reasoning": "c"},
                ],
                "clarification_question": "请选择",
            }
        )
        outcome = IntentParser(self.dictionary, llm).parse("带我去个地方", _ctx())
        names = [c.standard_name for c in outcome.result.top_candidates]
        self.assertEqual(names, ["卫生室", "传达室", "饮水"])
        self.assertEqual(outcome.source, "llm")


class TestParserDegradation(LlmBase):
    def _degraded(self, llm, text="带我去医务室", ctx=None) -> ParseOutcome:
        outcome = IntentParser(self.dictionary, llm).parse(text, ctx or _ctx())
        self.assertEqual(outcome.source, "rules")
        self.assertTrue(outcome.degraded)
        return outcome

    def test_invalid_json(self):
        class BadJson:
            def chat(self, messages, response_schema=None, schema_name="x"):
                return LlmResult(text="抱歉，我无法回答", ttft_ms=10, total_ms=20)

        outcome = self._degraded(BadJson())
        self.assertEqual(outcome.result.top_candidates[0].standard_name, "卫生室")

    def test_http_error(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(503, content=b"busy")

        client = LlmClient(self._config(), transport=httpx.MockTransport(handler))
        self._degraded(client)

    def test_timeout(self):
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ReadTimeout("timeout", request=request)

        client = LlmClient(self._config(), transport=httpx.MockTransport(handler))
        self._degraded(client)

    def test_no_llm_configured(self):
        outcome = self._degraded(None)
        self.assertEqual(outcome.result.resolution_status, "resolved_unique_concept")

    def test_all_candidates_unknown(self):
        llm = FakeLlm(
            {
                "intent_type": "navigate_to_destination",
                "resolution_status": "resolved_unique_concept",
                "top_candidates": [
                    {"standard_name": "火星大厅", "match_type": "exact_or_synonym", "confidence": 0.9, "reasoning": "x"}
                ],
            }
        )
        outcome = self._degraded(llm)

    def test_resolved_without_candidates(self):
        llm = FakeLlm(
            {
                "intent_type": "navigate_to_destination",
                "resolution_status": "resolved_unique_concept",
                "top_candidates": [],
            }
        )
        self._degraded(llm)

    def test_degraded_direct_answer_still_speaks(self):
        class BadJson:
            def chat(self, messages, response_schema=None, schema_name="x"):
                return LlmResult(text="not json", ttft_ms=10, total_ms=20)

        outcome = IntentParser(self.dictionary, BadJson()).parse(
            "我现在在哪儿，前面门是开着的吗？", _ctx()
        )
        self.assertEqual(outcome.source, "rules")
        self.assertTrue(outcome.degraded)
        self.assertEqual(outcome.result.intent_type, "query_where_am_i")
        speech = outcome.result.direct_response_speech or ""
        self.assertIn("一楼中段走道", speech)
        self.assertIn("盲道", speech)


class TestFastPath(LlmBase):
    def test_where_am_i_skips_llm(self):
        llm = FakeLlm({"intent_type": "out_of_scope"})
        outcome = IntentParser(self.dictionary, llm).parse("我在哪儿", _ctx())
        self.assertEqual(outcome.source, "fast_path")
        self.assertFalse(outcome.degraded)
        self.assertEqual(len(llm.calls), 0)
        speech = outcome.result.direct_response_speech or ""
        self.assertIn("一楼中段走道", speech)
        self.assertIn("盲道", speech)
        self.assertIn("正前方", speech)
        self.assertNotIn("F1-", speech)

    def test_where_am_i_tolerates_punctuation(self):
        llm = FakeLlm({"intent_type": "out_of_scope"})
        outcome = IntentParser(self.dictionary, llm).parse("我在哪儿？", _ctx())
        self.assertEqual(outcome.source, "fast_path")
        self.assertEqual(len(llm.calls), 0)

    def test_repeat_skips_llm(self):
        llm = FakeLlm({"intent_type": "out_of_scope"})
        outcome = IntentParser(self.dictionary, llm).parse("再说一遍", None)
        self.assertEqual(outcome.source, "fast_path")
        self.assertEqual(outcome.result.intent_type, "navigation_control")
        self.assertEqual(len(llm.calls), 0)

    def test_walking_speech_within_limit(self):
        ctx = _ctx("walking")
        speech = IntentParser(self.dictionary, None).parse("周围有什么", ctx).result.direct_response_speech
        self.assertLessEqual(len(speech or ""), 20)


if __name__ == "__main__":
    unittest.main()
