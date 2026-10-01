"""WP3 acceptance: `uv run pytest tests/core/test_gateway.py`.

Covers the model gateway (`docs/prds/M1-walking-skeleton.plan.md` -> Shared
contracts, Per-WP checklists -> WP3): the `complete()` signature, reasoning
disabled by default, the per-turn call-budget contextvar, the gateway's own
30 s timeout (independent of LiteLLM's `timeout=`), the turn its audit rows
are attributed to, and outbound secret redaction. Every test except the one
`live` streaming test patches LiteLLM with a fake.
"""

import asyncio
import json
from collections.abc import AsyncIterator
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from litellm.types.utils import Usage
from mana_leak_core import gateway, tracing
from mana_leak_core.contracts.enums import AuditEventType, Route, Severity
from mana_leak_core.contracts.errors import ErrorCode, ManaLeakError
from mana_leak_core.db.models import AuditEvent
from mana_leak_core.settings import Settings, get_settings
from mana_leak_core.tracing import TraceContext
from sqlalchemy import select

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(autouse=True)
def _reset_turn_budget():
    """Contextvars persist across tests in the same thread/session unless
    reset; every test that cares about budget state calls
    `start_turn_budget()` itself, but this guarantees a clean starting
    point regardless of test order."""
    gateway._call_count.set(0)
    gateway._call_budget.set(None)
    gateway._turn_ids.set(None)
    yield


