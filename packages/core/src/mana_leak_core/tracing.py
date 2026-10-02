"""Langfuse tracing (`docs/contracts.md` -> External adapters -> Langfuse;
`docs/prds/M2-observability.plan.md` -> Shared contracts, Per-WP checklists
-> WP3).

Only module that imports the Langfuse SDK, mirroring `gateway.py`'s
sole-LiteLLM-importer pattern. Callers (`orchestrator.py`, `gateway.py`,
`mana_leak_api.main`/`routers.health`) use the functions below and nothing
else, and they need no `try`/`except` of their own, because this module
guarantees:

- **Never raises.** `start_turn_trace`/`record_generation` catch every
  exception raised by the SDK itself (client construction, the span context
  manager's `__enter__` and `__exit__`) and yield a no-op `Observation`
  instead. Exceptions raised by the caller's own code inside the `with`
  block propagate unchanged -- only the SDK's own calls are swallowed.
- **Never blocks a turn.** Span creation in this OpenTelemetry-based SDK is
  local and in-memory: no tracing call on the turn path makes a network
  call, so an unreachable *or* hung Langfuse (AC-5, AC-12) costs a turn
  nothing, with no timeout involved. The two calls that do talk to Langfuse
  are `refresh_health()` (background task only; bounded by the SDK's own 5 s
  request timeout) and `shutdown()` (process exit; bounded here by
  `asyncio.wait_for`). `auth_check`/`shutdown` are synchronous, blocking SDK
  methods, so both run through `asyncio.to_thread`, never on the event loop.
- **No-op when disabled.** With either key unset, no client is ever
  constructed and no outbound call is ever attempted (AC-6).

This module does **no** redaction (M2 plan, F8: fail closed, do not scrub).
`gateway.check_no_secrets(...)` runs *before* anything here is called -- on
the user message, in `orchestrator._run_turn`, before the turn trace opens,
and on every outbound message list inside `complete()` -- and raises
`ManaLeakError(internal_error)` on a hit, so no secret-bearing value ever
reaches a tracing call (NFR-2, AC-8).

Correlation (FR-4, NFR-3): the Langfuse trace ID is bit-identical to the
turn's `turn_id` (a UUID's 32-hex-character form is a valid OTel trace ID,
so `uuid.UUID(hex=trace_id) == turn_id` round-trips), the trace name is the
same `turn_id`, and the session ID is the `conversation_id` already
persisted on `message` -- no new column, and nothing to look up to go from a
persisted turn to its trace.

Child linkage is ambient, not threaded: `record_generation` opens its
generation on the module-level client and relies on OTel's contextvar-based
"currently active span" to nest under the turn trace, so no span handle
travels between `orchestrator.py` and `gateway.py`. That requires both to
run in the same `asyncio` task, which `mana_leak_api.sse.sse_stream`'s
single long-lived producer task already guarantees (the same constraint
`gateway.start_turn_budget`'s contextvars have). The SDK's context managers
are synchronous-only on this version (`async with` raises `TypeError`), so
every call site uses a plain `with`, including inside `async def`
generators: a contextvar set inside an async generator's body is visible
across its own `yield` points as long as one task drives it start to finish.
"""

from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Literal, Protocol
from uuid import UUID

from langfuse import Langfuse, propagate_attributes

from .contracts.enums import Route
from .settings import get_settings

logger = logging.getLogger(__name__)

__all__ = [
    "Health",
    "Observation",
    "TraceContext",
    "current_health",
    "health_refresh_loop",
    "record_generation",
    "refresh_health",
    "shutdown",
    "start_turn_trace",
]

#: `/health`'s `langfuse` field (`docs/contracts.md` -> REST API ->
#: `HealthResponse`).
Health = Literal["ok", "disabled", "unavailable"]


@dataclass(frozen=True)
class TraceContext:
    """`docs/contracts.md` -> External adapters -> Langfuse, verbatim.

    `prompt_version` is always `None` at M2 -- no prompt-versioning scheme
    exists yet; the milestone that introduces one sets it.
    """

    conversation_id: UUID
    turn_id: UUID
    route: Route
    prompt_version: str | None = None


