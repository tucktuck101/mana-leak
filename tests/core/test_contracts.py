"""WP1 acceptance: `uv run pytest tests/core/test_contracts.py`.

Covers the shared contracts WP1 owns (`docs/prds/M1-walking-skeleton.plan.md`
-> Shared contracts, Per-WP checklists -> WP1): core enums, the M1 error
taxonomy subset, the `TurnEvent` union, conversation/health contracts, and
`Settings`/`get_settings`.
"""

import uuid
from datetime import UTC, datetime

import pytest
from mana_leak_core.contracts import (
    AuditEventType,
    ConversationDetail,
    ConversationSummary,
    ErrorCode,
    ErrorEvent,
    ErrorInfo,
    ErrorResponse,
    Final,
    HealthResponse,
    ManaLeakError,
    MessageEnd,
    MessageOut,
    MessageRole,
    MessageStart,
    Route,
    Severity,
    TextDelta,
    ToolEnd,
    ToolStart,
    TurnEvent,
    TurnResult,
)
from mana_leak_core.settings import Settings, get_settings
from pydantic import TypeAdapter, ValidationError

# --- Core enums -------------------------------------------------------------


def test_route_has_all_members_but_only_other_is_reachable_in_m1() -> None:
    assert {member.value for member in Route} == {"cards", "combos", "judge", "other"}


def test_message_role_members() -> None:
    assert {member.value for member in MessageRole} == {"user", "assistant", "tool"}


def test_severity_members() -> None:
    assert {member.value for member in Severity} == {"info", "warning", "error"}


def test_audit_event_type_members_match_contract() -> None:
    assert {member.value for member in AuditEventType} == {
        "screening_result",
        "route_selected",
        "tool_rejected",
        "tool_failed",
        "timeout",
        "retry_exhausted",
        "limit_reached",
        "judge_transition",
        "citation_validation_failed",
        "structured_output_invalid",
        "dependency_degraded",
    }


# --- Error taxonomy ----------------------------------------------------------


def test_error_code_is_the_m1_reachable_subset() -> None:
    assert {member.value for member in ErrorCode} == {
        "validation_error",
        "not_found",
        "conflict",
        "dependency_unavailable",
        "timeout",
        "internal_error",
    }


def test_error_response_shape_matches_http_error_mapping() -> None:
    response = ErrorResponse(
        error=ErrorInfo(code=ErrorCode.not_found, message="conversation not found")
    )
    assert response.model_dump() == {
        "error": {
            "code": "not_found",
            "message": "conversation not found",
            "retryable": False,
            "details": {},
        }
    }


def test_mana_leak_error_carries_code_and_is_a_real_exception() -> None:
    error = ManaLeakError(ErrorCode.conflict, "turn already in flight", retryable=False)
    assert error.code is ErrorCode.conflict
    assert error.message == "turn already in flight"
    assert error.details == {}
    with pytest.raises(ManaLeakError) as excinfo:
        raise error
    assert excinfo.value.code is ErrorCode.conflict


def test_error_info_rejects_unknown_code() -> None:
    with pytest.raises(ValidationError):
        ErrorInfo(code="ambiguous_match", message="not reachable in M1")  # type: ignore[arg-type]


# --- Streaming events ---------------------------------------------------------


def _ids() -> dict[str, uuid.UUID]:
    return {"conversation_id": uuid.uuid4(), "turn_id": uuid.uuid4()}


def test_turn_event_ordering_round_trips_through_the_discriminated_union() -> None:
    adapter = TypeAdapter(TurnEvent)
    ids = _ids()
    message_id = uuid.uuid4()

    sequence = [
        MessageStart(**ids, message_id=message_id),
        TextDelta(**ids, delta="Hello"),
        Final(**ids, route=Route.other, text="Hello", result=None),
        MessageEnd(**ids),
    ]

    for event in sequence:
        payload = event.model_dump_json()
        parsed = adapter.validate_json(payload)
        assert parsed == event


def test_error_event_terminates_a_turn_in_place_of_final() -> None:
    adapter = TypeAdapter(TurnEvent)
    ids = _ids()
    error_event = ErrorEvent(
        **ids, error=ErrorInfo(code=ErrorCode.timeout, message="model call timed out")
    )
    parsed = adapter.validate_json(error_event.model_dump_json())
    assert parsed == error_event
    assert parsed.error.code is ErrorCode.timeout


def test_final_and_error_event_share_the_same_error_info_type() -> None:
    final_error_field = Final.model_fields["error"].annotation
    error_event_field = ErrorEvent.model_fields["error"].annotation
    assert final_error_field == ErrorInfo | None
    assert error_event_field is ErrorInfo


def test_tool_events_carry_call_id_tool_and_outcome() -> None:
    ids = _ids()
    start = ToolStart(**ids, call_id="call_1", tool="find_combos", args={"card_names": ["Kiki"]})
    end = ToolEnd(**ids, call_id="call_1", tool="find_combos", ok=True, summary="2 combos")
    assert start.type == "tool_start"
    assert end.type == "tool_end"


def test_other_route_turn_result_is_none_not_omitted_or_empty_dict() -> None:
    ids = _ids()
    final = Final(**ids, route=Route.other, text="A plain answer.", result=None)
    dumped = final.model_dump()
    assert "result" in dumped
    assert dumped["result"] is None
    assert TurnResult is type(None)
    with pytest.raises(ValidationError):
        Final(**ids, route=Route.other, text="x", result={})  # type: ignore[arg-type]


# --- Conversation / health contracts ------------------------------------------


