# Mana Leak

Mana Leak is a local-first, EDH-focused AI assistant for card search, Commander combo discovery, and evidence-grounded MTG rules judging. It combines local Scryfall-derived card data, Commander Spellbook combos, and the current Comprehensive Rules, and exposes one shared core through a streamed web chat, a CLI, and an MCP server.

Built as a **24-hour solo buildathon** project. The target is **local execution only** via Docker Compose; no public deployment.

**Status:** repository scaffold. No application features are implemented yet.

## Prerequisites

- Docker with Docker Compose
- [uv](https://docs.astral.sh/uv/) (installs Python 3.12)
- Node.js 22+ and npm
- An OpenRouter API key

## Quick start

> Placeholder — finalised once the application is implemented.

```bash
cp .env.example .env   # fill in values
make setup             # local Python + web dependencies
make test
make docker-up         # postgres + api + web
```

Other commands: `make dev`, `make lint`, `make format`, `make docker-down`.

## Documentation

- [Project context](mana-leak-context.md)
- [Vision](docs/vision.md)
- [Architecture](docs/architecture.md)
- [Data model](docs/data-model.md)
- [Contracts](docs/contracts.md)
- [Build plan](docs/build-plan.md)
