"""FastAPI app (`docs/contracts.md` -> REST API). Entry point for Compose's
api start command (`uvicorn mana_leak_api.main:app`, WP7).
"""

from fastapi import FastAPI

from mana_leak_api.errors import register_exception_handlers
from mana_leak_api.routers import conversations, health, messages

app = FastAPI(title="Mana Leak API")
register_exception_handlers(app)
app.include_router(health.router)
app.include_router(conversations.router)
app.include_router(messages.router)
