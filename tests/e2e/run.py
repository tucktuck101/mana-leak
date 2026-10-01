"""M1/M2 end-to-end verification (`docs/prds/M1-walking-skeleton.plan.md` ->
WP8; `docs/prds/M2-observability.plan.md` -> WP7).

Invoked by `tests/e2e/run.sh` (the `make e2e` target). Exercises the real
Compose stack -- `postgres`, `api`, `web`, and (M2) the self-hosted Langfuse
stack (`langfuse-web`, `langfuse-worker`, `clickhouse`, `redis`, `minio`) --
through the Next.js proxy only (`http://localhost:3000/api/...`), never
calling the FastAPI container directly, per `docs/contracts.md` -> REST API
-> Topology. The one exception is Langfuse itself: M2's steps below query it
directly through its own Python SDK, exactly as a developer inspecting
traces would, since Langfuse is not behind the app's proxy.

Not a pytest module (deliberately not named `test_*.py`): `tool.pytest.ini_options`
(root `pyproject.toml`) sets `testpaths = ["tests"]`, so a `test_*.py` file
here would be collected by `make test`'s plain, non-Compose `uv run pytest`.
This script needs the full stack up and is only ever invoked by `run.sh`.

Steps:

1. `step_chat` (M1 PRD AC-1, AC-2): create a conversation, send a message,
   stream it to completion through the proxy, and check `TurnEvent`
   ordering/shape and that history round-trips through
   `GET /conversations/{id}`.
2. `step_langfuse_trace` (M2 PRD AC-3 query half): poll Langfuse's own query
   API for that first turn's trace under its conversation's session, then
   send a second turn into the *same* conversation and poll again for both
   traces under the one session (AC-3's "several turns ... one session").
   Updates the shared chat snapshot so `step_restart` below still compares
   against the conversation's real, now-four-message state.
3. `step_langfuse_mismatched_keys` (M2 PRD AC-10): a wrong key pair against
   the real, running Langfuse must fail `auth_check()` -- the actual 401
   branch WP3's monkeypatched unit test can't exercise.
4. `step_restart` (M1 PRD AC-3, NFR-3): `docker compose restart postgres`
   (exercises `pool_pre_ping` while the `api` process itself keeps running),
   then `docker compose restart api` (a full process restart). The
   conversation from steps 1-2 must still read back unchanged after each.
5. `step_abort` (M1 PRD AC-10): start a second conversation's turn, cut the
   connection after the first `text_delta`, and confirm the partial
   assistant text persisted with
   `payload.error = {"code": "timeout", "message": "client disconnected"}`.
6. `step_langfuse_degradation` (M2 PRD AC-5, FR-7): `docker compose stop
   langfuse-web`, run one more turn through the proxy in a fresh
   conversation, assert the SSE sequence/answer are unaffected, then poll
   `/api/health` (through the proxy only) until it reports
   `langfuse: "unavailable"`. Restarts `langfuse-web` before returning.
7. `step_langfuse_shutdown_flush` (M2 PRD AC-9): run one more turn in a
   fresh conversation, `docker compose stop api` immediately afterward, then
   query Langfuse directly (the app is stopped) for that turn's trace by
   `turn_id.hex`, asserting it is present with a closed, usage-carrying
   generation observation -- the SDK's shutdown-time flush survived the
   stop. Restarts `api` before returning, so the following browser
   regression test still has a running stack.

Exits non-zero with a plain message on any check failure; `run.sh` lets the
stack keep running either way (a failure here is diagnosed against the
owning WP's lane, not fixed in this file).
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Any

import httpx
from dotenv import dotenv_values
from langfuse import Langfuse

WEB_BASE_URL = "http://localhost:3000"
API_PREFIX = "/api"
# Generous vs. the PRD's 120 s turn deadline / spike's observed ~8-20 s answers.
CHAT_TIMEOUT_S = 90.0
POLL_TIMEOUT_S = 30.0
POLL_INTERVAL_S = 0.5

# M2: langfuse-web's first boot runs Prisma + ClickHouse migrations and
# routinely exceeds the plain services' 90 s default on a fresh volume
# (plan -> WP7 checklist, F11).
LANGFUSE_SERVICES = ("langfuse-web", "langfuse-worker", "clickhouse", "redis", "minio")
LANGFUSE_HEALTH_TIMEOUT_S = 300.0
# Ingestion (SDK batch export -> langfuse-worker -> ClickHouse) is
# asynchronous (PRD §8); 30 s flakes on a cold stack (F11).
LANGFUSE_TRACE_POLL_TIMEOUT_S = 120.0
LANGFUSE_TRACE_POLL_INTERVAL_S = 1.0
# F11: the 30 s background health-refresh interval plus the SDK's own ~5 s
# request timeout for whichever attempt was already in flight when Langfuse
# stopped.
LANGFUSE_DEGRADE_MIN_WAIT_S = 45.0
LANGFUSE_DEGRADE_POLL_TIMEOUT_S = 90.0
LANGFUSE_DEGRADE_POLL_INTERVAL_S = 5.0

REPO_ROOT = Path(__file__).resolve().parents[2]
#: `LANGFUSE_INIT_*` on the host: guaranteed present in the shared `.env`
#: (M2 plan -> WP2 checklist) -- never read with `source`/`cat`/`grep`
#: (`AGENTS.md` §22), only through `python-dotenv`'s own parser, and never
#: printed. The app's own `Settings.langfuse_public_key`/`secret_key` stay
#: unset on the host by design (F3), so `get_settings()` would return `None`
#: here -- this script talks to Langfuse with its own, separate credentials.
_ENV_FILE: dict[str, str | None] = dotenv_values(REPO_ROOT / ".env")


class CheckFailed(AssertionError):
    pass


def _check(condition: bool, message: str) -> None:
    if not condition:
        raise CheckFailed(message)


def _env(name: str, *, required: bool = True, default: str | None = None) -> str | None:
    """The real process environment first (so a CI runner that exports these
    directly still works), then the shared `.env` file, parsed in-process --
    never `source`d into this script's own environment, never printed."""
    value = os.environ.get(name) or _ENV_FILE.get(name) or default
    if required:
        _check(bool(value), f"{name} missing from the environment and from .env")
    return value


