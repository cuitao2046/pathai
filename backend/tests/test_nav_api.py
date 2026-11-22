# -*- coding: utf-8 -*-
"""M8 行为测试：API-06..09 导航会话（docs/24 §8）。

mock 数据使用真实地图包 2026-10-25.1（坐标为局部米制），几何选点：
- 起点=1 层电梯节点 F1-TF-0011（盲模式对全部可达地点已验证）；
- 目的地=F1-RM-0003 资源教室（非卫生间，避免 wall_fallback 触发盲 422）。
"""
from __future__ import annotations

import json
from dataclasses import replace
from datetime import datetime, timedelta, timezone
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
#: 目的地房间（F1-RM-0003）的到达门洞：房间目的地规划终点改为门洞节点，
#: 到达判定也以门洞为锚点（资源教室多个门中离起点最近且可达的是 F1-TD-0014）。
DEST_DOOR = "F1-TD-0014"
#: 到达点 = 目的地房间门洞（距判定终点 0m）。
DEST_P = _COORDS[DEST_DOOR]
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


@pytest.fixture(autouse=True)
def refresh_idempotency_origin_time():
    # Freeze within each test, not for the lifetime of a potentially long test run.
    _FROZEN_ORIGIN['observedAt'] = _now()


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
    def test_publishing_new_map_does_not_invalidate_pinned_session(self, client, auth_headers, app, maps_dir):
        from pathai_backend.map_repository import MapRepository
        first = _create(client, auth_headers).json()
        site = maps_dir / 'school-building-01'
        newer = json.loads((site / f'{REAL_MAP_VERSION}.json').read_text(encoding='utf-8'))
        newer['mapVersion'] = '2099-01-01.1'
        newer['publishedAt'] = '2099-01-01T00:00:00Z'
        (site / '2099-01-01.1.json').write_text(json.dumps(newer), encoding='utf-8')
        app.state.map_repository = MapRepository.load(maps_dir, 'https://api.test')
        update = _update(client, auth_headers, first['sessionId'], 1, *SAFE_P)
        assert update.status_code == 200, update.text
        query = client.get(f"{CREATE_URL}/{first['sessionId']}", headers=auth_headers).json()
        assert query['mapVersion'] == REAL_MAP_VERSION
        # A client that deliberately retains its loaded snapshot may also start a new session.
        second = _create(client, auth_headers, idem='retained-map')
        assert second.status_code == 201, second.text
        assert second.json()['mapVersion'] == REAL_MAP_VERSION

    def test_route_starts_from_actual_origin_not_snapped_node(self, client, auth_headers):
        # 默认起点比入网节点偏 0.2m：路线首点必须是真实位置（不吸附成节点坐标）。
        origin = _origin()
        body = _create(client, auth_headers).json()
        first = body["route"]["polyline"][0]
        assert first["x"] == pytest.approx(origin["x"])
        assert first["y"] == pytest.approx(origin["y"])
        assert first["floorId"] == "1"
        # 距离包含「真实位置 → 入网节点」首段（约 0.2m）。
        assert body["route"]["distanceM"] > 0.2

    def test_stale_origin_cannot_create_session(self, client, auth_headers):
        origin = _origin()
        origin['observedAt'] = '2000-01-01T00:00:00Z'
        assert _create(client, auth_headers, origin=origin).status_code == 422

    def test_slightly_stale_origin_is_advisory_not_rejection(self, client, auth_headers):
        # 起点约 18 s（预览 4.8 s + 概要 + 确认播报会自然超窗）：由客户端负责新鲜度，
        # 服务端不得再 422，只回带观测龄与 stale 提示。
        origin = _origin()
        origin['observedAt'] = (datetime.now(timezone.utc) - timedelta(seconds=18)).isoformat().replace("+00:00", "Z")
        resp = _create(client, auth_headers, origin=origin)
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body['originObservationStale'] is True
        assert 17.0 <= body['originObservationAgeS'] <= 25.0

    def test_fresh_origin_reports_not_stale(self, client, auth_headers):
        body = _create(client, auth_headers).json()
        assert body['originObservationStale'] is False
        assert body['originObservationAgeS'] is not None
        assert body['originObservationAgeS'] < 15.0

    def test_stale_destination_coordinates_cannot_trigger_arrival(self, client, auth_headers, session):
        resp = client.post(f"{CREATE_URL}/{session['sessionId']}/updates", headers=auth_headers, json={
            'sequence': 1, 'location': {'x': DEST_P[0], 'y': DEST_P[1], 'floorId': '1',
            'confidence': 1.0, 'quality': 'reliable', 'observedAt': '2000-01-01T00:00:00Z'},
        })
        assert resp.status_code == 200
        assert resp.json()['status'] == 'recovering'
        assert resp.json()['event'] == 'relocalizing'
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
        assert route["routeRulesVersion"] == "1.1.0"
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

    def test_concurrent_same_key_returns_existing_not_500(
        self, client, auth_headers, monkeypatch
    ):
        """并发重复创建：唯一约束冲突按幂等语义返回既有会话，不得 500。

        首个请求正常落库后，把第二次请求的首次幂等查询强制返回 None，
        复现「两请求都读到无记录 → 后提交者撞唯一约束」的竞态窗口。
        """
        from sqlalchemy.orm import Session as OrmSession

        from pathai_backend.models import NavIdempotency

        first = _create(client, auth_headers, idem="race", origin=_FROZEN_ORIGIN)
        assert first.status_code == 201, first.text

        original_get = OrmSession.get
        skipped = {"done": False}

        def racing_get(db, entity, ident, *args, **kwargs):
            if entity is NavIdempotency and not skipped["done"]:
                skipped["done"] = True
                return None
            return original_get(db, entity, ident, *args, **kwargs)

        monkeypatch.setattr(OrmSession, "get", racing_get)
        second = _create(client, auth_headers, idem="race", origin=_FROZEN_ORIGIN)
        assert second.status_code == 201, second.text
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


