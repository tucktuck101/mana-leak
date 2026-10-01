Current activity: M2 — PRD Approved (cef035f+). PRD plan drafted: docs/prds/M2-observability.plan.md (Draft).

Done:
- M1 PRD + plan Complete; ROADMAP M1 = Complete. Verification log in docs/prds/M1-walking-skeleton.plan.md.
- make test (122 Python + 19 web), make lint, make e2e (incl. Playwright browser test, 11/11) pass on master.

In flight: nothing. Compose stack (postgres/api/web) is running.

Waiting on user: review of docs/prds/M2-observability.plan.md + its open questions (test DB vs schema, 3 Opus lanes). Do NOT dispatch until approved.

Known follow-ups (not M1 scope): lanes share one mana_leak_test DB, so concurrent lane test runs interfere — give each worktree its own test DB before M2 lanes run in parallel.

Next action: on approval, run preflight (ALTER ROLE mana_leak CREATEDB if DB approach; docker prune for disk), dispatch wave 1 (WP1 m2-test-db, WP2 m2-compose, WP3 m2-tracing-core).