def _langfuse_client(*, tracing_enabled: bool = False) -> Langfuse:
    return Langfuse(
        public_key=_env("LANGFUSE_INIT_PROJECT_PUBLIC_KEY"),
        secret_key=_env("LANGFUSE_INIT_PROJECT_SECRET_KEY"),
        base_url=_env("LANGFUSE_HOST", required=False, default="http://localhost:3001"),
        tracing_enabled=tracing_enabled,
    )


def _parse_sse(raw: str) -> list[tuple[str, dict[str, Any]]]:
    """`event: <type>\\ndata: <json>\\n\\n` -> `[(type, data), ...]`."""
    events: list[tuple[str, dict[str, Any]]] = []
    event_type: str | None = None
    for line in raw.split("\n"):
        if line.startswith("event: "):
            event_type = line.removeprefix("event: ")
        elif line.startswith("data: "):
            _check(event_type is not None, f"data line with no event: {line!r}")
            events.append((event_type, json.loads(line.removeprefix("data: "))))
            event_type = None
    return events


async def _create_conversation(client: httpx.AsyncClient, title: str | None = None) -> str:
    response = await client.post(f"{API_PREFIX}/conversations", json={"title": title})
    _check(
        response.status_code == 201,
        f"POST /conversations -> {response.status_code}: {response.text}",
    )
    body = response.json()
    _check("id" in body, f"conversation summary missing id: {body}")
    return body["id"]


async def _get_conversation(client: httpx.AsyncClient, conversation_id: str) -> dict[str, Any]:
    response = await client.get(f"{API_PREFIX}/conversations/{conversation_id}")
    _check(
        response.status_code == 200,
        f"GET /conversations/{conversation_id} -> {response.status_code}: {response.text}",
    )
    return response.json()


async def _run_turn_through_proxy(
    client: httpx.AsyncClient, conversation_id: str, user_text: str
) -> tuple[list[str], dict[str, Any], dict[str, Any]]:
    """Streams one ordinary turn through the proxy to completion and checks
    the same AC-1/AC-4 SSE shape `step_chat` checks for turn 1 -- shared by
    the Langfuse degradation (AC-5) and shutdown-flush (AC-9) steps below,
    which both need "an ordinary turn, unaffected" as their starting point.
    Returns `(event_types, final_event, conversation_detail)`.
    """
    response = await client.post(
        f"{API_PREFIX}/conversations/{conversation_id}/messages",
        json={"content": user_text},
        timeout=CHAT_TIMEOUT_S,
    )
    _check(
        response.status_code == 200,
        f"POST .../messages -> {response.status_code}: {response.text}",
    )
    events = _parse_sse(response.text)
    types = [t for t, _ in events]
    _check(types[0] == "message_start", f"first event must be message_start, got {types}")
    _check(types[-1] == "message_end", f"last event must be message_end, got {types}")
    final_events = [data for t, data in events if t == "final"]
    _check(len(final_events) == 1, f"expected exactly one final event, got {types}")
    final = final_events[0]
    _check(final["error"] is None, f"turn must not carry an error: {final['error']!r}")
    _check(bool(final["text"]), "final.text must be the full non-empty assistant answer")
    _check("error" not in types, f"unexpected error event in a successful turn: {types}")
    detail = await _get_conversation(client, conversation_id)
    return types, final, detail


