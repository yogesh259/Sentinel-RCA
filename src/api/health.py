"""Deep health check: Render routes traffic only while Postgres and Redis both answer."""

import logging

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import text

from src.db.session import session_scope
from src.services.redis_dedup import get_redis

logger = logging.getLogger("sentinel-rca.api.health")

router = APIRouter(tags=["health"])


async def postgres_ok() -> bool:
    """True if Postgres answers SELECT 1."""
    try:
        async with session_scope() as session:
            return await session.scalar(text("SELECT 1")) == 1
    except Exception as exc:
        logger.warning("Health check Postgres ping failed: %s", exc)
        return False


async def redis_ok() -> bool:
    """True if Redis answers PING."""
    try:
        return bool(await (await get_redis()).ping())
    except Exception as exc:
        logger.warning("Health check Redis ping failed: %s", exc)
        return False


@router.get("/health")
async def health_check() -> dict:
    """Return 200 when both stores are reachable, else 503 naming the failing one."""
    pg, redis = await postgres_ok(), await redis_ok()
    if pg and redis:
        return {"status": "ok", "postgres": "connected", "redis": "connected"}

    raise HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail={
            "status": "degraded",
            "postgres": "connected" if pg else "unavailable",
            "redis": "connected" if redis else "unavailable",
        },
    )
