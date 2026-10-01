.PHONY: setup dev test lint format docker-up docker-down

setup: ## Install Python and web dependencies
	uv sync
	cd apps/web && npm install

dev: ## Run API (port 8000) and web (port 3000) locally
	(trap 'kill 0' INT TERM; \
	  uv run uvicorn mana_leak_api.main:app --reload --port 8000 & \
	  cd apps/web && npm run dev & \
	  wait)

test:
	uv run pytest

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
