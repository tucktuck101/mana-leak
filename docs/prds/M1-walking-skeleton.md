# PRD — M1: Walking skeleton

## Status

Approved

## Roadmap source

- Milestone: M1 — Walking skeleton (`docs/ROADMAP.md`)
- Roadmap outcome: "A persisted, resumable, streamed chat conversation works end to end in the browser, with a plain model answer and no domain capability yet."
- Depends on: Design docs, repository scaffold (no earlier milestone)
- Demonstrable increment: Chat with an AI in the browser, reload the page, and continue the conversation.
- Open PRD inputs resolved here: none (`docs/ROADMAP.md` → Decisions & deviations → Open PRD inputs lists only M5/M7/S1 items; none are assigned to M1)

## 1. Problem / context

Every later milestone adds one domain capability to an already-working product instead of integrating persistence, streaming, and the model gateway for the first time at the end (`docs/ROADMAP.md` → M1 → Why this milestone exists; Delivery strategy). Before any domain capability exists, Mana Leak needs the full vertical slice vision.md calls for — a single web chat core interface, persisted conversations, and one shared core reached through adapters (`docs/vision.md` → Core experience, "One shared core") — proven end to end: database, the FastAPI SSE adapter, the turn orchestrator, the model gateway, and the Next.js UI (`docs/architecture.md` → Container architecture, Shared-core invariant, Core components → Turn orchestrator).

This milestone builds that walking skeleton: a conversation persists, a turn streams through `process_turn`, and the model answers generically on the one route that exists this early (`other`). No card, combo, rules, or judge capability is introduced here.

## 2. Outcome

A user can open the browser chat, start a conversation, exchange messages that stream a plain assistant answer, reload the page, and continue the same conversation with its full history intact — with no routing, tool use, or domain capability yet. Consistent with the roadmap outcome above.

## 3. Scope

### In scope

- Docker Compose stack: Postgres with Alembic migrations for `conversation`, `message`, and `audit_event` (`docs/data-model.md` → Conversation and judge state, Audit data).
- The FastAPI SSE endpoint and the `process_turn` turn-orchestration skeleton: deterministic validation, user-message persistence, screening, context building, route execution, and the `final`/`message_end` event sequence (`docs/contracts.md` → Turn orchestration), restricted to the parts reachable with only the `other` route available.
- The model gateway (LiteLLM/OpenRouter) with configuration, enforcing the per-call model timeout and the per-turn deadline itself and emitting `limit_reached` via `emit_audit_event` when either fires (`docs/contracts.md` → External adapters, Operational limits; `docs/ROADMAP.md` → M1 spike results).
- The Next.js chat UI behind a `/api/*` route-handler proxy to `API_BASE_URL`, with SSE streaming passthrough; the browser never calls the API directly (`docs/architecture.md` → Container architecture; `docs/contracts.md` → Streaming events → Next.js proxy passthrough).
- Persisted, resumable conversations: `POST /conversations`, `GET /conversations`, `GET /conversations/{id}`, `POST /conversations/{id}/messages` (`docs/contracts.md` → REST API).
- Every message resolves to the `other` route with a plain, general streamed model answer; no routing logic and no tools yet (`docs/ROADMAP.md` → M1 Scope; `docs/contracts.md` → Turn orchestration step 6, `other`-route answer has `result=None`).
- `GET /health`, so the Compose health check never blocks on a route that doesn't exist (`docs/contracts.md` → REST API → `HealthResponse`).

### Out of scope

- Cards, combos, rules retrieval, judging, routing beyond `other`, tool use (`docs/ROADMAP.md` → M1 Out of scope).
- Observability/Langfuse tracing (M2); `/health`'s `langfuse` field reports the gateway's pre-integration state, not a traced turn.
- CLI and MCP adapters (Stretch S1).
- Any table beyond `conversation`, `message`, and `audit_event` — no `card`, `combo`, `combo_card`, `rule_chunk`, `judge_session`, `ruling`, `eval_case`, or `eval_run` (those arrive with M3–M5).
- The eval harness and its gates (introduced at M5/M9).
- Model-based safeguard screening (M8). In M1 only the deterministic validation (empty, malformed, or over-length messages; `contracts.md` → Turn orchestration step 1) runs; every valid message is treated as `clear`.
- Summarisation of turns older than the last 10 (M8). M1 sends the last 10 turns; `conversation.summary` stays empty.

## 4. Functional requirements

