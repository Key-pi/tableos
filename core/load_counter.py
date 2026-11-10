"""Requests and bot updates per minute for the platform's load page (PQ-051).

Each process counts in memory and adds its counts to Redis at most every
``FLUSH_SECONDS`` — one short pipelined write to the hash of the minute of
the flush (``botera:load:<minute>``, kept ``KEEP_SECONDS``). The snapshot
task (``apps/analytics/system_load.py``) reads the last whole minute. Never
in the way of a request: with Redis down the counts are dropped.

Off in tests unless a test turns it on (``LOAD_COUNTER_ENABLED``).
"""

from __future__ import annotations

import logging
import threading
import time
from collections import defaultdict
from collections.abc import Callable

from django.conf import settings

logger = logging.getLogger(__name__)

FLUSH_SECONDS = 10
KEEP_SECONDS = 15 * 60
KEY = "botera:load:{minute}"
# Requests not counted: files and the health check (Docker asks it often).
SKIPPED_PREFIXES = ("/static/", "/media/", "/health/", "/favicon.ico")


def _redis_client():
    import redis

    return redis.Redis.from_url(
        settings.REDIS_URL, socket_timeout=0.5, socket_connect_timeout=0.5
    )


class LoadCounter:
    def __init__(self, client: Callable | None = None):
        self._client_factory = client or _redis_client
        self._client = None
        self._pending: dict[str, int] = defaultdict(int)
        self._last_flush = time.time()
        self._lock = threading.Lock()

    def record(self, kind: str, seconds: float) -> None:
        with self._lock:
            self._pending[kind] += 1
            self._pending[f"{kind}_ms"] += round(seconds * 1000)

    def pending(self) -> dict[str, int]:
        return dict(self._pending)

    def due(self, now: float | None = None) -> bool:
        now = time.time() if now is None else now
        return bool(self._pending) and now - self._last_flush >= FLUSH_SECONDS

    def flush(self, now: float | None = None) -> None:
        """Adds the counts to the minute's hash; drops them on any error."""

        now = time.time() if now is None else now
        with self._lock:
            counts, self._pending = self._pending, defaultdict(int)
            self._last_flush = now
        if not counts:
            return
        minute = int(now) // 60
        key = KEY.format(minute=minute)
        try:
            if self._client is None:
                self._client = self._client_factory()
            pipe = self._client.pipeline()
            for field, amount in counts.items():
                pipe.hincrby(key, field, amount)
            pipe.expire(key, KEEP_SECONDS)
            pipe.execute()
        except Exception:  # noqa: BLE001 — a counter must never break a request
            logger.debug("Load counts dropped: Redis is not reachable", exc_info=True)
            self._client = None


counter = LoadCounter()


def enabled() -> bool:
    return getattr(settings, "LOAD_COUNTER_ENABLED", False)


def record(kind: str, seconds: float) -> bool:
    """Counts one request or update; True when it is time to flush."""

    if not enabled():
        return False
    counter.record(kind, seconds)
    return counter.due()


def read_minute(minute: int, client=None) -> dict[str, int]:
    """The counts of one minute (empty when unknown)."""

    try:
        raw = (client or _redis_client()).hgetall(KEY.format(minute=minute))
    except Exception:  # noqa: BLE001
        return {}
    return {
        (name.decode() if isinstance(name, bytes) else name): int(value)
        for name, value in raw.items()
    }
