#!/usr/bin/env bash
# `make e2e` target (Makefile, WP7). Full-stack end-to-end check for M1
# (docs/prds/M1-walking-skeleton.plan.md -> WP8 row/checklist): brings up
# the real Compose stack, waits for postgres/api/web to report healthy,
# then runs tests/e2e/run.py, which drives the chat/restart/abort flow
# entirely through the Next.js proxy (http://localhost:3000/api/...).
#
# Does not tear the stack down on exit (per-WP checklist: WP8 is the only
# lane that runs the full stack, and it leaves it running). Does not use
# `docker compose down -v` at any point: Postgres may already be running
# with data other lanes/tests depend on.
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/../.."

echo "==> docker compose up -d --build (postgres, api, web)"
docker compose up -d --build postgres api web

echo "==> running tests/e2e/run.py"
exec uv run python3 tests/e2e/run.py