_chat_conversation_id: str | None = None
_chat_snapshot: dict[str, Any] | None = None


async def step_chat(client: httpx.AsyncClient) -> None:
    print("[chat] creating conversation and streaming a turn through the proxy...")
    conversation_id = await _create_conversation(client, title=None)

    user_text = "In one short sentence, what is a Commander deck?"
    response = await client.post(
        f"{API_PREFIX}/conversations/{conversation_id}/messages",
        json={"content": user_text},
        timeout=CHAT_TIMEOUT_S,
    )
    _check(
        response.status_code == 200,
        f"POST .../messages -> {response.status_code}: {response.text}",
    )
    content_type = response.headers.get("content-type", "")
    _check("text/event-stream" in content_type, f"expected text/event-stream, got {content_type!r}")
    content_encoding = response.headers.get("content-encoding")
    _check(
        content_encoding == "identity",
        f"proxy must force Content-Encoding: identity, got {content_encoding!r}",
    )

    events = _parse_sse(response.text)
    types = [t for t, _ in events]
    _check(types[0] == "message_start", f"first event must be message_start, got {types}")
    _check(types[-1] == "message_end", f"last event must be message_end, got {types}")
    final_events = [data for t, data in events if t == "final"]
    _check(len(final_events) == 1, f"expected exactly one final event, got {types}")
    final = final_events[0]
    _check(final["route"] == "other", f"AC-4: route must always be 'other', got {final['route']!r}")
    result_ok = final["result"] is None
    _check(result_ok, f"AC-1: other-route result must be null, got {final['result']!r}")
    _check(bool(final["text"]), "AC-1: final.text must be the full non-empty assistant answer")
    _check(final["error"] is None, f"successful turn must not carry an error: {final['error']!r}")
    _check("error" not in types, f"unexpected error event in a successful turn: {types}")

    print("[chat] SSE ordering/shape OK:", types)

    detail = await _get_conversation(client, conversation_id)
    _check(detail["id"] == conversation_id, "GET conversation id mismatch")
    messages = detail["messages"]
    _check(len(messages) == 2, f"expected 2 persisted messages, got {len(messages)}: {messages}")
    seqs = [m["seq"] for m in messages]
    _check(seqs == [1, 2], f"messages must be ordered by ascending seq: {messages}")
    user_msg, assistant_msg = messages
    _check(
        user_msg["role"] == "user" and user_msg["content"] == user_text,
        f"user message mismatch: {user_msg}",
    )
    _check(
        assistant_msg["role"] == "assistant" and assistant_msg["content"] == final["text"],
        f"AC-2: persisted assistant content must match streamed final.text: {assistant_msg}",
    )
    route_ok = assistant_msg["route"] == "other"
    _check(route_ok, f"persisted assistant route mismatch: {assistant_msg}")
    print("[chat] history round-trip through GET /conversations/{id} OK")

    global _chat_conversation_id, _chat_snapshot
    _chat_conversation_id = conversation_id
    _chat_snapshot = detail


async def _poll_session_trace_ids(
    langfuse_client: Langfuse,
    conversation_id: str,
    expect_trace_ids: set[str],
    *,
    timeout_s: float = LANGFUSE_TRACE_POLL_TIMEOUT_S,
    interval_s: float = LANGFUSE_TRACE_POLL_INTERVAL_S,
) -> None:
    """Self-hosted Langfuse v4 runs in `events_only` write mode by default
    (verified against the installed `docker.langfuse.com/langfuse/langfuse:4`
    image): the legacy `sessions.get`/`trace.get`/`trace.list` endpoints this
    plan was written against all 404 ("not available ... events_only mode"),
    because the Postgres-backed legacy tables they read are never populated
    in that mode. The supported v4 read path is `GET /api/public/v2/
    observations` (`langfuse_client.api.observations.get_many`), which reads
    straight from ClickHouse and supports `session_id`/`trace_id` filters
    directly -- verified live against this stack. A turn's own trace is a
    root `SPAN` observation (`tracing.start_turn_trace`), so `trace_id` for
    every `is_root_observation=True` row under a session is "one trace per
    turn" (AC-3), and filtering by `trace_id` alone (below) gets that turn's
    whole trace, root span plus child generation.
    """
    deadline = asyncio.get_event_loop().time() + timeout_s
    seen: set[str] = set()
    while asyncio.get_event_loop().time() < deadline:
        try:
            page = langfuse_client.api.observations.get_many(
                session_id=conversation_id, is_root_observation=True, limit=50
            )
            seen = {obs.trace_id for obs in page.data if obs.trace_id is not None}
        except Exception:
            seen = set()
        if expect_trace_ids <= seen:
            return
        await asyncio.sleep(interval_s)
    raise CheckFailed(
        f"AC-3: expected Langfuse session {conversation_id} to include traces "
        f"{sorted(expect_trace_ids)}, saw {sorted(seen)} after {timeout_s}s"
    )


