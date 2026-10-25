# -*- coding: utf-8 -*-
"""M8 行为测试：API-06..09 导航会话（docs/24 §8）。"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

CREATE_URL = "/api/v1/nav/sessions"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _origin(x=5.0, y=5.2, floor="1", confidence=0.9) -> dict:
    return {"x": x, "y": y, "floorId": floor, "confidence": confidence, "observedAt": _now()}


#: 幂等测试必须用完全一致的请求体（observedAt 冻结），否则哈希不同触发 409。
_FROZEN_ORIGIN = _origin()


def _create(client, headers, destination="classroom-1f", idem="key-1", origin=None,
            user_mode="blind") -> dict:
    resp = client.post(
        CREATE_URL,
        json={
            "siteId": "school-building-01",
            "mapVersion": "2026-10-01.1",
            "origin": origin or _origin(),
            "destinationId": destination,
            "userMode": user_mode,
            "locale": "zh-CN",
        },
        headers={**headers, "Idempotency-Key": idem},
    )
    return resp


def _update(client, headers, session_id: str, sequence: int, x, y, floor="1",
            quality="reliable", confidence=0.9) -> dict:
    resp = client.post(
        f"{CREATE_URL}/{session_id}/updates",
        json={
            "sequence": sequence,
            "location": {"x": x, "y": y, "floorId": floor, "confidence": confidence,
                         "quality": quality, "observedAt": _now()},
        },
        headers=headers,
    )
    return resp


@pytest.fixture()
def session(client, auth_headers) -> dict:
    resp = _create(client, auth_headers)
    assert resp.status_code == 201, resp.text
    return resp.json()


class TestCreateSession:
    def test_unauthenticated_401(self, client):
        resp = _create(client, {})
        assert resp.status_code == 401

    def test_missing_idempotency_key_400(self, client, auth_headers):
        resp = client.post(
            CREATE_URL,
            json={"siteId": "school-building-01", "mapVersion": "2026-10-01.1",
                  "origin": _origin(), "destinationId": "classroom-1f", "userMode": "blind"},
            headers=auth_headers,
        )
        assert resp.status_code == 400
        assert resp.json()["error"]["code"] == "INVALID_REQUEST"

    def test_unknown_site_404(self, client, auth_headers):
        resp = client.post(
            CREATE_URL,
            json={"siteId": "nope", "mapVersion": "2026-10-01.1", "origin": _origin(),
                  "destinationId": "classroom-1f", "userMode": "blind"},
            headers={**auth_headers, "Idempotency-Key": "k"},
        )
        assert resp.status_code == 404
        assert resp.json()["error"]["code"] == "SITE_NOT_FOUND"

    def test_unknown_place_404(self, client, auth_headers):
        resp = _create(client, auth_headers, destination="no-such-place", idem="k2")
        assert resp.status_code == 404
        assert resp.json()["error"]["code"] == "PLACE_NOT_FOUND"

    @pytest.mark.parametrize("origin", [_origin(confidence=0.3), _origin(floor=None)])
    def test_unreliable_origin_422(self, client, auth_headers, origin):
        resp = _create(client, auth_headers, origin=origin, idem="k3")
        assert resp.status_code == 422
        assert resp.json()["error"]["code"] == "LOCATION_UNRELIABLE"

    def test_success_201(self, client, auth_headers):
        resp = _create(client, auth_headers)
        assert resp.status_code == 201
        body = resp.json()
        assert body["sessionId"].startswith("nav_")
        assert body["status"] == "active"
        assert body["destination"]["placeId"] == "classroom-1f"
        route = body["route"]
        assert route["routeRulesVersion"] == "1.0.0"
        assert route["mapVersion"] == "2026-10-01.1"
        assert route["distanceM"] > 0
        assert len(route["polyline"]) >= 2
        assert len(route["segments"]) >= 1
        assert route["segments"][0]["instructionSequence"] == 1

    def test_blind_cross_floor_uses_elevator(self, client, auth_headers):
        resp = _create(client, auth_headers, destination="library-2f", idem="kf")
        assert resp.status_code == 201
        actions = [s["action"] for s in resp.json()["route"]["segments"]]
        assert "enter_elevator" in actions and "exit_elevator" in actions

    def test_idempotent_replay_same_key(self, client, auth_headers):
        first = _create(client, auth_headers, idem="same-key", origin=_FROZEN_ORIGIN)
        second = _create(client, auth_headers, idem="same-key", origin=_FROZEN_ORIGIN)
        assert second.status_code == 201
        assert second.json()["sessionId"] == first.json()["sessionId"]

    def test_idempotency_conflict_same_key_different_body(self, client, auth_headers):
        _create(client, auth_headers, idem="dup", origin=_FROZEN_ORIGIN)
        resp = _create(client, auth_headers, destination="library-1f", idem="dup",
                       origin=_FROZEN_ORIGIN)
        assert resp.status_code == 409
        assert resp.json()["error"]["code"] == "IDEMPOTENCY_CONFLICT"

    def test_different_key_creates_new_session(self, client, auth_headers):
        first = _create(client, auth_headers, idem="k1").json()
        second = _create(client, auth_headers, idem="k2").json()
        assert first["sessionId"] != second["sessionId"]


class TestNavUpdate:
    """更新坐标几何约束（样例地图很小）：

    - 到达判定：距终点 F1-TR-0002 (10,5) ≤ 5m 即 arrived——非到达用例须用
      距终点 >5m 的坐标（(4,4) 距 6.08m，且距路线首顶点 1.41m 未偏航）；
    - 偏航判定：距全部 polyline 顶点 >8m——(0,14) 满足，且吸附到图书馆
      节点（盲模式可达），可触发重规划。
    """

    def test_unauthenticated_401(self, client, session):
        resp = _update(client, {}, session["sessionId"], 1, 5.1, 5.3)
        assert resp.status_code == 401

    def test_unknown_session_404(self, client, auth_headers):
        resp = _update(client, auth_headers, "nav_nope", 1, 5.0, 5.0)
        assert resp.status_code == 404
        assert resp.json()["error"]["code"] == "SESSION_NOT_FOUND"

    def test_other_users_session_403(self, client, auth_headers, fake, session):
        fake.openid_by_code = {"code-b": "openid-B"}
        other = client.post("/api/v1/auth/wechat", json={"code": "code-b"}).json()
        resp = _update(client, {"Authorization": f"Bearer {other['accessToken']}"},
                       session["sessionId"], 1, 5.1, 5.3)
        assert resp.status_code == 403
        assert resp.json()["error"]["code"] == "FORBIDDEN"

    def test_trusted_update_returns_instruction(self, client, session, auth_headers):
        resp = _update(client, auth_headers, session["sessionId"], 1, 4.0, 4.0)
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "active"
        assert body["event"] == "instruction"
        assert body["routeChanged"] is False
        assert body["processedSequence"] == 1
        assert body["instruction"]["instructionId"]

    def test_untrusted_location_recovering(self, client, session, auth_headers):
        resp = _update(client, auth_headers, session["sessionId"], 1, None, None,
                       quality="lost")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "recovering"
        assert body["event"] == "relocalizing"
        assert body["instruction"]["action"] == "relocalize"
        assert body["instruction"]["priority"] == "safety"

    def test_recovering_then_trusted_returns_active(self, client, session, auth_headers):
        _update(client, auth_headers, session["sessionId"], 1, None, None, quality="lost")
        resp = _update(client, auth_headers, session["sessionId"], 2, 4.0, 4.0)
        assert resp.json()["status"] == "active"
        assert resp.json()["event"] == "instruction"

    def test_stale_sequence_409(self, client, session, auth_headers):
        _update(client, auth_headers, session["sessionId"], 5, 4.0, 4.0)
        resp = _update(client, auth_headers, session["sessionId"], 4, 4.0, 4.0)
        assert resp.status_code == 409
        assert resp.json()["error"]["code"] == "STALE_NAV_UPDATE"

    def test_equal_sequence_replays_processed_response(self, client, session, auth_headers):
        first = _update(client, auth_headers, session["sessionId"], 5, 4.0, 4.0)
        replay = _update(client, auth_headers, session["sessionId"], 5, 4.0, 4.0)
        assert replay.status_code == 200
        assert replay.json()["processedSequence"] == first.json()["processedSequence"]

    def test_arrival_once(self, client, session, auth_headers):
        # 目的地 classroom-1f 节点 F1-TR-0002 (10,5)
        resp = _update(client, auth_headers, session["sessionId"], 7, 10.0, 5.0)
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "arrived"
        assert body["event"] == "arrived"
        assert body["instruction"]["action"] == "arrive"
        assert body["routeChanged"] is False
        later = _update(client, auth_headers, session["sessionId"], 8, 10.0, 5.0)
        assert later.status_code == 409
        assert later.json()["error"]["code"] == "SESSION_NOT_ACTIVE"

    def test_off_route_triggers_reroute(self, client, session, auth_headers):
        resp = _update(client, auth_headers, session["sessionId"], 3, 0.0, 14.0)
        assert resp.status_code == 200
        body = resp.json()
        assert body["event"] == "reroute"
        assert body["routeChanged"] is True
        assert body["route"]["segments"]

    def test_update_ended_session_409(self, client, session, auth_headers):
        client.post(f"{CREATE_URL}/{session['sessionId']}/end",
                    json={"reason": "user_cancelled"}, headers=auth_headers)
        resp = _update(client, auth_headers, session["sessionId"], 2, 4.0, 4.0)
        assert resp.status_code == 409
        assert resp.json()["error"]["code"] == "SESSION_NOT_ACTIVE"


class TestSessionQuery:
    def test_active_query(self, client, session, auth_headers):
        _update(client, auth_headers, session["sessionId"], 1, 4.0, 4.0)
        resp = client.get(f"{CREATE_URL}/{session['sessionId']}", headers=auth_headers)
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "active"
        assert body["siteId"] == "school-building-01"
        assert body["destination"]["placeId"] == "classroom-1f"
        assert body["currentInstruction"]["instructionId"]
        assert body["lastProcessedSequence"] == 1
        assert body["endReason"] is None

    def test_other_user_query_403(self, client, session, auth_headers, fake):
        fake.openid_by_code = {"code-b": "openid-B"}
        other = client.post("/api/v1/auth/wechat", json={"code": "code-b"}).json()
        resp = client.get(f"{CREATE_URL}/{session['sessionId']}",
                          headers={"Authorization": f"Bearer {other['accessToken']}"})
        assert resp.status_code == 403

    def test_unknown_session_404(self, client, auth_headers):
        resp = client.get(f"{CREATE_URL}/nav_nope", headers=auth_headers)
        assert resp.status_code == 404
        assert resp.json()["error"]["code"] == "SESSION_NOT_FOUND"

    def test_arrived_query_terminal_shape(self, client, session, auth_headers):
        _update(client, auth_headers, session["sessionId"], 7, 10.0, 5.0)
        body = client.get(f"{CREATE_URL}/{session['sessionId']}", headers=auth_headers).json()
        assert body["status"] == "arrived"
        assert body["currentInstruction"] is None
        assert body["endReason"] == "arrived"
        assert body["endedAt"]

    def test_ended_query_shape(self, client, session, auth_headers):
        client.post(f"{CREATE_URL}/{session['sessionId']}/end",
                    json={"reason": "user_cancelled"}, headers=auth_headers)
        body = client.get(f"{CREATE_URL}/{session['sessionId']}", headers=auth_headers).json()
        assert body["status"] == "ended"
        assert body["currentInstruction"] is None
        assert body["endReason"] == "user_cancelled"
        assert body["endedAt"]


class TestEndSession:
    def test_end_then_idempotent_replay(self, client, session, auth_headers):
        first = client.post(f"{CREATE_URL}/{session['sessionId']}/end",
                            json={"reason": "user_cancelled"}, headers=auth_headers)
        assert first.status_code == 200
        body = first.json()
        assert body["status"] == "ended"
        assert body["reason"] == "user_cancelled"
        assert body["endedAt"]
        again = client.post(f"{CREATE_URL}/{session['sessionId']}/end",
                            json={"reason": "user_cancelled"}, headers=auth_headers)
        assert again.status_code == 200
        assert again.json() == body

    def test_client_reported_arrival_rejected(self, client, session, auth_headers):
        resp = client.post(f"{CREATE_URL}/{session['sessionId']}/end",
                           json={"reason": "arrived"}, headers=auth_headers)
        assert resp.status_code == 409
        assert resp.json()["error"]["code"] == "SESSION_NOT_ACTIVE"

    def test_end_arrived_session_409(self, client, session, auth_headers):
        _update(client, auth_headers, session["sessionId"], 7, 10.0, 5.0)
        resp = client.post(f"{CREATE_URL}/{session['sessionId']}/end",
                           json={"reason": "user_finished"}, headers=auth_headers)
        assert resp.status_code == 409
        assert resp.json()["error"]["code"] == "SESSION_NOT_ACTIVE"

    def test_unknown_session_404(self, client, auth_headers):
        resp = client.post(f"{CREATE_URL}/nav_nope/end", json={"reason": "user_cancelled"},
                           headers=auth_headers)
        assert resp.status_code == 404
        assert resp.json()["error"]["code"] == "SESSION_NOT_FOUND"
