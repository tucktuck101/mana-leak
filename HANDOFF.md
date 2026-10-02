Current activity: M2 milestone gate (Fable): fix first — 1 MAJOR (M2-01 boot evidence), 10 MINOR, 5 NIT (report ~/.omp-tmp/m2-milestone-review.json). Fix lanes m2-fix-trace (M2-02,06,07,12,14) and m2-fix-tests (M2-03,04,05,13) running; M2-08..11 carried to M3 Open PRD inputs (610135e).

Done: M1 Complete. M2 PRD + plan Fable-reviewed (single pass each), all findings applied; secrets fail closed (user decision); per-worktree test schemas (user decision).

In flight: 2 fix lanes.

Waiting on user: nothing (user approved the fixes and the `docker compose down -v` fresh-boot check).

Next action: merge fix lanes; M2-01 boot checks (down -v + up --build; postgres/api/web only); re-run make e2e; then Fable milestone review ROUND 2 (user request). If round 2 finds anything: fix it, then WAIT for the user before marking M2 Complete. If clean: mark M2 Complete.
