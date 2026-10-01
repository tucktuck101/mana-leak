"""WP3 acceptance: `uv run pytest tests/core/test_tracing.py`.

Covers `mana_leak_core.tracing` (`docs/prds/M2-observability.plan.md` ->
Shared contracts, Per-WP checklists -> WP3): the disabled no-op path (AC-6),
the explicitly configured self-hosted endpoint (AC-11), turn/generation span
shape and parent-child linkage (FR-4, NFR-3), the never-raises guarantee
every caller depends on (NFR-1), health mapping including the
mismatched-keys case (FR-7, AC-7, AC-10), and the hung-Langfuse bound
(AC-12).

No test here contacts a real Langfuse: spans go to an in-memory OTel
exporter (`tests/core/conftest.py`), and the two tests that do open a socket
open a local one that accepts and never answers.

AC-8 (secret redaction) is deliberately *not* tested here: this module does
no redaction (plan F8, user decision -- fail closed upstream instead), and
WP5 owns that test against `gateway.check_no_secrets`.
"""

import asyncio
import socket
import threading
import time
from collections.abc import Callable, Iterator
from typing import Any
from uuid import UUID, uuid4

import pytest
from mana_leak_core import tracing
from mana_leak_core.contracts.enums import Route
from mana_leak_core.settings import Settings, get_settings
from mana_leak_core.tracing import TraceContext

pytestmark = pytest.mark.anyio


def _settings(**overrides: Any) -> Settings:
    """`Settings` with tracing explicitly off unless a test turns it on.

    Never inherits the ambient Langfuse configuration: both `.env` and the
    developer's/orchestrator's shell environment may legitimately carry
    `LANGFUSE_*` values (they do on this machine), and a test whose subject
    *is* the enabled/disabled decision must not depend on either.
    """
    base = get_settings().model_dump()
    base["openrouter_api_key"] = get_settings().openrouter_api_key
    base["langfuse_public_key"] = None
    base["langfuse_secret_key"] = None
    base["langfuse_host"] = "http://localhost:3001"
    base.update(overrides)
    return Settings(**base)


def _enabled_settings(**overrides: Any) -> Settings:
    return _settings(
        langfuse_public_key="pk-lf-test",
        langfuse_secret_key="sk-lf-test",
        **overrides,
    )


def _trace(route: Route = Route.other) -> TraceContext:
    return TraceContext(conversation_id=uuid4(), turn_id=uuid4(), route=route)


@pytest.fixture
def hung_langfuse() -> Iterator[str]:
    """A local TCP listener that accepts connections and never responds --
    the AC-12 "Langfuse hangs" case, which a stopped port would not
    reproduce (that one fails fast)."""
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


class _ExplodingClient:
    """A client whose every span call fails, for the never-raises tests."""

    def __init__(self, error: Exception) -> None:
        self.error = error

    def start_as_current_observation(self, **kwargs: Any) -> Any:
        raise self.error


# --- Disabled: no client, no outbound call (FR-3, AC-6) -------------------


async def test_no_client_is_constructed_when_keys_are_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    constructed: list[dict[str, Any]] = []
    monkeypatch.setattr(tracing, "get_settings", lambda: _settings())
    monkeypatch.setattr(tracing, "Langfuse", lambda **kwargs: constructed.append(kwargs))

    assert tracing._get_client() is None
    assert constructed == []


@pytest.mark.parametrize(
    ("public_key", "secret_key"),
    [(None, "sk-lf-test"), ("pk-lf-test", None), (None, None)],
)
def test_either_key_missing_disables_tracing(
    monkeypatch: pytest.MonkeyPatch, public_key: str | None, secret_key: str | None
) -> None:
    monkeypatch.setattr(
        tracing,
        "get_settings",
        lambda: _settings(langfuse_public_key=public_key, langfuse_secret_key=secret_key),
    )

    assert tracing._get_client() is None
    assert tracing.current_health() == "disabled"


async def test_tracing_calls_are_no_ops_when_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    constructed: list[dict[str, Any]] = []
    monkeypatch.setattr(tracing, "get_settings", lambda: _settings())
    monkeypatch.setattr(tracing, "Langfuse", lambda **kwargs: constructed.append(kwargs))
    trace = _trace()

    with tracing.start_turn_trace(trace, input="hello") as turn_span:
        turn_span.update(output="ignored")
        with tracing.record_generation(trace, model="m", input=[{"role": "user"}]) as generation:
            generation.update(output="ignored", usage_details={"input": 1})

    await tracing.refresh_health()
    await tracing.shutdown()

    assert constructed == []
    assert tracing.current_health() == "disabled"


async def test_record_generation_without_a_turn_trace_is_a_no_op(
    span_capture: Any,
) -> None:
    with tracing.record_generation(None, model="m", input="x") as generation:
        generation.update(output="ignored")

    assert span_capture.finished_spans() == []


# --- Self-hosted endpoint, always explicit (FR-3, AC-11) ------------------