class Observation(Protocol):
    """What callers may do with a yielded span: a real `LangfuseSpan`/
    `LangfuseGeneration`, or the no-op stand-in used whenever tracing is
    disabled or the SDK failed."""

    def update(self, **kwargs: Any) -> None: ...


class _NoOpObservation:
    def update(self, **kwargs: Any) -> None:
        return None


_NOOP: Observation = _NoOpObservation()

#: Last state `refresh_health()` observed; `None` until it first runs.
_health: Health | None = None


#: Deployment identity on every span (M2-14). The only supported deployment
#: is the local Docker Compose stack (`docs/architecture.md`), so the name
#: is a constant; `release` is the `APP_VERSION` git SHA the provenance
#: table (`docs/contracts.md` -> Provenance -> Application) already defines,
#: or `None` when it is unset.
_ENVIRONMENT = "local"


@lru_cache
def _get_client() -> Langfuse | None:
    """The one memoized client (same `@lru_cache` pattern as
    `get_settings()`; tests that change Langfuse settings clear both).

    `None` means tracing is disabled -- either key unset (FR-3) -- or that
    construction failed, which `refresh_health()` reports as `unavailable`
    rather than `disabled` (contracts.md -> `HealthResponse`: `disabled`
    means "no keys configured"). Either way every tracing call is a no-op.

    Every option whose SDK default comes from a `LANGFUSE_*` environment
    variable and that would change *behaviour* is passed explicitly, so a
    stray shell variable on a host run cannot silently alter tracing
    (FR-3): `base_url` (never the `https://cloud.langfuse.com` default --
    AC-11, Langfuse here is self-hosted permanently), `sample_rate` (1.0 --
    every turn is traced; `LANGFUSE_SAMPLE_RATE` is read only when this is
    `None`), and the `environment`/`release` identity above. `tracing_enabled`
    is passed for the same reason, with one SDK caveat worth knowing:
    `langfuse 4.16.0` ANDs it with `LANGFUSE_TRACING_ENABLED`, so that one
    variable can still switch tracing off -- an explicit operator opt-out,
    and `/health` keeps reporting `ok` because `auth_check()` is unaffected.
    """
    settings = get_settings()
    if not settings.langfuse_public_key or not settings.langfuse_secret_key:
        return None
    try:
        return Langfuse(
            public_key=settings.langfuse_public_key,
            secret_key=settings.langfuse_secret_key.get_secret_value(),
            base_url=settings.langfuse_host,
            tracing_enabled=True,
            sample_rate=1.0,
            environment=_ENVIRONMENT,
            release=os.environ.get("APP_VERSION") or None,
        )
    except Exception:
        logger.warning("langfuse: client construction failed; tracing is disabled", exc_info=True)
        return None


def _close(stack: ExitStack, exc: BaseException | None) -> None:
    """Unwind the SDK context managers, swallowing their own failures.

    The caller's exception (if any) is handed to the SDK so the span records
    it, but the return value is ignored: a tracing call must never suppress
    a real turn error, and must never replace it with one of its own.
    """
    try:
        if exc is None:
            stack.close()
        else:
            stack.__exit__(type(exc), exc, exc.__traceback__)
    except Exception:
        logger.warning("langfuse: failed to close observation", exc_info=True)


@contextmanager
def start_turn_trace(trace: TraceContext, *, input: str | None = None) -> Iterator[Observation]:
    """The turn-level trace the orchestrator opens once per turn (FR-4).

    Trace ID and name are `trace.turn_id`; the session is
    `trace.conversation_id`; `metadata` carries the turn's route and prompt
    version. Callers pass only the turn's input -- everything else comes
    from `trace`.
    """
    client = _get_client()
    if client is None:
        yield _NOOP
        return

    stack = ExitStack()
    observation: Observation
    try:
        observation = stack.enter_context(
            client.start_as_current_observation(
                trace_context={"trace_id": trace.turn_id.hex},
                name=str(trace.turn_id),
                as_type="span",
                input=input,
                metadata={"route": trace.route.value, "prompt_version": trace.prompt_version},
            )
        )
        stack.enter_context(
            propagate_attributes(
                session_id=str(trace.conversation_id),
                trace_name=str(trace.turn_id),
            )
        )
    except Exception:
        logger.warning("langfuse: failed to open turn trace; tracing this turn is a no-op")
        logger.debug("langfuse: turn trace failure detail", exc_info=True)
        _close(stack, None)
        yield _NOOP
        return

    try:
        yield observation
    except BaseException as exc:
        _close(stack, exc)
        raise
    else:
        _close(stack, None)


