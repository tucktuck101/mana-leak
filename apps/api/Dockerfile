FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim
WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
COPY pyproject.toml uv.lock .python-version ./
COPY packages/core ./packages/core
COPY apps/api ./apps/api
COPY evals/fixtures ./evals/fixtures
RUN uv sync --frozen --no-dev --package mana-leak-api
ENV PATH="/app/.venv/bin:$PATH"
EXPOSE 8000
CMD ["sh", "-c", "alembic -c packages/core/alembic.ini upgrade head && exec uvicorn mana_leak_api.main:app --host 0.0.0.0 --port 8000"]
