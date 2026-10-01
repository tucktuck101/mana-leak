"""Model gateway (`docs/contracts.md` -> External adapters -> Model gateway,
Langfuse, Operational limits; `docs/prds/M1-walking-skeleton.plan.md` ->
Shared contracts, Per-WP checklists -> WP3).

Only module that imports LiteLLM. `complete()`'s signature matches
contracts.md verbatim; M1 callers (`process_turn`'s `other` route) always
pass `tools=None`, `response_model=None`, `trace=None` -- tool calling and
structured output arrive with the milestones that use them, and
`TraceContext`/Langfuse tracing is out of M1 scope (WP3 never builds
`TraceContext`; the `trace` parameter exists now only so this signature
does not change later).

Three things the gateway enforces on every call, independent of LiteLLM:

- **Per-turn model-call budget.** `process_turn` (WP4) calls
  `start_turn_budget()` once at turn start, in the same `asyncio` task that
  will call `complete()` -- contextvars propagate down that task's awaits
  but not across a spawned `Task`/thread, and M1 never calls `complete()`
  concurrently within a turn. `complete()` reads and increments a
  contextvar on every call, raising *before* the call that would exceed the
  absolute cap (`Settings.model_calls_max`, default 8) and emitting a
  `limit_reached` audit event itself (contracts.md -> Operational limits:
  "the model gateway (`complete()`) increments it on every call and raises
  `model_limit_exceeded` when the next call would exceed the absolute
  cap").
- **Model-call timeout.** The M1 spike (`docs/ROADMAP.md` -> M1 spike
  results) found LiteLLM's own `timeout=` kwarg does not reliably cancel a
  hung call (a 30 s timeout did not stop a 104 s call), so the gateway
  wraps every call -- for `stream=True`, the full chunk-by-chunk
  consumption, not just the initial connection -- in its own
  `asyncio.timeout(Settings.model_call_timeout_s)`, converting the
  resulting `TimeoutError` into `ManaLeakError(ErrorCode.timeout)` and
  emitting `limit_reached` itself (PRD AC-6; the turn orchestrator
  separately emits `limit_reached` for the 120 s overall turn deadline --
  exactly one emitter per limit).
- **Secret redaction.** Before any outbound message is sent, the gateway
  asserts that no configured `SecretStr` value in `Settings` (just
  `openrouter_api_key` in M1) appears in any message's `content` --
  iterating `Settings`' fields generically rather than naming them, so
  later `SecretStr` additions (`LANGFUSE_SECRET_KEY`, `JEV_API_KEY`, ...)
  are covered without another edit here.

Reasoning is disabled by default on every call
(`extra_body={"reasoning": {"enabled": False}}`, not opt-in per call) --
the M1 spike measured ~90 s / ~2,000 tokens of hidden reasoning on a
routing-sized call versus 8 s / 35 tokens with it off. Enabling it per call
is a later, deliberate choice; none of M1's callers do.

`ModelResponse`/`ModelChunk` are this module's own types (contracts.md pins
only `complete()`'s signature, not their internal shape) -- OpenAI/LiteLLM
chat-completion field names (`choices[0].message.content`,
`choices[0].delta.content`, `choices[0].finish_reason`) verified directly
against installed `litellm` 1.103.1 and one live OpenRouter call.

`embed()` (contracts.md's other gateway function) is not implemented here:
no M1 surface calls it (RAG/embeddings start at M5), and the Shared
contracts row this module implements only quotes `complete()`.

Known contract gap (recorded in `.workmux/HANDOFF.md` for the orchestrator):
`ErrorCode` (WP1, `contracts/errors.py`, already merged, outside this WP's
ownership) does not include `model_limit_exceeded`, even though
`docs/contracts.md` -> Error taxonomy defines it and `Settings`' own
docstring (WP1, `settings.py`) describes the gateway raising it. The
absolute-cap branch below therefore raises `ManaLeakError` with the literal
string `"model_limit_exceeded"` instead of a (currently nonexistent)
`ErrorCode` member; the wire-level value (the JSON/SSE `error.code` string)
is identical to what `ErrorCode.model_limit_exceeded` would serialise to,
so no downstream behaviour differs once the enum gains the member.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Sequence
from contextvars import ContextVar
from typing import Any

import litellm
from pydantic import BaseModel, SecretStr

from .audit import emit_audit_event
from .contracts.enums import AuditEventType, Severity
from .contracts.errors import ErrorCode, ManaLeakError
from .settings import Settings, get_settings

logger = logging.getLogger(__name__)

__all__ = [
    "ModelChunk",
    "ModelResponse",
    "complete",
    "current_call_count",
    "start_turn_budget",
]


class ModelResponse(BaseModel):
    """Non-streaming result of `complete(stream=False)`."""

    text: str
    finish_reason: str | None = None
    model: str


class ModelChunk(BaseModel):
    """One piece of a streaming result from `complete(stream=True)`."""

    delta: str
    finish_reason: str | None = None


# --- Per-turn model-call budget (contracts.md -> Operational limits) -------

_call_count: ContextVar[int] = ContextVar("mana_leak_gateway_call_count", default=0)
_call_budget: ContextVar[int | None] = ContextVar("mana_leak_gateway_call_budget", default=None)


def start_turn_budget(max_calls: int | None = None) -> None:
    """Reset the per-turn model-call counter and set the absolute cap that
    `complete()` enforces on every subsequent call in this `asyncio` task
    (default `Settings.model_calls_max`, 8). `process_turn` (WP4) calls
    this once, at the start of each turn, before making any `complete()`
    call for that turn.
    """
    _call_count.set(0)
    _call_budget.set(max_calls if max_calls is not None else get_settings().model_calls_max)


def current_call_count() -> int:
    """Calls made so far against the current turn's budget. Introspection
    for tests/callers; M1's single `other`-route call never needs it."""
    return _call_count.get()


