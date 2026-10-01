"""M1 WP4 and M2 WP5 acceptance: `uv run pytest tests/core/test_turn.py`.

Covers the conversation service (`mana_leak_core.conversations`) and the turn
orchestrator (`mana_leak_core.orchestrator.process_turn`) against
`docs/contracts.md` -> Core service interfaces, Turn orchestration, Streaming
events, Conversation behaviour, M1's PRD AC-1, AC-4, AC-5, AC-7, AC-10,
AC-11, and M2's PRD AC-3 (code half), AC-4, AC-5, AC-8, AC-12.

Everything except the one `live` test replaces the model gateway's
`complete()` with a scripted fake; persistence, event ordering, the
conversation lock, and the turn deadline are all exercised for real against
the Compose `mana_leak_test` database (the `db` fixture, `tests/conftest.py`).

The M2 turn-trace section at the bottom drives turns in through the FastAPI
route and `sse_stream` rather than iterating `process_turn` directly (PRD §8;
plan -> WP5 checklist): which task drives the turn generator decides whether
OpenTelemetry's context survives it, so the adapter is part of what those
tests assert. Spans go to the in-memory exporter from `tests/core/conftest.py`
-- except in the two degradation tests, which deliberately build a real
client against a dead or hung local port.
"""

import asyncio
import json
import logging
import socket
import threading
import time
import uuid
from collections.abc import Callable, Iterator
from typing import Any

import pytest
from mana_leak_api.sse import sse_stream
from mana_leak_core import conversations, gateway, orchestrator, tracing
from mana_leak_core import db as db_module
from mana_leak_core.contracts.enums import AuditEventType, MessageRole, Route
from mana_leak_core.contracts.errors import ErrorCode, ManaLeakError
from mana_leak_core.contracts.events import (
    ErrorEvent,
    Final,
    MessageEnd,
    MessageStart,
    TextDelta,
)
from mana_leak_core.db.models import AuditEvent
from mana_leak_core.gateway import ModelChunk
from mana_leak_core.settings import Settings, get_settings
from pydantic import SecretStr
from sqlalchemy import select

pytestmark = pytest.mark.anyio


# --- Fixtures ----------------------------------------------------------------


@pytest.fixture
async def turn_db(db, _migrated_test_db: str | None, monkeypatch: pytest.MonkeyPatch):
    """`db` (migrated + truncated per test) plus `get_session()`'s own engine
    pointed at the same `mana_leak_test` database, because the conversation
    service and the orchestrator open their own sessions."""
    if _migrated_test_db is None:  # pragma: no cover - `db` already skipped
        pytest.skip("mana_leak_test database is unreachable")

    monkeypatch.setenv("DATABASE_URL", _migrated_test_db)
    get_settings.cache_clear()
    db_module.get_engine.cache_clear()
    db_module.session._session_factory.cache_clear()
    try:
        yield db
    finally:
        await db_module.get_engine().dispose()
        get_settings.cache_clear()
        db_module.get_engine.cache_clear()
        db_module.session._session_factory.cache_clear()


class FakeGateway:
    """Scripted stand-in for `mana_leak_core.gateway.complete`. Records the
    messages it was called with and replays chunks, optionally hanging after
    them (to exercise the turn deadline and client disconnect) or failing.

    It opens a `tracing.record_generation` observation in the same place the
    real gateway does -- inside the streaming generator's own body, not in
    `complete()` -- so the orchestrator's turn trace can be asserted for
    real (AC-4) without depending on the gateway's own M2 changes. With
    tracing disabled (every other test here) that call is a no-op."""

    def __init__(
        self,
        *,
        deltas: tuple[str, ...] = ("Mana ", "Leak."),
        hang_after_deltas: bool = False,
        raises: Exception | None = None,
    ) -> None:
        self.deltas = deltas
        self.hang_after_deltas = hang_after_deltas
        self.raises = raises
        self.calls: list[dict[str, Any]] = []

    async def complete(self, messages, **kwargs: Any):
        self.calls.append({"messages": list(messages), **kwargs})
        if self.raises is not None:
            raise self.raises
        return self._stream(list(messages), kwargs)

    async def _stream(self, messages: list[Any], kwargs: dict[str, Any]):
        with tracing.record_generation(
            kwargs.get("trace"), model=kwargs.get("model", "fake-model"), input=messages
        ) as generation:
            for delta in self.deltas:
                yield ModelChunk(delta=delta)
            if self.hang_after_deltas:
                await asyncio.sleep(3600)
            yield ModelChunk(delta="", finish_reason="stop")
            generation.update(output="".join(self.deltas))


@pytest.fixture
def fake_gateway(monkeypatch: pytest.MonkeyPatch):
    def _install(**kwargs: Any) -> FakeGateway:
        fake = FakeGateway(**kwargs)
        monkeypatch.setattr(orchestrator, "complete", fake.complete)
        return fake

    return _install


