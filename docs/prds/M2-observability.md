# PRD — M2: Observability

## Status

Approved

Review: Fable single pass 2026-10-01 — 7 MAJOR / 7 MINOR / 3 NIT; all applied.

## Roadmap source

- Milestone: M2 — Observability (`docs/ROADMAP.md`)
- Roadmap outcome: "Every chat turn is traced end to end in self-hosted Langfuse, correlated by conversation, degrading safely when Langfuse is unavailable."
- Depends on: M1 (Walking skeleton)
- Demonstrable increment: Have a conversation in the browser chat, then inspect every turn of it in Langfuse as one session.
- Open PRD inputs resolved here: none (`docs/ROADMAP.md` → Decisions & deviations → Open PRD inputs lists only M5/M7/S1 items; none are assigned to M2)

## 1. Problem / context

M1 proved the walking skeleton — persisted, streamed chat through `process_turn` — with no tracing: the model gateway's `trace` parameter is an unused placeholder (`trace: object | None = None`) kept only so the signature does not change later, and `GET /health`'s `langfuse` field is hardcoded to `"disabled"` (`packages/core/src/mana_leak_core/gateway.py` module docstring; `apps/api/src/mana_leak_api/routers/health.py`; M1 PRD → Out of scope: "Observability/Langfuse tracing (M2)"). Every milestone from M3 onward adds tool calls, retrieval, and judging whose behaviour is hard to debug without tracing already live, so tracing needs to work before that complexity arrives (`docs/ROADMAP.md` → M2 → Why this milestone exists).

Langfuse is self-hosted only — there is no Cloud fallback (`docs/ROADMAP.md` → Decisions & deviations → Round 3 R3-2; `docs/architecture.md` → System context, Container architecture). It must provision headlessly via its own `LANGFUSE_INIT_*` environment variables so the application's `LANGFUSE_PUBLIC_KEY`/`LANGFUSE_SECRET_KEY` authenticate on first boot with no manual UI step, and it must never become an application dependency: a turn's answer never depends on Langfuse succeeding (`docs/architecture.md` → Container architecture table, Observability, Failure and degradation; `docs/contracts.md` → External adapters → Langfuse).

