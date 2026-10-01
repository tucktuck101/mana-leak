Current activity: M1 — Walking skeleton is Complete (2026-10-01). Next milestone: M2 — Observability (self-hosted Langfuse).

Done:
- M1 PRD + plan Complete; ROADMAP M1 = Complete. Verification log in docs/prds/M1-walking-skeleton.plan.md.
- make test (122 Python + 19 web), make lint, make e2e (incl. Playwright browser test, 11/11) pass on master.

In flight: nothing. Compose stack (postgres/api/web) is running.

Waiting on user: (1) OpenRouter key rotation (optional, user's call); (2) go-ahead to start M2 PRD.

Known follow-ups (not M1 scope): lanes share one mana_leak_test DB, so concurrent lane test runs interfere — give each worktree its own test DB before M2 lanes run in parallel.

Next action: derive docs/prds/M2-observability.md from ROADMAP M2 per docs/prds/README.md; user reviews it.
