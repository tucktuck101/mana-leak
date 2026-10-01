Current activity: M1 — all WPs + m1-fix-web merged. Browser AC-12 still fails: first turn of a NEW chat is aborted client-side ~2 s in ("client disconnected"), next send lost, title stale. Fix lane m1-fix-web2 (omp-worker-opus) running.

Done:
- M1 PRD Approved; plan Approved after Fable single-pass review (all 16 findings applied).
- Wave 1 dispatched 2026-10-01: m1-contracts (WP1, omp-worker-lite), m1-web (WP6, omp-worker), m1-compose (WP7, omp-worker-lite). Lane prompts in .workmux/prompts/ (git-excluded).

In flight: lane m1-fix-web2. Compose stack up (localhost:3000/8000).

Waiting on user: rotate the OpenRouter key (a failing test printed it into the session transcript; fixed in f93e4b6) and update .env.

Next action: when m1-fix-web2 settles, checks, merge, `docker compose up -d --build web`, redo AC-12 walkthrough (new chat, 3 turns, verify server messages have no payload.error, reload, continue), then §49a milestone gate, mark M1 Complete.