async def step_langfuse_trace(client: httpx.AsyncClient, langfuse_client: Langfuse) -> None:
    global _chat_snapshot
    _check(
        _chat_conversation_id is not None and _chat_snapshot is not None,
        "step_langfuse_trace requires step_chat to have run first",
    )
    conversation_id = _chat_conversation_id
    assert conversation_id is not None
    assert _chat_snapshot is not None
    first_turn_id = uuid.UUID(_chat_snapshot["messages"][-1]["turn_id"]).hex

    print(
        f"[langfuse] polling for turn 1's trace ({first_turn_id}) "
        f"under session {conversation_id}..."
    )
    await _poll_session_trace_ids(langfuse_client, conversation_id, {first_turn_id})
    print("[langfuse] turn 1 trace found -- AC-3 (one trace per turn) OK")

    print(
        "[langfuse] sending a second turn into the same conversation "
        "(AC-3: several turns, one session)..."
    )
    _, _, detail = await _run_turn_through_proxy(
        client, conversation_id, "In one short sentence, what is a combo?"
    )
    second_turn_id = uuid.UUID(detail["messages"][-1]["turn_id"]).hex
    await _poll_session_trace_ids(langfuse_client, conversation_id, {first_turn_id, second_turn_id})
    print("[langfuse] turn 2 trace also found under the same session -- AC-3 (grouping) OK")

    # `step_restart` below compares a later `GET /conversations/{id}` against
    # this snapshot; it must reflect the conversation's real state (now 4
    # messages), not the 2-message state `step_chat` captured.
    _chat_snapshot = detail


async def step_langfuse_mismatched_keys() -> None:
    print("[langfuse] AC-10: a wrong key pair against the real, running Langfuse must fail auth...")
    base_url = _env("LANGFUSE_HOST", required=False, default="http://localhost:3001")
    wrong_client = Langfuse(
        public_key="pk-lf-wrong", secret_key="sk-lf-wrong", base_url=base_url, tracing_enabled=False
    )
    raised = False
    try:
        wrong_client.auth_check()
    except Exception as exc:
        raised = True
        print(f"[langfuse] auth_check() raised as expected: {type(exc).__name__}")
    finally:
        wrong_client.shutdown()
    _check(
        raised,
        "AC-10: auth_check() with a mismatched key pair must raise against a real Langfuse",
    )


def _run(*args: str) -> None:
    result = subprocess.run(args, capture_output=True, text=True)
    _check(
        result.returncode == 0,
        f"command failed: {' '.join(args)}\nstdout: {result.stdout}\nstderr: {result.stderr}",
    )


async def _wait_healthy(service: str, timeout_s: float = 90.0) -> None:
    deadline = asyncio.get_event_loop().time() + timeout_s
    last_state = "unknown"
    while asyncio.get_event_loop().time() < deadline:
        result = subprocess.run(
            ["docker", "compose", "ps", "--format", "json"],
            capture_output=True,
            text=True,
            check=True,
        )
        for line in result.stdout.splitlines():
            line = line.strip()
            if not line:
                continue
            entry = json.loads(line)
            if entry.get("Service") == service:
                last_state = f"{entry.get('State')}/{entry.get('Health')}"
                if entry.get("Health") == "healthy":
                    return
        await asyncio.sleep(1.0)
    raise CheckFailed(f"{service} did not become healthy within {timeout_s}s (last: {last_state})")