def _check_no_secrets(messages: Sequence[dict[str, str]], settings: Settings) -> None:
    secrets = [
        value.get_secret_value()
        for _name, value in settings
        if isinstance(value, SecretStr) and value.get_secret_value()
    ]
    for message in messages:
        content = message.get("content") or ""
        if any(secret in content for secret in secrets):
            logger.error("outbound message blocked: contains a configured secret value")
            raise ManaLeakError(
                ErrorCode.internal_error,
                "outbound message blocked: contains a configured secret value",
            )


async def _reserve_call(settings: Settings) -> None:
    """Cap-check + increment (contracts.md -> Operational limits: "What
    counts toward model calls per turn"). Raises before the call that would
    exceed the absolute cap, instead of attempting it."""
    budget = _call_budget.get()
    if budget is None:
        budget = settings.model_calls_max
    used = _call_count.get()
    if used >= budget:
        logger.warning("model-call budget exceeded: %s calls, cap %s", used, budget)
        await emit_audit_event(
            AuditEventType.limit_reached,
            Severity.warning,
            details={"limit": "model_calls_max", "value": budget},
        )
        raise ManaLeakError(
            "model_limit_exceeded",  # ErrorCode gap -- see module docstring
            f"model-call budget of {budget} calls per turn exceeded",
            details={"limit": "model_calls_max", "value": budget},
        )
    _call_count.set(used + 1)


async def _emit_call_timeout(settings: Settings) -> None:
    await emit_audit_event(
        AuditEventType.limit_reached,
        Severity.warning,
        details={"limit": "model_call_timeout_s", "value": settings.model_call_timeout_s},
    )


def _build_kwargs(
    messages: Sequence[dict[str, str]],
    *,
    model: str,
    tools: list[Any] | None,
    response_model: type[BaseModel] | None,
    stream: bool,
    max_tokens: int,
    settings: Settings,
) -> dict[str, Any]:
    kwargs: dict[str, Any] = {
        "model": model,
        "messages": list(messages),
        "max_tokens": max_tokens,
        "stream": stream,
        "api_key": settings.openrouter_api_key.get_secret_value(),
        "extra_body": {"reasoning": {"enabled": False}},
    }
    if tools is not None:
        kwargs["tools"] = tools
    if response_model is not None:
        kwargs["response_format"] = response_model
    return kwargs


async def complete(
    messages: Sequence[dict[str, str]],
    *,
    model: str,
    tools: list[Any] | None = None,
    response_model: type[BaseModel] | None = None,
    stream: bool = False,
    max_tokens: int = 1500,
    trace: object | None = None,  # TraceContext; unused in M1, see module docstring
) -> ModelResponse | AsyncIterator[ModelChunk]:
    """`docs/contracts.md` -> External adapters -> Model gateway. M1 callers
    pass `tools=None`, `response_model=None`, `trace=None`; see the module
    docstring for the budget/timeout/redaction behaviour enforced on every
    call."""
    settings = get_settings()
    _check_no_secrets(messages, settings)
    await _reserve_call(settings)
    kwargs = _build_kwargs(
        messages,
        model=model,
        tools=tools,
        response_model=response_model,
        stream=stream,
        max_tokens=max_tokens,
        settings=settings,
    )
    if stream:
        return _stream(kwargs, settings)
    return await _complete_once(kwargs, settings)


async def _complete_once(kwargs: dict[str, Any], settings: Settings) -> ModelResponse:
    try:
        async with asyncio.timeout(settings.model_call_timeout_s):
            response = await litellm.acompletion(**kwargs)
    except TimeoutError:
        await _emit_call_timeout(settings)
        raise ManaLeakError(ErrorCode.timeout, "model call timed out", retryable=True) from None
    choice = response.choices[0]
    return ModelResponse(
        text=choice.message.content or "",
        finish_reason=choice.finish_reason,
        model=kwargs["model"],
    )


async def _stream(kwargs: dict[str, Any], settings: Settings) -> AsyncIterator[ModelChunk]:
    try:
        async with asyncio.timeout(settings.model_call_timeout_s):
            response = await litellm.acompletion(**kwargs)
            async for part in response:
                choice = part.choices[0]
                delta = choice.delta.content or ""
                finish_reason = choice.finish_reason
                if delta or finish_reason:
                    yield ModelChunk(delta=delta, finish_reason=finish_reason)
    except TimeoutError:
        await _emit_call_timeout(settings)
        raise ManaLeakError(ErrorCode.timeout, "model call timed out", retryable=True) from None
