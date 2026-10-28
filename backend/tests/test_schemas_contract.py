# -*- coding: utf-8 -*-
"""API 契约冻结测试：以 docs/24 §8 示例为基线做正反校验。

文档示例为契约演示；对示例补充契约冻结要求的字段后校验：
- 指令须带 instructionSequence（会话内单调递增）与 expiresAt（默认 10s 有效）；
- dataSha256 须为 64 位十六进制摘要；
- 所有模型 extra=forbid，未知字段一律拒绝。
"""
from __future__ import annotations

import copy
from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from pathai_backend.schemas import (
    AuthResponse,
    BeaconObservation,
    CreateSessionRequest,
    CreateSessionResponse,
    EndSessionRequest,
    EndSessionResponse,
    IntentParseRequest,
    IntentParseResponse,
    LocationEstimateRequest,
    MapVersionResponse,
    NavUpdateRequest,
    NavUpdateResponse,
    PlaceCandidate,
    PositionEstimateResponse,
    RouteFallbackType,
    RouteInfo,
    SessionQueryResponse,
    Trigger,
    WechatLoginRequest,
)
from pathai_backend.schemas.common import NavigationInstruction

UTC = timezone.utc


def _dt(minute: int, second: int = 0) -> datetime:
    return datetime(2026, 10, 23, 8, minute, second, tzinfo=UTC)


_SHA256 = "a" * 64


# ---------------------------------------------------------------------------
# docs/24 §8 示例 payload（含契约冻结补充字段）
# ---------------------------------------------------------------------------

def _instruction(**overrides) -> dict:
    payload = {
        "instructionId": "ins_2",
        "instructionSequence": 2,
        "action": "turn_right",
        "text": "前方路口请右转。",
        "distanceM": 6.0,
        "landmark": {
            "placeId": "service-desk-1",
            "name": "服务台",
            "relativeDirection": "left",
            "distanceM": 4.0,
        },
        "priority": "turn",
        "trigger": {"type": "decision_point", "nodeId": "intersection-17"},
        "expiresAt": "2026-10-23T08:00:13Z",
    }
    payload.update(overrides)
    return payload


def _route_payload() -> dict:
    return {
        "routeId": "route_abc123",
        "mapVersion": "2026-10-25.1",
        "routeRulesVersion": "1.0.0",
        "distanceM": 126.5,
        "estimatedDurationSec": None,
        "polyline": [
            {"x": 12.4, "y": 8.1, "floorId": "1"},
            {"x": 20.0, "y": 8.1, "floorId": "1"},
        ],
        "segments": [
            {
                "instructionId": "seg_1",
                "instructionSequence": 1,
                "action": "continue",
                "text": "请沿走廊直行。",
                "distanceM": 18.0,
                "landmark": None,
                "priority": "normal",
                "trigger": {"type": "distance", "valueM": 12.0},
                "expiresAt": None,
            }
        ],
        "fallbackType": "none",
        "safeForBlind": True,
        "riskNotices": [],
    }


def _destination() -> dict:
    return {"placeId": "library-301", "name": "三楼图书馆", "floorId": "3"}


# ---------------------------------------------------------------------------
# 正例：docs/24 §8 示例 round-trip
# ---------------------------------------------------------------------------

class TestApi01Auth:
    def test_login_request_and_response_roundtrip(self):
        req = WechatLoginRequest.model_validate({"code": "wx-login-code-01"})
        assert req.code == "wx-login-code-01"

        resp = AuthResponse.model_validate(
            {
                "accessToken": "opaque-token-value",
                "tokenType": "Bearer",
                "expiresIn": 7200,
                "userId": "anon_0123456789abcdef",
            }
        )
        assert resp.tokenType == "Bearer"
        assert resp.expiresIn == 7200
        dumped = resp.model_dump(mode="json")
        assert dumped["userId"] == "anon_0123456789abcdef"


