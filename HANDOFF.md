Current activity: M1 implementation — wave 1 merged (WP1, WP6, WP7 + secret-leak fix f93e4b6). Wave 2 dispatched: m1-db (WP2), m1-gateway (WP3).

Done:
- M1 PRD Approved; plan Approved after Fable single-pass review (all 16 findings applied).
- Wave 1 dispatched 2026-10-01: m1-contracts (WP1, omp-worker-lite), m1-web (WP6, omp-worker), m1-compose (WP7, omp-worker-lite). Lane prompts in .workmux/prompts/ (git-excluded).

In flight: wave 2 lanes m1-db, m1-gateway; Compose postgres running (keep it up for pre-merge tests).

Waiting on user: rotate the OpenRouter key (a failing test printed it into the session transcript; fixed in f93e4b6) and update .env.

Next action: when wave 2 settles, pre-merge checks, merge WP2 then WP3, re-run WP3 tests against the real audit.py, then dispatch wave 3 (WP4 m1-turn omp-worker-opus, WP5 m1-api omp-worker).
