# -*- coding: utf-8 -*-
"""M8 行为测试：API-06..09 导航会话（docs/24 §8）。

mock 数据使用真实地图包 2026-10-25.1（坐标为局部米制），几何选点：
- 起点=1 层电梯节点 F1-TF-0011（盲模式对全部可达地点已验证）；
- 目的地=F1-RM-0003 资源教室（非卫生间，避免 wall_fallback 触发盲 422）。
"""
from __future__ import annotations

import json
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

import pytest

CREATE_URL = "/api/v1/nav/sessions"

#: 真实地图版本。
REAL_MAP_VERSION = "2026-10-25.1"

#: 真实地图节点坐标（模块级读一次）。
_REAL_MAP_FILE = (
    Path(__file__).resolve().parent.parent
    / "maps_data" / "school-building-01" / f"{REAL_MAP_VERSION}.json"
)
_RAW = json.loads(_REAL_MAP_FILE.read_text(encoding="utf-8"))
_COORDS = {
    n["id"]: tuple(n["coordinates"])
    for f in _RAW["floors"].values()
    for n in f["topology"]["nodes"]
}

#: 起点节点（1 层电梯）与目的地节点（资源教室）。
ORIGIN_NODE = "F1-TF-0011"
DEST_PLACE = "F1-RM-0003"
DEST_NODE = "F1-TR-0003"
#: 偏航点：固化时距起点→目的地路线全部顶点 11.12m（>8m），
#: 且从其吸附节点盲模式可达目的地（重规划可成功）。
OFFROUTE_NODE = "F1-TR-0001"

#: 非到达/常规更新点 = 起点节点（路线上，距目的地 69.48m > 5m）。
SAFE_P = _COORDS[ORIGIN_NODE]
#: 到达点 = 目的地节点（距判定终点 0m ≤ 5m）。
DEST_P = _COORDS[DEST_NODE]
#: 偏航更新点。
OFFROUTE_P = _COORDS[OFFROUTE_NODE]

#: 几何前提冻结（防止地图再发布静默破坏选点语义）。
assert ((SAFE_P[0] - DEST_P[0]) ** 2 + (SAFE_P[1] - DEST_P[1]) ** 2) ** 0.5 > 5.0


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _origin(x=None, y=None, floor="1", confidence=0.9) -> dict:
    # 默认起点：电梯节点 + 0.2m 定位噪声偏移（吸附回 F1-TF-0011）
    if x is None:
        x = SAFE_P[0] + 0.2
    if y is None:
        y = SAFE_P[1]
    return {"x": x, "y": y, "floorId": floor, "confidence": confidence, "observedAt": _now()}


#: 幂等测试必须用完全一致的请求体（observedAt 冻结），否则哈希不同触发 409。
_FROZEN_ORIGIN = _origin()


