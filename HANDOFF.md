Current activity: Design-docs review gate (AGENTS.md §49a), round 2 review.

Done this activity:
- Round 1 Fable review (56 findings, 20 MAJOR); report ~/.omp-tmp/mana-leak-review-r1.md (local).
- User decided all behavioural/architectural majors (~/.omp-tmp/r1-decisions.md; summarised in ROADMAP.md "Decisions & deviations").
- Round 1 repairs committed (contracts, architecture, data-model, ROADMAP, AGENTS, context banner, config).

In flight: Fable round 2 review (agent AdversarialDocReview2), same criteria as round 1; report to ~/.omp-tmp/mana-leak-review-r2.md.

Waiting on user: DOC-022 fuzzy card-lookup rule (similarity ≥0.6 + margin 0.1 can't produce the "Kiki" → ambiguous example).

Next action: when round 2 returns, triage; fix technical findings with one subagent per file; ask user on product/architecture findings; commit; round 3 if any BLOCKER/MAJOR (max 5 rounds).