@pytest.fixture
def audit_events(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    mock = AsyncMock()
    monkeypatch.setattr(gateway, "emit_audit_event", mock)
    return mock


@pytest.fixture
async def gateway_audit_against_db(db, monkeypatch: pytest.MonkeyPatch):
    """Routes the *real* `emit_audit_event` (imported by `gateway.py`) through
    the `db` fixture's session against `mana_leak_test`, instead of mocking
    it away, so gateway tests can assert the actual `audit_event` row that
    `_reserve_call`/`_call_timeout` write (PRD AC-6, NFR-4)."""

    async def _single_session():
        yield db

    monkeypatch.setattr("mana_leak_core.audit.get_session", _single_session)
    return db


def _settings(**overrides: Any) -> Settings:
    base = get_settings().model_dump()
    base["openrouter_api_key"] = get_settings().openrouter_api_key
    base.update(overrides)
    return Settings(**base)


def _fake_message(content: str, finish_reason: str | None) -> SimpleNamespace:
    return SimpleNamespace(
        choices=[
            SimpleNamespace(message=SimpleNamespace(content=content), finish_reason=finish_reason)
        ]
    )


def _fake_chunk(delta: str | None, finish_reason: str | None) -> SimpleNamespace:
    return SimpleNamespace(
        choices=[SimpleNamespace(delta=SimpleNamespace(content=delta), finish_reason=finish_reason)]
    )


def _fake_usage_chunk(usage: Usage) -> SimpleNamespace:
    """The terminal chunk `stream_options={"include_usage": True}` makes
    LiteLLM emit: no choices at all, the provider's usage attached
    (verified against `litellm`'s own `streaming_handler.py`)."""
    return SimpleNamespace(choices=[], usage=usage)


class FakeLiteLLM:
    """Records every `acompletion(**kwargs)` call and replays a scripted
    response: a plain non-streaming result, an async-iterable of chunks for
    `stream=True`, or a hang (to exercise the gateway's own timeout)."""

    def __init__(
        self,
        *,
        text: str = "hello",
        finish_reason: str = "stop",
        chunks: list[tuple[str | None, str | None]] | None = None,
        hang_s: float | None = None,
        hang_after_chunk: bool = False,
        usage: Usage | None = None,
    ) -> None:
        self.text = text
        self.finish_reason = finish_reason
        self.chunks = (
            chunks if chunks is not None else [("hel", None), ("lo", None), (None, "stop")]
        )
        self.hang_s = hang_s
        self.hang_after_chunk = hang_after_chunk
        self.usage = usage
        self.calls: list[dict[str, Any]] = []

    async def acompletion(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if kwargs.get("stream"):
            return self._stream()
        if self.hang_s is not None:
            await asyncio.sleep(self.hang_s)
        response = _fake_message(self.text, self.finish_reason)
        if self.usage is not None:
            response.usage = self.usage
        return response

    async def _stream(self) -> AsyncIterator[Any]:
        if self.hang_s is not None and not self.hang_after_chunk:
            await asyncio.sleep(self.hang_s)
        for i, (delta, finish_reason) in enumerate(self.chunks):
            if self.hang_s is not None and self.hang_after_chunk and i == 1:
                await asyncio.sleep(self.hang_s)
            yield _fake_chunk(delta, finish_reason)
        if self.usage is not None:
            yield _fake_usage_chunk(self.usage)


@pytest.fixture
def fake_litellm(monkeypatch: pytest.MonkeyPatch):
    def _install(**kwargs: Any) -> FakeLiteLLM:
        fake = FakeLiteLLM(**kwargs)
        monkeypatch.setattr(gateway.litellm, "acompletion", fake.acompletion)
        return fake

    return _install


# --- Non-streaming ----------------------------------------------------------


async def test_complete_non_streaming_returns_model_response(fake_litellm) -> None:
    fake = fake_litellm(text="the answer", finish_reason="stop")

    result = await gateway.complete(
        [{"role": "user", "content": "hi"}], model="openrouter/x/y", max_tokens=42
    )

    assert isinstance(result, gateway.ModelResponse)
    assert result.text == "the answer"
    assert result.finish_reason == "stop"
    assert result.model == "openrouter/x/y"
    assert fake.calls[0]["max_tokens"] == 42
    assert fake.calls[0]["stream"] is False


async def test_api_key_comes_from_settings_not_env(fake_litellm) -> None:
    fake = fake_litellm()
    await gateway.complete([{"role": "user", "content": "hi"}], model="openrouter/x/y")
    assert fake.calls[0]["api_key"] == get_settings().openrouter_api_key.get_secret_value()


async def test_tools_and_response_model_omitted_when_not_passed(fake_litellm) -> None:
    fake = fake_litellm()
    await gateway.complete([{"role": "user", "content": "hi"}], model="openrouter/x/y")
    assert "tools" not in fake.calls[0]
    assert "response_format" not in fake.calls[0]


# --- Reasoning disabled by default ------------------------------------------


async def test_reasoning_disabled_by_default_non_streaming(fake_litellm) -> None:
    fake = fake_litellm()
    await gateway.complete([{"role": "user", "content": "hi"}], model="m")
    assert fake.calls[0]["extra_body"] == {"reasoning": {"enabled": False}}


async def test_reasoning_disabled_by_default_streaming(fake_litellm) -> None:
    fake = fake_litellm()
    chunks = [
        c
        async for c in await gateway.complete(
            [{"role": "user", "content": "hi"}], model="m", stream=True
        )
    ]
    assert fake.calls[0]["extra_body"] == {"reasoning": {"enabled": False}}
    assert chunks  # sanity: the fixture's default chunks were consumed


# --- Streaming ---------------------------------------------------------------


async def test_complete_streaming_yields_model_chunks(fake_litellm) -> None:
    fake_litellm(chunks=[("hel", None), ("lo", None), (None, "stop")])

    stream = await gateway.complete([{"role": "user", "content": "hi"}], model="m", stream=True)
    chunks = [c async for c in stream]

    assert all(isinstance(c, gateway.ModelChunk) for c in chunks)
    assert "".join(c.delta for c in chunks) == "hello"
    assert chunks[-1].finish_reason == "stop"


async def test_streaming_skips_empty_role_only_chunks(fake_litellm) -> None:
    # A role-announcement chunk (delta=None, finish_reason=None) carries no
    # text and no terminal signal; the gateway should not surface it.
    fake_litellm(chunks=[(None, None), ("hi", None), (None, "stop")])

    stream = await gateway.complete([{"role": "user", "content": "hi"}], model="m", stream=True)
    chunks = [c async for c in stream]

    assert [c.delta for c in chunks] == ["hi", ""]
    assert chunks[-1].finish_reason == "stop"


# --- Per-turn call budget ----------------------------------------------------


async def test_call_budget_increments_per_call(fake_litellm) -> None:
    fake_litellm()
    gateway.start_turn_budget(max_calls=8)
    assert gateway.current_call_count() == 0
    await gateway.complete([{"role": "user", "content": "hi"}], model="m")
    assert gateway.current_call_count() == 1
    await gateway.complete([{"role": "user", "content": "hi"}], model="m")
    assert gateway.current_call_count() == 2


async def test_call_budget_raises_model_limit_exceeded_at_cap(fake_litellm, audit_events) -> None:
    fake = fake_litellm()
    gateway.start_turn_budget(max_calls=2)
    await gateway.complete([{"role": "user", "content": "hi"}], model="m")
    await gateway.complete([{"role": "user", "content": "hi"}], model="m")

    with pytest.raises(ManaLeakError) as excinfo:
        await gateway.complete([{"role": "user", "content": "hi"}], model="m")

    assert excinfo.value.code is ErrorCode.model_limit_exceeded
    assert len(fake.calls) == 2  # the third call was never attempted

    audit_events.assert_called_once_with(
        AuditEventType.limit_reached,
        Severity.warning,
        conversation_id=None,
        turn_id=None,
        details={"limit": "model_calls_max", "value": 2},
    )


async def test_call_budget_exceeded_writes_limit_reached_audit_row(
    fake_litellm, gateway_audit_against_db
) -> None:
    """Exercises the real `emit_audit_event` (not a mock) against
    `mana_leak_test`: `_reserve_call` must await the write, not fire-and-forget
    a coroutine that never runs (PRD AC-6, NFR-4)."""
    fake_litellm()
    gateway.start_turn_budget(max_calls=2)
    await gateway.complete([{"role": "user", "content": "hi"}], model="m")
    await gateway.complete([{"role": "user", "content": "hi"}], model="m")

    with pytest.raises(ManaLeakError):
        await gateway.complete([{"role": "user", "content": "hi"}], model="m")

    db = gateway_audit_against_db
    rows = (
        (
            await db.execute(
                select(AuditEvent).where(
                    AuditEvent.event_type == AuditEventType.limit_reached.value
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(rows) == 1
    assert rows[0].severity == Severity.warning.value
    assert rows[0].details == {"limit": "model_calls_max", "value": 2}


async def test_call_budget_defaults_to_settings_model_calls_max(fake_litellm, monkeypatch) -> None:
    monkeypatch.setattr(gateway, "get_settings", lambda: _settings(model_calls_max=1))
    fake_litellm()

    await gateway.complete([{"role": "user", "content": "hi"}], model="m")
    with pytest.raises(ManaLeakError) as excinfo:
        await gateway.complete([{"role": "user", "content": "hi"}], model="m")
    assert excinfo.value.code is ErrorCode.model_limit_exceeded


async def test_start_turn_budget_resets_count_for_a_new_turn(fake_litellm) -> None:
    fake_litellm()
    gateway.start_turn_budget(max_calls=1)
    await gateway.complete([{"role": "user", "content": "hi"}], model="m")
    with pytest.raises(ManaLeakError):
        await gateway.complete([{"role": "user", "content": "hi"}], model="m")

    gateway.start_turn_budget(max_calls=1)  # next turn
    assert gateway.current_call_count() == 0
    await gateway.complete([{"role": "user", "content": "hi"}], model="m")  # does not raise


async def test_audit_rows_name_the_turn_they_belong_to(fake_litellm, audit_events) -> None:
    """`limit_reached` rows written by the gateway carry the turn's ids,
    so they are attributable (`data-model.md` -> `audit_event`: a null
    `conversation_id` means a CLI/MCP call with no conversation)."""
    fake_litellm()
    conversation_id, turn_id = uuid4(), uuid4()
    gateway.start_turn_budget(max_calls=1, conversation_id=conversation_id, turn_id=turn_id)
    await gateway.complete([{"role": "user", "content": "hi"}], model="m")

    with pytest.raises(ManaLeakError):
        await gateway.complete([{"role": "user", "content": "hi"}], model="m")

    assert audit_events.call_args.kwargs["conversation_id"] == conversation_id
    assert audit_events.call_args.kwargs["turn_id"] == turn_id


# --- Model-call timeout (AC-6, NFR-1) ----------------------------------------


async def test_non_streaming_call_past_timeout_raises_timeout_and_emits_limit_reached(
    fake_litellm, audit_events, monkeypatch
) -> None:
    monkeypatch.setattr(gateway, "get_settings", lambda: _settings(model_call_timeout_s=1))
    fake_litellm(hang_s=3)

    with pytest.raises(ManaLeakError) as excinfo:
        await gateway.complete([{"role": "user", "content": "hi"}], model="m")

    assert excinfo.value.code is ErrorCode.timeout
    assert excinfo.value.retryable is True
    # `details` names the limit so the client can tell a model-call timeout
    # from the turn deadline and from a disconnect (all `ErrorCode.timeout`).
    assert excinfo.value.details == {"limit": "model_call_timeout_s", "value": 1}
    audit_events.assert_called_once_with(
        AuditEventType.limit_reached,
        Severity.warning,
        conversation_id=None,
        turn_id=None,
        details={"limit": "model_call_timeout_s", "value": 1},
    )


async def test_timeout_writes_limit_reached_audit_row(
    fake_litellm, gateway_audit_against_db, monkeypatch
) -> None:
    """Exercises the real `emit_audit_event` (not a mock) against
    `mana_leak_test`: `_call_timeout` must await the write, not
    fire-and-forget a coroutine that never runs (PRD AC-6, NFR-4)."""
    monkeypatch.setattr(gateway, "get_settings", lambda: _settings(model_call_timeout_s=1))
    fake_litellm(hang_s=3)

    with pytest.raises(ManaLeakError) as excinfo:
        await gateway.complete([{"role": "user", "content": "hi"}], model="m")

    assert excinfo.value.code == ErrorCode.timeout

    db = gateway_audit_against_db
    rows = (
        (
            await db.execute(
                select(AuditEvent).where(
                    AuditEvent.event_type == AuditEventType.limit_reached.value
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(rows) == 1
    assert rows[0].severity == Severity.warning.value
    assert rows[0].details == {"limit": "model_call_timeout_s", "value": 1}


async def test_streaming_call_past_timeout_raises_timeout(
    fake_litellm, audit_events, monkeypatch
) -> None:
    monkeypatch.setattr(gateway, "get_settings", lambda: _settings(model_call_timeout_s=1))
    fake_litellm(hang_s=3)

    stream = await gateway.complete([{"role": "user", "content": "hi"}], model="m", stream=True)
    with pytest.raises(ManaLeakError) as excinfo:
        async for _ in stream:
            pass

    assert excinfo.value.code == ErrorCode.timeout
    audit_events.assert_called_once()


async def test_streaming_timeout_mid_stream(fake_litellm, monkeypatch) -> None:
    """The timeout bounds the *whole* call, not just the first chunk -- a
    stream that starts promptly but stalls partway through must still be
    cancelled (ROADMAP M1 spike: LiteLLM's own `timeout=` did not stop a
    hung call)."""
    monkeypatch.setattr(gateway, "get_settings", lambda: _settings(model_call_timeout_s=1))
    fake_litellm(chunks=[("a", None), ("b", None)], hang_s=3, hang_after_chunk=True)

    stream = await gateway.complete([{"role": "user", "content": "hi"}], model="m", stream=True)
    received: list[str] = []
    with pytest.raises(ManaLeakError) as excinfo:
        async for chunk in stream:
            received.append(chunk.delta)

    assert received == ["a"]
    assert excinfo.value.code == ErrorCode.timeout


async def test_call_within_timeout_succeeds(fake_litellm, monkeypatch) -> None:
    monkeypatch.setattr(gateway, "get_settings", lambda: _settings(model_call_timeout_s=5))
    fake_litellm(hang_s=0.01, text="fine")

    result = await gateway.complete([{"role": "user", "content": "hi"}], model="m")
    assert result.text == "fine"


# --- Secret redaction ---------------------------------------------------------


async def test_redaction_blocks_outbound_message_containing_configured_secret(fake_litellm) -> None:
    fake = fake_litellm()
    secret = get_settings().openrouter_api_key.get_secret_value()

    with pytest.raises(ManaLeakError) as excinfo:
        await gateway.complete([{"role": "user", "content": f"my key is {secret}"}], model="m")

    assert excinfo.value.code == ErrorCode.internal_error
    assert fake.calls == []  # blocked before any outbound call was attempted
    assert secret not in str(excinfo.value)  # message never echoes the secret


async def test_redaction_allows_ordinary_messages(fake_litellm) -> None:
    fake_litellm()
    await gateway.complete(
        [{"role": "user", "content": "what is this card?"}], model="m"
    )  # no raise


async def test_check_no_secrets_is_callable_directly_by_other_core_modules() -> None:
    """`orchestrator.py` (M2 WP5) calls this same check on the user message
    before it opens the turn trace, so the fail-closed guarantee covers
    Langfuse input too (M2 PRD NFR-2). Public name, same behaviour."""
    settings = get_settings()
    secret = settings.openrouter_api_key.get_secret_value()

    gateway.check_no_secrets([{"role": "user", "content": "how does cascade work?"}], settings)

    with pytest.raises(ManaLeakError) as excinfo:
        gateway.check_no_secrets([{"role": "user", "content": f"key: {secret}"}], settings)
    assert excinfo.value.code is ErrorCode.internal_error
    assert secret not in str(excinfo.value)


# --- Generation observations (M2 FR-4, FR-5, AC-4) ---------------------------


def _trace() -> TraceContext:
    return TraceContext(conversation_id=uuid4(), turn_id=uuid4(), route=Route.other)


def _attr(span, name: str) -> Any:
    return span.attributes.get(f"langfuse.observation.{name}")


async def _drain(stream: AsyncIterator[gateway.ModelChunk]) -> list[gateway.ModelChunk]:
    return [chunk async for chunk in stream]


async def test_streaming_asks_the_provider_for_usage(fake_litellm) -> None:
    """Without `stream_options={"include_usage": True}` LiteLLM never
    populates `.usage` on a streamed chunk, so FR-5's token counts would
    not exist at all. Non-streaming calls carry usage already."""
    fake = fake_litellm()
    await _drain(await gateway.complete([{"role": "user", "content": "hi"}], model="m", stream=True))
    await gateway.complete([{"role": "user", "content": "hi"}], model="m")

    assert fake.calls[0]["stream_options"] == {"include_usage": True}
    assert "stream_options" not in fake.calls[1]


async def test_streaming_terminal_usage_chunk_is_consumed_not_yielded(fake_litellm) -> None:
    """The usage-only chunk has `choices == []`; indexing it would raise
    `IndexError` on every real streamed turn, and it carries no text to
    surface to the caller."""
    fake_litellm(
        chunks=[("hel", None), ("lo", None), (None, "stop")],
        usage=Usage(prompt_tokens=11, completion_tokens=5, total_tokens=16),
    )

    chunks = await _drain(
        await gateway.complete([{"role": "user", "content": "hi"}], model="m", stream=True)
    )

    assert "".join(chunk.delta for chunk in chunks) == "hello"
    assert chunks[-1].finish_reason == "stop"


async def test_streamed_generation_is_a_child_of_the_turn_trace(fake_litellm, span_capture) -> None:
    """AC-4: one turn-level trace with the model call recorded as its child
    generation -- never a second root span -- carrying the provider's token
    counts and cost."""
    fake_litellm(
        chunks=[("hel", None), ("lo", None), (None, "stop")],
        usage=Usage(prompt_tokens=11, completion_tokens=5, total_tokens=16, cost=0.00042),
    )
    trace = _trace()

    with tracing.start_turn_trace(trace, input="hi"):
        chunks = await _drain(
            await gateway.complete(
                [{"role": "user", "content": "hi"}], model="m/x", stream=True, trace=trace
            )
        )

    assert "".join(chunk.delta for chunk in chunks) == "hello"
    spans = span_capture.finished_spans()
    assert sorted(span.name for span in spans) == sorted(["m/x", str(trace.turn_id)])
    turn_span = span_capture.span_named(str(trace.turn_id))
    generation = span_capture.span_named("m/x")
    assert generation.parent is not None
    assert generation.parent.span_id == turn_span.context.span_id
    assert format(generation.context.trace_id, "032x") == trace.turn_id.hex
    assert _attr(generation, "type") == "generation"
    assert _attr(generation, "model.name") == "m/x"
    assert json.loads(_attr(generation, "input")) == [{"role": "user", "content": "hi"}]
    assert _attr(generation, "output") == "hello"
    assert json.loads(_attr(generation, "usage_details")) == {"input": 11, "output": 5, "total": 16}
    assert json.loads(_attr(generation, "cost_details")) == {"total": 0.00042}


async def test_generation_reports_no_cost_when_the_provider_reports_none(
    fake_litellm, span_capture
) -> None:
    """`litellm`'s `Usage` *deletes* `cost` when the provider reported none
    (verified: attribute access raises `AttributeError`), so the gateway
    must read it defensively and record an explicit null rather than a
    fabricated number (FR-5, AC-4)."""
    usage = Usage(prompt_tokens=7, completion_tokens=2, total_tokens=9)
    assert not hasattr(usage, "cost")  # the shape this test exists for
    fake_litellm(chunks=[("ok", "stop")], usage=usage)
    trace = _trace()

    with tracing.start_turn_trace(trace, input="hi"):
        await _drain(
            await gateway.complete(
                [{"role": "user", "content": "hi"}], model="m/x", stream=True, trace=trace
            )
        )

    generation = span_capture.span_named("m/x")
    assert json.loads(_attr(generation, "usage_details")) == {"input": 7, "output": 2, "total": 9}
    assert _attr(generation, "cost_details") is None


async def test_non_streaming_call_is_recorded_as_a_generation(fake_litellm, span_capture) -> None:
    fake_litellm(
        text="the answer",
        usage=Usage(prompt_tokens=3, completion_tokens=4, total_tokens=7, cost=0.001),
    )
    trace = _trace()

    with tracing.start_turn_trace(trace, input="hi"):
        result = await gateway.complete(
            [{"role": "user", "content": "hi"}], model="m/x", trace=trace
        )

    assert result.text == "the answer"
    generation = span_capture.span_named("m/x")
    assert generation.parent is not None
    assert generation.parent.span_id == span_capture.span_named(str(trace.turn_id)).context.span_id
    assert _attr(generation, "output") == "the answer"
    assert json.loads(_attr(generation, "usage_details")) == {"input": 3, "output": 4, "total": 7}


async def test_no_generation_is_recorded_without_a_trace(fake_litellm, span_capture) -> None:
    """A caller outside a turn (CLI/MCP today) must not produce a stray
    root generation span, even with tracing fully configured."""
    fake_litellm(usage=Usage(prompt_tokens=1, completion_tokens=1, total_tokens=2))

    await _drain(
        await gateway.complete([{"role": "user", "content": "hi"}], model="m/x", stream=True)
    )

    assert span_capture.finished_spans() == []


async def test_a_failing_model_call_still_closes_its_generation(
    fake_litellm, span_capture, audit_events, monkeypatch
) -> None:
    """The turn's own error must propagate unchanged (the gateway's typed
    `timeout`), with the generation closed rather than left open."""
    monkeypatch.setattr(gateway, "get_settings", lambda: _settings(model_call_timeout_s=1))
    fake_litellm(hang_s=5, hang_after_chunk=True)
    trace = _trace()

    with tracing.start_turn_trace(trace, input="hi"):
        stream = await gateway.complete(
            [{"role": "user", "content": "hi"}], model="m/x", stream=True, trace=trace
        )
        with pytest.raises(ManaLeakError) as excinfo:
            await _drain(stream)

    assert excinfo.value.code is ErrorCode.timeout
    generation = span_capture.span_named("m/x")
    assert generation.end_time is not None


# --- Live (AC-1, AC-12 latency sanity; CHAT_MODEL) ---------------------------


@pytest.mark.live
async def test_live_streaming_completion_against_chat_model() -> None:
    settings = get_settings()
    gateway.start_turn_budget()

    stream = await gateway.complete(
        [{"role": "user", "content": "Reply with exactly the word OK."}],
        model=settings.chat_model,
        stream=True,
        max_tokens=20,
    )
    text = "".join(chunk.delta for chunk in [c async for c in stream])

    assert text.strip()
    assert gateway.current_call_count() == 1


@pytest.mark.live
async def test_live_streamed_usage_reaches_the_generation(span_capture) -> None:
    """AC-4's "token counts greater than zero" against the real provider:
    the fake can only replay the chunk shape this asserts is real, i.e.
    that `stream_options={"include_usage": True}` actually survives
    LiteLLM/OpenRouter and lands on the generation span."""
    settings = get_settings()
    gateway.start_turn_budget()
    trace = _trace()

    with tracing.start_turn_trace(trace, input="Reply with exactly the word OK."):
        stream = await gateway.complete(
            [{"role": "user", "content": "Reply with exactly the word OK."}],
            model=settings.chat_model,
            stream=True,
            max_tokens=20,
            trace=trace,
        )
        text = "".join([chunk.delta async for chunk in stream])

    generation = span_capture.span_named(settings.chat_model)
    usage = json.loads(_attr(generation, "usage_details"))
    assert text.strip()
    assert _attr(generation, "output") == text
    assert usage["input"] > 0 and usage["output"] > 0
    # Cost is the provider's to report; recorded when present, never faked.
    cost = _attr(generation, "cost_details")
    assert cost is None or json.loads(cost)["total"] >= 0
