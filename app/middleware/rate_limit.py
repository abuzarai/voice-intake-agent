"""In-memory sliding-window rate limiter (stdlib only) for Gemini-facing
endpoints: session creation and websocket handshakes. Per-source-IP budget;
unsafe close when a single caller exceeds the window."""

import time
import threading
from collections import defaultdict, deque

from fastapi import Header, HTTPException, Request


class SlidingWindowLimiter:
    def __init__(self, max_requests: int, window_seconds: int):
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self._hits: dict[str, deque] = defaultdict(deque)
        self._lock = threading.Lock()

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        with self._lock:
            q = self._hits[key]
            while q and now - q[0] > self.window_seconds:
                q.popleft()
            if len(q) >= self.max_requests:
                return False
            q.append(now)
            return True


def _source_key(x_forwarded_for: str, request: Request) -> str:
    if x_forwarded_for:
        first = x_forwarded_for.split(",")[0].strip()
        if first:
            return first
    return request.client.host if request.client else "unknown"


# Budgets: generous per-IP ceilings (compose network/gateway egress means many
# users can share one IP) — the goal is abuse throttling, not UX braking.
# 60 sessions + 60 ws handshakes per 60s per source.
session_limiter = SlidingWindowLimiter(max_requests=60, window_seconds=60)
ws_limiter = SlidingWindowLimiter(max_requests=60, window_seconds=60)


def rate_limit_sessions(
    request: Request, x_forwarded_for: str = Header(default="")
) -> None:
    if not session_limiter.allow(_source_key(x_forwarded_for, request)):
        raise HTTPException(status_code=429, detail="Too many requests")


def rate_limit_ws(
    request: Request, x_forwarded_for: str = Header(default="")
) -> None:
    if not ws_limiter.allow(_source_key(x_forwarded_for, request)):
        raise HTTPException(status_code=429, detail="Too many requests")