PREVIEW_URL = "/api/v1/nav/preview"


class TestPreviewRoute:
    """API-06b：只规划不建会话（客户端预览/概要播报依赖它）。"""

    def _post(self, client, headers, origin=None, destination=DEST_PLACE,
              map_version=REAL_MAP_VERSION):
        return client.post(
            PREVIEW_URL,
            json={
                "siteId": "school-building-01",
                "mapVersion": map_version,
                "origin": origin or _origin(),
                "destinationId": destination,
                "userMode": "blind",
                "locale": "zh-CN",
            },
            headers=headers,
        )

    def test_unauthenticated_401(self, client):
        assert self._post(client, {}).status_code == 401

    def test_returns_route_without_creating_session(self, client, auth_headers):
        from sqlalchemy import func, select

        from pathai_backend.database import session_scope
        from pathai_backend.models import NavSession

        with session_scope() as db:
            before = db.scalar(select(func.count()).select_from(NavSession))

        resp = self._post(client, auth_headers)
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["mapVersion"] == REAL_MAP_VERSION
        assert body["destination"]["placeId"] == DEST_PLACE
        assert len(body["route"]["polyline"]) >= 2
        assert body["route"]["segments"]
        # 无 LLM 概要时明确返回 null，客户端用模板兜底
        assert body["overviewText"] is None
        # 起点新鲜度由客户端判断：服务端只回带观测龄提示
        assert body["originObservationStale"] is False

        with session_scope() as db:
            after = db.scalar(select(func.count()).select_from(NavSession))
        assert after == before  # 预览不得落库

    def test_stale_origin_preview_is_advisory_not_rejection(self, client, auth_headers):
        origin = _origin()
        origin["observedAt"] = (
            datetime.now(timezone.utc) - timedelta(seconds=18)
        ).isoformat().replace("+00:00", "Z")
        resp = self._post(client, auth_headers, origin=origin)
        assert resp.status_code == 200, resp.text
        assert resp.json()["originObservationStale"] is True

    def test_absurd_origin_timestamp_still_rejected(self, client, auth_headers):
        origin = _origin()
        origin["observedAt"] = "2000-01-01T00:00:00Z"
        resp = self._post(client, auth_headers, origin=origin)
        assert resp.status_code == 422
        assert resp.json()["error"]["details"]["reason"] == "observation_time_invalid"

    def test_same_route_as_created_session(self, client, auth_headers):
        preview = self._post(client, auth_headers)
        assert preview.status_code == 200, preview.text
        created = _create(client, auth_headers, idem="preview-parity")
        assert created.status_code == 201, created.text
        preview_route = preview.json()["route"]
        session_route = created.json()["route"]
        assert preview_route["polyline"] == session_route["polyline"]
        assert preview_route["distanceM"] == session_route["distanceM"]
        assert preview_route["hazardLayerStatus"] == session_route["hazardLayerStatus"]

    def test_unreliable_origin_422(self, client, auth_headers):
        resp = self._post(client, auth_headers, origin=_origin(confidence=0.1))
        assert resp.status_code == 422
        assert resp.json()["error"]["code"] == "LOCATION_UNRELIABLE"

    def test_unknown_map_version_404(self, client, auth_headers):
        resp = self._post(client, auth_headers, map_version="1999-01-01.1")
        assert resp.status_code == 404
        assert resp.json()["error"]["code"] == "MAP_NOT_PUBLISHED"


class TestNavUpdate:
    """更新坐标几何约束（真实地图包 2026-10-25.1，局部米制坐标）：

    - 到达判定：房间目的地以门洞节点为锚点，距门洞 ≤ 2.5m 即 arrived（冻结值，
      可在设置页修改）——到达用例用 DEST_P（0m，门洞）；非到达用例须用距锚点
      >2.5m 的坐标（SAFE_P 距门洞约 100m，且即路线首顶点，未偏航）；
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
        # spec 0.3.1：恢复提示必须说明原因与用户可采取的措施
        assert "导航已暂停" in body["instruction"]["text"]
        assert "原地稍候" in body["instruction"]["text"]

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
        # 目的地 F1-RM-0003 的房间门洞 F1-TD-0014（DEST_P 距判定锚点 0m ≤ 2.5m）
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

    def test_arrival_requires_inside_2m_radius(self, client, session, auth_headers):
        # 距目的地节点约 3m：旧 5m 阈值会误判到达，冻结为 2m 后仍应保持导航中。
        resp = _update(client, auth_headers, session["sessionId"], 6, DEST_P[0] + 3.0, DEST_P[1])
        assert resp.status_code == 200
        assert resp.json()["status"] == "active"
        assert resp.json()["event"] == "instruction"

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