This milestone wires the stack, the headless provisioning, the conversation-as-session/trace-per-turn correlation, and the no-op degrade path into the one turn type that currently exists (`other`-route, M1's single model call per turn). It does not add tool-, retrieval-, or Judge-specific trace detail — those arrive with the milestones that introduce each capability (`docs/ROADMAP.md` → M2 → Out of scope).

## 2. Outcome

A user can have a conversation in the browser chat and then inspect every turn of that conversation as a trace in the local, self-hosted Langfuse UI, grouped under one session per conversation — reaching that state from a fresh checkout with a populated `.env` and no manual Langfuse setup step. Stopping Langfuse, or never configuring its keys, does not change the chat experience: turns stream and answer exactly as before. Consistent with the roadmap outcome above.

## 3. Scope

### In scope

- A self-hosted Langfuse stack added to the local Compose file, following the official Langfuse Docker Compose for the pinned version, on its own `langfuse` database/user — never mixed with `mana_leak` (`docs/architecture.md` → Container architecture; `docs/data-model.md` → Application and Langfuse separation).
- Headless provisioning via Langfuse's `LANGFUSE_INIT_*` environment variables (org, project, public + secret key, admin user) so the app's own `LANGFUSE_PUBLIC_KEY`/`LANGFUSE_SECRET_KEY` match on first boot, with no manual UI step (`docs/ROADMAP.md` → M2 → Scope; `.env.example`, already documenting both variable sets).
- Reading `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY`, and `LANGFUSE_HOST` from configuration (`docs/contracts.md` → Configuration, already specifying these as optional with tracing disabled when either key is empty).
- Conversation-as-session correlation (`conversation.id` is the Langfuse session ID) with one trace per turn (keyed by `turn_id`), opened and closed by the orchestrator so it spans the whole turn, with the model gateway's existing `trace: TraceContext` parameter attaching each `complete()` call as a child generation of that trace (`docs/contracts.md` → External adapters → Langfuse; `data-model.md` → `conversation`, `message`).
- Trace content for the turn types that exist at this milestone: route and model call(s) with token/cost metadata (`docs/architecture.md` → Operational boundaries, Observability) — not tool calls, retrieval source IDs, or Judge transitions, which do not exist until M3–M7.
- No-op degradation when Langfuse is unavailable or unconfigured: every tracing call becomes a no-op, and no domain function's result depends on it (`docs/contracts.md` → External adapters → Langfuse).
- Replacing M1's hardcoded `GET /health` `langfuse: "disabled"` stub with the model gateway's real, last-cached Langfuse SDK state (`ok`/`disabled`/`unavailable`), refreshed in the background and never probed synchronously per request (`docs/contracts.md` → REST API → `HealthResponse`).
- Extending the gateway's existing generic secret-redaction check to cover the newly configured Langfuse secret(s), so no secret reaches Langfuse (`docs/contracts.md` → External adapters → Langfuse; `gateway.py` module docstring: later `SecretStr` additions "are covered without another edit here").

### Out of scope

- Domain capabilities — cards, combos, rules, judging — not shipped yet; trace detail for tool calls, retrieval, and Judge transitions is added by the milestone that introduces each capability (`docs/ROADMAP.md` → M2 → Out of scope).
- CLI and MCP adapters (Stretch S1).
- Custom Langfuse dashboards, alerting, or scoring (`docs/ROADMAP.md` → M2 → Out of scope).
- The eval harness and its Langfuse-score integration (`eval_run.trace_id`) — introduced at M5/M9.
- A Langfuse Cloud fallback — self-hosted only, permanently (`docs/ROADMAP.md` → Decisions & deviations → Round 3 R3-2).
- Any new `mana_leak` table or column: `conversation.id`/`message.turn_id` already serve as the designated session/trace IDs; this milestone does not add persistence.

## 4. Functional requirements

- **FR-1** — The system shall run a self-hosted Langfuse stack (web, worker, ClickHouse, Redis/Valkey, MinIO) in the local Docker Compose stack, backed by its own `langfuse` database and user, separate from `mana_leak`.
- **FR-2** — When the Compose stack starts with `LANGFUSE_INIT_*` variables populated in `.env`, the system shall provision the Langfuse org, project, and API key pair headlessly, with no manual UI step, matching the app's own configured `LANGFUSE_PUBLIC_KEY`/`LANGFUSE_SECRET_KEY`.
- **FR-3** — The system shall load `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY`, and `LANGFUSE_HOST` from configuration (`Settings`) and configure the Langfuse client only from those values — the SDK's own environment lookup and its `https://cloud.langfuse.com` default are never relied upon — and shall enable tracing only when both keys are present.
- **FR-4** — When a chat turn is processed, the orchestrator shall open one Langfuse trace per turn — session ID equal to the turn's `conversation_id`, trace name and ID equal to the turn's `turn_id` — spanning the whole turn, and every model gateway `complete()` call within that turn shall be recorded, by the gateway, as a child generation observation of that trace.
- **FR-5** — The emitted trace shall carry the turn's route and its model call(s): token usage shall come from the provider's streamed usage, requested explicitly rather than discarded as M1 does, and cost shall come from the provider-reported cost passed alongside it when the provider supplies one, else recorded as explicitly null rather than silently omitted; it shall not claim tool-, retrieval-, or Judge-specific detail that does not exist yet.
- **FR-6** — When Langfuse is unreachable, unauthenticated, or its keys are unset, the system shall make every tracing call a no-op: the turn's SSE events and assistant answer shall be unaffected, and no part of turn processing shall depend on a Langfuse call succeeding.
- **FR-7** — `GET /health` shall report the model gateway's last cached Langfuse SDK state (`ok`, `disabled`, or `unavailable`), refreshed by a background task that never runs on the request event loop, never a synchronous per-request probe, replacing M1's hardcoded `"disabled"` value; any exception the refresh raises — including an authentication failure (wrong/mismatched keys) or a call that would otherwise block — shall be caught and mapped to `unavailable`, never propagated.
- **FR-8** — A fresh-volume `docker compose up` shall bring `postgres`, `api`, `web`, and every Langfuse-stack service to healthy, after which a chat turn in the browser produces a trace with no manual click-through in the Langfuse UI; `api` and `web` shall declare no Compose `depends_on` relationship to any Langfuse-stack service, so `docker compose up postgres api web` alone also reaches healthy.

## 5. Non-functional requirements

- **NFR-1** — Bounded execution / degradation: a Langfuse call that hangs, errors, or is simply absent shall never block or extend a turn past its existing model-call (30 s) or turn (120 s) budget (`docs/contracts.md` → Operational limits, External adapters → Langfuse: "every tracing call is a no-op").
- **NFR-2** — Security: any Langfuse secret (`LANGFUSE_SECRET_KEY`) shall be declared as a secret-typed configuration value so the gateway's existing generic secret-redaction check covers it automatically, before any content reaches Langfuse, without new redaction code; that check shall run before any Langfuse observation — trace or generation — records its input or metadata, not only before the model call itself, so content the orchestrator sets on the turn-level trace is covered too (`gateway.py` module docstring; `docs/contracts.md` → External adapters → Langfuse).
- **NFR-3** — Observability/correlation: every trace shall be addressable by the same `conversation_id`/`turn_id` pair already persisted on `message` (`conversation_id`, `turn_id`), so a trace is always traceable back to its persisted conversation turn without a new column (`docs/data-model.md` → `conversation`, `message`).

## 6. Interfaces and contracts affected

| Surface | Reference | Change |
|---|---|---|
| Local Compose Langfuse stack (`langfuse-web`, `langfuse-worker`, ClickHouse, Redis/Valkey, MinIO) | `architecture.md` → Container architecture, Local deployment | first introduced |
| `langfuse` database/user | `data-model.md` → Application and Langfuse separation | first introduced |
| `LANGFUSE_PUBLIC_KEY` / `LANGFUSE_SECRET_KEY` / `LANGFUSE_HOST` | `contracts.md` → Configuration | first introduced (read into `Settings`; already specified, unused until now) |
| `LANGFUSE_INIT_*` | `contracts.md` → Configuration; `architecture.md` → Local deployment | first introduced (Langfuse-side headless provisioning; already documented in `.env.example`) |
| `LANGFUSE_INIT_USER_NAME`, `NEXTAUTH_SECRET`, `SALT`, `ENCRYPTION_KEY`, `CLICKHOUSE_PASSWORD`, `REDIS_AUTH`, `MINIO_ROOT_PASSWORD` | `contracts.md` → Configuration; `.env.example` | first introduced (Compose-only Langfuse-stack secrets, read by `langfuse-web`/`langfuse-worker`/ClickHouse/Redis/MinIO containers, never by `api`) |
| `TraceContext(conversation_id, turn_id, route, prompt_version)` | `contracts.md` → External adapters → Langfuse | first introduced (`prompt_version` is `None` at M2 — no prompt-versioning scheme exists yet; a later milestone that introduces one sets it) |
| Model gateway `complete()` `trace` parameter | `contracts.md` → External adapters → Model gateway | implements (M1's `trace: object \| None` placeholder becomes a real `TraceContext`) |
| `GET /health` `langfuse` field | `contracts.md` → REST API → `HealthResponse` | implements (M1's hardcoded `"disabled"` becomes the gateway's real cached state) |

`docker-compose.yml`'s and `.env.example`'s existing `LANGFUSE_HOST` comments, which suggested an override to a Langfuse Cloud URL, are corrected by this milestone: self-hosted only, permanently, no Cloud fallback (`docs/ROADMAP.md` → Decisions & deviations → Round 3 R3-2).

No required behaviour in this milestone conflicts with a settled contract.

## 7. Acceptance criteria

- **AC-1** — Given a fresh-volume checkout with `.env` populated (including `LANGFUSE_INIT_*`), when `docker compose up` starts the stack, then `postgres`, `api`, `web`, and every Langfuse-stack service report healthy.
- **AC-2** — Given that same fresh start, when the Langfuse project is inspected (UI or API) after boot, then an org/project/key pair matching the app's configured `LANGFUSE_PUBLIC_KEY`/`LANGFUSE_SECRET_KEY` already exists, with no manual signup or project-creation step performed.
- **AC-3** — Given a conversation with several turns, when each turn completes, then Langfuse shows one trace per turn, all grouped under one session named by that conversation's ID.
- **AC-4 (boundary)** — Given an `other`-route turn (the only route that exists at this milestone), when its trace is inspected, then it shows one turn-level trace containing the turn's route, with the model call recorded as a child generation observation of that trace (never a second root span); the generation shows prompt and completion token counts greater than zero, and cost either present or explicitly null — never silently absent — depending on whether the provider reports one; the trace carries no tool-, retrieval-, or Judge-specific fields, because none of those capabilities exist yet.
- **AC-5 (degradation — Langfuse down)** — Given the Langfuse stack is stopped, when a chat turn is processed, then the SSE event sequence and assistant answer are identical to the stack-up case, and no part of turn processing errors or waits on Langfuse.
- **AC-6 (degradation — keys missing)** — Given `.env` has no `LANGFUSE_PUBLIC_KEY`/`LANGFUSE_SECRET_KEY` set, when a chat turn is processed, then tracing is disabled (no outbound Langfuse call is attempted) and the turn still answers normally.
- **AC-7 (failure — health reporting)** — Given Langfuse is reachable and authenticated, `GET /health` reports `langfuse="ok"`; given it is unreachable, it reports `langfuse="unavailable"`; given keys are unset, it reports `langfuse="disabled"`; given keys are set but mismatched, it reports `langfuse="unavailable"` — and in every case the response is produced without synchronously contacting Langfuse, so Langfuse's state never changes `/health`'s HTTP status code (still driven only by `database`); the background refresh runs at least once every 60 s, so a state change is reflected within 60 s, and a test may force an immediate refresh rather than wait.
- **AC-8 (boundary — secret redaction)** — Given a configured Langfuse secret, when message content coincidentally contains that value, then no observation of that turn — including the turn-level trace's recorded input — reaches Langfuse with that value present, the same way the gateway's existing redaction check already prevents `OPENROUTER_API_KEY` from reaching a model call.
- **AC-9 (demonstrable increment)** — A user has a multi-turn conversation in the browser chat, then opens the local self-hosted Langfuse UI and inspects every turn of that conversation as a trace grouped under one session — from a freshly checked-out repository with a populated `.env`, with no manual Langfuse setup step; the final turn's trace survives `docker compose stop api` (the SDK's automatic shutdown-time flush completes before the process exits).
- **AC-10 (degradation — keys mismatched)** — Given `LANGFUSE_PUBLIC_KEY`/`LANGFUSE_SECRET_KEY` set to a pair that does not match the provisioned Langfuse project, when a chat turn is processed, then the turn's SSE events and assistant answer are unaffected, no exception from the mismatch is logged as a turn failure, and `GET /health` reports `langfuse="unavailable"`.
- **AC-11 (boundary — host default endpoint)** — Given `LANGFUSE_HOST` unset in `.env` on a host (non-Compose) run, when the Langfuse client is configured, then its endpoint is `http://localhost:3001` — the value `Settings` defaults to — never the SDK's own `https://cloud.langfuse.com` default.
- **AC-12 (degradation — Langfuse hangs)** — Given the Langfuse endpoint accepts a connection but never responds, when a chat turn is processed, then the SSE event sequence and turn latency are unaffected within the existing model-call (30 s) and turn (120 s) budgets — the no-op degrade path also bounds a hang, not only a fast connection-refused.

## 8. Test and evaluation requirements

### Deterministic verification

- Fresh-volume `docker compose up` brings all six services (postgres, api, web, langfuse-web, langfuse-worker, plus their ClickHouse/Redis/MinIO dependencies) to healthy (AC-1); separately, `docker compose up postgres api web` alone (no Langfuse service started) also reaches healthy, proving `api`/`web` carry no Compose `depends_on` on the Langfuse stack — operational check, not a unit test.
- Headless provisioning: after a fresh start, the configured key pair authenticates against Langfuse with no manual project-creation step (AC-2) — operational check.
- Trace-per-turn and session grouping for a multi-turn conversation, verified via Langfuse's query API/UI, polling with a bounded timeout rather than asserting immediately after `message_end` — ingestion (SDK batch export → worker → ClickHouse) is asynchronous (AC-3).
- Trace content shape for an `other`-route turn — the orchestrator's turn-level trace with the model call recorded as its child generation, non-zero token counts, and cost-or-explicit-null — driven through `sse_stream` (not `process_turn` directly) with an in-memory span exporter, asserting the generation's parent is the turn trace and that no tracing error is logged (AC-4).
- SSE event sequence and turn outcome are identical whether Langfuse is reachable or not, exercised by pointing the gateway at a stopped/unreachable Langfuse host (AC-5), and at a host that accepts the connection but never responds, proving the no-op path also bounds a hang and not only a fast refusal (AC-12).
- No outbound Langfuse call is attempted when keys are unset (AC-6) — deterministic test asserting the tracing no-op path, mirroring the existing pattern for disabled-dependency tests.
- `/health`'s `langfuse` field for all three states (`ok`/`unavailable`/`disabled`) plus the keys-mismatched case (also `unavailable`), and that its HTTP status code depends only on `database` (AC-7, AC-10) — deterministic test, extending M1's existing `/health` database-state test, forcing an immediate background refresh rather than waiting out the 60 s bound.
- Secret redaction extended to the Langfuse secret, including the turn-level trace's recorded input/metadata and not only the model call, reusing the existing generic check's test pattern for `OPENROUTER_API_KEY` (AC-8).
- End-to-end demonstrable increment exercised through the browser chat and the Langfuse UI (AC-9).
- Keys-set-but-mismatched does not fail a turn and reports `langfuse="unavailable"` (AC-10) — deterministic test using a wrong key pair against a running Langfuse.
- Host-default endpoint with `LANGFUSE_HOST` unset resolves to `http://localhost:3001`, never the SDK's own Cloud default (AC-11) — deterministic test asserting the configured client's `base_url`.

### Evaluation

None for this milestone — the eval harness and its gates are introduced at M5/M9 (`docs/ROADMAP.md`; `docs/contracts.md` → Evaluation contracts).

## 9. Constraints

- Self-hosted Langfuse only; there is no Langfuse Cloud fallback, now or later (`docs/ROADMAP.md` → Decisions & deviations → Round 3 R3-2).
- This milestone traces only the `other`-route turn that exists today; tool-, retrieval-, and Judge-specific trace detail is added by M3–M7 as each capability ships (`docs/ROADMAP.md` → M2 → Out of scope).
- No new database table or column: `conversation.id` is already the designated Langfuse session ID and `message.turn_id` is already the designated trace ID (`docs/data-model.md` → `conversation`, `message`); this milestone wires existing fields to real Langfuse calls, it does not add persistence.
- The orchestrator owns the per-turn Langfuse trace: it alone opens the trace at the start of a turn and closes it at the end, spanning the whole turn regardless of how many model calls occur within it. The model gateway remains the only module that attaches Langfuse generation metadata to a model call, recorded as a child of that trace, mirroring its sole-LiteLLM-importer pattern (`gateway.py` module docstring; `docs/contracts.md` → Model gateway).
- Operational limits (model-call timeout 30 s, turn timeout 120 s, model-call cap 8) are unchanged and are defined once in `contracts.md` → Operational limits; this PRD does not redefine them.
- If this milestone's exit criterion (a chat turn appears as a trace under its conversation's session in local self-hosted Langfuse, provisioned headlessly) is not met on the first attempt, mark M2 `Blocked`, continue to M3 with tracing disabled, and retry at Checkpoint D (`docs/ROADMAP.md` → M2 → Depends on; Decisions & deviations → Round 3 R3-2).
- No authentication; the stack still binds to `127.0.0.1` (`docs/architecture.md` → Scope and assumptions) — the Langfuse stack is likewise local-only, no public deployment.
- The `langfuse` Python SDK dependency is already pinned (`langfuse>=4.7,<5`, `packages/core/pyproject.toml`); this milestone uses that pinned major version, it does not change it.
- The per-worktree test-isolation database (`mana_leak_test_<worktree>`, created on first use, dropped at session end) is development tooling, not product behaviour: it has no FR or AC of its own, is built as this milestone's first work package per the user decision recorded in Open issues, below, and is cited here only so the plan's first work package has a PRD anchor (`docs/ROADMAP.md`:654).