@pytest.fixture
def short_turn_deadline(monkeypatch: pytest.MonkeyPatch):
    """Shrink only `turn_timeout_s`, leaving the rest of `Settings` real."""

    def _install(seconds: int) -> None:
        base = get_settings()
        shortened = Settings(
            **{**base.model_dump(), "openrouter_api_key": base.openrouter_api_key},
        ).model_copy(update={"turn_timeout_s": seconds})
        monkeypatch.setattr(orchestrator, "get_settings", lambda: shortened)

    return _install


def _gateway_settings(**overrides: Any) -> Settings:
    """Real `Settings` with `overrides` applied, for patching the *gateway*'s
    `get_settings` (its own limits, not the orchestrator's)."""
    base = get_settings()
    return Settings(
        **{**base.model_dump(), "openrouter_api_key": base.openrouter_api_key},
    ).model_copy(update=overrides)


async def _new_conversation(title: str | None = None) -> uuid.UUID:
    return (await conversations.create_conversation(title)).id


async def _drain(generator) -> list:
    return [event async for event in generator]


async def _wait_for_assistant_message(conversation_id: uuid.UUID, timeout: float = 5.0):
    """The disconnect path persists under `asyncio.shield`, so the write can
    outlive the cancelled task by a moment."""
    deadline = asyncio.get_running_loop().time() + timeout
    while True:
        messages = (await conversations.get_conversation(conversation_id)).messages
        assistant = next((m for m in messages if m.role is MessageRole.assistant), None)
        if assistant is not None:
            return assistant
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError(f"no assistant message persisted: {messages}")
        await asyncio.sleep(0.05)


# --- Conversation service ------------------------------------------------------


async def test_create_conversation_persists_and_returns_it(turn_db) -> None:
    created = await conversations.create_conversation("Deck help")

    detail = await conversations.get_conversation(created.id)
    assert detail.id == created.id
    assert detail.title == "Deck help"
    assert detail.messages == []
    assert detail.active_judge_session is None


async def test_get_conversation_unknown_id_is_not_found(turn_db) -> None:
    with pytest.raises(ManaLeakError) as excinfo:
        await conversations.get_conversation(uuid.uuid4())

    assert excinfo.value.code == ErrorCode.not_found


async def test_list_conversations_is_most_recently_updated_first(turn_db) -> None:
    first = await _new_conversation("first")
    second = await _new_conversation("second")
    # A new message in `first` makes it the most recently updated.
    await conversations.append_message(first, uuid.uuid4(), MessageRole.user, "hello")

    listed = await conversations.list_conversations()

    assert [row.id for row in listed] == [first, second]


async def test_list_conversations_respects_limit(turn_db) -> None:
    for index in range(3):
        await _new_conversation(f"conversation {index}")

    assert len(await conversations.list_conversations(limit=2)) == 2


async def test_append_message_allocates_sequential_seq_per_conversation(turn_db) -> None:
    conversation_id = await _new_conversation()
    other_id = await _new_conversation()
    turn_id = uuid.uuid4()

    first = await conversations.append_message(
        conversation_id, turn_id, MessageRole.user, "why does this work?"
    )
    second = await conversations.append_message(
        conversation_id, turn_id, MessageRole.assistant, "because", route=Route.other
    )
    elsewhere = await conversations.append_message(
        other_id, uuid.uuid4(), MessageRole.user, "unrelated"
    )

    assert (first.seq, second.seq) == (1, 2)
    assert elsewhere.seq == 1  # seq is per conversation, not global


async def test_append_message_titles_the_conversation_from_the_first_user_message(
    turn_db,
) -> None:
    conversation_id = await _new_conversation()

    await conversations.append_message(
        conversation_id, uuid.uuid4(), MessageRole.user, "Does deathtouch beat trample?"
    )
    await conversations.append_message(
        conversation_id, uuid.uuid4(), MessageRole.user, "a later message"
    )

    detail = await conversations.get_conversation(conversation_id)
    assert detail.title == "Does deathtouch beat trample?"


async def test_append_message_keeps_an_explicit_title(turn_db) -> None:
    conversation_id = await _new_conversation("Chosen title")

    await conversations.append_message(
        conversation_id, uuid.uuid4(), MessageRole.user, "Does deathtouch beat trample?"
    )

    assert (await conversations.get_conversation(conversation_id)).title == "Chosen title"


async def test_append_message_truncates_a_long_title(turn_db) -> None:
    conversation_id = await _new_conversation()

    await conversations.append_message(
        conversation_id, uuid.uuid4(), MessageRole.user, "word " * 50
    )

    title = (await conversations.get_conversation(conversation_id)).title
    assert len(title) == conversations.TITLE_MAX_CHARS
    assert title.endswith("\u2026")


async def test_append_message_bumps_conversation_updated_at(turn_db) -> None:
    conversation_id = await _new_conversation()
    before = (await conversations.get_conversation(conversation_id)).updated_at

    await conversations.append_message(conversation_id, uuid.uuid4(), MessageRole.user, "hi")

    assert (await conversations.get_conversation(conversation_id)).updated_at > before


async def test_append_message_unknown_conversation_is_not_found(turn_db) -> None:
    with pytest.raises(ManaLeakError) as excinfo:
        await conversations.append_message(uuid.uuid4(), uuid.uuid4(), MessageRole.user, "orphan")

    assert excinfo.value.code == ErrorCode.not_found


