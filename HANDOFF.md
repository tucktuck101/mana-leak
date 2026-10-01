Current activity: M2 implementation. WP1–WP6 merged (latest 5285eb5); master make test 173 passed. WP7 m2-e2e (full-stack e2e with Langfuse) dispatched.

Done: M1 Complete. M2 PRD + plan Fable-reviewed (single pass each), all findings applied; secrets fail closed (user decision); per-worktree test schemas (user decision).

In flight: lane m2-e2e. It starts the Langfuse services.

Waiting on user: optional rotation of OPENROUTER_API_KEY (leaked twice into transcripts) and MANA_LEAK_DB_PASSWORD (leaked once).

Next action: when m2-e2e settles, checks, merge, run make e2e on master, browser + Langfuse UI check (AC-9), §49a milestone gate, doc-sync step, mark M2 Complete.
