# -*- coding: utf-8 -*-
"""过期行清理任务（评审第二批）：token/配对/管理会话/导航会话与幂等记录。

由 app lifespan 启动周期任务调用 run_purge()；删除走同步 Session，
在线程池中执行，不阻塞事件循环。保留窗口为常量（无需运行时调参）：

- access_tokens / admin_sessions / local_pairings：过期即删（留 1 天缓冲，
  避免时钟轻微偏移时误删仍被缓存的活跃凭证）；
- nav_sessions：终态且 ended_at 超过 7 天才删（保留排查窗口）；
- nav_idempotency_keys：随所属导航会话一并删除（重放窗口与保留期一致）。
"""
from __future__ import annotations

import logging
import time

from sqlalchemy import delete, select

from pathai_backend.database import session_scope
from pathai_backend.models import (
    AccessToken,
    AdminSession,
    LocalPairing,
    NavIdempotency,
    NavSession,
)
from pathai_backend.schemas.nav import TERMINAL_SESSION_STATUSES

logger = logging.getLogger(__name__)

#: 周期任务间隔（秒）。
PURGE_INTERVAL_S = 3600
#: 导航会话/幂等记录终态保留窗口（秒）：7 天。
NAV_RETENTION_S = 7 * 24 * 3600
#: 凭证过期后的删除缓冲（秒）：1 天。
EXPIRY_GRACE_S = 24 * 3600


def purge_expired(now_epoch: int | None = None) -> dict[str, int]:
    """删除过期行，返回各表删除行数（供日志与单测断言）。"""
    now = int(time.time()) if now_epoch is None else now_epoch
    counts: dict[str, int] = {}
    with session_scope() as db:
        counts["access_tokens"] = db.execute(
            delete(AccessToken).where(AccessToken.expires_at_epoch < now - EXPIRY_GRACE_S)
        ).rowcount
        counts["admin_sessions"] = db.execute(
            delete(AdminSession).where(AdminSession.expires_at_epoch < now - EXPIRY_GRACE_S)
        ).rowcount
        counts["local_pairings"] = db.execute(
            delete(LocalPairing).where(LocalPairing.expires_at_epoch < now)
        ).rowcount
        stale_ids = db.execute(
            select(NavSession.session_id).where(
                NavSession.status.in_([s.value for s in TERMINAL_SESSION_STATUSES]),
                NavSession.ended_at_epoch < now - NAV_RETENTION_S,
            )
        ).scalars().all()
        if stale_ids:
            counts["nav_idempotency_keys"] = db.execute(
                delete(NavIdempotency).where(NavIdempotency.session_id.in_(stale_ids))
            ).rowcount
            counts["nav_sessions"] = db.execute(
                delete(NavSession).where(NavSession.session_id.in_(stale_ids))
            ).rowcount
        db.commit()
    return counts


def run_purge() -> dict[str, int]:
    """执行一次清理并按删除规模记日志（后台任务入口，供线程池调用）。"""
    counts = purge_expired()
    total = sum(counts.values())
    if total > 0:
        logger.info("过期行清理完成: %s", counts)
    return counts
