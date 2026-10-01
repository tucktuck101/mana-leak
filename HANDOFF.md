Current activity: M2 — Observability. PRD drafted (docs/prds/M2-observability.md, Proposed); Langfuse stack, INIT vars, SDK v4 API verified and recorded in its Risks.

Done:
- M1 PRD + plan Complete; ROADMAP M1 = Complete. Verification log in docs/prds/M1-walking-skeleton.plan.md.
- make test (122 Python + 19 web), make lint, make e2e (incl. Playwright browser test, 11/11) pass on master.

In flight: nothing. Compose stack (postgres/api/web) is running.

Waiting on user: review of docs/prds/M2-observability.md (Proposed). Test-DB isolation decided (per-worktree DB, first M2 WP).

Known follow-ups (not M1 scope): lanes share one mana_leak_test DB, so concurrent lane test runs interfere — give each worktree its own test DB before M2 lanes run in parallel.

Next action: after user review + approval, write docs/prds/M2-observability.plan.md (do not dispatch until plan approved).