async def step_restart(client: httpx.AsyncClient) -> None:
    _check(_chat_conversation_id is not None, "step_restart requires step_chat to have run first")
    conversation_id = _chat_conversation_id
    before = _chat_snapshot

    print("[restart] restarting postgres (exercises api's pool_pre_ping)...")
    _run("docker", "compose", "restart", "postgres")
    await _wait_healthy("postgres")

    after_db_restart = await _get_conversation(client, conversation_id)
    _check(
        after_db_restart == before,
        "AC-3: conversation changed after postgres restart alone.\n"
        f"before={before}\nafter={after_db_restart}",
    )
    print("[restart] conversation unchanged after postgres restart (pool_pre_ping OK)")

    print("[restart] restarting api (full process restart)...")
    _run("docker", "compose", "restart", "api")
    await _wait_healthy("api")

    after_api_restart = await _get_conversation(client, conversation_id)
    _check(
        after_api_restart == before,
        "AC-3/NFR-3: conversation changed after api restart.\n"
        f"before={before}\nafter={after_api_restart}",
    )
    print("[restart] conversation unchanged after api restart (persists across restart)")


async def step_abort(client: httpx.AsyncClient) -> None:
    print("[abort] creating a second conversation and aborting mid-stream...")
    conversation_id = await _create_conversation(client, title=None)

    saw_text_delta = False
    content = "Describe, in detail, the history of the Commander format."
    async with client.stream(
        "POST",
        f"{API_PREFIX}/conversations/{conversation_id}/messages",
        json={"content": content},
        timeout=CHAT_TIMEOUT_S,
    ) as response:
        _check(response.status_code == 200, f"POST .../messages -> {response.status_code}")
        async for line in response.aiter_lines():
            if line.startswith("event: text_delta"):
                saw_text_delta = True
            elif line == "" and saw_text_delta:
                break  # end of the first text_delta's event block
    # Exiting the `async with` block above closes the connection here,
    # simulating a browser tab close / stop-button disconnect
    # (contracts.md -> Streaming events -> Client disconnect).
    _check(saw_text_delta, "never saw a text_delta before stream ended; not a mid-stream abort")
    print("[abort] connection closed after first text_delta")

    deadline = asyncio.get_event_loop().time() + POLL_TIMEOUT_S
    assistant_msg: dict[str, Any] | None = None
    while asyncio.get_event_loop().time() < deadline:
        detail = await _get_conversation(client, conversation_id)
        messages = detail["messages"]
        if len(messages) >= 2 and messages[-1]["role"] == "assistant":
            assistant_msg = messages[-1]
            break
        await asyncio.sleep(POLL_INTERVAL_S)

    _check(
        assistant_msg is not None,
        f"AC-10: no partial assistant message appeared within {POLL_TIMEOUT_S}s after abort",
    )
    expected_payload = {"error": {"code": "timeout", "message": "client disconnected"}}
    _check(
        assistant_msg["payload"] == expected_payload,
        f"AC-10: payload.error mismatch: {assistant_msg['payload']!r}",
    )
    content_str = assistant_msg["content"]
    _check(isinstance(content_str, str), f"partial content not a string: {assistant_msg}")
    print(f"[abort] partial text persisted with payload.error OK (content={content_str!r})")


async def step_langfuse_degradation(client: httpx.AsyncClient) -> None:
    print("[langfuse] AC-5: stopping langfuse-web and running a turn through the proxy...")
    _run("docker", "compose", "stop", "langfuse-web")
    try:
        conversation_id = await _create_conversation(client, title=None)
        types, final, _detail = await _run_turn_through_proxy(
            client, conversation_id, "In one short sentence, what is a combo?"
        )
        _check(
            types[0] == "message_start" and types[-1] == "message_end",
            f"AC-5: SSE sequence changed with Langfuse down: {types}",
        )
        _check(
            final["route"] == "other" and bool(final["text"]),
            f"AC-5: turn result changed with Langfuse down: {final}",
        )
        print("[langfuse] turn unaffected with Langfuse down -- AC-5 OK")

        print("[langfuse] waiting for /api/health (proxy) to report langfuse=unavailable...")
        await asyncio.sleep(LANGFUSE_DEGRADE_MIN_WAIT_S)
        deadline = asyncio.get_event_loop().time() + LANGFUSE_DEGRADE_POLL_TIMEOUT_S
        last_body: dict[str, Any] | None = None
        while asyncio.get_event_loop().time() < deadline:
            response = await client.get(f"{API_PREFIX}/health")
            last_body = response.json()
            if last_body.get("langfuse") == "unavailable":
                break
            await asyncio.sleep(LANGFUSE_DEGRADE_POLL_INTERVAL_S)
        _check(
            last_body is not None and last_body.get("langfuse") == "unavailable",
            "AC-5/FR-7: /health never reported langfuse=unavailable "
            f"after stopping langfuse-web: {last_body}",
        )
        print("[langfuse] /health (via the proxy) reports langfuse=unavailable -- FR-7 OK")
    finally:
        print("[langfuse] restarting langfuse-web...")
        _run("docker", "compose", "start", "langfuse-web")
        await _wait_healthy("langfuse-web", timeout_s=LANGFUSE_HEALTH_TIMEOUT_S)