class TestApi02Map:
    def test_map_version_response_roundtrip(self):
        payload = {
            "siteId": "school-building-01",
            "mapVersion": "2026-10-25.1",
            "publishedAt": "2026-10-25T14:10:01Z",
            "coordinateSystem": {
                "unit": "m",
                "originDescription": "由本地图版本定义",
                "headingReference": "map_north",
            },
            "floors": [
                {"floorId": "1", "displayName": "一楼", "level": 1},
                {"floorId": "2", "displayName": "二楼", "level": 2},
            ],
            "dataUrl": "https://cdn.example.com/map-data.json",
            "dataSha256": _SHA256,
        }
        resp = MapVersionResponse.model_validate(payload)
        assert [f.floorId for f in resp.floors] == ["1", "2"]
        assert resp.model_dump(mode="json")["mapVersion"] == "2026-10-25.1"


class TestApi03Places:
    def test_places_response_roundtrip(self):
        payload = {
            "mapVersion": "2026-10-25.1",
            "items": [
                {
                    "placeId": "library-301",
                    "name": "三楼图书馆",
                    "spokenName": "三楼图书馆",
                    "aliases": ["图书馆"],
                    "type": "library",
                    "floorId": "3",
                    "nodeId": "node_301_entry",
                    "accessible": True,
                }
            ],
            "nextCursor": None,
        }
        from pathai_backend.schemas import PlacesResponse

        resp = PlacesResponse.model_validate(payload)
        assert resp.items[0].placeId == "library-301"
        assert resp.nextCursor is None


class TestApi04Intent:
    def test_unique_match_roundtrip(self):
        payload = {
            "intentId": "intent_001",
            "intent": "start_navigation",
            "confidence": 0.93,
            "needsClarification": False,
            "destination": _destination(),
            "candidates": [],
            "responseText": "准备导航到三楼图书馆，请确认。",
        }
        resp = IntentParseResponse.model_validate(payload)
        assert resp.intent.value == "start_navigation"
        assert resp.destination is not None

    def test_clarification_roundtrip(self):
        payload = {
            "intentId": "intent_002",
            "intent": "start_navigation",
            "confidence": 0.61,
            "needsClarification": True,
            "destination": None,
            "candidates": [
                {"placeId": "library-301", "name": "三楼图书馆", "floorId": "3"},
                {"placeId": "library-201", "name": "二楼图书馆", "floorId": "2"},
            ],
            "responseText": "找到两个图书馆，请说二楼或三楼。",
        }
        resp = IntentParseResponse.model_validate(payload)
        assert resp.needsClarification is True
        assert len(resp.candidates) == 2

    def test_parse_request_roundtrip(self):
        req = IntentParseRequest.model_validate(
            {
                "siteId": "school-building-01",
                "mapVersion": "2026-10-25.1",
                "text": "带我去三楼图书馆",
                "sessionId": None,
                "locale": "zh-CN",
            }
        )
        assert req.text == "带我去三楼图书馆"


class TestApi05Location:
    def _request_payload(self) -> dict:
        return {
            "siteId": "school-building-01",
            "mapVersion": "2026-10-25.1",
            "sequence": 18,
            "collectedAt": "2026-10-23T08:00:00Z",
            "floorHint": "1",
            "beacons": [
                {"beaconId": "B-001", "rssi": -67, "observedAt": "2026-10-23T08:00:00Z"},
                {"beaconId": "B-002", "rssi": -75, "observedAt": "2026-10-23T08:00:00Z"},
            ],
            "imu": {
                "samples": [
                    {
                        "timestampMs": 28100412,
                        "gyroRadS": [0.01, 0.0, -0.02],
                        "accelMS2": [0.11, 0.02, 9.72],
                    }
                ],
                "headingDeg": 42.0,
            },
        }

    def test_estimate_request_with_imu_roundtrip(self):
        req = LocationEstimateRequest.model_validate(self._request_payload())
        assert req.sequence == 18
        assert req.beacons[0].rssi == -67
        assert req.imu is not None and len(req.imu.samples) == 1

    def test_estimate_response_roundtrip(self):
        payload = {
            "estimateId": "loc_001",
            "sequence": 18,
            "mapVersion": "2026-10-25.1",
            "position": {"x": 12.4, "y": 8.1, "floorId": "1"},
            "headingDeg": 41.5,
            "confidence": 0.82,
            "quality": "reliable",
            "matchedArea": {"placeId": "main-corridor", "name": "一楼走廊"},
            "algorithmVersion": "location-0.1",
            "paramsVersion": "loc-test.1",
            "observedAt": "2026-10-23T08:00:00Z",
        }
        resp = PositionEstimateResponse.model_validate(payload)
        assert resp.quality.value == "reliable"
        assert resp.paramsVersion == "loc-test.1"
        assert resp.position is not None

    def test_unreliable_allows_null_position(self):
        payload = {
            "estimateId": "loc_002",
            "sequence": 19,
            "mapVersion": "2026-10-25.1",
            "position": None,
            "headingDeg": None,
            "confidence": 0.2,
            "quality": "unreliable",
            "matchedArea": None,
            "algorithmVersion": "location-0.1",
            "paramsVersion": "loc-test.1",
            "observedAt": "2026-10-23T08:00:00Z",
            "ignoredBeaconIds": ["UNKNOWN-1"],
        }
        resp = PositionEstimateResponse.model_validate(payload)
        assert resp.position is None
        assert resp.ignoredBeaconIds == ["UNKNOWN-1"]