async def test_get_conversation_returns_messages_in_ascending_seq(turn_db) -> None:
    conversation_id = await _new_conversation()
    turn_id = uuid.uuid4()
    await conversations.append_message(conversation_id, turn_id, MessageRole.user, "one")
    await conversations.append_message(
        conversation_id,
        turn_id,
        MessageRole.assistant,
        "two",
        payload={"error": {"code": "timeout", "message": "client disconnected"}},
        route=Route.other,
    )

    messages = (await conversations.get_conversation(conversation_id)).messages

    assert [(m.seq, m.role, m.content) for m in messages] == [
        (1, MessageRole.user, "one"),
        (2, MessageRole.assistant, "two"),
    ]
    assert messages[1].route is Route.other
    assert messages[1].payload == {"error": {"code": "timeout", "message": "client disconnected"}}


async def test_build_context_keeps_only_the_last_ten_turns(turn_db) -> None:
    conversation_id = await _new_conversation()
    for index in range(12):
        turn_id = uuid.uuid4()
        await conversations.append_message(
            conversation_id, turn_id, MessageRole.user, f"question {index}"
        )
        await conversations.append_message(
            conversation_id, turn_id, MessageRole.assistant, f"answer {index}", route=Route.other
        )

    context = await conversations.build_context(conversation_id)

    # 10 turns x (user + assistant), starting at the 10th-from-last user message.
    assert len(context.messages) == 20
    assert context.messages[0].content == "question 2"
    assert context.messages[-1].content == "answer 11"
    assert [m.seq for m in context.messages] == sorted(m.seq for m in context.messages)
    assert context.summary is None  # summarisation is M8


async def test_build_context_of_an_empty_conversation_is_empty(turn_db) -> None:
    context = await conversations.build_context(await _new_conversation())

    assert context.messages == []


async def test_build_context_unknown_conversation_is_not_found(turn_db) -> None:
    with pytest.raises(ManaLeakError) as excinfo:
        await conversations.build_context(uuid.uuid4())

    assert excinfo.value.code == ErrorCode.not_found


# --- process_turn: happy path (AC-1, AC-4) -------------------------------------


async def test_turn_streams_message_start_deltas_final_message_end(turn_db, fake_gateway) -> None:
    fake_gateway(deltas=("Mana ", "Leak", "."))
    conversation_id = await _new_conversation()

    events = await _drain(orchestrator.process_turn(conversation_id, "hello there"))

    assert [type(event) for event in events] == [
        MessageStart,
        TextDelta,
        TextDelta,
        TextDelta,
        Final,
        MessageEnd,
    ]
    final = events[-2]
    assert final.route is Route.other
    assert final.result is None
    assert final.text == "Mana Leak."
    assert final.error is None
    # Every event belongs to the same turn and conversation.
    assert {event.turn_id for event in events} == {events[0].turn_id}
    assert {event.conversation_id for event in events} == {conversation_id}


async def test_turn_persists_user_and_assistant_messages(turn_db, fake_gateway) -> None:
    fake_gateway(deltas=("answered",))
    conversation_id = await _new_conversation()

    events = await _drain(orchestrator.process_turn(conversation_id, "why does this work?"))

    messages = (await conversations.get_conversation(conversation_id)).messages
    assert [(m.seq, m.role, m.content) for m in messages] == [
        (1, MessageRole.user, "why does this work?"),
        (2, MessageRole.assistant, "answered"),
    ]
    assistant = messages[1]
    # `message_start` announces the id the assistant message is persisted with,
    # so the client can correlate the stream with the reloaded history.
    assert assistant.id == events[0].message_id
    assert assistant.turn_id == events[0].turn_id
    assert assistant.route is Route.other
    assert assistant.payload is None  # `other`-route TurnResult is None


@pytest.mark.parametrize(
    "message",
    [
        "Lightning Bolt",
        "does deathtouch beat trample?",
        "what should I have for dinner?",
    ],
)
async def test_every_message_routes_to_other(turn_db, fake_gateway, message: str) -> None:
    fake_gateway()
    conversation_id = await _new_conversation()

    events = await _drain(orchestrator.process_turn(conversation_id, message))

    assert events[-2].route is Route.other
    assert events[-2].result is None


async def test_turn_sends_prior_history_to_the_model_once(turn_db, fake_gateway) -> None:
    fake = fake_gateway(deltas=("ok",))
    conversation_id = await _new_conversation()

    await _drain(orchestrator.process_turn(conversation_id, "first question"))
    await _drain(orchestrator.process_turn(conversation_id, "second question"))

    sent = fake.calls[1]["messages"]
    assert sent[0]["role"] == "system"
    assert [(m["role"], m["content"]) for m in sent[1:]] == [
        ("user", "first question"),
        ("assistant", "ok"),
        ("user", "second question"),  # the current message appears exactly once
    ]
    assert fake.calls[1]["stream"] is True
    assert fake.calls[1]["model"] == get_settings().chat_model


