# Mana Leak

Mana Leak is a local-first, EDH-focused AI assistant for card search, Commander combo discovery, and evidence-grounded MTG rules judging. It combines local Scryfall-derived card data, Commander Spellbook combos, and the current Comprehensive Rules, and exposes one shared core through a streamed web chat, a CLI, and an MCP server.

Built as a **24-hour solo buildathon** project. The target is **local execution only** via Docker Compose; no public deployment.

**Status:** M1 walking skeleton and M2 observability implemented — persisted, resumable, streamed chat conversation end to end (Postgres, FastAPI SSE, turn orchestrator, model gateway, Next.js UI), every turn traced in a self-hosted Langfuse instance (one trace per turn, grouped by conversation), degrading safely when Langfuse is unreachable or unconfigured. No card, combo, rules, or judge capability yet.

## Prerequisites

- Docker with Docker Compose
- [uv](https://docs.astral.sh/uv/) (installs Python 3.12)
- Node.js 22+ and npm
- An OpenRouter API key

```bash
cp .env.example .env   # fill in values (OPENROUTER_API_KEY, CHAT_MODEL, DB passwords, Langfuse keys/stack secrets)
make setup              # local Python + web dependencies
make test                # unit/integration tests (live tests only if OPENROUTER_API_KEY is set)
make docker-up           # postgres + api + web + the self-hosted Langfuse stack, built and started via Compose
```

Open `http://localhost:3000` in a browser: create a conversation, send a message, and watch the
assistant's answer stream in. Reload the page to confirm the conversation persists; continue it
with another message.

`.env.example` documents every Langfuse variable and **two separate key paths**: the Compose `api` service always reads `LANGFUSE_INIT_PROJECT_PUBLIC_KEY`/`_SECRET_KEY` (the headless-provisioned pair, below) — the app's own `LANGFUSE_PUBLIC_KEY`/`LANGFUSE_SECRET_KEY` have no effect under `make docker-up`; host/CLI runs (`make test`, `make dev`, `uv run mana-leak ...`) read `LANGFUSE_PUBLIC_KEY`/`LANGFUSE_SECRET_KEY`/`LANGFUSE_HOST` directly instead, and never see `LANGFUSE_INIT_*`. Either path is optional — tracing is disabled, and chat still answers, until its relevant pair is set — plus the Langfuse-stack-only secrets (`NEXTAUTH_SECRET`, `SALT`, `ENCRYPTION_KEY`, `CLICKHOUSE_PASSWORD`, `REDIS_AUTH`, `MINIO_ROOT_PASSWORD`). With `LANGFUSE_INIT_*` filled in, open `http://localhost:3001` after `make docker-up` to inspect every turn as a trace grouped under its conversation's session, with no manual project-creation step.

`make e2e` runs the full Compose stack end to end (`tests/e2e/run.sh`): brings up
`postgres`/`api`/`web` plus the Langfuse stack, waits for all eight services to report healthy,
streams a chat turn through the Next.js proxy and polls Langfuse's own query API for that turn's
trace under its conversation's session, restarts `postgres` and `api` to prove persistence
survives a container restart, aborts a stream mid-turn to prove partial text is persisted with
its error payload, stops `langfuse-web` to prove a turn is unaffected and `/health` reports
`langfuse: "unavailable"`, and finally stops `api` after one more turn to prove that turn's trace
survives the shutdown-time flush. It leaves the stack running afterward; use `make docker-down`
to stop it.

Other commands: `make dev` (run API + web locally without Docker), `make lint`, `make format`,
`make docker-down`.

## Documentation

- [Agent operating model](AGENTS.md)
- [Vision](docs/vision.md)
- [Architecture](docs/architecture.md)
- [Data model](docs/data-model.md)
- [Contracts](docs/contracts.md)
- [Roadmap](docs/ROADMAP.md)
- [Project context](mana-leak-context.md) — historical input that shaped the design; not an authority (see `AGENTS.md` §3)