def test_client_is_built_with_the_configured_host_never_the_sdk_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    constructed: list[dict[str, Any]] = []

    def _record(**kwargs: Any) -> str:
        constructed.append(kwargs)
        return "client"

    monkeypatch.setattr(tracing, "get_settings", lambda: _enabled_settings())
    monkeypatch.setattr(tracing, "Langfuse", _record)

    assert tracing._get_client() == "client"
    assert constructed == [
        {
            "public_key": "pk-lf-test",
            "secret_key": "sk-lf-test",
            "base_url": "http://localhost:3001",
        }
    ]


def test_langfuse_host_defaults_to_the_local_self_hosted_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC-11: with `LANGFUSE_HOST` unset, the endpoint is the local stack,
    not Langfuse Cloud. `_env_file=None` keeps the repo's own `.env` out of
    it, so the assertion is about the field default."""
    monkeypatch.delenv("LANGFUSE_HOST", raising=False)
    settings = Settings(
        database_url="postgresql+psycopg://u:p@localhost:5432/mana_leak",
        openrouter_api_key="k",
        chat_model="openrouter/vendor/model",
        _env_file=None,
    )

    assert settings.langfuse_host == "http://localhost:3001"


# --- Trace shape and ambient parent/child linkage (FR-4, NFR-3) -----------


async def test_turn_trace_and_generation_share_the_turn_id_as_trace_id(
    span_capture: Any,
) -> None:
    trace = _trace()

    with tracing.start_turn_trace(trace, input="why does this work?") as turn_span:
        with tracing.record_generation(
            trace, model="openrouter/vendor/model", input=[{"role": "user", "content": "hi"}]
        ) as generation:
            generation.update(output="because", usage_details={"input": 3, "output": 5})
        turn_span.update(output="because")

    spans = span_capture.finished_spans()
    assert sorted(span.name for span in spans) == sorted(
        [str(trace.turn_id), "openrouter/vendor/model"]
    )

    turn = span_capture.span_named(str(trace.turn_id))
    generation_span = span_capture.span_named("openrouter/vendor/model")

    # The Langfuse trace ID *is* the turn ID, not a derivative of it.
    assert format(turn.context.trace_id, "032x") == trace.turn_id.hex
    assert UUID(hex=format(turn.context.trace_id, "032x")) == trace.turn_id
    assert format(generation_span.context.trace_id, "032x") == trace.turn_id.hex
    # The model call is a child of the turn trace, never a second root.
    assert generation_span.parent is not None
    assert generation_span.parent.span_id == turn.context.span_id

    assert turn.attributes["session.id"] == str(trace.conversation_id)
    assert turn.attributes["langfuse.trace.name"] == str(trace.turn_id)
    assert turn.attributes["langfuse.observation.metadata.route"] == "other"
    assert turn.attributes["langfuse.observation.input"] == "why does this work?"
    assert generation_span.attributes["langfuse.observation.type"] == "generation"
    assert generation_span.attributes["session.id"] == str(trace.conversation_id)
    assert generation_span.attributes["langfuse.observation.output"] == "because"


async def test_turn_trace_records_the_route_it_was_given(span_capture: Any) -> None:
    trace = _trace(route=Route.cards)

    with tracing.start_turn_trace(trace):
        pass

    turn = span_capture.span_named(str(trace.turn_id))
    assert turn.attributes["langfuse.observation.metadata.route"] == "cards"


# --- Never raises, never suppresses (NFR-1) -------------------------------


@pytest.mark.parametrize("error", [RuntimeError("boom"), ValueError("bad span")])
async def test_turn_trace_degrades_to_a_no_op_when_the_sdk_fails(
    monkeypatch: pytest.MonkeyPatch, error: Exception
) -> None:
    monkeypatch.setattr(tracing, "_get_client", lambda: _ExplodingClient(error))
    trace = _trace()
    ran = False

    with tracing.start_turn_trace(trace, input="hi") as turn_span:
        turn_span.update(output="still fine")
        with tracing.record_generation(trace, model="m") as generation:
            generation.update(output="still fine")
        ran = True

    assert ran


async def test_tracing_never_swallows_the_callers_own_exception(
    span_capture: Any,
) -> None:
    trace = _trace()

    with pytest.raises(ValueError, match="turn failed"):
        with tracing.start_turn_trace(trace, input="hi"):
            raise ValueError("turn failed")

    # The span is still closed and exported, with the error recorded.
    turn = span_capture.span_named(str(trace.turn_id))
    assert turn.end_time is not None


async def test_a_span_that_fails_to_close_does_not_fail_the_turn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _BadExit:
        def __enter__(self) -> Any:
            return self

        def __exit__(self, *exc: Any) -> None:
            raise RuntimeError("exporter died")

        def update(self, **kwargs: Any) -> None:
            return None

    class _Client:
        def start_as_current_observation(self, **kwargs: Any) -> Any:
            return _BadExit()

    monkeypatch.setattr(tracing, "_get_client", lambda: _Client())
    monkeypatch.setattr(tracing, "propagate_attributes", lambda **kwargs: _BadExit())

    with tracing.start_turn_trace(_trace(), input="hi") as turn_span:
        turn_span.update(output="ok")


async def test_client_construction_failure_disables_tracing_silently(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _boom(**kwargs: Any) -> Any:
        raise RuntimeError("bad credentials shape")

    monkeypatch.setattr(tracing, "get_settings", lambda: _enabled_settings())
    monkeypatch.setattr(tracing, "Langfuse", _boom)

    assert tracing._get_client() is None
    with tracing.start_turn_trace(_trace(), input="hi") as turn_span:
        turn_span.update(output="ok")
    await tracing.refresh_health()
    assert tracing.current_health() == "disabled"


# --- Health mapping (FR-7, AC-7, AC-10) -----------------------------------


class _AuthClient:
    def __init__(self, result: Any = None, error: Exception | None = None) -> None:
        self.result = result
        self.error = error
        self.calls = 0

    def auth_check(self) -> Any:
        self.calls += 1
        if self.error is not None:
            raise self.error
        return self.result


async def test_health_is_ok_only_when_auth_check_returns_true(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _AuthClient(result=True)
    monkeypatch.setattr(tracing, "_get_client", lambda: client)

    await tracing.refresh_health()

    assert client.calls == 1
    assert tracing.current_health() == "ok"


@pytest.mark.parametrize("result", [False, None, "yes"])
async def test_a_non_true_auth_check_is_unavailable_not_ok(
    monkeypatch: pytest.MonkeyPatch, result: Any
) -> None:
    monkeypatch.setattr(tracing, "_get_client", lambda: _AuthClient(result=result))

    await tracing.refresh_health()

    assert tracing.current_health() == "unavailable"


async def test_an_auth_failure_is_unavailable_and_never_propagates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC-10's mechanism: mismatched keys make `auth_check()` raise; that
    must become `unavailable`, never an exception on anybody's path."""
    monkeypatch.setattr(
        tracing, "_get_client", lambda: _AuthClient(error=RuntimeError("401 unauthorized"))
    )

    await tracing.refresh_health()

    assert tracing.current_health() == "unavailable"