@contextmanager
def record_generation(
    trace: TraceContext | None, *, model: str, input: Any = None
) -> Iterator[Observation]:
    """One model call, as a child generation of the turn's trace (FR-4).

    Nesting is ambient (module docstring): the generation attaches to
    whatever span is currently active, which is the turn trace when the
    gateway is called from inside `start_turn_trace`. `trace=None` -- a
    caller outside a turn -- is always a no-op.
    """
    client = _get_client()
    if trace is None or client is None:
        yield _NOOP
        return

    stack = ExitStack()
    observation: Observation
    try:
        observation = stack.enter_context(
            client.start_as_current_observation(
                name=model,
                as_type="generation",
                model=model,
                input=input,
            )
        )
    except Exception:
        logger.warning("langfuse: failed to open generation; tracing this call is a no-op")
        logger.debug("langfuse: generation failure detail", exc_info=True)
        _close(stack, None)
        yield _NOOP
        return

    try:
        yield observation
    except BaseException as exc:
        _close(stack, exc)
        raise
    else:
        _close(stack, None)


def _keys_configured() -> bool:
    """Whether both Langfuse keys are set -- the one thing that separates
    `disabled` from `unavailable` (`docs/contracts.md` -> `HealthResponse`:
    `disabled` means "no keys configured")."""
    settings = get_settings()
    return bool(settings.langfuse_public_key and settings.langfuse_secret_key)


async def refresh_health() -> None:
    """One `auth_check()` attempt, off the event loop; never raises (FR-7).

    Only a literal `True` is success: this SDK's `auth_check()` usually
    re-raises on an auth failure but returns `False` on at least one
    internal failure mode, and a non-`True` result must never be read as
    `ok` (AC-7, AC-10).

    No client with keys configured means `Langfuse(...)` itself failed to
    construct (`_get_client` logged why): that is `unavailable`, not
    `disabled`, so an operator is not sent looking for a missing variable.
    """
    global _health
    client = _get_client()
    if client is None:
        _health = "unavailable" if _keys_configured() else "disabled"
        return
    try:
        authenticated = await asyncio.to_thread(client.auth_check)
    except Exception:
        logger.warning("langfuse: auth check failed; reporting unavailable")
        logger.debug("langfuse: auth check failure detail", exc_info=True)
        _health = "unavailable"
        return
    _health = "ok" if authenticated is True else "unavailable"


def current_health() -> Health:
    """The last cached state, synchronously and without any I/O -- `/health`
    never probes Langfuse per request (FR-7).

    Before the first refresh runs, the honest answer is `disabled` when no
    keys are configured, and `unavailable` otherwise: `ok` is only ever
    reported after an `auth_check()` actually returned `True`.
    """
    if _health is not None:
        return _health
    return "unavailable" if _keys_configured() else "disabled"


async def health_refresh_loop(interval_s: float = 30.0) -> None:
    """Refresh now, then every `interval_s` (AC-7 requires at least once per
    60 s). Runs as a background task owned by the API's lifespan; cancel it
    to stop. `CancelledError` is not caught here, so cancellation is
    immediate."""
    while True:
        await refresh_health()
        await asyncio.sleep(interval_s)


async def shutdown(timeout_s: float = 5.0) -> None:
    """Flush and shut the client down at process exit (AC-9), bounded and
    silent.

    The SDK's `shutdown()` flushes before shutting down, so no separate
    `flush()` is needed. This is the only Langfuse call in the codebase
    wrapped in `asyncio.wait_for`: it is also the only one whose blocking is
    both expected and on nobody's turn path.
    """
    client = _get_client()
    if client is None:
        return
    try:
        await asyncio.wait_for(asyncio.to_thread(client.shutdown), timeout_s)
    except Exception:
        logger.warning("langfuse: shutdown did not complete within %ss", timeout_s)
        logger.debug("langfuse: shutdown failure detail", exc_info=True)
