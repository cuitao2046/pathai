# -*- coding: utf-8 -*-
"""地图管理 API 测试（Gate 2-3）：场所浏览、草稿读写、字段规则、发布、stale 与回滚。"""
from __future__ import annotations

import re

import pytest

from pathai_backend.admin.deps import CSRF_HEADER

SITE = "school-building-01"
PLACE_ID = "F1-RM-0002"
BEACON_ID = "BK-01-001"
BASE_VERSION = "2026-10-25.1"
NEW_PASSWORD = "safe-password-1"
VERSION_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}\.\d+$")


@pytest.fixture()
def admin(client):
    """登录并完成首改密，返回 (csrf_token)。"""
    resp = client.post(
        "/api/v1/admin/auth/login", json={"username": "admin", "password": "admin"}
    )
    assert resp.status_code == 200
    csrf = resp.json()["csrfToken"]
    resp = client.post(
        "/api/v1/admin/auth/password",
        json={
            "currentPassword": "admin",
            "newPassword": NEW_PASSWORD,
            "confirmPassword": NEW_PASSWORD,
        },
        headers={CSRF_HEADER: csrf},
    )
    assert resp.status_code == 200
    return resp.json()["csrfToken"]


def get_json(client, path):
    resp = client.get(path)
    assert resp.status_code == 200, resp.text
    return resp.json()


def put_draft(client, csrf, patch, site=SITE):
    return client.put(
        f"/api/v1/admin/sites/{site}/draft", json=patch, headers={CSRF_HEADER: csrf}
    )


def post_action(client, csrf, path):
    return client.post(f"/api/v1/admin/sites/{path}", headers={CSRF_HEADER: csrf})


def publish(client, csrf, site=SITE):
    return post_action(client, csrf, f"{site}/draft/publish")


class TestSitesBrowse:
    def test_sites_list(self, client, admin):
        body = get_json(client, "/api/v1/admin/sites")
        assert body["items"] == [
            {
                "siteId": SITE,
                "latestVersion": BASE_VERSION,
                "publishedAt": "2026-10-25T14:10:01Z",
                "floorCount": 2,
                "placeCount": 56,
                "beaconCount": 61,
            }
        ]

    def test_versions_list(self, client, admin):
        body = get_json(client, f"/api/v1/admin/sites/{SITE}/versions")
        assert body["latestVersion"] == BASE_VERSION
        assert [item["mapVersion"] for item in body["items"]] == [BASE_VERSION]
        assert body["items"][0]["isLatest"] is True

    def test_version_detail(self, client, admin):
        body = get_json(client, f"/api/v1/admin/sites/{SITE}/versions/{BASE_VERSION}")
        places = {place["placeId"]: place for place in body["places"]}
        beacons = {beacon["beaconId"]: beacon for beacon in body["beacons"]}
        assert PLACE_ID in places and BEACON_ID in beacons
        assert "name" in places[PLACE_ID] and "locationDesc" in beacons[BEACON_ID]

    def test_version_detail_place_has_coordinates(self, client, admin):
        body = get_json(client, f"/api/v1/admin/sites/{SITE}/versions/{BASE_VERSION}")
        place = next(place for place in body["places"] if place["placeId"] == PLACE_ID)
        assert isinstance(place["x"], (int, float))
        assert isinstance(place["y"], (int, float))

    def test_floor_geometry(self, client, admin):
        body = get_json(
            client,
            f"/api/v1/admin/sites/{SITE}/versions/{BASE_VERSION}/floors/1/geometry",
        )
        assert body["floorId"] == "1"
        assert body["displayName"]
        assert len(body["bounds"]) == 4
        assert body["rooms"] and body["walls"] and body["columns"]
        assert len(body["rooms"][0]["points"]) >= 3
        assert len(body["rooms"][0]["points"][0]) == 2
        assert len(body["walls"][0]) == 4

    def test_floor_geometry_unknown_floor(self, client, admin):
        resp = client.get(
            f"/api/v1/admin/sites/{SITE}/versions/{BASE_VERSION}/floors/9/geometry"
        )
        assert resp.status_code == 404
        assert resp.json()["error"]["code"] == "FLOOR_NOT_FOUND"

    def test_unknown_version_404(self, client, admin):
        resp = client.get(f"/api/v1/admin/sites/{SITE}/versions/1999-01-01.1")
        assert resp.status_code == 404
        assert resp.json()["error"]["code"] == "MAP_NOT_PUBLISHED"

    def test_unknown_site_404(self, client, admin):
        resp = client.get("/api/v1/admin/sites/no-such-site/versions")
        assert resp.status_code == 404
        assert resp.json()["error"]["code"] == "SITE_NOT_FOUND"

    def test_requires_admin_session(self, client):
        resp = client.get("/api/v1/admin/sites")
        assert resp.status_code == 401


