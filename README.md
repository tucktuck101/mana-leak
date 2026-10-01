# Mana Leak

Mana Leak is a local-first, EDH-focused AI assistant for card search, Commander combo discovery, and evidence-grounded MTG rules judging. It combines local Scryfall-derived card data, Commander Spellbook combos, and the current Comprehensive Rules, and exposes one shared core through a streamed web chat, a CLI, and an MCP server.

Built as a **24-hour solo buildathon** project. The target is **local execution only** via Docker Compose; no public deployment.

**Status:** M1 walking skeleton implemented — persisted, resumable, streamed chat conversation end to end (Postgres, FastAPI SSE, turn orchestrator, model gateway, Next.js UI). No card, combo, rules, or judge capability yet.

## Prerequisites

- Docker with Docker Compose
- [uv](https://docs.astral.sh/uv/) (installs Python 3.12)
- Node.js 22+ and npm
- An OpenRouter API key

## Quick start

```bash
cp .env.example .env   # fill in values (OPENROUTER_API_KEY, CHAT_MODEL, DB passwords)
make setup              # local Python + web dependencies
make test                # unit/integration tests (live tests only if OPENROUTER_API_KEY is set)
make docker-up           # postgres + api + web, built and started via Compose
```

Open `http://localhost:3000` in a browser: create a conversation, send a message, and watch the
assistant's answer stream in. Reload the page to confirm the conversation persists; continue it
with another message.

`make e2e` runs the full Compose stack end to end (`tests/e2e/run.sh`): brings up
`postgres`/`api`/`web`, waits for all three to report healthy, streams a chat turn through the
Next.js proxy, restarts `postgres` and `api` to prove persistence survives a container restart,
and aborts a stream mid-turn to prove partial text is persisted with its error payload. It leaves
the stack running afterward; use `make docker-down` to stop it.

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
