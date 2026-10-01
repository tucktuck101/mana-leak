"""`GET /health` (`docs/contracts.md` -> REST API -> `HealthResponse`).

`database` is checked with one query per request; `langfuse` is always
`"disabled"` in M1 (no Langfuse integration yet -- PRD -> Out of scope).
Depends directly on `get_session()` (merged, WP2) rather than building a new
engine/connection path, so tests can override just this one dependency to
simulate a down database without needing an actually-unreachable Postgres.
"""

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from mana_leak_core.contracts.conversations import HealthResponse
from mana_leak_core.db.session import get_session
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

router = APIRouter()


@router.get("/health")
async def get_health(session: AsyncSession = Depends(get_session)) -> JSONResponse:  # noqa: B008
    try:
        await session.execute(text("SELECT 1"))
        database: str = "ok"
    except Exception:
        database = "unavailable"

    body = HealthResponse(
        status="ok" if database == "ok" else "degraded",
        database=database,  # type: ignore[arg-type]
        langfuse="disabled",
        rules_version=None,
        card_source_version=None,
    )
    return JSONResponse(
        status_code=200 if database == "ok" else 503, content=body.model_dump(mode="json")
    )