## 10. Risks and fallbacks

| Risk | Impact | Design-supported fallback |
|---|---|---|
| Langfuse stack wiring. Verified 2026-10-01 against the official `langfuse/langfuse` `docker-compose.yml` (main): images `docker.langfuse.com/langfuse/langfuse:4` and `langfuse-worker:4`, plus `clickhouse/clickhouse-server:25.12`, `redis:7`, MinIO (bucket `langfuse` created at start), and Postgres. Port clashes with Mana Leak: `langfuse-web` defaults to host port 3000, which Mana Leak's web uses, and the official stack ships its own Postgres on 5432; the official stack also publishes ClickHouse (8123/9000), Redis (6379), and MinIO (9090/9000), any of which can collide with a native/brew install on the dev host. | Wrong wiring prevents boot | Publish `langfuse-web` on host port 3001 (matching `LANGFUSE_HOST`'s host default) — and only that service; ClickHouse, Redis, and MinIO are not published to the host at all, only reachable on the Compose network. Point Langfuse at the existing Postgres `langfuse` database/user, not a second Postgres. ROADMAP Blocked/retry escape hatch if it still fails. |
| Headless provisioning. Verified in the same file: `langfuse-web` accepts `LANGFUSE_INIT_ORG_ID`, `_ORG_NAME`, `_PROJECT_ID`, `_PROJECT_NAME`, `_PROJECT_PUBLIC_KEY`, `_PROJECT_SECRET_KEY`, `_USER_EMAIL`, `_USER_NAME`, `_USER_PASSWORD`. Required server secrets: `NEXTAUTH_SECRET`, `SALT`, `ENCRYPTION_KEY` (64 hex chars), ClickHouse, Redis and MinIO credentials. | A mismatch between `LANGFUSE_INIT_PROJECT_*_KEY` and the app's `LANGFUSE_PUBLIC_KEY`/`SECRET_KEY` silently disables tracing | Derive the app keys from the same `.env` values as the INIT keys, so there is one source. The background health-refresh task — never the request or turn path — calls the SDK; `auth_check()` is blocking and re-raises API errors (a 401 on mismatch) rather than returning a value, so the refresh task wraps every call and maps any exception, including that 401, to `unavailable`. |
| SDK API. Verified against the installed `langfuse==4.16.0`: an OpenTelemetry-based client `Langfuse(public_key, secret_key, base_url\|host, ...)` / `get_client()`; spans via `start_as_current_observation(name, as_type="span"\|"generation", input, output, model, usage_details, ...)`; session and trace attributes via `propagate_attributes(session_id=..., trace_name=..., metadata=...)`; plus `flush()`, `auth_check()`, `shutdown()`. There is no v2-style `langfuse.trace()`. | Writing v2-era calls from memory would fail | Use only these verified APIs. Wrap every SDK call so exceptions become no-ops (`contracts.md` → Langfuse). |
| Running Langfuse's additional services (ClickHouse, Redis/Valkey, MinIO) alongside postgres/api/web may exceed local resource limits or slow a fresh `docker compose up` on constrained dev machines | Slower cold starts, flaky health checks, demo risk under the 24-hour constraint; observed 2026-10-01: 16 GiB free, with ~26.8 GB of Docker volumes and ~10.5 GB of build cache already on disk | Pre-flight `docker system prune` / stale-volume reclaim before first boot (≈35 GB reclaimable observed); expect ClickHouse and MinIO volumes to grow from near-zero at a fresh boot rather than assume headroom — beyond that, the ROADMAP Blocked/retry escape hatch. |

