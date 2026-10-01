"""WP2 acceptance: `uv run pytest tests/core/test_db.py`.

Covers the shared contracts WP2 owns (`docs/prds/M1-walking-skeleton.plan.md`
-> Shared contracts, Per-WP checklists -> WP2): the `conversation`,
`message`, `audit_event` SQLModel tables and their constraints/indexes
(`docs/data-model.md`), `get_session()` (`docs/contracts.md` -> Core service
interfaces), and `emit_audit_event` (`docs/contracts.md` -> Audit events).

Runs against the Compose `mana_leak_test` database via the `db` fixture
(`tests/conftest.py`, WP1), which migrates to head once per session and
truncates tables after each test. `db` is a plain
`sqlalchemy.ext.asyncio.AsyncSession` (not SQLModel's `exec()`-enabled
subclass), so queries here use `session.execute(select(...))` like the rest
of the shared fixture does.
"""

import asyncio
import uuid

import pytest
from mana_leak_core import db as db_module
from mana_leak_core.audit import emit_audit_event
from mana_leak_core.contracts.enums import AuditEventType, Severity
from mana_leak_core.db.models import AuditEvent, Conversation, Message
from sqlalchemy import select
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError

pytestmark = pytest.mark.anyio

# --- Table shape / constraints ------------------------------------------------


async def test_conversation_row_gets_app_side_id_and_timestamps(db) -> None:
    convo = Conversation(title="first chat")
    db.add(convo)
    await db.commit()
    await db.refresh(convo)

    assert isinstance(convo.id, uuid.UUID)
    assert convo.created_at.tzinfo is not None
    assert convo.updated_at.tzinfo is not None
    assert convo.summary is None
    assert convo.summary_through_seq is None


async def test_conversation_updated_at_bumps_on_update(db) -> None:
    convo = Conversation(title="first chat")
    db.add(convo)
    await db.commit()
    await db.refresh(convo)
    first_updated_at = convo.updated_at

    await asyncio.sleep(0.01)
    convo.title = "renamed chat"
    db.add(convo)
    await db.commit()
    await db.refresh(convo)

    assert convo.updated_at > first_updated_at


async def test_conversation_updated_at_index_orders_recent_first(db) -> None:
    older = Conversation(title="older")
    db.add(older)
    await db.commit()
    await asyncio.sleep(0.01)
    newer = Conversation(title="newer")
    db.add(newer)
    await db.commit()

    result = await db.execute(select(Conversation).order_by(Conversation.updated_at.desc()))
    ordered_titles = [row.title for row in result.scalars()]
    assert ordered_titles[:2] == ["newer", "older"]


async def test_message_requires_an_existing_conversation(db) -> None:
    orphan = Message(
        conversation_id=uuid.uuid4(),
        seq=1,
        turn_id=uuid.uuid4(),
        role="user",
        content="hello",
    )
    db.add(orphan)
    with pytest.raises(IntegrityError):
        await db.commit()
    await db.rollback()


async def test_message_role_check_constraint_rejects_unknown_role(db) -> None:
    convo = Conversation()
    db.add(convo)
    await db.commit()
    await db.refresh(convo)

    bad = Message(
        conversation_id=convo.id,
        seq=1,
        turn_id=uuid.uuid4(),
        role="system",  # not in ('user','assistant','tool')
        content="hello",
    )
    db.add(bad)
    with pytest.raises(IntegrityError):
        await db.commit()
    await db.rollback()


async def test_message_unique_conversation_id_seq(db) -> None:
    convo = Conversation()
    db.add(convo)
    await db.commit()
    await db.refresh(convo)
    conversation_id = convo.id  # captured before further commits expire `convo`

    turn_id = uuid.uuid4()
    db.add(
        Message(conversation_id=conversation_id, seq=1, turn_id=turn_id, role="user", content="one")
    )
    await db.commit()

    db.add(
        Message(conversation_id=conversation_id, seq=1, turn_id=turn_id, role="user", content="dup")
    )
    with pytest.raises(IntegrityError):
        await db.commit()
    await db.rollback()


async def test_message_payload_round_trips_as_jsonb(db) -> None:
    convo = Conversation()
    db.add(convo)
    await db.commit()
    await db.refresh(convo)

    msg = Message(
        conversation_id=convo.id,
        seq=1,
        turn_id=uuid.uuid4(),
        role="assistant",
        content="the answer",
        payload={"route": "other", "result": None},
        route="other",
    )
    db.add(msg)
    await db.commit()
    await db.refresh(msg)

    assert msg.payload == {"route": "other", "result": None}


async def test_audit_event_severity_check_constraint_rejects_unknown_severity(db) -> None:
    bad = AuditEvent(event_type=AuditEventType.timeout.value, severity="critical")
    db.add(bad)
    with pytest.raises(IntegrityError):
        await db.commit()
    await db.rollback()


