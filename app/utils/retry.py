"""Bounded retry for Gemini transient errors (429 / 5xx), async + sync."""

import asyncio
import logging
import random
import time

logger = logging.getLogger(__name__)

TRANSIENT_STATUSES = {429, 500, 502, 503}
DEFAULT_ATTEMPTS = 3


def _transient_status_of(exc: BaseException):
    status = getattr(exc, "code", None)
    if status is None:
        status = getattr(getattr(exc, "response", None), "status_code", None)
    if status is None:
        status = getattr(getattr(exc, "response", None), "status", None)
    return int(status) if status else None


def _backoff(attempt: int) -> float:
    return min(10.0, 1.0 * (2**attempt)) * (0.5 + random.random())


async def agemini_call_with_retry(coro_factory, attempts: int = DEFAULT_ATTEMPTS):
    for attempt in range(attempts):
        try:
            return await coro_factory()
        except Exception as exc:
            status = _transient_status_of(exc)
            if status not in TRANSIENT_STATUSES or attempt == attempts - 1:
                raise
            await asyncio.sleep(_backoff(attempt))


def gemini_call_with_retry(fn, attempts: int = DEFAULT_ATTEMPTS):
    """Sync variant (STT runs in the request thread pool)."""
    for attempt in range(attempts):
        try:
            return fn()
        except Exception as exc:
            status = _transient_status_of(exc)
            if status not in TRANSIENT_STATUSES or attempt == attempts - 1:
                raise
            time.sleep(_backoff(attempt))