class TestDraftCrud:
    def test_initial_state_no_draft(self, client, admin):
        body = get_json(client, f"/api/v1/admin/sites/{SITE}/draft")
        assert body == {"exists": False, "siteId": SITE, "latestVersion": BASE_VERSION}

    def test_draft_roundtrip(self, client, admin):
        resp = put_draft(client, admin, {"places": {PLACE_ID: {"name": "女卫生间（新）"}}})
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["baseVersion"] == BASE_VERSION
        assert body["stale"] is False
        by_id = {place["placeId"]: place for place in body["places"]}
        assert by_id[PLACE_ID]["name"] == "女卫生间（新）"
        # 未修改的地点保持原值
        others = [p for pid, p in by_id.items() if pid != PLACE_ID]
        assert all(p["name"] for p in others)

        reloaded = get_json(client, f"/api/v1/admin/sites/{SITE}/draft")
        assert reloaded["patch"] == {"places": {PLACE_ID: {"name": "女卫生间（新）"}}, "beacons": {}}

    def test_beacon_desc_roundtrip(self, client, admin):
        resp = put_draft(client, admin, {"beacons": {BEACON_ID: {"locationDesc": "一楼大厅立柱旁"}}})
        assert resp.status_code == 200
        by_id = {b["beaconId"]: b for b in resp.json()["beacons"]}
        assert by_id[BEACON_ID]["locationDesc"] == "一楼大厅立柱旁"
        # 只读字段照常返回（不因 patch 丢失）
        assert by_id[BEACON_ID]["mountType"] is not None or by_id[BEACON_ID]["semanticTag"] is not None

    def test_empty_desc_allowed(self, client, admin):
        resp = put_draft(client, admin, {"beacons": {BEACON_ID: {"locationDesc": ""}}})
        assert resp.status_code == 200

    def test_unknown_place_id(self, client, admin):
        resp = put_draft(client, admin, {"places": {"NOPE": {"name": "x"}}})
        assert resp.status_code == 422
        assert resp.json()["error"]["code"] == "PLACE_NOT_FOUND"

    def test_unknown_beacon_id(self, client, admin):
        resp = put_draft(client, admin, {"beacons": {"NOPE": {"locationDesc": "x"}}})
        assert resp.status_code == 422
        assert resp.json()["error"]["code"] == "BEACON_NOT_FOUND"

    def test_extra_patch_fields_rejected(self, client, admin):
        resp = put_draft(client, admin, {"mapVersion": "hacked"})
        assert resp.status_code == 400
        assert resp.json()["error"]["code"] == "INVALID_REQUEST"
        resp = put_draft(client, admin, {"places": {PLACE_ID: {"nodeId": "x"}}})
        assert resp.status_code == 400

    def test_reset(self, client, admin):
        put_draft(client, admin, {"places": {PLACE_ID: {"name": "临时"}}})
        resp = post_action(client, admin, f"{SITE}/draft/reset")
        assert resp.status_code == 200
        body = get_json(client, f"/api/v1/admin/sites/{SITE}/draft")
        assert body["exists"] is False