class TestApi06CreateSession:
    def test_create_request_roundtrip(self):
        payload = {
            "siteId": "school-building-01",
            "mapVersion": "2026-10-25.1",
            "origin": {
                "x": 12.4,
                "y": 8.1,
                "floorId": "1",
                "confidence": 0.82,
                "observedAt": "2026-10-23T08:00:00Z",
            },
            "destinationId": "library-301",
            "userMode": "blind",
            "locale": "zh-CN",
        }
        req = CreateSessionRequest.model_validate(payload)
        assert req.userMode.value == "blind"

    def test_create_response_roundtrip_and_first_instruction_is_segment_zero(self):
        payload = {
            "sessionId": "nav_abc12345",
            "status": "active",
            "mapVersion": "2026-10-25.1",
            "paramsVersion": "loc-test.1",
            "destination": _destination(),
            "route": _route_payload(),
            "createdAt": "2026-10-23T08:00:01Z",
        }
        resp = CreateSessionResponse.model_validate(payload)
        assert resp.status.value == "active"
        # 首条指令 = route.segments[0]，且是完整的 NavigationInstruction
        assert isinstance(resp.route.segments[0], NavigationInstruction)
        assert resp.route.segments[0].instructionSequence == 1
        assert resp.route.routeRulesVersion == "1.0.0"


class TestApi07NavUpdate:
    def _location_payload(self, **overrides) -> dict:
        payload = {
            "x": 15.1,
            "y": 8.0,
            "floorId": "1",
            "confidence": 0.79,
            "quality": "reliable",
            "observedAt": "2026-10-23T08:00:03Z",
        }
        payload.update(overrides)
        return payload

    def test_update_request_roundtrip(self):
        req = NavUpdateRequest.model_validate(
            {"sequence": 19, "location": self._location_payload()}
        )
        assert req.sequence == 19

    def test_instruction_event_roundtrip(self):
        payload = {
            "sessionId": "nav_abc12345",
            "status": "active",
            "event": "instruction",
            "instruction": _instruction(),
            "routeChanged": False,
            "processedSequence": 19,
        }
        resp = NavUpdateResponse.model_validate(payload)
        assert resp.event.value == "instruction"
        assert resp.instruction.landmark is not None

    def test_relocalizing_event_roundtrip(self):
        payload = {
            "sessionId": "nav_abc12345",
            "status": "recovering",
            "event": "relocalizing",
            "instruction": _instruction(
                instructionId="ins_relocate",
                instructionSequence=3,
                action="relocalize",
                text="正在重新确定位置，请暂缓前行。",
                distanceM=None,
                landmark=None,
                priority="safety",
                trigger={"type": "immediate"},
            ),
            "routeChanged": False,
            "processedSequence": 19,
        }
        resp = NavUpdateResponse.model_validate(payload)
        assert resp.status.value == "recovering"
        assert resp.instruction.action.value == "relocalize"
        assert resp.instruction.trigger.type == "immediate"

    def test_reroute_event_carries_new_route(self):
        payload = {
            "sessionId": "nav_abc12345",
            "status": "active",
            "event": "reroute",
            "instruction": _instruction(instructionId="ins_new_1", instructionSequence=4),
            "routeChanged": True,
            "processedSequence": 20,
            "route": {**_route_payload(), "routeId": "route_new999"},
        }
        resp = NavUpdateResponse.model_validate(payload)
        assert resp.route is not None
        assert resp.route.routeId == "route_new999"

    def test_arrived_event_roundtrip(self):
        payload = {
            "sessionId": "nav_abc12345",
            "status": "arrived",
            "event": "arrived",
            "instruction": _instruction(
                instructionId="ins_arrive",
                instructionSequence=5,
                action="arrive",
                text="已到达三楼图书馆。",
                priority="normal",
            ),
            "routeChanged": False,
            "processedSequence": 30,
        }
        resp = NavUpdateResponse.model_validate(payload)
        assert resp.status.value == "arrived"


