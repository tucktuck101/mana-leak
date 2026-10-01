#!/usr/bin/env bash
# `make e2e` target (Makefile, WP7). Full-stack end-to-end check for M1
# (docs/prds/M1-walking-skeleton.plan.md -> WP8 row/checklist): brings up
# the real Compose stack, waits for postgres/api/web to report healthy,
# then runs tests/e2e/run.py, which drives the chat/restart/abort flow
# entirely through the Next.js proxy (http://localhost:3000/api/...), and
# finally the browser regression test (apps/web/e2e), which drives the same
# stack with a real Chromium.
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
uv run python3 tests/e2e/run.py

# Real-browser flow: new chat -> two turns, ten times over, plus a Send
# clicked before hydration on a freshly loaded conversation page
# (apps/web/e2e/first-turn.spec.ts). Needs Chromium:
# `npm --prefix apps/web exec -- playwright install chromium` once.
echo "==> running npm --prefix apps/web run test:browser"
exec npm --prefix apps/web run test:browser