- **FR-1** — The system shall let a user create a new conversation from the browser chat.
- **FR-2** — When a user sends a message in a conversation, the system shall stream a plain, general assistant answer as an ordered sequence of `TurnEvent`s over SSE.
- **FR-3** — The system shall persist every conversation and its messages so that reloading the browser page, or requesting an unknown conversation, resumes or correctly reports on it.
- **FR-4** — The system shall retain persisted conversations, messages, and audit events across a Postgres/API container restart.
- **FR-5** — The system shall resolve every turn to the `other` route with a plain answer (`result=None`), regardless of message content; no domain routing or tool use shall occur.
- **FR-6** — When a message is posted to a conversation that already has a turn in flight, the system shall reject the new request as a `conflict` instead of starting a concurrent turn.
- **FR-7** — When a model call or the overall turn exceeds its configured timeout, the system shall stop the turn in a structured way, emit an `error` event before `message_end`, and record a `limit_reached` audit event.
- **FR-8** — The system shall expose `/health`, reporting database status accurately (and Langfuse status without blocking on it), so Compose health checks and a fresh-checkout `docker compose up` succeed.
- **FR-9** — The browser shall reach the API only through the Next.js `/api/*` proxy, which streams the SSE response unchanged, including forwarding a client abort to the upstream connection.

## 5. Non-functional requirements

- **NFR-1** — Bounded execution: the model gateway shall enforce the model-call timeout and the turn deadline itself (explicit cancellation), not relying on the underlying LiteLLM client's `timeout` parameter alone, because that parameter alone did not stop a slow call in the M1 spike (`docs/ROADMAP.md` → M1 spike results).
- **NFR-2** — Degradation/latency: the model gateway shall disable model reasoning by default on every call unless a later milestone deliberately enables it per call, since reasoning-enabled calls took 88–105 s against an 8 s reasoning-disabled call in the M1 spike, risking the per-call and per-turn budgets (`docs/ROADMAP.md` → M1 spike results; `docs/contracts.md` → Operational limits).
- **NFR-3** — Persistence/recoverability: `conversation`, `message`, and `audit_event` rows shall survive a container restart (Postgres named volume, Alembic migrations) (`docs/data-model.md` → Conversation and judge state, Audit data; `docs/architecture.md` → Local deployment).
- **NFR-4** — Observability (pre-Langfuse): every limit-exceeding event (model-call timeout, turn timeout) shall be recorded as an `audit_event` row independent of Langfuse, which is not yet integrated (M2) (`docs/contracts.md` → Operational limits, Error taxonomy; `docs/data-model.md` → `audit_event`).

## 6. Interfaces and contracts affected