class TestApi08SessionQuery:
    def test_active_session_roundtrip(self):
        payload = {
            "sessionId": "nav_abc12345",
            "status": "active",
            "siteId": "school-building-01",
            "mapVersion": "2026-10-25.1",
            "paramsVersion": "loc-test.1",
            "destination": _destination(),
            "currentInstruction": {
                "instructionId": "ins_2",
                "text": "前方路口请右转。",
                "action": "turn_right",
            },
            "lastProcessedSequence": 19,
            "updatedAt": "2026-10-23T08:00:03Z",
        }
        resp = SessionQueryResponse.model_validate(payload)
        assert resp.currentInstruction is not None

    def test_ended_session_roundtrip(self):
        payload = {
            "sessionId": "nav_abc12345",
            "status": "ended",
            "siteId": "school-building-01",
            "mapVersion": "2026-10-25.1",
            "paramsVersion": "loc-test.1",
            "destination": _destination(),
            "currentInstruction": None,
            "lastProcessedSequence": 19,
            "updatedAt": "2026-10-23T08:01:00Z",
            "endedAt": "2026-10-23T08:01:00Z",
            "endReason": "user_cancelled",
        }
        resp = SessionQueryResponse.model_validate(payload)
        assert resp.endReason.value == "user_cancelled"


class TestApi09EndSession:
    def test_end_request_and_response_roundtrip(self):
        req = EndSessionRequest.model_validate({"reason": "user_cancelled"})
        assert req.reason.value == "user_cancelled"

        resp = EndSessionResponse.model_validate(
            {
                "sessionId": "nav_abc12345",
                "status": "ended",
                "reason": "user_cancelled",
                "endedAt": "2026-10-23T08:01:00Z",
            }
        )
        assert resp.status.value == "ended"


# ---------------------------------------------------------------------------
# 反例：非法输入必须被契约拒绝
# ---------------------------------------------------------------------------

def _rejects(payload: dict, model, match: str | None = None) -> None:
    with pytest.raises(ValidationError, match=match):
        model.model_validate(payload)