## 11. Dependencies

- Milestones: M1 (Walking skeleton) — `conversation`/`message`/`audit_event`, `process_turn`, the model gateway, and `/health` all already exist; this milestone wires Langfuse into them.
- Existing capabilities: the model gateway's `trace: object | None` placeholder parameter (`gateway.py`); the orchestrator's `conversation_id`/`turn_id` (`orchestrator.py`); `.env.example`'s already-documented Langfuse and `LANGFUSE_INIT_*` variables; `docker-compose.yml`'s existing `LANGFUSE_DB_PASSWORD` plumbing and placeholder comment block for the Langfuse stack; the `langfuse` Python SDK already pinned in `packages/core/pyproject.toml` (`langfuse>=4.7,<5`).
- External: a self-hosted Langfuse stack (its own PostgreSQL database, ClickHouse, Redis/Valkey, MinIO) — no Langfuse Cloud, no other external observability service.

## 12. Traceability

| Requirement | Source | Acceptance criteria | Verification |
|---|---|---|---|
| FR-1 | ROADMAP M2 Scope; `architecture.md` → Container architecture | AC-1, AC-9 | operational check |
| FR-2 | ROADMAP M2 Scope; `architecture.md` → Local deployment | AC-2, AC-9 | operational check |
| FR-3 | `contracts.md` → Configuration | AC-6, AC-7, AC-11 | deterministic test |
| FR-4 | ROADMAP M2 Scope/Outcome; `contracts.md` → External adapters → Langfuse | AC-3, AC-4, AC-9 | deterministic test / operational check |
| FR-5 | ROADMAP M2 Out of scope; `architecture.md` → Operational boundaries, Observability | AC-4 | deterministic test |
| FR-6 | ROADMAP M2 Scope/Success evidence; `contracts.md` → External adapters → Langfuse | AC-5, AC-6, AC-10, AC-12 | deterministic test |
| FR-7 | `contracts.md` → REST API → `HealthResponse` | AC-7, AC-10 | deterministic test |
| FR-8 | ROADMAP M2 Success evidence | AC-1, AC-9 | operational check |
| NFR-1 | `contracts.md` → Operational limits, External adapters → Langfuse | AC-5, AC-12 | deterministic test |
| NFR-2 | `gateway.py` (existing redaction); `contracts.md` → Langfuse | AC-8 | deterministic test |
| NFR-3 | `data-model.md` → `conversation`, `message` | AC-3 | deterministic test |

## 13. Open issues / design gaps

None. Resolved 2026-10-01:

- Test isolation (user decision): each worktree's DB-backed tests use their own database (`mana_leak_test_<worktree>`), created on first use and dropped at session end, so parallel lanes can't interfere. This is development tooling, not product behaviour; the M2 plan builds it as its first work package.
- Langfuse stack topology, `LANGFUSE_INIT_*` set, and SDK v4 API: verified (see Risks).

## 14. Completion condition

This PRD is complete only when:

- every acceptance criterion holds;
- the required verification (including the two operational checks: fresh-volume `docker compose up` to a fully healthy stack, and headless Langfuse provisioning with no manual UI step) passes;
- the milestone's demonstrable increment works end to end: a conversation in the browser chat, then every turn of it visible in Langfuse as one session;
- the required degradation paths work: Langfuse stopped, and Langfuse keys unset, both leave chat answers unaffected;
- no BLOCKER or MAJOR review finding remains open.

Implementation existing is not sufficient. If the exit criterion cannot be met, this PRD's milestone is marked `Blocked` in `docs/ROADMAP.md` per the documented escape hatch (Constraints, above), not silently marked `Complete`.
