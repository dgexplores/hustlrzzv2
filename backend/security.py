"""Small, dependency-free controls shared by HTTP and WebSocket entry points."""

from __future__ import annotations

import re
import threading
import time
from collections import defaultdict, deque

from backend import config


class SlidingWindowLimiter:
    # ponytail: process-local limiter; use a shared gateway/Redis limiter when
    # the API runs with multiple replicas.
    def __init__(self):
        self._events: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def allow(self, key: str, limit: int, window_seconds: int = 60) -> bool:
        now = time.monotonic()
        with self._lock:
            events = self._events[key]
            while events and events[0] <= now - window_seconds:
                events.popleft()
            if len(events) >= limit:
                return False
            events.append(now)
            return True


limiter = SlidingWindowLimiter()


def trusted_origin(origin: str | None) -> bool:
    if not origin:
        return False
    if origin in config.CORS_ORIGINS:
        return True
    return bool(config.CORS_ORIGIN_REGEX and re.fullmatch(config.CORS_ORIGIN_REGEX, origin))