def test_conversation_detail_has_no_active_judge_session_in_m1() -> None:
    now = datetime.now(UTC)
    detail = ConversationDetail(
        id=uuid.uuid4(),
        title="Kiki-Jiki combo",
        created_at=now,
        updated_at=now,
        messages=[
            MessageOut(
                id=uuid.uuid4(),
                seq=1,
                turn_id=uuid.uuid4(),
                role=MessageRole.user,
                content="hi",
                created_at=now,
            )
        ],
    )
    assert detail.active_judge_session is None
    assert isinstance(detail, ConversationSummary)
    with pytest.raises(ValidationError):
        ConversationDetail.model_validate(
            {**detail.model_dump(mode="json"), "active_judge_session": {"id": str(uuid.uuid4())}}
        )


def test_health_response_langfuse_is_disabled_not_a_boolean() -> None:
    health = HealthResponse(
        status="ok",
        database="ok",
        langfuse="disabled",
        rules_version=None,
        card_source_version=None,
    )
    assert health.langfuse == "disabled"
    with pytest.raises(ValidationError):
        HealthResponse(
            status="ok",
            database="ok",
            langfuse=False,  # type: ignore[arg-type]
            rules_version=None,
            card_source_version=None,
        )


# --- Settings ------------------------------------------------------------------

# Every env var `Settings` reads. Real shells/CI may export `OPENROUTER_API_KEY`
# (and friends) from the repo's `.env` before `make test` runs the live-test
# gate (plan.md -> Execution model); without clearing these first, a value the
# process already has beats the test's own `tmp_path` env file and a mismatch
# would print the real secret in pytest's assertion diff.
_SETTINGS_ENV_VARS = (
    "DATABASE_URL",
    "OPENROUTER_API_KEY",
    "CHAT_MODEL",
    "LOG_LEVEL",
    "MODEL_CALL_TIMEOUT_S",
    "TURN_TIMEOUT_S",
    "MODEL_CALLS_MAX",
    "MAX_TOKENS",
    "MAX_USER_MESSAGE_CHARS",
    "CONTEXT_TURNS",
)


def _clear_settings_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in _SETTINGS_ENV_VARS:
        monkeypatch.delenv(key, raising=False)


def test_settings_loads_required_fields_and_limit_defaults(tmp_path, monkeypatch) -> None:
    _clear_settings_env(monkeypatch)
    env_file = tmp_path / ".env"
    env_file.write_text(
        "DATABASE_URL=postgresql+psycopg://mana_leak:pw@localhost:5432/mana_leak\n"
        "OPENROUTER_API_KEY=test-key\n"
        "CHAT_MODEL=openrouter/test/model\n"
    )
    settings = Settings(_env_file=env_file)  # type: ignore[call-arg]
    assert settings.database_url.get_secret_value().endswith("/mana_leak")
    assert settings.openrouter_api_key.get_secret_value() == "test-key"
    assert settings.chat_model == "openrouter/test/model"
    assert settings.log_level == "INFO"
    assert settings.model_call_timeout_s == 30
    assert settings.turn_timeout_s == 120
    assert settings.model_calls_max == 8
    assert settings.max_tokens == 1500
    assert settings.max_user_message_chars == 8000
    assert settings.context_turns == 10


def test_settings_ignores_unrelated_env_keys(tmp_path, monkeypatch) -> None:
    _clear_settings_env(monkeypatch)
    env_file = tmp_path / ".env"
    env_file.write_text(
        "DATABASE_URL=postgresql+psycopg://mana_leak:pw@localhost:5432/mana_leak\n"
        "OPENROUTER_API_KEY=test-key\n"
        "CHAT_MODEL=openrouter/test/model\n"
        # Unrelated to M1's Settings fields; extra='ignore' must not choke on it.
        "LANGFUSE_PUBLIC_KEY=pk-test\n"
    )
    settings = Settings(_env_file=env_file)  # type: ignore[call-arg]
    assert settings.openrouter_api_key.get_secret_value() == "test-key"
    assert not hasattr(settings, "langfuse_public_key")


def test_settings_never_leaks_secret_in_repr_or_str(tmp_path, monkeypatch) -> None:
    _clear_settings_env(monkeypatch)
    env_file = tmp_path / ".env"
    env_file.write_text(
        "DATABASE_URL=postgresql+psycopg://mana_leak:super-secret-db-pw@localhost:5432/mana_leak\n"
        "OPENROUTER_API_KEY=sk-or-super-secret-value\n"
        "CHAT_MODEL=openrouter/test/model\n"
    )
    settings = Settings(_env_file=env_file)  # type: ignore[call-arg]
    assert "sk-or-super-secret-value" not in repr(settings)
    assert "sk-or-super-secret-value" not in str(settings.openrouter_api_key)
    assert str(settings.openrouter_api_key) == "**********"
    assert "super-secret-db-pw" not in repr(settings)
    assert "super-secret-db-pw" not in str(settings.database_url)
    assert str(settings.database_url) == "**********"


def test_settings_requires_core_fields(tmp_path, monkeypatch) -> None:
    _clear_settings_env(monkeypatch)
    env_file = tmp_path / ".env"
    env_file.write_text("LOG_LEVEL=DEBUG\n")
    with pytest.raises(ValidationError):
        Settings(_env_file=env_file)  # type: ignore[call-arg]


def test_get_settings_is_cached(monkeypatch) -> None:
    get_settings.cache_clear()
    _clear_settings_env(monkeypatch)
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://mana_leak:pw@localhost:5432/mana_leak")
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.setenv("CHAT_MODEL", "openrouter/test/model")
    try:
        first = get_settings()
        second = get_settings()
        assert first is second
    finally:
        get_settings.cache_clear()