class TestInvalidInputs:
    def test_rssi_below_range_rejected(self):
        _rejects(
            {"beaconId": "B-001", "rssi": -120, "observedAt": "2026-10-23T08:00:00Z"},
            BeaconObservation,
        )

    def test_rssi_above_range_rejected(self):
        _rejects(
            {"beaconId": "B-001", "rssi": -10, "observedAt": "2026-10-23T08:00:00Z"},
            BeaconObservation,
        )

    def test_confidence_above_one_rejected(self):
        payload = {
            "estimateId": "loc_x",
            "sequence": 1,
            "mapVersion": "2026-10-25.1",
            "position": {"x": 1.0, "y": 1.0, "floorId": "1"},
            "confidence": 1.5,
            "quality": "reliable",
            "algorithmVersion": "location-0.1",
            "observedAt": "2026-10-23T08:00:00Z",
        }
        _rejects(payload, PositionEstimateResponse)

    def test_invalid_quality_enum_rejected(self):
        payload = {
            "estimateId": "loc_x",
            "sequence": 1,
            "mapVersion": "2026-10-25.1",
            "position": {"x": 1.0, "y": 1.0, "floorId": "1"},
            "confidence": 0.9,
            "quality": "perfect",
            "algorithmVersion": "location-0.1",
            "observedAt": "2026-10-23T08:00:00Z",
        }
        _rejects(payload, PositionEstimateResponse)

    def test_invalid_intent_enum_rejected(self):
        payload = {
            "intentId": "intent_x",
            "intent": "open_door",
            "confidence": 0.9,
            "needsClarification": False,
            "responseText": "x",
        }
        _rejects(payload, IntentParseResponse)

    def test_http_data_url_rejected(self):
        payload = {
            "siteId": "s",
            "mapVersion": "v1",
            "publishedAt": "2026-10-25T14:10:01Z",
            "coordinateSystem": {"unit": "m", "originDescription": "o", "headingReference": "map_north"},
            "floors": [{"floorId": "1", "displayName": "一楼", "level": 1}],
            "dataUrl": "http://cdn.example.com/map-data.json",
            "dataSha256": _SHA256,
        }
        _rejects(payload, MapVersionResponse)

    def test_trigger_distance_without_value_m_rejected(self):
        _rejects({"type": "distance"}, Trigger)

    def test_trigger_decision_point_without_node_id_rejected(self):
        _rejects({"type": "decision_point"}, Trigger)

    def test_trigger_immediate_with_extra_fields_rejected(self):
        _rejects({"type": "immediate", "valueM": 3.0}, Trigger)

    def test_bad_user_id_format_rejected(self):
        _rejects(
            {
                "accessToken": "t",
                "tokenType": "Bearer",
                "expiresIn": 7200,
                "userId": "anon_SHORT",
            },
            AuthResponse,
        )

    def test_zero_sequence_rejected(self):
        _rejects(
            {
                "siteId": "s",
                "mapVersion": "v1",
                "sequence": 0,
                "collectedAt": "2026-10-23T08:00:00Z",
                "beacons": [
                    {"beaconId": "B-001", "rssi": -67, "observedAt": "2026-10-23T08:00:00Z"}
                ],
            },
            LocationEstimateRequest,
        )

    def test_empty_beacons_rejected(self):
        _rejects(
            {
                "siteId": "s",
                "mapVersion": "v1",
                "sequence": 1,
                "collectedAt": "2026-10-23T08:00:00Z",
                "beacons": [],
            },
            LocationEstimateRequest,
        )

    def test_wall_fallback_marked_safe_rejected(self):
        route = _route_payload()
        route["fallbackType"] = "wall_fallback"
        route["safeForBlind"] = True
        _rejects(route, RouteInfo)

    def test_wall_fallback_without_risk_notices_rejected(self):
        route = _route_payload()
        route["fallbackType"] = "wall_fallback"
        route["safeForBlind"] = False
        _rejects(route, RouteInfo)

    def test_trusted_quality_without_position_rejected(self):
        payload = {
            "estimateId": "loc_x",
            "sequence": 1,
            "mapVersion": "v1",
            "position": None,
            "confidence": 0.9,
            "quality": "reliable",
            "algorithmVersion": "location-0.1",
            "observedAt": "2026-10-23T08:00:00Z",
        }
        _rejects(payload, PositionEstimateResponse)

    def test_trusted_update_without_coords_rejected(self):
        payload = {
            "sequence": 1,
            "location": {
                "x": None,
                "y": None,
                "floorId": None,
                "confidence": 0.8,
                "quality": "reliable",
                "observedAt": "2026-10-23T08:00:03Z",
            },
        }
        _rejects(payload, NavUpdateRequest)

    def test_relocalizing_with_active_status_rejected(self):
        payload = {
            "sessionId": "nav_abc12345",
            "status": "active",
            "event": "relocalizing",
            "instruction": _instruction(
                action="relocalize",
                priority="safety",
                trigger={"type": "immediate"},
            ),
            "routeChanged": False,
            "processedSequence": 1,
        }
        _rejects(payload, NavUpdateResponse)

    def test_arrived_with_active_status_rejected(self):
        payload = {
            "sessionId": "nav_abc12345",
            "status": "active",
            "event": "arrived",
            "instruction": _instruction(action="arrive"),
            "routeChanged": False,
            "processedSequence": 1,
        }
        _rejects(payload, NavUpdateResponse)

    def test_reroute_without_route_rejected(self):
        payload = {
            "sessionId": "nav_abc12345",
            "status": "active",
            "event": "reroute",
            "instruction": _instruction(),
            "routeChanged": False,
            "processedSequence": 1,
        }
        _rejects(payload, NavUpdateResponse)

    def test_terminal_query_with_current_instruction_rejected(self):
        payload = {
            "sessionId": "nav_abc12345",
            "status": "arrived",
            "siteId": "s",
            "mapVersion": "v1",
            "destination": _destination(),
            "currentInstruction": {
                "instructionId": "ins_2",
                "text": "前方路口请右转。",
                "action": "turn_right",
            },
            "updatedAt": "2026-10-23T08:00:03Z",
        }
        _rejects(payload, SessionQueryResponse)

    def test_ended_query_without_reason_rejected(self):
        payload = {
            "sessionId": "nav_abc12345",
            "status": "ended",
            "siteId": "s",
            "mapVersion": "v1",
            "destination": _destination(),
            "updatedAt": "2026-10-23T08:00:03Z",
            "endedAt": "2026-10-23T08:01:00Z",
        }
        _rejects(payload, SessionQueryResponse)

    def test_needs_clarification_with_destination_rejected(self):
        payload = {
            "intentId": "intent_x",
            "intent": "start_navigation",
            "confidence": 0.6,
            "needsClarification": True,
            "destination": _destination(),
            "responseText": "x",
        }
        _rejects(payload, IntentParseResponse)

    def test_unknown_extra_field_rejected(self):
        payload = {
            "siteId": "s",
            "mapVersion": "v1",
            "sequence": 1,
            "collectedAt": "2026-10-23T08:00:00Z",
            "beacons": [
                {"beaconId": "B-001", "rssi": -67, "observedAt": "2026-10-23T08:00:00Z"}
            ],
            "unexpectedField": True,
        }
        _rejects(payload, LocationEstimateRequest)

    def test_bad_session_id_prefix_rejected(self):
        payload = {
            "sessionId": "sess_123",
            "status": "active",
            "mapVersion": "v1",
            "destination": _destination(),
            "route": _route_payload(),
            "createdAt": "2026-10-23T08:00:01Z",
        }
        _rejects(payload, CreateSessionResponse)