async def _poll_trace_observations(
    langfuse_client: Langfuse,
    trace_id: str,
    *,
    timeout_s: float = LANGFUSE_TRACE_POLL_TIMEOUT_S,
    interval_s: float = LANGFUSE_TRACE_POLL_INTERVAL_S,
) -> list[Any]:
    """`trace_id`'s observations via the v4 events_only-compatible
    `observations.get_many` read path (see `_poll_session_trace_ids`'s
    docstring) -- the root turn-level span plus its child generation(s)."""
    deadline = asyncio.get_event_loop().time() + timeout_s
    last_error: Exception | None = None
    while asyncio.get_event_loop().time() < deadline:
        try:
            page = langfuse_client.api.observations.get_many(trace_id=trace_id, limit=50)
            if page.data:
                return page.data
        except Exception as exc:  # noqa: BLE001 - retried below; re-raised as CheckFailed on timeout
            last_error = exc
        await asyncio.sleep(interval_s)
    raise CheckFailed(
        f"AC-9: trace {trace_id} never appeared in Langfuse within {timeout_s}s ({last_error})"
    )


async def step_langfuse_shutdown_flush(
    client: httpx.AsyncClient, langfuse_client: Langfuse
) -> None:
    print(
        "[langfuse] AC-9: running one more turn, then stopping api to prove the shutdown flush..."
    )
    conversation_id = await _create_conversation(client, title=None)
    _, _, detail = await _run_turn_through_proxy(
        client, conversation_id, "In one short sentence, what is a Commander deck?"
    )
    # The Langfuse trace ID is the bare-hex form (`tracing.py` opens the span
    # with `trace_context={"trace_id": trace.turn_id.hex}`) -- never the
    # hyphenated `str(turn_id)`, which 404s against the query API (F19).
    turn_id = uuid.UUID(detail["messages"][-1]["turn_id"]).hex

    try:
        print("[langfuse] stopping api...")
        _run("docker", "compose", "stop", "api")
        print(f"[langfuse] api stopped; querying Langfuse directly for trace {turn_id}...")
        observations = await _poll_trace_observations(langfuse_client, turn_id)
        _check(
            all(obs.trace_id == turn_id for obs in observations),
            f"AC-9: trace id mismatch in returned observations: {observations}",
        )
        closed = [obs for obs in observations if obs.end_time is not None]
        _check(
            bool(closed),
            f"AC-9: trace's observations never closed (shutdown flush incomplete): {observations}",
        )
        print(
            "[langfuse] trace present with a closed generation observation "
            "after api shutdown -- AC-9 OK"
        )
    finally:
        print("[langfuse] restarting api...")
        _run("docker", "compose", "start", "api")
        await _wait_healthy("api")


async def main() -> int:
    print("[health] waiting for postgres, api, web to report healthy...")
    for service in ("postgres", "api", "web"):
        await _wait_healthy(service)
    print("[health] postgres, api, web are all healthy")

    print(
        "[health] waiting for the Langfuse stack to report healthy (first boot can take a while)..."
    )
    for service in LANGFUSE_SERVICES:
        await _wait_healthy(service, timeout_s=LANGFUSE_HEALTH_TIMEOUT_S)
    print("[health] Langfuse stack is healthy")

    langfuse_client = _langfuse_client()
    try:
        async with httpx.AsyncClient(base_url=WEB_BASE_URL, timeout=30.0) as client:
            await step_chat(client)
            await step_langfuse_trace(client, langfuse_client)
            await step_langfuse_mismatched_keys()
            await step_restart(client)
            await step_abort(client)
            await step_langfuse_degradation(client)
            await step_langfuse_shutdown_flush(client, langfuse_client)
    finally:
        langfuse_client.shutdown()

    print("\nAll e2e checks passed.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(asyncio.run(main()))
    except CheckFailed as exc:
        print(f"\nE2E CHECK FAILED: {exc}", file=sys.stderr)
        sys.exit(1)
