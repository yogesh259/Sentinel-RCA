import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from src.api import approvals, health, incidents, traces
from src.db.session import dispose_engine
from src.graph import set_default_checkpointer
from src.graph.checkpointer import open_checkpointer
from src.services.mcp_client import open_mcp_sessions
from src.services.redis_dedup import close_redis

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("sentinel-rca")


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    async with open_checkpointer() as checkpointer, open_mcp_sessions():
        set_default_checkpointer(checkpointer)
        logger.info("Sentinel-RCA started.")
        yield

    await close_redis()
    await dispose_engine()
    logger.info("Sentinel-RCA stopped.")


app = FastAPI(title="Sentinel-RCA", version="0.5.0", lifespan=lifespan)

app.include_router(incidents.router)
app.include_router(approvals.router)
app.include_router(traces.router)
app.include_router(health.router)
