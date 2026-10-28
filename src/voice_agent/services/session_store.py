# -*- coding: utf-8 -*-
"""待澄清会话状态（spec §5-D4）：进程内 TTL，接口预留给后续持久化。

仅保存「上一轮反问挂起的候选」，不保存音频与用户身份；
默认 180 秒过期，过期后新问题按无上下文重新解析。
"""
from __future__ import annotations

import time
from dataclasses import dataclass

DEFAULT_TTL_SECONDS = 180.0
DEFAULT_MAX_SESSIONS = 1024


@dataclass(frozen=True)
class _Entry:
    items: tuple[dict, ...]
    expires_at: float


class SessionStateStore:
    def __init__(
        self,
        ttl_seconds: float = DEFAULT_TTL_SECONDS,
        max_sessions: int = DEFAULT_MAX_SESSIONS,
        clock=time.monotonic,
    ):
        self.ttl_seconds = ttl_seconds
        self.max_sessions = max_sessions
        self._clock = clock
        self._entries: dict[str, _Entry] = {}

    def get(self, key: str) -> tuple[dict, ...]:
        entry = self._entries.get(key)
        if entry is None:
            return ()
        if entry.expires_at <= self._clock():
            self._entries.pop(key, None)
            return ()
        return entry.items

    def put(self, key: str, items: list[dict] | tuple[dict, ...]) -> None:
        if not items:
            self.clear(key)
            return
        if len(self._entries) >= self.max_sessions:
            self._prune()
        self._entries[key] = _Entry(
            items=tuple(items), expires_at=self._clock() + self.ttl_seconds
        )

    def clear(self, key: str) -> None:
        self._entries.pop(key, None)

    def _prune(self) -> None:
        now = self._clock()
        expired = [key for key, entry in self._entries.items() if entry.expires_at <= now]
        for key in expired:
            self._entries.pop(key, None)
