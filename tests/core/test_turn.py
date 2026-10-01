"""WP4 acceptance: `uv run pytest tests/core/test_turn.py`.

Covers the conversation service (`mana_leak_core.conversations`) and the turn
orchestrator (`mana_leak_core.orchestrator.process_turn`) against
`docs/contracts.md` -> Core service interfaces, Turn orchestration, Streaming
events, Conversation behaviour, and PRD AC-1, AC-4, AC-5, AC-7, AC-10, AC-11.

Everything except the one `live` test replaces the model gateway's
`complete()` with a scripted fake; persistence, event ordering, the
conversation lock, and the turn deadline are all exercised for real against
the Compose `mana_leak_test` database (the `db` fixture, `tests/conftest.py`).
"""

import asyncio
import uuid
from typing import Any

import pytest
from mana_leak_core import conversations, gateway, orchestrator
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
    them (to exercise the turn deadline and client disconnect) or failing."""

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
        return self._stream()

    async def _stream(self):
        for delta in self.deltas:
            yield ModelChunk(delta=delta)
        if self.hang_after_deltas:
            await asyncio.sleep(3600)
        yield ModelChunk(delta="", finish_reason="stop")


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


async def test_an_unmapped_error_code_is_surfaced_as_internal_error(turn_db, fake_gateway) -> None:
    # The gateway raises `model_limit_exceeded`, which is outside M1's
    # `ErrorCode` subset (`contracts/errors.py`).
    fake_gateway(raises=ManaLeakError("model_limit_exceeded", "budget of 8 calls exceeded"))
    conversation_id = await _new_conversation()

    events = await _drain(orchestrator.process_turn(conversation_id, "hello"))

    assert events[1].error.code is ErrorCode.internal_error
    assert events[1].error.message == "budget of 8 calls exceeded"


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
