Current activity: M1 implementation — waves 1–3 merged. In flight: m1-fix-audit (gateway awaits emit_audit_event), m1-api-real (WP5 vs real process_turn + concurrent 409 test), m1-e2e (WP8).

Done:
- M1 PRD Approved; plan Approved after Fable single-pass review (all 16 findings applied).
- Wave 1 dispatched 2026-10-01: m1-contracts (WP1, omp-worker-lite), m1-web (WP6, omp-worker), m1-compose (WP7, omp-worker-lite). Lane prompts in .workmux/prompts/ (git-excluded).

In flight: lanes m1-fix-audit, m1-api-real, m1-e2e. e2e restarts Compose postgres; DB tests in other lanes may need a rerun.

Waiting on user: rotate the OpenRouter key (a failing test printed it into the session transcript; fixed in f93e4b6) and update .env.

Next action: when lanes settle, pre-merge checks, merge fix-audit, api-real, e2e; then manual browser AC-12 walkthrough, §49a milestone gate, mark M1 Complete in ROADMAP.