# ---------------------------------------------------------------------------
# 契约行为语义
# ---------------------------------------------------------------------------

class TestContractSemantics:
    def test_trigger_distance_valid(self):
        t = Trigger.model_validate({"type": "distance", "valueM": 12.0})
        assert t.valueM == 12.0

    def test_instruction_expires_at_roundtrip(self):
        expires = datetime.now(tz=UTC) + timedelta(seconds=10)
        ins = NavigationInstruction.model_validate(
            {**_instruction(), "expiresAt": expires.isoformat()}
        )
        assert ins.expiresAt == expires

    def test_user_mode_defaults_to_blind(self):
        payload = {
            "siteId": "s",
            "mapVersion": "v1",
            "origin": {
                "x": 1.0,
                "y": 1.0,
                "floorId": "1",
                "confidence": 0.9,
                "observedAt": "2026-10-23T08:00:00Z",
            },
            "destinationId": "p1",
        }
        req = CreateSessionRequest.model_validate(payload)
        assert req.userMode == "blind"

    def test_fallback_types_cover_spec_values(self):
        assert {t.value for t in RouteFallbackType} == {
            "none",
            "best_door",
            "all_doors",
            "wall_fallback",
        }

    def test_place_candidate_shared_between_intent_and_nav(self):
        cand = PlaceCandidate.model_validate(_destination())
        assert cand.placeId == "library-301"