async def test_health_before_the_first_refresh_is_never_ok(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(tracing, "get_settings", lambda: _enabled_settings())

    assert tracing._health is None
    assert tracing.current_health() == "unavailable"


async def test_health_refresh_loop_refreshes_immediately_then_repeats(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _AuthClient(result=True)
    monkeypatch.setattr(tracing, "_get_client", lambda: client)

    task = asyncio.create_task(tracing.health_refresh_loop(interval_s=0.01))
    try:
        for _ in range(100):
            await asyncio.sleep(0.01)
            if client.calls >= 2:
                break
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    # Refreshed before the first sleep, so `/health` is never stale at boot.
    assert client.calls >= 2
    assert tracing.current_health() == "ok"


# --- Bounded shutdown (AC-9 mechanism, NFR-1) -----------------------------


async def test_shutdown_is_bounded_and_never_raises() -> None:
    class _SlowClient:
        def __init__(self) -> None:
            self.started = threading.Event()

        def shutdown(self) -> None:
            self.started.set()
            time.sleep(10)

    client = _SlowClient()
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(tracing, "_get_client", lambda: client)
        started = time.monotonic()
        await tracing.shutdown(timeout_s=0.2)
        elapsed = time.monotonic() - started

    assert client.started.is_set()
    assert elapsed < 5.0


async def test_shutdown_swallows_sdk_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    class _BrokenClient:
        def shutdown(self) -> None:
            raise RuntimeError("already closed")

    monkeypatch.setattr(tracing, "_get_client", lambda: _BrokenClient())

    await tracing.shutdown()


# --- A hung Langfuse costs a turn nothing (AC-12, NFR-1) ------------------


async def test_spans_against_a_hung_langfuse_cost_nothing(
    hung_langfuse: str, span_capture_factory: Callable[..., Any]
) -> None:
    capture = span_capture_factory(base_url=hung_langfuse)
    trace = _trace()

    started = time.monotonic()
    with tracing.start_turn_trace(trace, input="hi") as turn_span:
        with tracing.record_generation(trace, model="m", input="hi") as generation:
            generation.update(output="x")
        turn_span.update(output="x")
    elapsed = time.monotonic() - started

    assert elapsed < 1.0
    assert len(capture.finished_spans()) == 2


async def test_refresh_health_against_a_hung_langfuse_returns_bounded(
    hung_langfuse: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The SDK's own ~5 s request timeout bounds `auth_check()`; this module
    adds no timeout of its own on the health path."""
    from langfuse import Langfuse

    client = Langfuse(
        public_key=f"pk-test-{uuid4().hex}",
        secret_key=f"sk-test-{uuid4().hex}",
        base_url=hung_langfuse,
    )
    monkeypatch.setattr(tracing, "_get_client", lambda: client)

    started = time.monotonic()
    try:
        await tracing.refresh_health()
        elapsed = time.monotonic() - started
    finally:
        await tracing.shutdown()

    assert elapsed < 20.0
    assert tracing.current_health() == "unavailable"