| Surface | Reference | Change |
|---|---|---|
| `POST /conversations`, `GET /conversations`, `GET /conversations/{id}` | `contracts.md` → REST API | first introduced |
| `POST /conversations/{id}/messages` (SSE) | `contracts.md` → REST API, Streaming events | first introduced |
| `GET /health` / `HealthResponse` | `contracts.md` → REST API | first introduced |
| `TurnEvent` / SSE wire format | `contracts.md` → Streaming events → Canonical `TurnEvent`, SSE mapping | first introduced |
| `process_turn` orchestration entrypoint | `contracts.md` → Turn orchestration | first introduced (steps 1–4, 6 `other`-only, 7–8; no routing/tool steps) |
| Model gateway (`complete()`) | `architecture.md` → Core components (Model gateway); `contracts.md` → External adapters, Operational limits | first introduced |
| Next.js `/api/*` proxy | `architecture.md` → Container architecture (web); `contracts.md` → Streaming events → Next.js proxy passthrough | first introduced |
| `conversation`, `message` tables | `data-model.md` → Conversation and judge state → `conversation`, `message` | first introduced |
| `audit_event` table | `data-model.md` → Audit data → `audit_event` | first introduced |
| `ErrorCode`/`ErrorResponse` (subset: `validation_error`, `not_found`, `conflict`, `dependency_unavailable`, `timeout`, `internal_error`) | `contracts.md` → Error taxonomy, HTTP error mapping | first introduced (only the codes M1's surfaces can raise) |
| Local Compose stack (`postgres`, `api`, `web`) | `architecture.md` → Local deployment | first introduced |

No required behaviour in this milestone conflicts with a settled contract.

## 7. Acceptance criteria

- **AC-1** — Given a new conversation, when the user sends a message in the browser chat, then SSE events arrive in the order `message_start` → `text_delta`* → `final` → `message_end`, and `final` carries `route="other"`, `result=null`, and the full assistant text.
- **AC-2** — Given a conversation with prior turns, when the browser page is reloaded, then `GET /conversations/{id}` (through the proxy) returns the full ordered message history and the UI lets the user continue the conversation.
- **AC-3** — Given a conversation created before a restart, when the `postgres` and `api` containers are restarted via Compose, then the conversation and its messages are still retrievable afterward, unchanged.
- **AC-4** — Given any user message content (a card name, a rules-sounding question, or off-topic text), when a turn is processed, then the route is always `other` and the answer is a plain general model response — never a domain-routed or tool-using answer.
- **AC-5** — Given a conversation with a turn already streaming, when a second message is posted to the same conversation before that turn's `message_end`, then the request is rejected with `409 conflict` and no second turn starts concurrently.
- **AC-6** — Given a model call that does not return within its configured timeout, when the turn is processed, then the gateway itself cancels the call (independent of the LiteLLM client's own timeout parameter), the turn ends with a structured `error` event (`timeout`), and a `limit_reached` audit event is recorded.
- **AC-7** — Given a turn that runs past the overall turn deadline, when the deadline elapses, then the turn emits an `error` event (`timeout`) followed by `message_end`, and any partial assistant text already produced is persisted with `payload.error` set.
- **AC-8** — Given the database is reachable, when `GET /health` is called, then it returns `200` with `status="ok"` and `database="ok"`; given the database is unreachable, it returns `503` with `status="degraded"` and `database="unavailable"`, without the response depending on a synchronous Langfuse check.
- **AC-9** — Given a fresh checkout with a populated environment file, when `docker compose up` starts the stack, then `postgres`, `api`, and `web` all report healthy, because the api's Compose health check calls `/health`, which exists from first boot.
- **AC-10** — Given the browser is streaming a turn through the Next.js proxy, when the client aborts the connection, then the proxy forwards the abort signal to its upstream `fetch`, the API cancels the turn task and persists partial assistant text with `payload.error = {"code": "timeout", "message": "client disconnected"}`, and the proxied response is never compressed or buffered (`Content-Encoding: identity`), so streamed bytes reach the browser as the API flushes them.
- **AC-11** — Given an unknown `conversation_id`, when `GET /conversations/{conversation_id}` is called, then the response is `404` with `not_found`.
- **AC-12 (demonstrable increment)** — A user opens the browser chat, creates a conversation, exchanges several messages that each stream a plain assistant answer, reloads the page, and continues the same conversation with its full prior history visible — all through the Next.js UI, which never calls the API directly.

## 8. Test and evaluation requirements

### Deterministic verification

- SSE event ordering, types, and `final` payload shape (AC-1).
- Conversation/message persistence across reload (AC-2) and across container restart (AC-3).
- Route is always `other` with `result=None` for varied message content (AC-4).
- A concurrent second message on the same conversation is rejected with `conflict`, not run (AC-5).
- Model-call timeout is enforced by the gateway's own cancellation, not by the LiteLLM `timeout` parameter alone; the resulting `error` event and `limit_reached` audit row (AC-6).
- Turn-deadline expiry produces `error`(`timeout`) → `message_end` and persists partial text with `payload.error` (AC-7).
- `/health` response shape and status code for database up and down (AC-8).
- A fresh-checkout `docker compose up` brings all three containers to healthy (AC-9) — operational check, not a unit test.
- Proxy passthrough: no response compression, byte-for-byte streaming, and abort-signal forwarding on client disconnect (AC-10).
- `GET /conversations/{id}` for an unknown ID returns `404 not_found` (AC-11).
- End-to-end demonstrable increment exercised through the browser UI (AC-12).

### Evaluation

None for this milestone — the eval harness and its gates are introduced at M5/M9 (`docs/ROADMAP.md` → Decisions & deviations → Round 3 item 1; `docs/contracts.md` → Evaluation contracts).

## 9. Constraints

- Only `conversation`, `message`, and `audit_event` exist at this milestone; no `card`, `combo`, `rule_chunk`, `judge_session`, `ruling`, `eval_case`, or `eval_run` tables (`docs/data-model.md`; `docs/ROADMAP.md` → M1 Scope).
- The browser never calls the API directly; only the Next.js `/api/*` proxy does, and FastAPI has no CORS configuration (`docs/architecture.md` → Container architecture — web "Must not"; `docs/contracts.md` → REST API → Topology).
- The model gateway is the only module that imports LiteLLM; it applies timeouts, retries, structured-output parsing, and tracing metadata, and owns the per-turn model-call counter (`docs/architecture.md` → Core components (Model gateway); `docs/contracts.md` → External adapters).
- Operational limits (model-call timeout 30 s, turn timeout 120 s, model-call cap 8 hard / ≤3 soft) are defined once in `contracts.md` → Operational limits; this PRD does not redefine them.
- Development model is `CHAT_MODEL=openrouter/deepseek/deepseek-v4-flash`, chosen for cost, not quality — model quality is compared at M9 (`docs/ROADMAP.md` → Decisions & deviations → Development model choice).
- M1 spike finding: the development model reasons by default, taking 88–105 s per structured call versus 8 s with reasoning disabled; **the gateway disables reasoning by default** (`extra_body={"reasoning": {"enabled": False}}`), and enabling it per call is a later, deliberate choice (`docs/ROADMAP.md` → M1 spike results).
- M1 spike finding: LiteLLM's own `timeout=30` parameter did not stop a 104 s call; **the gateway enforces model timeouts itself** (`asyncio.timeout`/`wait_for` around the call) (`docs/ROADMAP.md` → M1 spike results).
- No authentication; the stack binds to `127.0.0.1`, single local user (`docs/architecture.md` → Scope and assumptions).

## 10. Risks and fallbacks

| Risk | Impact | Design-supported fallback |
|---|---|---|
| The development model reasons by default, inflating per-call latency roughly 10×–13× (88–105 s vs. 8 s) | Turns intermittently breach the 30 s model-call timeout / 120 s turn budget, breaking the walking-skeleton demo | Gateway disables reasoning by default (NFR-2); re-enabling it per call is deferred to a later, deliberate milestone decision (`docs/ROADMAP.md` → M1 spike results) |
| LiteLLM's `timeout` parameter alone does not reliably cancel a slow call (observed 104 s despite `timeout=30`) | A hung model call could run past its deadline, leaving the client without a terminal SSE event | Gateway wraps every call in its own cancellation (`asyncio.timeout`/`wait_for`), independent of the LiteLLM parameter (NFR-1) |
| The Next.js proxy buffers, re-encodes, or compresses the SSE response | Streamed text arrives in bursts instead of incrementally, degrading the chat experience and masking a client abort | Compression disabled for the proxy route (`Content-Encoding: identity`) and byte-for-byte passthrough, per `contracts.md` → Streaming events → Next.js proxy passthrough; flagged if the deployed Next.js runtime cannot disable compression for route handlers — none beyond that |
| The browser disconnects mid-stream (navigation, tab close, stop button) without the server noticing | The turn task keeps running server-side, wasting the model-call/turn budget and leaving ambiguous persisted state | Proxy forwards the incoming request's abort signal to the upstream `fetch`; the API cancels the turn task and persists partial text with `payload.error` (`contracts.md` → Streaming events → Client disconnect) |
| A Postgres/API container restart loses conversation state | The milestone's core demo (reload, continue) fails, and every later milestone inherits the same instability | Alembic migrations plus a named Postgres volume persist `conversation`/`message`/`audit_event` across restarts (`data-model.md`; `architecture.md` → Local deployment) |

## 11. Dependencies

- Milestones: none — M1 depends only on the design documents and the repository scaffold (`docs/ROADMAP.md` → M1 Depends on).
- Existing capabilities: the `uv` workspace scaffold (`packages/core`, `apps/api`, `apps/web`) (`docs/architecture.md` → Scope and assumptions).
- External: OpenRouter (via LiteLLM) for chat completions; PostgreSQL 16 (the `vector` extension is created at DB init but unused until M5). No Langfuse (M2), no Commander Spellbook (M4), no Comprehensive Rules corpus (M5) are needed.

## 12. Traceability

| Requirement | Source | Acceptance criteria | Verification |
|---|---|---|---|
| FR-1 | ROADMAP M1 Scope; `contracts.md` → REST API | AC-1, AC-12 | deterministic test |
| FR-2 | ROADMAP M1 Scope; `contracts.md` → Turn orchestration, Streaming events | AC-1, AC-12 | deterministic test |
| FR-3 | ROADMAP M1 Success evidence; `contracts.md` → REST API | AC-2, AC-11, AC-12 | deterministic test |
| FR-4 | `data-model.md` → Conversation and judge state, Audit data; `architecture.md` → Local deployment | AC-3 | deterministic test (restart) |
| FR-5 | ROADMAP M1 Scope/Success evidence; `contracts.md` → Turn orchestration step 6 | AC-4 | deterministic test |
| FR-6 | `contracts.md` → REST API → Conversation behaviour | AC-5 | deterministic test |
| FR-7 | ROADMAP M1 Success evidence; `contracts.md` → Operational limits, Error taxonomy | AC-6, AC-7 | deterministic test |
| FR-8 | ROADMAP M1 Scope/Success evidence; `contracts.md` → REST API → `HealthResponse` | AC-8, AC-9 | deterministic test / operational check |
| FR-9 | `architecture.md` → Container architecture; `contracts.md` → Streaming events → Next.js proxy passthrough | AC-10, AC-12 | deterministic test |
| NFR-1 | ROADMAP → M1 spike results | AC-6 | deterministic test |
| NFR-2 | ROADMAP → M1 spike results | AC-1, AC-12 | deterministic test (latency bound) |
| NFR-3 | `data-model.md`; `architecture.md` → Local deployment | AC-3 | deterministic test (restart) |
| NFR-4 | `contracts.md` → Operational limits, Error taxonomy; `data-model.md` → `audit_event` | AC-6, AC-7 | deterministic test |

## 13. Open issues / design gaps

None. Resolved by the user on 2026-10-01: M1 does deterministic validation only, with model screening deferred to M8; summarisation is deferred to M8 (see Out of scope). Both decisions are recorded in `docs/ROADMAP.md` → Decisions & deviations.
