Current activity: M2 — Observability, implementation. PRD + plan Approved (docs/prds/M2-observability{,.plan}.md). Wave 1 dispatched 2026-10-01: m2-test-db (WP1, lite), m2-compose (WP2, omp-worker), m2-tracing-core (WP3, opus). Prompts in .workmux/prompts/m2-wp*.md.

Done: M1 Complete. M2 PRD + plan Fable-reviewed (single pass each), all findings applied; secrets fail closed (user decision); per-worktree test schemas (user decision).

In flight: wave 1 lanes. Compose postgres/api/web running.

Waiting on user: nothing.

Next action: when wave 1 settles: fan-in (handoff, owned paths only, acceptance, make test, make lint), merge serially WP1 → WP2 → WP3, then wave 2 in merge order WP4 → WP6 → WP5 (re-test WP5 after WP6), then WP7 e2e, browser + Langfuse UI check, §49a milestone gate, doc-sync step, mark M2 Complete.
