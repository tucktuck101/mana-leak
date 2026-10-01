Current activity: M1 — milestone-gate fixes F1–F10 all merged (latest 84b90d4); fresh-volume boot + make e2e pass (F7). Browser smoke re-found an intermittent first-turn abort in a new chat; lane m1-fix-web3 (Opus) adding a Playwright regression test (10 runs) + root-cause fix.

Done:
- M1 PRD Approved; plan Approved after Fable single-pass review (all 16 findings applied).
- Wave 1 dispatched 2026-10-01: m1-contracts (WP1, omp-worker-lite), m1-web (WP6, omp-worker), m1-compose (WP7, omp-worker-lite). Lane prompts in .workmux/prompts/ (git-excluded).

In flight: lane m1-fix-web3. Compose stack up on a fresh volume.

Waiting on user: rotate the OpenRouter key (a failing test printed it into the session transcript; fixed in f93e4b6) and update .env.

Next action: when m1-fix-web3 settles, verify the browser test failed before and passes 10/10 after, merge, rebuild web, make e2e (now includes browser test), then mark M1 Complete (ROADMAP + PRD + plan).