def _create(client, headers, destination=DEST_PLACE, idem="key-1", origin=None,
            user_mode="blind") -> dict:
    resp = client.post(
        CREATE_URL,
        json={
            "siteId": "school-building-01",
            "mapVersion": REAL_MAP_VERSION,
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
            json={"siteId": "school-building-01", "mapVersion": REAL_MAP_VERSION,
                  "origin": _origin(), "destinationId": DEST_PLACE, "userMode": "blind"},
            headers=auth_headers,
        )
        assert resp.status_code == 400
        assert resp.json()["error"]["code"] == "INVALID_REQUEST"

    def test_unknown_site_404(self, client, auth_headers):
        resp = client.post(
            CREATE_URL,
            json={"siteId": "nope", "mapVersion": REAL_MAP_VERSION, "origin": _origin(),
                  "destinationId": DEST_PLACE, "userMode": "blind"},
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
        assert body["paramsVersion"] == "unset"
        assert body["destination"]["placeId"] == DEST_PLACE
        route = body["route"]
        assert route["routeRulesVersion"] == "1.0.0"
        assert route["mapVersion"] == REAL_MAP_VERSION
        assert route["distanceM"] > 0
        assert len(route["polyline"]) >= 2
        assert len(route["segments"]) >= 1
        assert route["segments"][0]["instructionSequence"] == 1

    def test_blind_cross_floor_uses_elevator(self, client, auth_headers):
        resp = _create(client, auth_headers, destination="F2-RM-0003", idem="kf")
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
        resp = _create(client, auth_headers, destination="F1-RM-0008", idem="dup",
                       origin=_FROZEN_ORIGIN)
        assert resp.status_code == 409
        assert resp.json()["error"]["code"] == "IDEMPOTENCY_CONFLICT"

    def test_different_key_creates_new_session(self, client, auth_headers):
        first = _create(client, auth_headers, idem="k1").json()
        second = _create(client, auth_headers, idem="k2").json()
        assert first["sessionId"] != second["sessionId"]

    def test_params_version_frozen_at_session_creation(self, client, auth_headers, app, settings):
        # 创建时固化：之后服务端参数版本变化，会话查询仍返回创建时的版本
        app.state.settings = replace(settings, location_params_version="loc-freeze.1")
        created = _create(client, auth_headers, idem="freeze").json()
        assert created["paramsVersion"] == "loc-freeze.1"

        app.state.settings = replace(settings, location_params_version="loc-freeze.2")
        query = client.get(
            f"{CREATE_URL}/{created['sessionId']}", headers=auth_headers
        ).json()
        assert query["paramsVersion"] == "loc-freeze.1"


class TestNavUpdate:
    """更新坐标几何约束（真实地图包 2026-10-25.1，局部米制坐标）：

    - 到达判定：距目的地节点 F1-TR-0003 ≤ 5m 即 arrived——到达用例用 DEST_P
      （0m）；非到达用例须用距终点 >5m 的坐标（SAFE_P 距 69.48m，且即路线
      首顶点，未偏航）；
    - 偏航判定：距全部 polyline 顶点 >8m——OFFROUTE_P 固化时距路线 11.12m，
      且吸附节点 F1-TR-0001 盲模式可达目的地，可触发重规划。
    """

    def test_unauthenticated_401(self, client, session):
        resp = _update(client, {}, session["sessionId"], 1, *SAFE_P)
        assert resp.status_code == 401

    def test_unknown_session_404(self, client, auth_headers):
        resp = _update(client, auth_headers, "nav_nope", 1, *SAFE_P)
        assert resp.status_code == 404
        assert resp.json()["error"]["code"] == "SESSION_NOT_FOUND"

    def test_other_users_session_403(self, client, auth_headers, fake, session):
        fake.openid_by_code = {"code-b": "openid-B"}
        other = client.post("/api/v1/auth/wechat", json={"code": "code-b"}).json()
        resp = _update(client, {"Authorization": f"Bearer {other['accessToken']}"},
                       session["sessionId"], 1, *SAFE_P)
        assert resp.status_code == 403
        assert resp.json()["error"]["code"] == "FORBIDDEN"

    def test_trusted_update_returns_instruction(self, client, session, auth_headers):
        resp = _update(client, auth_headers, session["sessionId"], 1, *SAFE_P)
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
        resp = _update(client, auth_headers, session["sessionId"], 2, *SAFE_P)
        assert resp.json()["status"] == "active"
        assert resp.json()["event"] == "instruction"

    def test_stale_sequence_409(self, client, session, auth_headers):
        _update(client, auth_headers, session["sessionId"], 5, *SAFE_P)
        resp = _update(client, auth_headers, session["sessionId"], 4, *SAFE_P)
        assert resp.status_code == 409
        assert resp.json()["error"]["code"] == "STALE_NAV_UPDATE"

    def test_equal_sequence_replays_processed_response(self, client, session, auth_headers):
        first = _update(client, auth_headers, session["sessionId"], 5, *SAFE_P)
        replay = _update(client, auth_headers, session["sessionId"], 5, *SAFE_P)
        assert replay.status_code == 200
        assert replay.json()["processedSequence"] == first.json()["processedSequence"]

    def test_arrival_once(self, client, session, auth_headers):
        # 目的地 F1-RM-0003 节点 F1-TR-0003（DEST_P 距判定终点 0m ≤ 5m）
        resp = _update(client, auth_headers, session["sessionId"], 7, *DEST_P)
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "arrived"
        assert body["event"] == "arrived"
        assert body["instruction"]["action"] == "arrive"
        assert body["routeChanged"] is False
        later = _update(client, auth_headers, session["sessionId"], 8, *DEST_P)
        assert later.status_code == 409
        assert later.json()["error"]["code"] == "SESSION_NOT_ACTIVE"

    def test_off_route_triggers_reroute(self, client, session, auth_headers):
        resp = _update(client, auth_headers, session["sessionId"], 3, *OFFROUTE_P)
        assert resp.status_code == 200
        body = resp.json()
        assert body["event"] == "reroute"
        assert body["routeChanged"] is True
        assert body["route"]["segments"]

    def test_update_ended_session_409(self, client, session, auth_headers):
        client.post(f"{CREATE_URL}/{session['sessionId']}/end",
                    json={"reason": "user_cancelled"}, headers=auth_headers)
        resp = _update(client, auth_headers, session["sessionId"], 2, *SAFE_P)
        assert resp.status_code == 409
        assert resp.json()["error"]["code"] == "SESSION_NOT_ACTIVE"


class TestSessionQuery:
    def test_active_query(self, client, session, auth_headers):
        _update(client, auth_headers, session["sessionId"], 1, *SAFE_P)
        resp = client.get(f"{CREATE_URL}/{session['sessionId']}", headers=auth_headers)
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "active"
        assert body["siteId"] == "school-building-01"
        assert body["paramsVersion"] == "unset"
        assert body["destination"]["placeId"] == DEST_PLACE
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
        _update(client, auth_headers, session["sessionId"], 7, *DEST_P)
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
        _update(client, auth_headers, session["sessionId"], 7, *DEST_P)
        resp = client.post(f"{CREATE_URL}/{session['sessionId']}/end",
                           json={"reason": "user_finished"}, headers=auth_headers)
        assert resp.status_code == 409
        assert resp.json()["error"]["code"] == "SESSION_NOT_ACTIVE"

    def test_unknown_session_404(self, client, auth_headers):
        resp = client.post(f"{CREATE_URL}/nav_nope/end", json={"reason": "user_cancelled"},
                           headers=auth_headers)
        assert resp.status_code == 404
        assert resp.json()["error"]["code"] == "SESSION_NOT_FOUND"
