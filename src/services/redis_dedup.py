"""Redis client, storm-suppression (deduplication), and rate-limiting helpers."""


import hashlib
from datetime import UTC, datetime

import redis.asyncio as aioredis

from src.core.config import config

DEFAULT_STORM_DEDUP_TTL_SECONDS: int = 1800  # 30-minute alert storm suppression window

# Module-level client — initialised lazily via get_redis()
_redis_client: aioredis.Redis | None = None


async def get_redis() -> aioredis.Redis:
    """Return (and cache) an async Redis connection."""
    global _redis_client
    if _redis_client is None:
        url = (
            config.redis_url.get_secret_value()
            if hasattr(config.redis_url, "get_secret_value")
            else str(config.redis_url)
        )
        _redis_client = aioredis.from_url(url, decode_responses=True)
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
        ttl_seconds = DEFAULT_STORM_DEDUP_TTL_SECONDS

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


async def check_rate_limit(client_ip: str) -> bool:
    """Check per-IP/hour and global/day rate limits using INCR + EXPIRE.

    Returns True if the request is within limits.
    Returns False if rate limits are exceeded (caller should reject with HTTP 429).
    """
    r = await get_redis()

    # Per-IP hourly limit
    ip_key = f"sentinel:rate:ip:{client_ip}"
    ip_count = await r.incr(ip_key)
    if ip_count == 1:
        await r.expire(ip_key, 3600)
    if ip_count > config.rate_limit_per_hour:
        return False

    # Global daily limit
    today = datetime.now(UTC).strftime("%Y-%m-%d")
    global_key = f"sentinel:rate:global:{today}"
    global_count = await r.incr(global_key)
    if global_count == 1:
        await r.expire(global_key, 86400)
    if global_count > config.llm_daily_limit:
        return False

    return True


async def close_redis() -> None:
    """Cleanly shut down the Redis connection pool."""
    global _redis_client
    if _redis_client is not None:
        await _redis_client.aclose()
        _redis_client = None