async def test_turn_resets_the_model_call_budget_at_turn_start(turn_db, fake_gateway) -> None:
    fake_gateway()
    gateway._call_count.set(5)  # leftover from an earlier turn in this task

    await _drain(orchestrator.process_turn(await _new_conversation(), "hello"))

    # The fake never calls the real gateway, so a zero count proves
    # `start_turn_budget()` ran at the start of this turn.
    assert gateway.current_call_count() == 0


# --- process_turn: validation (step 1) -----------------------------------------


@pytest.mark.parametrize(
    "user_message",
    ["", "   ", None],
)
async def test_missing_user_message_is_a_validation_error(
    turn_db, fake_gateway, user_message
) -> None:
    fake = fake_gateway()
    conversation_id = await _new_conversation()

    with pytest.raises(ManaLeakError) as excinfo:
        await anext(orchestrator.process_turn(conversation_id, user_message))

    assert excinfo.value.code == ErrorCode.validation_error
    assert fake.calls == []
    assert (await conversations.get_conversation(conversation_id)).messages == []


async def test_over_length_user_message_is_a_validation_error(turn_db, fake_gateway) -> None:
    fake_gateway()
    conversation_id = await _new_conversation()
    too_long = "x" * (get_settings().max_user_message_chars + 1)

    with pytest.raises(ManaLeakError) as excinfo:
        await anext(orchestrator.process_turn(conversation_id, too_long))

    assert excinfo.value.code == ErrorCode.validation_error
    assert (await conversations.get_conversation(conversation_id)).messages == []


async def test_a_message_at_the_length_limit_is_accepted(turn_db, fake_gateway) -> None:
    fake_gateway(deltas=("ok",))
    conversation_id = await _new_conversation()
    at_limit = "x" * get_settings().max_user_message_chars

    events = await _drain(orchestrator.process_turn(conversation_id, at_limit))

    assert isinstance(events[-2], Final)


async def test_unknown_session_action_is_a_validation_error(turn_db, fake_gateway) -> None:
    fake_gateway()
    conversation_id = await _new_conversation()

    with pytest.raises(ManaLeakError) as excinfo:
        await anext(orchestrator.process_turn(conversation_id, "hello", session_action="explode"))

    assert excinfo.value.code == ErrorCode.validation_error


@pytest.mark.parametrize("session_action", ["answer", "new_question"])
async def test_known_session_action_is_accepted_as_a_no_op(
    turn_db, fake_gateway, session_action: str
) -> None:
    fake_gateway(deltas=("ok",))
    conversation_id = await _new_conversation()

    events = await _drain(
        orchestrator.process_turn(conversation_id, "hello", session_action=session_action)
    )

    assert events[-2].route is Route.other


async def test_forced_route_other_is_accepted_and_other_routes_are_refused(
    turn_db, fake_gateway
) -> None:
    fake_gateway(deltas=("ok",))
    conversation_id = await _new_conversation()

    events = await _drain(
        orchestrator.process_turn(conversation_id, "hello", forced_route=Route.other)
    )
    assert events[-2].route is Route.other

    with pytest.raises(ManaLeakError) as excinfo:
        await anext(orchestrator.process_turn(conversation_id, "hello", forced_route=Route.judge))
    assert excinfo.value.code == ErrorCode.validation_error


async def test_turn_on_an_unknown_conversation_is_not_found(turn_db, fake_gateway) -> None:
    fake_gateway()

    with pytest.raises(ManaLeakError) as excinfo:
        await anext(orchestrator.process_turn(uuid.uuid4(), "hello"))

    assert excinfo.value.code == ErrorCode.not_found


# --- process_turn: conflict lock (AC-5) ----------------------------------------


async def test_second_turn_on_the_same_conversation_conflicts(turn_db, fake_gateway) -> None:
    fake_gateway(deltas=("partial",), hang_after_deltas=True)
    conversation_id = await _new_conversation()

    first = orchestrator.process_turn(conversation_id, "first")
    assert isinstance(await anext(first), MessageStart)

    second = orchestrator.process_turn(conversation_id, "second")
    with pytest.raises(ManaLeakError) as excinfo:
        await anext(second)  # raised on the first __anext__(), before any event

    assert excinfo.value.code == ErrorCode.conflict
    # The rejected turn persisted nothing of its own.
    contents = [m.content for m in (await conversations.get_conversation(conversation_id)).messages]
    assert "second" not in contents

    await first.aclose()


async def test_a_different_conversation_is_not_blocked(turn_db, fake_gateway) -> None:
    fake_gateway(deltas=("partial",), hang_after_deltas=True)
    busy = await _new_conversation()
    other = await _new_conversation()

    first = orchestrator.process_turn(busy, "first")
    await anext(first)

    second = orchestrator.process_turn(other, "elsewhere")
    assert isinstance(await anext(second), MessageStart)

    await first.aclose()
    await second.aclose()


