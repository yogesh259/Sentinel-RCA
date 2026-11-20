"""Redis client and storm-suppression (deduplication) helpers."""

from __future__ import annotations

import hashlib

import redis.asyncio as aioredis

from app.core.config import settings

# Module-level client — initialised lazily via get_redis()
_redis_client: aioredis.Redis | None = None


async def get_redis() -> aioredis.Redis:
    """Return (and cache) an async Redis connection."""
    global _redis_client
    if _redis_client is None:
        _redis_client = aioredis.from_url(
            settings.redis_url, decode_responses=True
        )
    return _redis_client


def compute_fingerprint(exception_type: str, file_name: str, line_number: int) -> str:
    """Deterministic fingerprint hash from (exception_type, file_name, line_number).

    This is pure Python — never delegated to an LLM.
    """
    raw = f"{exception_type}|{file_name}|{line_number}"
    return hashlib.sha256(raw.encode()).hexdigest()


async def check_and_set_dedup(
    fingerprint: str,
    ttl_seconds: int | None = None,
) -> tuple[bool, int]:
    """Storm suppression via Redis SETNX.

    Returns:
        (is_new, duplicate_count)
        - is_new=True  → fingerprint was NOT seen; caller should process it.
        - is_new=False → duplicate; duplicate_count is how many times it's been seen.
    """
    if ttl_seconds is None:
        ttl_seconds = settings.dedup_ttl_seconds

    r = await get_redis()
    key = f"sentinel:dedup:{fingerprint}"
    counter_key = f"sentinel:dedup_count:{fingerprint}"

    # SETNX: returns True if the key was set (i.e. new fingerprint)
    was_set = await r.set(key, "1", nx=True, ex=ttl_seconds)

    if was_set:
        # First occurrence — reset counter
        await r.set(counter_key, 0, ex=ttl_seconds)
        return True, 0
    else:
        # Duplicate — increment counter
        count = await r.incr(counter_key)
        return False, int(count)


async def close_redis() -> None:
    """Cleanly shut down the Redis connection pool."""
    global _redis_client
    if _redis_client is not None:
        await _redis_client.aclose()
        _redis_client = None
