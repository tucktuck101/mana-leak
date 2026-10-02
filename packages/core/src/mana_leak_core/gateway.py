"""Model gateway (`docs/contracts.md` -> External adapters -> Model gateway,
Langfuse, Operational limits; `docs/prds/M1-walking-skeleton.plan.md` ->
Shared contracts, Per-WP checklists -> WP3; `docs/prds/M2-observability.plan.md`
-> Per-WP checklists -> WP4).

Only module that imports LiteLLM. `complete()`'s signature matches
contracts.md verbatim; callers (`process_turn`'s `other` route) still pass
`tools=None` and `response_model=None` -- tool calling and structured
output arrive with the milestones that use them -- but `trace` is real
from M2 on: it is a `tracing.TraceContext | None` (same keyword, same
position, same `None` default as M1's placeholder) and drives the
generation observation described below.

Four things the gateway enforces on every call, independent of LiteLLM:

- **Per-turn model-call budget.** `process_turn` (WP4) calls
  `start_turn_budget(conversation_id=..., turn_id=...)` once at turn start,
  in the same `asyncio` task that will call `complete()` -- contextvars
  propagate down that task's awaits and into a `Task` spawned from it (the
  child copies the context at creation), but a value set *after* that copy
  is invisible to the child, and a set inside the child never reaches the
  parent. The SSE adapter therefore drives `process_turn` from one
  long-lived producer task (`mana_leak_api.sse.sse_stream`) instead of a
  task per event, so every `complete()` call in a turn shares one counter.
  `complete()` reads and increments that contextvar on every call, raising
  *before* the call that would exceed the absolute cap
  (`Settings.model_calls_max`, default 8) and emitting a `limit_reached`
  audit event itself (contracts.md -> Operational limits: "the model
  gateway (`complete()`) increments it on every call and raises
  `model_limit_exceeded` when the next call would exceed the absolute
  cap"). The same call also records the turn's `conversation_id`/`turn_id`,
  which every audit row this module writes is attributed to
  (`docs/data-model.md` -> `audit_event`: a null `conversation_id` means a
  CLI/MCP call with no conversation, not a turn the gateway could not name).
- **Model-call timeout.** The M1 spike (`docs/ROADMAP.md` -> M1 spike
  results) found LiteLLM's own `timeout=` kwarg does not reliably cancel a
  hung call (a 30 s timeout did not stop a 104 s call), so the gateway
  bounds every call with `Settings.model_call_timeout_s` itself -- for
  `stream=True`, the full chunk-by-chunk consumption, not just the initial
  connection: one loop-time deadline is computed before the call and each
  individual `await` (the connection, then every `__anext__`) runs under
  `asyncio.timeout_at(deadline)`. The scope is never held across a `yield`,
  because a timeout scope held open across a `yield` fires inside whichever
  task resumed the generator (and is simply never armed in the task that is
  actually stalled). The resulting `TimeoutError` becomes
  `ManaLeakError(ErrorCode.timeout)` and the gateway emits
  `limit_reached` itself (PRD AC-6; the turn orchestrator
  separately emits `limit_reached` for the 120 s overall turn deadline --
  exactly one emitter per limit).
- **Secret redaction.** Before any outbound message is sent -- and before
  anything about it is traced -- the gateway asserts that no configured
  `SecretStr` value in `Settings` (`openrouter_api_key`,
  `langfuse_secret_key`, ...) appears in any message's `content`,
  iterating `Settings`' fields generically rather than naming them, so
  later `SecretStr` additions are covered without another edit here. The
  check is public as `check_no_secrets()` (M2 WP4): `orchestrator.py`
  calls the same function on the user message before it opens the turn
  trace, so the fail-closed guarantee covers the turn-level observation
  too (M2 PRD NFR-2), with no scrubbing anywhere in `tracing.py`.
- **Generation observations (M2).** When the caller passes a
  `TraceContext`, every `complete()` call is recorded as a child
  generation of the turn's trace (`tracing.record_generation`, M2 FR-4).
  Nesting is ambient -- OTel's currently-active span -- so no span handle
  crosses the orchestrator/gateway boundary; it holds because the SSE
  adapter drives the whole turn from one task (see the budget bullet).
  The span is opened inside `_stream()`'s own generator body, not in
  `complete()`, which returns the generator unstarted. Streamed token
  usage is requested explicitly (`stream_options={"include_usage": True}`,
  without which LiteLLM never populates `.usage` on a streamed chunk) and
  reported on the generation together with the provider's cost when it
  supplies one, else an explicit null (M2 FR-5).

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
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Mapping, Sequence
from contextvars import ContextVar
from typing import Any
from uuid import UUID

import litellm
from pydantic import BaseModel, SecretStr

from .audit import emit_audit_event
from .contracts.enums import AuditEventType, Severity
from .contracts.errors import ErrorCode, ManaLeakError
from .settings import Settings, get_settings
from .tracing import Observation, TraceContext, record_generation

logger = logging.getLogger(__name__)

__all__ = [
    "ModelChunk",
    "ModelResponse",
    "check_no_secrets",
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
#: The turn every audit row written by this module is attributed to.
_turn_ids: ContextVar[tuple[UUID, UUID] | None] = ContextVar(
    "mana_leak_gateway_turn_ids", default=None
)


def start_turn_budget(
    max_calls: int | None = None,
    *,
    conversation_id: UUID | None = None,
    turn_id: UUID | None = None,
) -> None:
    """Reset the per-turn model-call counter, set the absolute cap that
    `complete()` enforces on every subsequent call in this `asyncio` task
    (default `Settings.model_calls_max`, 8), and record the turn this
    gateway's audit rows belong to. `process_turn` (WP4) calls this once, at
    the start of each turn, before making any `complete()` call for it; a
    caller with no conversation (CLI/MCP) leaves the ids unset, which is the
    documented meaning of a null `conversation_id` in `audit_event`.
    """
    _call_count.set(0)
    _call_budget.set(max_calls if max_calls is not None else get_settings().model_calls_max)
    named = conversation_id is not None and turn_id is not None
    _turn_ids.set((conversation_id, turn_id) if named else None)


def current_call_count() -> int:
    """Calls made so far against the current turn's budget. Introspection
    for tests/callers; M1's single `other`-route call never needs it."""
    return _call_count.get()


