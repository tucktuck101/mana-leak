Current activity: M1 — Fable milestone gate returned "fix first" (2 MAJOR, 6 MINOR, 2 NIT; report ~/.omp-tmp/m1-milestone-review.json). F10 done (c0aa815). Fix lanes running: m1-fix-gw (F1 F2 F3 F9), m1-fix-env (F4 F6), m1-fix-enum (F5), m1-fix-make (F8).

Done:
- M1 PRD Approved; plan Approved after Fable single-pass review (all 16 findings applied).
- Wave 1 dispatched 2026-10-01: m1-contracts (WP1, omp-worker-lite), m1-web (WP6, omp-worker), m1-compose (WP7, omp-worker-lite). Lane prompts in .workmux/prompts/ (git-excluded).

In flight: 4 fix lanes. User approved F7: `docker compose down -v` fresh-volume boot + make e2e after merges.

Waiting on user: rotate the OpenRouter key (a failing test printed it into the session transcript; fixed in f93e4b6) and update .env.

Next action: when lanes settle, checks, merge (fix-gw first; then type process_turn session_action as SessionControl), make test/lint, F7 fresh-volume run (down -v, up --build, make e2e), browser smoke, then mark M1 Complete (ROADMAP + PRD status).
