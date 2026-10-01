Current activity: M1 implementation — waves 1–2 merged. Wave 3 dispatched: m1-turn (WP4, omp-worker-opus), m1-api (WP5, omp-worker).

Done:
- M1 PRD Approved; plan Approved after Fable single-pass review (all 16 findings applied).
- Wave 1 dispatched 2026-10-01: m1-contracts (WP1, omp-worker-lite), m1-web (WP6, omp-worker), m1-compose (WP7, omp-worker-lite). Lane prompts in .workmux/prompts/ (git-excluded).

In flight: wave 3 lanes m1-turn, m1-api; Compose postgres running.

Waiting on user: rotate the OpenRouter key (a failing test printed it into the session transcript; fixed in f93e4b6) and update .env.

Next action: when wave 3 settles, pre-merge checks, merge WP4 then WP5, re-run WP5 tests against real process_turn and add the real two-concurrent-POST test (fix lane if needed), then dispatch wave 4 (WP8 m1-e2e).