async def _audit_limit_reached(limit: str, value: int) -> None:
    conversation_id, turn_id = _turn_ids.get() or (None, None)
    await emit_audit_event(
        AuditEventType.limit_reached,
        Severity.warning,
        conversation_id=conversation_id,
        turn_id=turn_id,
        details={"limit": limit, "value": value},
    )


def check_no_secrets(messages: Sequence[Mapping[str, Any]], settings: Settings) -> None:
    """Fail closed if any configured secret value appears in `messages`
    (module docstring -> Secret redaction). `complete()` calls this before
    the model call and before any generation observation; `_run_turn`
    calls it on the user message before opening the turn trace, so no
    secret-bearing content reaches Langfuse at all (M2 PRD NFR-2, AC-8).
    Raises `ManaLeakError(internal_error)` without echoing the secret.

    `content` is searched as text whatever its shape (M2-12): a message
    whose `content` is a list of multimodal/tool blocks, as the chat
    protocol allows and tool calls will produce, would otherwise turn
    `secret in content` into a *list membership* test -- a secret inside a
    block would pass, which is exactly the fail-open this check exists to
    prevent. `str()` (not `json.dumps`) is the normalisation, because its
    output escapes nothing: a secret containing a newline, a quote or a
    non-ASCII character still matches as a substring.
    """
    secrets = [
        value.get_secret_value()
        for _name, value in settings
        if isinstance(value, SecretStr) and value.get_secret_value()
    ]
    for message in messages:
        content = message.get("content")
        text = content if isinstance(content, str) else str(content)
        if any(secret in text for secret in secrets):
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
        await _audit_limit_reached("model_calls_max", budget)
        raise ManaLeakError(
            ErrorCode.model_limit_exceeded,
            f"model-call budget of {budget} calls per turn exceeded",
            details={"limit": "model_calls_max", "value": budget},
        )
    _call_count.set(used + 1)


