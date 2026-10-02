Mode: AFK (user away; resume normal mode when the user says they're back). Decisions normally put to the user are made by a Fable stand-in agent and logged in afk-decisions.md (gitignored, repo root). At least one Fable review at every gate, then judge whether another round is needed.

Current activity: M2 milestone gate. Fable round 1: fix first (1 MAJOR M2-01 boot evidence, 10 MINOR, 5 NIT; report ~/.omp-tmp/m2-milestone-review.json). Fix lanes m2-fix-trace (M2-02,06,07,12,14) and m2-fix-tests (M2-03,04,05,13) running. M2-08..11 carried to M3 Open PRD inputs (610135e). User said: no second review round for M2.

Done: M1 Complete. M2 WP1–WP7 merged; make e2e with Langfuse passes; Langfuse UI verified.

In flight: 2 fix lanes.

Waiting on user: nothing (AFK).

Next action: merge fix lanes; M2-01 (approved before AFK): `docker compose down -v`, `up --build -d` until all 8 healthy, then `up -d postgres api web` only; re-run make e2e; record; mark M2 Complete. Then M3 (Cards): PRD → Fable review → plan → Fable review → dispatch → verify → Fable milestone gate → Complete; then M4 and onward.
