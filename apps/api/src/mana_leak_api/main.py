"""FastAPI app (`docs/contracts.md` -> REST API). Entry point for Compose's
api start command (`uvicorn mana_leak_api.main:app`, WP7).

`lifespan` owns the one background task that keeps
`mana_leak_core.tracing.current_health()` fresh (FR-7): started on startup,
refreshing immediately and then every 30 s (`tracing.health_refresh_loop`'s
default), cancelled on shutdown before `tracing.shutdown()` flushes and
closes the Langfuse client (AC-9) -- bounded by `tracing.shutdown`'s own
`timeout_s`, so no second `asyncio.wait_for` is needed here.
"""

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI
from mana_leak_core import tracing
from mana_leak_core.settings import get_settings

from mana_leak_api.errors import register_exception_handlers
from mana_leak_api.routers import conversations, health, messages

# Configured once at process startup so `LOG_LEVEL` (contracts.md ->
# Configuration) actually controls what `logging.getLogger(__name__)` calls
# elsewhere in the core/api packages emit, instead of Python's WARNING-level
# last-resort handler.
logging.basicConfig(level=get_settings().log_level)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    refresh_task = asyncio.create_task(tracing.health_refresh_loop())
    try:
        yield
    finally:
        refresh_task.cancel()
        with suppress(asyncio.CancelledError):
            await refresh_task
        await tracing.shutdown()


app = FastAPI(title="Mana Leak API", lifespan=lifespan)
register_exception_handlers(app)
app.include_router(health.router)
app.include_router(conversations.router)
app.include_router(messages.router)