async def test_the_lock_is_released_after_an_aborted_turn(turn_db, fake_gateway) -> None:
    fake_gateway(deltas=("partial",), hang_after_deltas=True)
    conversation_id = await _new_conversation()

    aborted = orchestrator.process_turn(conversation_id, "first")
    await anext(aborted)
    await aborted.aclose()

    # A new turn on the same conversation must start, not conflict.
    retried = orchestrator.process_turn(conversation_id, "second")
    assert isinstance(await anext(retried), MessageStart)
    await retried.aclose()


# --- process_turn: client disconnect (AC-10) -----------------------------------


async def test_client_disconnect_persists_partial_text_with_payload_error(
    turn_db, fake_gateway
) -> None:
    fake_gateway(deltas=("partial answer",), hang_after_deltas=True)
    conversation_id = await _new_conversation()

    turn = orchestrator.process_turn(conversation_id, "hello")
    await anext(turn)  # message_start
    delta = await anext(turn)
    assert isinstance(delta, TextDelta)

    await turn.aclose()  # the SSE client went away

    messages = (await conversations.get_conversation(conversation_id)).messages
    assistant = messages[-1]
    assert assistant.role is MessageRole.assistant
    assert assistant.content == "partial answer"
    assert assistant.payload == {"error": {"code": "timeout", "message": "client disconnected"}}
    assert assistant.route is Route.other


async def test_cancelling_the_streaming_task_persists_partial_text(turn_db, fake_gateway) -> None:
    """The real disconnect shape: the API's turn task is cancelled while the
    generator is suspended inside the model stream, not politely closed."""
    fake_gateway(deltas=("partial answer",), hang_after_deltas=True)
    conversation_id = await _new_conversation()
    streaming = asyncio.Event()

    async def consume() -> None:
        async for event in orchestrator.process_turn(conversation_id, "hello"):
            if isinstance(event, TextDelta):
                streaming.set()

    task = asyncio.create_task(consume())
    await streaming.wait()
    await asyncio.sleep(0)  # let the generator reach the hanging model stream
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assistant = await _wait_for_assistant_message(conversation_id)
    assert assistant.content == "partial answer"
    assert assistant.payload == {"error": {"code": "timeout", "message": "client disconnected"}}
    # The conversation is usable again: the lock was released in `finally`.
    retried = orchestrator.process_turn(conversation_id, "still there?")
    assert isinstance(await anext(retried), MessageStart)
    await retried.aclose()


async def test_a_completed_turn_is_not_persisted_twice_on_close(turn_db, fake_gateway) -> None:
    fake_gateway(deltas=("done",))
    conversation_id = await _new_conversation()

    turn = orchestrator.process_turn(conversation_id, "hello")
    await _drain(turn)
    await turn.aclose()

    messages = (await conversations.get_conversation(conversation_id)).messages
    assert [m.role for m in messages] == [MessageRole.user, MessageRole.assistant]
    assert messages[-1].payload is None


# --- process_turn: turn deadline (AC-7) and model failure (AC-6 server half) ----


async def test_turn_deadline_emits_error_then_message_end_and_persists_partial_text(
    turn_db, fake_gateway, short_turn_deadline
) -> None:
    short_turn_deadline(1)
    fake_gateway(deltas=("partial",), hang_after_deltas=True)
    conversation_id = await _new_conversation()

    events = await _drain(orchestrator.process_turn(conversation_id, "hello"))

    assert [type(event) for event in events] == [MessageStart, TextDelta, ErrorEvent, MessageEnd]
    assert events[-2].error.code is ErrorCode.timeout

    assistant = (await conversations.get_conversation(conversation_id)).messages[-1]
    assert assistant.content == "partial"
    assert assistant.payload["error"]["code"] == "timeout"

    rows = (
        (await turn_db.execute(select(AuditEvent).where(AuditEvent.turn_id == events[0].turn_id)))
        .scalars()
        .all()
    )
    assert [row.event_type for row in rows] == [AuditEventType.limit_reached.value]
    assert rows[0].details == {"limit": "turn_timeout_s", "value": 1}


async def test_a_model_failure_ends_the_turn_with_an_error_event(turn_db, fake_gateway) -> None:
    fake_gateway(raises=ManaLeakError(ErrorCode.timeout, "model call timed out", retryable=True))
    conversation_id = await _new_conversation()

    events = await _drain(orchestrator.process_turn(conversation_id, "hello"))

    assert [type(event) for event in events] == [MessageStart, ErrorEvent, MessageEnd]
    assert events[1].error.code is ErrorCode.timeout
    assert events[1].error.retryable is True

    assistant = (await conversations.get_conversation(conversation_id)).messages[-1]
    assert assistant.role is MessageRole.assistant
    assert assistant.content == ""
    assert assistant.payload == {"error": {"code": "timeout", "message": "model call timed out"}}


async def test_an_unexpected_failure_becomes_an_internal_error_event(turn_db, fake_gateway) -> None:
    fake_gateway(raises=RuntimeError("litellm exploded"))
    conversation_id = await _new_conversation()

    events = await _drain(orchestrator.process_turn(conversation_id, "hello"))

    assert [type(event) for event in events] == [MessageStart, ErrorEvent, MessageEnd]
    assert events[1].error.code is ErrorCode.internal_error
    # The raw exception text never reaches the client.
    assert "litellm exploded" not in events[1].error.message