async def _call_timeout(settings: Settings) -> ManaLeakError:
    """Audit the limit and build the typed error for a call that ran past
    `model_call_timeout_s`. `details` names the limit, so the SSE `error`
    event is distinguishable from the orchestrator's turn deadline and from
    a client disconnect (all three carry `ErrorCode.timeout`)."""
    await _audit_limit_reached("model_call_timeout_s", settings.model_call_timeout_s)
    return ManaLeakError(
        ErrorCode.timeout,
        "model call timed out",
        retryable=True,
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
    if stream:
        # Without this, LiteLLM never populates `.usage` on a streamed
        # chunk and FR-5's token counts would be unavailable (verified).
        kwargs["stream_options"] = {"include_usage": True}
    if tools is not None:
        kwargs["tools"] = tools
    if response_model is not None:
        kwargs["response_format"] = response_model
    return kwargs


def _record_result(observation: Observation, output: str, usage: Any) -> None:
    """Close out a generation with the provider's own numbers (FR-5).

    `litellm`'s `Usage` *deletes* `cost` when the provider reported none
    (verified: attribute access then raises `AttributeError`), so every
    field is read with `getattr`; a missing cost is reported as an
    explicit null rather than a fabricated number.
    """
    cost = getattr(usage, "cost", None) if usage is not None else None
    observation.update(
        output=output,
        usage_details=(
            {
                "input": getattr(usage, "prompt_tokens", 0),
                "output": getattr(usage, "completion_tokens", 0),
                "total": getattr(usage, "total_tokens", 0),
            }
            if usage is not None
            else None
        ),
        cost_details={"total": cost} if cost is not None else None,
    )


async def complete(
    messages: Sequence[dict[str, str]],
    *,
    model: str,
    tools: list[Any] | None = None,
    response_model: type[BaseModel] | None = None,
    stream: bool = False,
    max_tokens: int = 1500,
    trace: TraceContext | None = None,
) -> ModelResponse | AsyncIterator[ModelChunk]:
    """`docs/contracts.md` -> External adapters -> Model gateway. Callers
    pass `tools=None`, `response_model=None`; `trace` is the turn's
    `TraceContext` when the call belongs to a turn (M2 FR-4) and `None`
    otherwise. See the module docstring for the budget/timeout/redaction
    behaviour enforced on every call."""
    settings = get_settings()
    check_no_secrets(messages, settings)
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
        return _stream(kwargs, settings, trace)
    return await _complete_once(kwargs, settings, trace)


async def _complete_once(
    kwargs: dict[str, Any], settings: Settings, trace: TraceContext | None
) -> ModelResponse:
    with record_generation(trace, model=kwargs["model"], input=kwargs["messages"]) as generation:
        try:
            async with asyncio.timeout(settings.model_call_timeout_s):
                response = await litellm.acompletion(**kwargs)
        except TimeoutError:
            raise await _call_timeout(settings) from None
        choice = response.choices[0]
        text = choice.message.content or ""
        _record_result(generation, text, getattr(response, "usage", None))
        return ModelResponse(
            text=text,
            finish_reason=choice.finish_reason,
            model=kwargs["model"],
        )


async def _stream(
    kwargs: dict[str, Any], settings: Settings, trace: TraceContext | None
) -> AsyncIterator[ModelChunk]:
    """Bounds the whole call -- connection plus every chunk -- with one
    loop-time deadline, opening a timeout scope around each single `await`
    and never across the `yield` (see the module docstring: a scope held
    across a `yield` is armed in whichever task resumes the generator, not
    in the task that is stalled waiting for the next chunk).

    The generation observation is opened here, in the generator's own
    body, rather than in `complete()`: `complete()` returns this generator
    unstarted, so a span opened there would be closed before the first
    chunk ever arrived.
    """
    with record_generation(trace, model=kwargs["model"], input=kwargs["messages"]) as generation:
        deadline = asyncio.get_running_loop().time() + settings.model_call_timeout_s
        try:
            async with asyncio.timeout_at(deadline):
                response = await litellm.acompletion(**kwargs)
        except TimeoutError:
            raise await _call_timeout(settings) from None
        parts = response.__aiter__()
        text: list[str] = []
        usage: Any = None
        while True:
            try:
                async with asyncio.timeout_at(deadline):
                    part = await anext(parts)
            except StopAsyncIteration:
                _record_result(generation, "".join(text), usage)
                return
            except TimeoutError:
                raise await _call_timeout(settings) from None
            chunk_usage = getattr(part, "usage", None)
            if chunk_usage is not None:
                usage = chunk_usage
            if not part.choices:
                # `stream_options={"include_usage": True}` makes LiteLLM
                # emit a terminal usage-only chunk with `choices == []`
                # (verified); indexing it would raise `IndexError`.
                continue
            choice = part.choices[0]
            delta = choice.delta.content or ""
            finish_reason = choice.finish_reason
            if delta:
                text.append(delta)
            if delta or finish_reason:
                yield ModelChunk(delta=delta, finish_reason=finish_reason)
