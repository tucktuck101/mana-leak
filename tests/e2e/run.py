"""M1 end-to-end verification (`docs/prds/M1-walking-skeleton.plan.md` -> WP8).

Invoked by `tests/e2e/run.sh` (the `make e2e` target). Exercises the real
Compose stack — `postgres`, `api`, `web` — through the Next.js proxy only
(`http://localhost:3000/api/...`), never calling the FastAPI container
directly, per `docs/contracts.md` -> REST API -> Topology.

Not a pytest module (deliberately not named `test_*.py`): `tool.pytest.ini_options`
(root `pyproject.toml`) sets `testpaths = ["tests"]`, so a `test_*.py` file
here would be collected by `make test`'s plain, non-Compose `uv run pytest`.
This script needs the full stack up and is only ever invoked by `run.sh`.

Steps (PRD AC-1, AC-2, AC-3, AC-10; plan WP8 row/checklist):

1. Chat: create a conversation, send a message, stream it to completion
   through the proxy, and check `TurnEvent` ordering/shape (AC-1) and that
   history round-trips through `GET /conversations/{id}` (AC-2).
2. Restart: `docker compose restart postgres` (exercises `pool_pre_ping`
   while the `api` process itself keeps running), then
   `docker compose restart api` (a full process restart). The conversation
   from step 1 must still read back unchanged after each (AC-3, NFR-3).
3. Abort: start a second turn, cut the connection after the first
   `text_delta`, and confirm the partial assistant text persisted with
   `payload.error = {"code": "timeout", "message": "client disconnected"}`
   (AC-10).

Exits non-zero with a plain message on any check failure; `run.sh` lets the
stack keep running either way (per-WP checklist: WP8 doesn't tear the stack
down, and a failure here is diagnosed against the owning WP's lane, not
fixed in this file).
"""

from __future__ import annotations

import asyncio
import json
import subprocess
import sys
from typing import Any

import httpx

WEB_BASE_URL = "http://localhost:3000"
API_PREFIX = "/api"
# Generous vs. the PRD's 120 s turn deadline / spike's observed ~8-20 s answers.
CHAT_TIMEOUT_S = 90.0
POLL_TIMEOUT_S = 30.0
POLL_INTERVAL_S = 0.5


class CheckFailed(AssertionError):
    pass


def _check(condition: bool, message: str) -> None:
    if not condition:
        raise CheckFailed(message)


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


async def main() -> int:
    print("[health] waiting for postgres, api, web to report healthy...")
    for service in ("postgres", "api", "web"):
        await _wait_healthy(service)
    print("[health] postgres, api, web are all healthy")

    async with httpx.AsyncClient(base_url=WEB_BASE_URL, timeout=30.0) as client:
        await step_chat(client)
        await step_restart(client)
        await step_abort(client)

    print("\nAll e2e checks passed.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(asyncio.run(main()))
    except CheckFailed as exc:
        print(f"\nE2E CHECK FAILED: {exc}", file=sys.stderr)
        sys.exit(1)
