Current activity: M1 — all WPs merged; make e2e passes. Browser AC-12 walkthrough found web defects (D1 tail of streamed answer missing in UI; D2 title not refreshed); fix lane m1-fix-web running.

Done:
- M1 PRD Approved; plan Approved after Fable single-pass review (all 16 findings applied).
- Wave 1 dispatched 2026-10-01: m1-contracts (WP1, omp-worker-lite), m1-web (WP6, omp-worker), m1-compose (WP7, omp-worker-lite). Lane prompts in .workmux/prompts/ (git-excluded).

In flight: lane m1-fix-web (apps/web). Compose stack running on localhost:3000/8000.

Waiting on user: rotate the OpenRouter key (a failing test printed it into the session transcript; fixed in f93e4b6) and update .env.

Next action: when m1-fix-web settles, pre-merge checks, merge, rebuild web container, redo AC-12 browser walkthrough (wait for Stop button to clear between turns), then §49a milestone gate, mark M1 Complete.
