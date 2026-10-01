Current activity: Design-docs review gate (AGENTS.md §49a), round 1 repair.

Done this activity:
- Round 1 Fable review: 56 findings (0 BLOCKER, 20 MAJOR). Report: ~/.omp-tmp/mana-leak-review-r1.md (local, not in repo).
- User decided all behavioural/architectural majors; decisions in ~/.omp-tmp/r1-decisions.md (also to be recorded in ROADMAP.md "Decisions & deviations").
- Banner added to mana-leak-context.md; AGENTS.md §3 tie-break, deviation-record pointer, §49a fan-out rule, §53a resume rule.

In flight (uncommitted): per-file repair agents on docs/contracts.md, docs/architecture.md, docs/data-model.md, docs/ROADMAP.md, and config (docker-compose.yml, .env.example, packages/core/pyproject.toml, uv.lock, README.md).

Waiting on user: nothing.

Next action: when repairs finish, review diff, run `make test` and `docker compose config -q` (temp .env), commit + push "docs: round 1 review repairs", then start Fable round 2 against the same criteria (max 5 rounds; repairs fanned out one agent per file).