async def test_audit_event_conversation_id_is_optional(db) -> None:
    event = AuditEvent(
        event_type=AuditEventType.limit_reached.value,
        severity=Severity.warning.value,
        details={"limit": "model_calls_max", "value": 8},
    )
    db.add(event)
    await db.commit()
    await db.refresh(event)

    assert event.conversation_id is None
    assert event.details == {"limit": "model_calls_max", "value": 8}


async def test_conversation_delete_cascades_to_message_and_audit_event(db) -> None:
    convo = Conversation()
    db.add(convo)
    await db.commit()
    await db.refresh(convo)

    db.add(
        Message(conversation_id=convo.id, seq=1, turn_id=uuid.uuid4(), role="user", content="hi")
    )
    db.add(
        AuditEvent(
            conversation_id=convo.id,
            event_type=AuditEventType.route_selected.value,
            severity=Severity.info.value,
        )
    )
    await db.commit()

    await db.delete(convo)
    await db.commit()

    remaining_messages = (
        (await db.execute(select(Message).where(Message.conversation_id == convo.id)))
        .scalars()
        .all()
    )
    remaining_events = (
        (await db.execute(select(AuditEvent).where(AuditEvent.conversation_id == convo.id)))
        .scalars()
        .all()
    )
    assert remaining_messages == []
    assert remaining_events == []


# --- get_session() -------------------------------------------------------------


@pytest.fixture
async def _session_against_test_db(monkeypatch: pytest.MonkeyPatch, _migrated_test_db: str | None):
    """Point `get_session()`'s process-wide engine at `mana_leak_test`, the
    same database the `db` fixture uses, then restore it afterward."""
    if _migrated_test_db is None:
        pytest.skip("mana_leak_test database is unreachable")

    from mana_leak_core.settings import get_settings

    raw_url = get_settings().database_url.get_secret_value()
    test_url = make_url(raw_url).set(database="mana_leak_test")
    monkeypatch.setenv("DATABASE_URL", test_url.render_as_string(hide_password=False))
    get_settings.cache_clear()
    db_module.get_engine.cache_clear()
    db_module.session._session_factory.cache_clear()
    try:
        yield
    finally:
        await db_module.get_engine().dispose()
        get_settings.cache_clear()
        db_module.get_engine.cache_clear()
        db_module.session._session_factory.cache_clear()


async def test_get_session_yields_a_working_session_against_configured_database(
    _session_against_test_db,
) -> None:
    got = []
    async for session in db_module.get_session():
        result = await session.execute(select(1))
        got.append(result.scalar_one())
    assert got == [1]


def test_get_engine_enables_pool_pre_ping() -> None:
    engine = db_module.get_engine()
    assert engine.pool._pre_ping is True


# --- emit_audit_event -----------------------------------------------------------


async def test_emit_audit_event_writes_a_row_with_given_fields(db, monkeypatch) -> None:
    async def _single_session():
        yield db

    monkeypatch.setattr("mana_leak_core.audit.get_session", _single_session)

    convo = Conversation()
    db.add(convo)
    await db.commit()
    await db.refresh(convo)
    conversation_id = convo.id  # captured before emit_audit_event's own commit expires `convo`

    turn_id = uuid.uuid4()
    await emit_audit_event(
        AuditEventType.timeout,
        Severity.error,
        conversation_id=conversation_id,
        turn_id=turn_id,
        details={"scope": "model", "seconds": 30},
    )
    # `db` is the same session emit_audit_event wrote through (autoflush is on
    # by default), so the row is visible without a separate commit/refetch.
    row = (
        (await db.execute(select(AuditEvent).where(AuditEvent.turn_id == turn_id))).scalars().one()
    )
    assert row.event_type == "timeout"
    assert row.severity == "error"
    assert row.conversation_id == conversation_id
    assert row.details == {"scope": "model", "seconds": 30}


async def test_emit_audit_event_defaults_details_to_empty_dict(db, monkeypatch) -> None:
    async def _single_session():
        yield db

    monkeypatch.setattr("mana_leak_core.audit.get_session", _single_session)

    turn_id = uuid.uuid4()
    await emit_audit_event(AuditEventType.limit_reached, Severity.warning, turn_id=turn_id)

    row = (
        (await db.execute(select(AuditEvent).where(AuditEvent.turn_id == turn_id))).scalars().one()
    )
    assert row.details == {}
    assert row.conversation_id is None


async def test_emit_audit_event_never_raises_when_the_write_fails(monkeypatch) -> None:
    async def _broken_session():
        raise RuntimeError("database is down")
        yield  # pragma: no cover - makes this an async generator

    monkeypatch.setattr("mana_leak_core.audit.get_session", _broken_session)

    # Must not raise, per the contract ("never raises") and WP2 checklist
    # ("logs and swallows a failed write").
    await emit_audit_event(AuditEventType.timeout, Severity.error)