class TestDraftFieldRules:
    def test_blank_name_rejected(self, client, admin):
        resp = put_draft(client, admin, {"places": {PLACE_ID: {"name": "   "}}})
        assert resp.status_code == 422
        error = resp.json()["error"]
        assert error["code"] == "INVALID_FIELD_VALUE"
        assert error["details"] == {"objectId": PLACE_ID, "field": "name"}

    def test_name_too_long_rejected(self, client, admin):
        resp = put_draft(client, admin, {"places": {PLACE_ID: {"name": "长" * 129}}})
        assert resp.status_code == 422
        assert resp.json()["error"]["code"] == "INVALID_FIELD_VALUE"

    def test_name_trimmed_on_save(self, client, admin):
        put_draft(client, admin, {"places": {PLACE_ID: {"name": "  资源教室  "}}})
        body = get_json(client, f"/api/v1/admin/sites/{SITE}/draft")
        assert body["patch"]["places"][PLACE_ID]["name"] == "资源教室"

    def test_alias_dedup_casefold(self, client, admin):
        patch = {"places": {PLACE_ID: {"aliases": [" Toilet ", "toilet", "TOILET", "厕所"]}}}
        put_draft(client, admin, patch)
        body = get_json(client, f"/api/v1/admin/sites/{SITE}/draft")
        assert body["patch"]["places"][PLACE_ID]["aliases"] == ["Toilet", "厕所"]

    def test_too_many_aliases_rejected(self, client, admin):
        aliases = [f"别名{i:02d}" for i in range(33)]
        resp = put_draft(client, admin, {"places": {PLACE_ID: {"aliases": aliases}}})
        assert resp.status_code == 422
        assert resp.json()["error"]["details"]["field"] == "aliases"

    def test_beacon_desc_too_long_rejected(self, client, admin):
        resp = put_draft(client, admin, {"beacons": {BEACON_ID: {"locationDesc": "x" * 201}}})
        assert resp.status_code == 422
        assert resp.json()["error"]["details"]["field"] == "locationDesc"


class TestValidate:
    def test_validate_ok(self, client, admin):
        put_draft(client, admin, {"places": {PLACE_ID: {"name": "新名字"}}})
        resp = post_action(client, admin, f"{SITE}/draft/validate")
        assert resp.status_code == 200
        assert resp.json() == {"ok": True, "stale": False, "issues": []}

    def test_validate_without_draft_404(self, client, admin):
        resp = post_action(client, admin, f"{SITE}/draft/validate")
        assert resp.status_code == 404
        assert resp.json()["error"]["code"] == "DRAFT_NOT_FOUND"