async def test_the_model_call_cap_ends_the_turn_with_model_limit_exceeded(
    turn_db, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The real gateway, not a fake: with the absolute cap at 0 the turn's
    only model call is refused before LiteLLM is contacted. The client sees
    the code `contracts.md` -> Error taxonomy documents
    (`model_limit_exceeded`, not a remapped `internal_error`), and the
    gateway's own `limit_reached` row names the turn it belongs to."""
    monkeypatch.setattr(gateway, "get_settings", lambda: _gateway_settings(model_calls_max=0))
    conversation_id = await _new_conversation()

    events = await _drain(orchestrator.process_turn(conversation_id, "hello"))

    assert [type(event) for event in events] == [MessageStart, ErrorEvent, MessageEnd]
    assert events[1].error.code is ErrorCode.model_limit_exceeded
    assert events[1].error.details == {"limit": "model_calls_max", "value": 0}

    assistant = (await conversations.get_conversation(conversation_id)).messages[-1]
    assert assistant.payload["error"]["code"] == "model_limit_exceeded"

    rows = (
        (await turn_db.execute(select(AuditEvent).where(AuditEvent.turn_id == events[0].turn_id)))
        .scalars()
        .all()
    )
    assert [row.event_type for row in rows] == [AuditEventType.limit_reached.value]
    assert rows[0].conversation_id == conversation_id
    assert rows[0].details == {"limit": "model_calls_max", "value": 0}


# --- process_turn: Langfuse turn trace (M2; AC-3, AC-4, AC-5, AC-8, AC-12) -----


#: `propagate_attributes(session_id=...)`'s span attribute, the key
#: `start_turn_trace`'s `metadata={"route": ...}` lands under, and the
#: observation output -- all three the Langfuse SDK's own names, pinned here
#: so an SDK rename fails loudly instead of silently dropping the session
#: grouping AC-3 depends on.
SESSION_ATTRIBUTE = "session.id"
ROUTE_ATTRIBUTE = "langfuse.observation.metadata.route"
OUTPUT_ATTRIBUTE = "langfuse.observation.output"


@pytest.fixture
def hung_langfuse() -> Iterator[str]:
    """A local TCP listener that accepts connections and never answers: the
    AC-12 hang, which a stopped port (AC-5) does not reproduce because that
    one is refused immediately."""
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen(8)
    accepted: list[socket.socket] = []

    def _accept() -> None:
        while True:
            try:
                conn, _ = server.accept()
            except OSError:
                return
            accepted.append(conn)

    thread = threading.Thread(target=_accept, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.getsockname()[1]}"
    finally:
        server.close()
        for conn in accepted:
            conn.close()


@pytest.fixture
def real_langfuse_client(monkeypatch: pytest.MonkeyPatch) -> Iterator[Callable[[str], None]]:
    """Enable tracing with a *real* SDK client and a real OTLP exporter
    pointed at `base_url` -- no in-memory exporter, because what AC-5/AC-12
    are about is the network the exporter would use. The keys are unique per
    client: the SDK keys its internal resource manager by `public_key` and
    silently reuses the first client registered under one."""
    built: list[Any] = []

    def _enable(base_url: str) -> None:
        monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", f"pk-lf-{uuid.uuid4().hex}")
        monkeypatch.setenv("LANGFUSE_SECRET_KEY", f"sk-lf-{uuid.uuid4().hex}")
        monkeypatch.setenv("LANGFUSE_HOST", base_url)
        get_settings.cache_clear()
        tracing._get_client.cache_clear()
        client = tracing._get_client()
        assert client is not None, "tracing should be enabled for this test"
        built.append(client)

    yield _enable
    for client in built:
        client.shutdown()
    get_settings.cache_clear()


def _parse_sse(raw: str) -> list[tuple[str, dict[str, Any]]]:
    """`event: <type>\\ndata: <json>\\n\\n` -> `[(type, data), ...]`, skipping
    `: ping` comments (contracts.md -> SSE mapping)."""
    events: list[tuple[str, dict[str, Any]]] = []
    event_type: str | None = None
    for line in raw.split("\n"):
        if line.startswith("event: "):
            event_type = line.removeprefix("event: ")
        elif line.startswith("data: ") and event_type is not None:
            events.append((event_type, json.loads(line.removeprefix("data: "))))
            event_type = None
    return events


async def _sse_turn(
    conversation_id: uuid.UUID, content: str
) -> list[tuple[str, dict[str, Any]]]:
    """One turn through `sse.py`'s encoder, driven the way the API drives it
    -- not by iterating `process_turn` directly (PRD §8, plan -> WP5
    checklist).

    Which task drives the turn generator is part of what is under test: an
    OpenTelemetry context attached in one task and detached in another is
    exactly the failure AC-4's "no tracing error is logged" rules out, and it
    is invisible when a test iterates the orchestrator itself.

    This mirrors the router: the first `__anext__` is pulled by the caller so
    a pre-stream `ManaLeakError` (conflict, or AC-8's secret check) maps to
    an HTTP status instead of an in-stream event. That split is itself the
    bug WP6 fixes by starting `sse_stream`'s producer task first; when it
    lands, this helper follows the new call shape.
    """
    agen = orchestrator.process_turn(conversation_id, content)
    first_event = await anext(agen)
    raw = b"".join([chunk async for chunk in sse_stream(agen, first_event)])
    return _parse_sse(raw.decode())


def _tracing_settings(**overrides: Any) -> Settings:
    base = get_settings()
    return Settings(
        **{**base.model_dump(), "openrouter_api_key": base.openrouter_api_key},
    ).model_copy(update=overrides)


async def test_the_turn_trace_nests_the_model_call_as_its_generation(
    turn_db, fake_gateway, span_capture
) -> None:
    """AC-4: one turn-level trace carrying the route, with the model call as
    a child generation of it -- never a second root span."""
    fake_gateway(deltas=("Mana ", "Leak."))
    conversation_id = await _new_conversation()

    events = await _sse_turn(conversation_id, "what does mana leak do?")

    assert [name for name, _ in events] == [
        "message_start",
        "text_delta",
        "text_delta",
        "final",
        "message_end",
    ]
    turn_id = uuid.UUID(events[0][1]["turn_id"])

    spans = span_capture.finished_spans()
    assert sorted(span.name for span in spans) == sorted(
        [str(turn_id), get_settings().chat_model]
    ), f"expected exactly a turn span and its generation, got {[s.name for s in spans]}"
    turn_span = span_capture.span_named(str(turn_id))
    generation = span_capture.span_named(get_settings().chat_model)

    # The turn span's only ancestor is the synthetic context `start_turn_trace`
    # uses to pin the OTel trace id to `turn_id`; it is the trace's own root
    # observation, and the generation hangs off it rather than opening a
    # second root (AC-4).
    assert turn_span.parent is not None
    assert turn_span.parent.trace_id == turn_span.context.trace_id
    assert generation.parent is not None
    assert generation.parent.span_id == turn_span.context.span_id
    # NFR-3: the Langfuse trace id *is* the persisted `turn_id`, on both spans.
    assert format(turn_span.context.trace_id, "032x") == turn_id.hex
    assert format(generation.context.trace_id, "032x") == turn_id.hex
    assert turn_span.attributes[SESSION_ATTRIBUTE] == str(conversation_id)
    assert turn_span.attributes[ROUTE_ATTRIBUTE] == Route.other.value
    assert turn_span.attributes[OUTPUT_ATTRIBUTE] == "Mana Leak."


async def test_a_traced_turn_logs_no_opentelemetry_context_error(
    turn_db, fake_gateway, span_capture, caplog: pytest.LogCaptureFixture
) -> None:
    """AC-4's second half (PRD §8: "no tracing error is logged").

    The turn-level `with` is entered on the generator's first `__anext__()`
    and left on its last, so whoever pulls the first event must also pull the
    rest: OpenTelemetry's `detach` compares the token's context identity and
    logs `Failed to detach context` when a `with` block is entered in one
    task and left in another. Verified reproducible both ways in isolation,
    so this assertion is a real guard, not a formality.

    It fails until the SSE adapter starts its producer task before the first
    event (plan -> WP6 checklist, F1): today the route pulls `message_start`
    in the request task and the producer task pulls the rest, which splits
    this `with` block across two contexts. Nothing in `orchestrator.py` can
    fix that from its side -- a turn generator cannot choose its driver.
    """
    fake_gateway(deltas=("Mana ", "Leak."))
    conversation_id = await _new_conversation()

    with caplog.at_level(logging.ERROR, logger="opentelemetry.context"):
        events = await _sse_turn(conversation_id, "what does mana leak do?")

    assert [name for name, _ in events][-1] == "message_end"
    assert [record.getMessage() for record in caplog.records] == []


async def test_each_turn_is_its_own_trace_under_one_session(
    turn_db, fake_gateway, span_capture
) -> None:
    """AC-3's code half: one trace per turn, all under a session named by the
    conversation id."""
    fake_gateway(deltas=("ok",))
    conversation_id = await _new_conversation()

    first = await _sse_turn(conversation_id, "first question")
    second = await _sse_turn(conversation_id, "second question")

    turn_ids = [uuid.UUID(events[0][1]["turn_id"]) for events in (first, second)]
    assert turn_ids[0] != turn_ids[1]
    turn_spans = [span_capture.span_named(str(turn_id)) for turn_id in turn_ids]

    assert [format(span.context.trace_id, "032x") for span in turn_spans] == [
        turn_id.hex for turn_id in turn_ids
    ]
    assert {span.attributes[SESSION_ATTRIBUTE] for span in turn_spans} == {str(conversation_id)}


async def test_the_turn_trace_records_partial_text_when_the_turn_errors(
    turn_db, fake_gateway, span_capture
) -> None:
    """The single `turn_span.update(output=...)` covers the failure branches
    too: a turn that ends in an `error` event still records what was
    persisted, and still produces exactly one trace."""
    fake_gateway(raises=ManaLeakError(ErrorCode.timeout, "model call timed out", retryable=True))
    conversation_id = await _new_conversation()

    events = await _sse_turn(conversation_id, "what does mana leak do?")

    assert [name for name, _ in events] == ["message_start", "error", "message_end"]
    turn_id = uuid.UUID(events[0][1]["turn_id"])
    turn_span = span_capture.span_named(str(turn_id))
    assert turn_span.attributes[OUTPUT_ATTRIBUTE] == ""


async def test_a_message_containing_a_langfuse_secret_is_never_traced(
    turn_db, fake_gateway, span_capture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AC-8/NFR-2: the gateway's generic secret check runs *before* the turn
    trace opens, so a message carrying the configured `LANGFUSE_SECRET_KEY`
    fails the turn closed -- no span at all, not a scrubbed one.

    The error is raised on the generator's first `__anext__()`, so it reaches
    the adapter as an HTTP error (`errors.py` maps `internal_error` to 500),
    never as an in-stream `error` event.
    """
    secret = "sk-lf-5f0c2a9e4d7b41c8"
    monkeypatch.setattr(
        orchestrator,
        "get_settings",
        lambda: _tracing_settings(langfuse_secret_key=SecretStr(secret)),
    )
    fake = fake_gateway(deltas=("never reached",))
    conversation_id = await _new_conversation()

    with pytest.raises(ManaLeakError) as excinfo:
        await _sse_turn(conversation_id, f"is {secret} a good card?")

    assert excinfo.value.code == ErrorCode.internal_error
    assert secret not in excinfo.value.message
    assert span_capture.finished_spans() == []  # no trace was ever opened
    assert fake.calls == []  # and no model call was attempted
    assert (await conversations.get_conversation(conversation_id)).messages == []


def _comparable(events: list[tuple[str, dict[str, Any]]]) -> list[tuple[str, dict[str, Any]]]:
    """SSE events with the per-turn identifiers dropped, so two turns of the
    same conversation can be compared for sequence and content."""
    volatile = ("conversation_id", "turn_id", "message_id")
    return [
        (name, {k: v for k, v in payload.items() if k not in volatile}) for name, payload in events
    ]


async def _timed_turn(conversation_id: uuid.UUID) -> tuple[list[Any], float]:
    started = time.monotonic()
    events = await _sse_turn(conversation_id, "what does mana leak do?")
    return events, time.monotonic() - started


async def test_an_unreachable_langfuse_leaves_the_turn_identical(
    turn_db, fake_gateway, real_langfuse_client
) -> None:
    """AC-5/FR-6/NFR-1: Langfuse stopped (connection refused) changes neither
    the SSE sequence, the answer, nor the turn's timing."""
    fake_gateway(deltas=("Mana ", "Leak."))
    baseline_events, baseline_elapsed = await _timed_turn(await _new_conversation())

    # A port nothing is listening on: `start_turn_trace` must not even try.
    closed = socket.socket()
    closed.bind(("127.0.0.1", 0))
    stopped_port = closed.getsockname()[1]
    closed.close()
    real_langfuse_client(f"http://127.0.0.1:{stopped_port}")

    events, elapsed = await _timed_turn(await _new_conversation())

    assert _comparable(events) == _comparable(baseline_events)
    assert elapsed < baseline_elapsed + 2.0, f"tracing added {elapsed - baseline_elapsed:.2f}s"


async def test_a_hung_langfuse_leaves_the_turn_identical(
    turn_db, fake_gateway, real_langfuse_client, hung_langfuse: str
) -> None:
    """AC-12/NFR-1: a Langfuse that accepts the connection and never answers
    is bounded the same way, because span creation is purely in-memory --
    `orchestrator.py` adds no timeout of its own for it."""
    fake_gateway(deltas=("Mana ", "Leak."))
    baseline_events, baseline_elapsed = await _timed_turn(await _new_conversation())

    real_langfuse_client(hung_langfuse)

    events, elapsed = await _timed_turn(await _new_conversation())

    assert _comparable(events) == _comparable(baseline_events)
    assert elapsed < baseline_elapsed + 2.0, f"tracing added {elapsed - baseline_elapsed:.2f}s"


# --- Live (AC-1 through the real gateway; CHAT_MODEL) --------------------------


@pytest.mark.live
async def test_live_turn_streams_and_persists_a_plain_answer(turn_db) -> None:
    conversation_id = await _new_conversation()

    events = await _drain(
        orchestrator.process_turn(conversation_id, "In one short sentence, what is a mana leak?")
    )

    assert isinstance(events[0], MessageStart)
    assert isinstance(events[-1], MessageEnd)
    final = events[-2]
    assert isinstance(final, Final), f"turn ended with {events[-2]!r}"
    assert final.route is Route.other
    assert final.result is None
    assert final.text.strip()
    assert final.text == "".join(e.delta for e in events if isinstance(e, TextDelta))

    messages = (await conversations.get_conversation(conversation_id)).messages
    assert [m.role for m in messages] == [MessageRole.user, MessageRole.assistant]
    assert messages[1].content == final.text
    assert messages[1].payload is None
    assert gateway.current_call_count() == 1  # exactly one model call for an `other` turn
