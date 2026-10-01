.PHONY: setup dev test lint format docker-up docker-down e2e

setup: ## Install Python and web dependencies
	uv sync
	cd apps/web && npm install

dev: ## Run API (port 8000) and web (port 3000) locally
	(trap 'kill 0' INT TERM; \
	  uv run uvicorn mana_leak_api.main:app --reload --port 8000 & \
	  cd apps/web && npm run dev & \
	  wait)

test: ## Run pytest and web vitest; live pytest tests included only when OPENROUTER_API_KEY is set (shell env or .env)
	@OPENROUTER_API_KEY="$${OPENROUTER_API_KEY:-$$(grep -m1 '^OPENROUTER_API_KEY=' .env 2>/dev/null | cut -d= -f2-)}"; \
	if [ -n "$$OPENROUTER_API_KEY" ]; then \
	  OPENROUTER_API_KEY="$$OPENROUTER_API_KEY" uv run pytest; \
	else \
	  uv run pytest -m 'not live'; \
	fi
	npm --prefix apps/web run test

lint:
	uv run ruff check .
	uv run ruff format --check .
	cd apps/web && npm run lint

format:
	uv run ruff check --fix .
	uv run ruff format .

docker-up:
	docker compose up --build -d

docker-down:
	docker compose down

e2e: ; tests/e2e/run.sh