class TestPublish:
    def test_publish_creates_new_version(self, client, admin, auth_headers):
        put_draft(client, admin, {"places": {PLACE_ID: {"name": "女卫生间（改）"}}})
        resp = publish(client, admin)
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["baseVersion"] == BASE_VERSION
        assert VERSION_PATTERN.match(body["mapVersion"])
        assert body["mapVersion"] != BASE_VERSION
        new_version = body["mapVersion"]

        sites = get_json(client, "/api/v1/admin/sites")["items"]
        assert sites[0]["latestVersion"] == new_version

        versions = get_json(client, f"/api/v1/admin/sites/{SITE}/versions")
        assert [item["mapVersion"] for item in versions["items"]] == [new_version, BASE_VERSION]
        assert [item["isLatest"] for item in versions["items"]] == [True, False]

        detail = get_json(client, f"/api/v1/admin/sites/{SITE}/versions/{new_version}")
        by_id = {place["placeId"]: place for place in detail["places"]}
        assert by_id[PLACE_ID]["name"] == "女卫生间（改）"

        # 旧版本仍完整可读
        old = get_json(client, f"/api/v1/admin/sites/{SITE}/versions/{BASE_VERSION}")
        old_by_id = {place["placeId"]: place for place in old["places"]}
        assert old_by_id[PLACE_ID]["name"] != "女卫生间（改）"

    def test_publish_visible_in_public_api(self, client, admin, auth_headers):
        put_draft(client, admin, {"beacons": {BEACON_ID: {"locationDesc": "校长办公室门口"}}})
        resp = publish(client, admin)
        assert resp.status_code == 200
        new_version = resp.json()["mapVersion"]

        latest = client.get(
            f"/api/v1/sites/{SITE}/map", headers=auth_headers
        ).json()
        assert latest["mapVersion"] == new_version
        old = client.get(
            f"/api/v1/sites/{SITE}/map",
            params={"version": BASE_VERSION},
            headers=auth_headers,
        ).json()
        assert old["mapVersion"] == BASE_VERSION

    def test_publish_without_draft_404(self, client, admin):
        resp = publish(client, admin)
        assert resp.status_code == 404
        assert resp.json()["error"]["code"] == "DRAFT_NOT_FOUND"

    def test_publish_consumes_draft(self, client, admin):
        put_draft(client, admin, {"places": {PLACE_ID: {"name": "发布后清理"}}})
        resp = publish(client, admin)
        assert resp.status_code == 200
        draft = get_json(client, f"/api/v1/admin/sites/{SITE}/draft")
        assert draft == {
            "exists": False,
            "siteId": SITE,
            "latestVersion": resp.json()["mapVersion"],
        }

    def test_stale_draft_blocks_publish(self, client, admin):
        put_draft(client, admin, {"places": {PLACE_ID: {"name": "第一轮"}}})
        # 发布成功会消费草稿，因此用回滚发布新版本制造「草稿基准过期」
        resp = client.post(
            f"/api/v1/admin/sites/{SITE}/versions/{BASE_VERSION}/rollback",
            headers={CSRF_HEADER: admin},
        )
        assert resp.status_code == 200
        second_version = resp.json()["mapVersion"]

        # 草稿基准仍是旧版本 → stale，发布被拒
        draft = get_json(client, f"/api/v1/admin/sites/{SITE}/draft")
        assert draft["stale"] is True
        put_draft(client, admin, {"places": {PLACE_ID: {"name": "第二轮"}}})
        resp = publish(client, admin)
        assert resp.status_code == 409
        assert resp.json()["error"]["code"] == "DRAFT_BASE_STALE"

        # 重置后基于新 latest 重建草稿，可发布
        post_action(client, admin, f"{SITE}/draft/reset")
        put_draft(client, admin, {"places": {PLACE_ID: {"name": "第二轮"}}})
        resp = publish(client, admin)
        assert resp.status_code == 200
        assert resp.json()["baseVersion"] == second_version

    def test_validate_reports_stale(self, client, admin):
        put_draft(client, admin, {"places": {PLACE_ID: {"name": "第一轮"}}})
        resp = client.post(
            f"/api/v1/admin/sites/{SITE}/versions/{BASE_VERSION}/rollback",
            headers={CSRF_HEADER: admin},
        )
        assert resp.status_code == 200
        resp = post_action(client, admin, f"{SITE}/draft/validate")
        assert resp.status_code == 200
        assert resp.json()["ok"] is True
        assert resp.json()["stale"] is True


class TestRollback:
    def test_rollback_restores_old_content_as_new_version(self, client, admin, auth_headers):
        put_draft(client, admin, {"places": {PLACE_ID: {"name": "改名之后"}}})
        assert publish(client, admin).status_code == 200

        resp = client.post(
            f"/api/v1/admin/sites/{SITE}/versions/{BASE_VERSION}/rollback",
            headers={CSRF_HEADER: admin},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["rolledBackFrom"] == BASE_VERSION
        assert VERSION_PATTERN.match(body["mapVersion"])
        restored_version = body["mapVersion"]

        sites = get_json(client, "/api/v1/admin/sites")["items"]
        assert sites[0]["latestVersion"] == restored_version

        detail = get_json(client, f"/api/v1/admin/sites/{SITE}/versions/{restored_version}")
        by_id = {place["placeId"]: place for place in detail["places"]}
        assert by_id[PLACE_ID]["name"] != "改名之后"  # 恢复为基线内容

        latest = client.get(f"/api/v1/sites/{SITE}/map", headers=auth_headers).json()
        assert latest["mapVersion"] == restored_version

    def test_rollback_unknown_version_404(self, client, admin):
        resp = client.post(
            f"/api/v1/admin/sites/{SITE}/versions/1999-01-01.1/rollback",
            headers={CSRF_HEADER: admin},
        )
        assert resp.status_code == 404
        assert resp.json()["error"]["code"] == "MAP_NOT_PUBLISHED"
