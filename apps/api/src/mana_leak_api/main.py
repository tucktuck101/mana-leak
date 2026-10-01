"""FastAPI app (`docs/contracts.md` -> REST API). Entry point for Compose's
api start command (`uvicorn mana_leak_api.main:app`, WP7).
"""

import logging

from fastapi import FastAPI
from mana_leak_core.settings import get_settings

from mana_leak_api.errors import register_exception_handlers
from mana_leak_api.routers import conversations, health, messages

# Configured once at process startup so `LOG_LEVEL` (contracts.md ->
# Configuration) actually controls what `logging.getLogger(__name__)` calls
# elsewhere in the core/api packages emit, instead of Python's WARNING-level
# last-resort handler.
logging.basicConfig(level=get_settings().log_level)

app = FastAPI(title="Mana Leak API")
register_exception_handlers(app)
app.include_router(health.router)
app.include_router(conversations.router)
app.include_router(messages.router)
