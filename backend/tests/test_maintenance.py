# -*- coding: utf-8 -*-
"""过期行清理任务单测（评审第二批）。

覆盖：过期凭证/配对/管理会话删除与未过期保留；终态导航会话超保留窗
连同幂等记录一并删除；活跃会话与新终态会话保留。
"""
from __future__ import annotations

import time

from pathai_backend.database import get_engine, session_scope
from pathai_backend.maintenance import (
    EXPIRY_GRACE_S,
    NAV_RETENTION_S,
    purge_expired,
)
from pathai_backend.models import (
    AccessToken,
    AdminSession,
    AnonymousSubject,
    LocalPairing,
    NavIdempotency,
    NavSession,
)


def _subject(db, openid_hash: str) -> AnonymousSubject:
    row = AnonymousSubject(
        openid_hash=openid_hash,
        user_id=f"user-{openid_hash[:8]}",
        created_at_epoch=1,
    )
    db.add(row)
    db.flush()
    return row


def _nav_session(db, subject_id: int, session_id: str, status: str, ended_at: int | None):
    db.add(
        NavSession(
            session_id=session_id,
            subject_id=subject_id,
            site_id="school-building-01",
            map_version="2026-10-25.1",
            destination_place_id="p-1",
            destination_name="目的地",
            destination_floor_id="F1",
            user_mode="visual_impairment",
            status=status,
            route_json='{"segments":[]}',
            current_index=0,
            last_sequence=1,
            location_algorithm_version="v1",
            route_rules_version="v1",
            created_at_epoch=ended_at - 600 if ended_at else 1,
            updated_at_epoch=ended_at or 1,
            ended_at_epoch=ended_at,
            end_reason="arrived" if status == "arrived" else None,
        )
    )
    db.add(
        NavIdempotency(
            subject_id=subject_id,
            idempotency_key=f"key-{session_id}",
            request_hash="0" * 64,
            session_id=session_id,
            response_json="{}",
        )
    )


def test_purge_deletes_expired_and_keeps_fresh(app):
    now = int(time.time())
    with session_scope() as db:
        s = _subject(db, "hash-purge-1")
        db.add_all(
            [
                AccessToken(
                    token_hash="expired-token",
                    subject_id=s.id,
                    created_at_epoch=now - 9999,
                    expires_at_epoch=now - EXPIRY_GRACE_S - 10,  # 过期且超缓冲
                ),
                AccessToken(
                    token_hash="fresh-token",
                    subject_id=s.id,
                    created_at_epoch=now,
                    expires_at_epoch=now + 3600,
                ),
                AccessToken(
                    token_hash="grace-token",  # 刚过期，仍在缓冲期
                    subject_id=s.id,
                    created_at_epoch=now - 7200,
                    expires_at_epoch=now - 10,
                ),
                AdminSession(
                    token_hash="expired-admin",
                    csrf_hash="c1",
                    created_at_epoch=now - 9999,
                    expires_at_epoch=now - EXPIRY_GRACE_S - 10,
                ),
                AdminSession(
                    token_hash="fresh-admin",
                    csrf_hash="c2",
                    created_at_epoch=now,
                    expires_at_epoch=now + 3600,
                ),
                LocalPairing(
                    pair_id="expired-pair",
                    key_b64="k" * 43,
                    status="paired",
                    expires_at_epoch=now - 1,
                    created_at_epoch=now - 7200,
                ),
                LocalPairing(
                    pair_id="fresh-pair",
                    key_b64="k" * 43,
                    status="paired",
                    expires_at_epoch=now + 3600,
                    created_at_epoch=now,
                ),
            ]
        )
        db.commit()

    counts = purge_expired(now)

    assert counts["access_tokens"] == 1
    assert counts["admin_sessions"] == 1
    assert counts["local_pairings"] == 1
    with session_scope() as db:
        remaining_tokens = {r for (r,) in db.query(AccessToken.token_hash)}
        assert remaining_tokens == {"fresh-token", "grace-token"}
        remaining_admin = {r for (r,) in db.query(AdminSession.token_hash)}
        assert remaining_admin == {"fresh-admin"}
        remaining_pairs = {r for (r,) in db.query(LocalPairing.pair_id)}
        assert remaining_pairs == {"fresh-pair"}


def test_purge_removes_stale_terminal_sessions_with_idempotency(app):
    now = int(time.time())
    with session_scope() as db:
        s = _subject(db, "hash-purge-2")
        # 超保留窗的终态会话：连同幂等记录删除
        _nav_session(db, s.id, "sess-old", "arrived", now - NAV_RETENTION_S - 100)
        # 新终态会话：保留
        _nav_session(db, s.id, "sess-recent", "ended", now - 60)
        # 活跃会话：保留
        _nav_session(db, s.id, "sess-active", "active", None)
        db.commit()

    counts = purge_expired(now)

    assert counts["nav_sessions"] == 1
    assert counts["nav_idempotency_keys"] == 1
    with session_scope() as db:
        remaining = {r for (r,) in db.query(NavSession.session_id)}
        assert remaining == {"sess-recent", "sess-active"}
        remaining_keys = {r for (r,) in db.query(NavIdempotency.idempotency_key)}
        assert remaining_keys == {"key-sess-recent", "key-sess-active"}


def test_purge_on_empty_db_is_noop(app):
    assert purge_expired() == {
        "access_tokens": 0,
        "admin_sessions": 0,
        "local_pairings": 0,
    }
    assert get_engine() is not None
