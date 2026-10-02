# Mana Leak — 24-hour buildathon reflection

**Window:** 2026-10-01 13:58 to 2026-10-02 15:22 NZDT (first to last commit,
about 25.5 hours of wall clock including a 6.5-hour overnight gap).
**State at freeze:** M1 and M2 Complete; M3 (Cards) stopped at its PRD review
gate. 135 commits on `master`.

Written by the orchestrating agent from the repository, the commit history and
the session record. Claims about why something happened are the agent's
reading of that record, not the developer's own account.

## What was built

A local-only, Docker Compose application with two finished milestones out of a
planned ten.

- **M1, walking skeleton.**
  - A Next.js chat UI that reaches the API only through a `/api/*` proxy, so
    SSE passes through untouched.
  - FastAPI with an SSE turn endpoint and conversation REST routes.
  - A shared Python core: `process_turn` orchestration, a LiteLLM/OpenRouter
    model gateway with its own timeouts, call cap and reasoning off by
    default, and Postgres persistence through Alembic.
  - Conversations that persist across reloads and restarts, with partial text
    kept on abort and a 409 for a concurrent turn.
  - `make e2e` covering health, streaming, restarts and abort, plus a
    Playwright browser regression test.
- **M2, observability.**
  - Self-hosted Langfuse: web, worker, ClickHouse, Redis and MinIO on the
    existing Postgres, with only the Langfuse UI published, on 127.0.0.1:3001.
    Keys are provisioned headlessly through `LANGFUSE_INIT_*`.
  - One trace per turn (trace ID = turn, session = conversation), with every
    model call as a child generation carrying real token counts and provider
    cost.
  - Secrets fail closed before anything is traced.
  - The app keeps answering when Langfuse is down, hung or given wrong keys,
    and `/health` reports `unavailable`.
  - Bounded shutdown flush, with uvicorn as PID 1.
- **Supporting work.** Per-worktree test schemas so parallel lanes don't
  corrupt each other's tests; 176 Python and 19 web tests; about 8,900 lines of
  Python and TypeScript.
- **Design set.**
  - Docs: vision, architecture, data model, contracts, ROADMAP, a PRD authoring
    standard, and an agent operating model (`AGENTS.md`). About 64,000 words
    including the PRDs and plans.
  - Supporting process: a review-gate process, a session-resume handoff file,
    and a usage-budget rule.

Not built: cards, combos, rules retrieval, the judge, stateful clarification,
safeguards, evaluation, demo hardening, and the CLI/MCP stretch. In other
words, none of the product's Magic-specific behaviour exists yet.

## How it was built

The method stayed the same from start to finish:

1. Design documents first, then three rounds of adversarial review on them
   (Fable reviewer, fixes fanned out one agent per file).
2. A walking-skeleton roadmap.
3. Per milestone, a PRD and a PRD plan, each with its own Fable review gate.
   The plan was implemented as workmux lanes: parallel git worktrees, each with
   its own agent and owned files, merged serially by the orchestrator after
   mechanical checks.
4. A Fable milestone review before marking the milestone Complete.

Product and architecture questions went to the developer and were recorded in
ROADMAP → Decisions & deviations. For the last stretch, a Fable stand-in made
those calls instead, logged in the gitignored `afk-decisions.md`.

## Hour by hour

From commit timestamps (NZDT). Commit counts show where activity was, not
effort: review rounds and lane runs often produced no commits for 20–30
minutes, then several at once.

| Hour | Commits | What happened |
|---|---:|---|
| 10-01 13–14 | 10 | Repo scaffold (uv workspace, Next.js, Compose, pgvector Postgres); `.gitignore` fix; vision written. |
| 15 | 3 | Architecture; data model; ROADMAP replaces the build-plan model; `AGENTS.md` added. |
| 16 | 4 | Design review round 1 (56 findings) and repairs; session-resume rule and `HANDOFF.md`; usage-budget rule. |
| 17 | 0 | Round 1 repairs and round 2 review in flight. |
| 18 | 2 | Round 2 repairs: roadmap reorganised to a walking skeleton; session controls; rules explanations. |
| 19 | 12 | Round 3 repairs, design gate closed (CLI/MCP to stretch); dev model chosen on cost; M1 LLM spike; PRD standard and template; M1 PRD; workmux config and rules. |
| 20 | 18 | M1 plan, Fable plan review, fixes; wave 1 merged (contracts, Compose, web); a test leaked the API key and was fixed; wave 2 merged. |
| 21 | 12 | Wave 3 (turn orchestration, API); fix lanes (audit await, real-API tests); WP8 e2e. |
| 22 | 11 | Disk full broke e2e (Docker pruned); browser walkthrough found two web bugs; fixes. |
| 23 | 9 | M1 milestone review (2 MAJOR); four fix lanes; fresh-volume boot. |
| 10-02 00 | 5 | Pre-hydration click bug found and fixed with a Playwright test; M1 Complete; M2 PRD drafted. |
| 01 | 2 | M2 PRD reviewed and approved; M2 plan drafted. |
| 02–07 | 0 | Overnight gap; Fable quota reset. |
| 08 | 2 | M2 plan decisions (test schemas, Opus lanes); plan under Fable review. |
| 09 | 5 | M2 plan review (9 MAJOR) and fixes; wave 1 dispatched. |
| 10 | 5 | WP1 and WP3 merged; WP2 froze on `docker run` and was relaunched; wave 2 dispatched early. |
| 11 | 16 | WP4, WP2 and WP6 merged; a lane leaked secrets via `docker compose config`, so a new `AGENTS.md` rule was added; WP5 merged. |
| 12 | 4 | WP7 e2e merged; e2e on master failed (shell keys overrode Compose); fixed; Langfuse UI verified. |
| 13 | 9 | M2 milestone review (1 MAJOR, 10 MINOR); fix lanes; AFK mode set up. |
| 14 | 5 | Fresh-volume boot failed (ClickHouse start window) and was fixed; M2 Complete; M3 PRD drafted and reviewed. |
| 15 | 1 | M3 PRD repairs in flight; frozen. |

Rough split of the active hours, estimated from the table above:

| Activity | Share of active time |
|---|---:|
| Design docs and their review rounds | ~25% (13:58–20:00 on day one) |
| M1 | ~25% |
| M2 | ~35% |
| M3 PRD | ~10% |
| Process rules, handoff and orchestration | ~5% |

## Where the time went

Design dominated the first six hours. Three adversarial review rounds over
roughly 6,000 lines of docs produced 56, then 27, then 33 findings. Each repair
round introduced new problems: round 2 found that four of its five majors came
from round 1's own fixes. At hour four you said, "im 4/24hrs into this with no
code", and the gate was closed after round 3 with the remaining minors deferred.
The design itself held up well afterwards. The volume of review was the cost.

Each milestone then paid a fixed overhead of four reviewed artefacts (PRD, PRD
review, plan, plan review) before any lane started. That took about 1.5–2 hours
per milestone, including one gate on a plan already approved by you, and two
milestones landed in roughly 15 active hours. The remaining eight would not
have fit at that rate.

Waiting was a large, mostly invisible cost:
- Fable reviews took 6–26 minutes each, and there were about twelve.
- Lanes took 10–35 minutes.
- `make e2e` took 4–6 minutes, longer with the Langfuse stack.
- Model calls take about 11 s to the first token on DeepSeek.
- One lane froze for about an hour on a `docker run` during an image pull
  before it was noticed and relaunched.

## What went well

- **The review gates caught real defects before they shipped**, several of
  which no test would have caught:
  - the 8-call cap being unenforceable through the API (per-task contextvars);
  - an OpenTelemetry context crossing tasks;
  - litellm's usage-only final chunk crashing every streamed turn;
  - the SDK singleton making the secret-leak test pass vacuously;
  - a schema search path that would have isolated nothing.
- **End-to-end and browser checks found what unit tests could not.** The
  dropped final chunk, the Send/Stop slot swap, the pre-hydration native form
  submit, the shell-key override of Compose, and the ClickHouse first-boot
  window were all found by running the real stack, then pinned with
  regression tests.
- **Workmux lanes with single-owner files** let up to four agents work in
  parallel with almost no merge conflicts, and cross-lane edits were raised as
  blockers rather than made silently.
- **Recorded decisions.** Every product and architecture decision is in
  ROADMAP → Decisions & deviations, so the design trail is recoverable without
  this conversation.
- **The walking-skeleton reorganisation** meant each finished milestone is a
  working, demonstrable product.

## What didn't

- **Scope.** Two of ten milestones is a clear miss against the plan. The
  process was tuned for correctness per milestone, not throughput across the
  24 hours. Both the roadmap and the vision call for "complete over broad" and
  "speed is key", and the ceremony per milestone worked against that.
- **Secrets leaked into transcripts three times**, none into git:
  1. A failing test printed the real `OPENROUTER_API_KEY` in pytest's diff.
  2. A lane printed `docker compose config`, exposing four values.
  3. The orchestrator's own shell environment held unrelated Langfuse keys,
     which Compose then preferred over `.env`.

  Each led to a fix and a rule (`SecretStr`, hermetic tests, the `AGENTS.md`
  §22 rule against printing resolved config, INIT-only key mapping), but the
  OpenRouter key and the app database password should still be rotated.
- **Tests that passed while the product was broken.** Mocks hid the un-awaited
  audit write. The shared test database made parallel lanes corrupt each
  other's runs. Unit tests never drove the real browser. Each was found later
  than it could have been.
- **Infrastructure friction on the machine:** a full disk crashed Postgres
  mid-test, slow image pulls, and the ClickHouse cold start. None of these are
  Mana Leak bugs, but together they cost about an hour.
- **Orchestrator slips:**
  - an `npm` command ran from a backtick in a prompt string;
  - the progress-table updates were mis-targeted;
  - a merge was retried without a diagnosis first;
  - one lane's acceptance relied on its own shell setup (it unset the
    Langfuse keys), which hid the key bug until master's e2e run.

## Loose ends at freeze

- **Uncommitted at freeze:** the M3 doc repairs (`contracts.md`,
  `data-model.md`, `ROADMAP.md`) from the M3 PRD review are committed
  separately and labelled frozen and unreviewed. The M3 PRD itself still has
  round-1 findings unapplied; its repair agent was stopped.
- **AFK stand-in decisions** for M3 are in `afk-decisions.md` (gitignored, local
  only). They were not reviewed by you.
- **Key rotation:** `OPENROUTER_API_KEY` (exposed twice) and
  `MANA_LEAK_DB_PASSWORD` (exposed once).
- **Review evidence:** reports and stand-in decisions are in `~/.omp-tmp/`,
  outside the repo.
- **The stack:** the full stack is still running locally.

## Lessons learned

These are the developer's own takeaways, paraphrased.

**Don't get stuck in the design phase.** The first six hours went on design
documents and three rounds of review before any application code existed. The
design held up, but most of what it settled could have been worked out during
M1 and M2 with real code to test against. A short design pass, then a running
skeleton, is a better trade-off at this timescale.

**Agentic review gates are worth having, but they cost more time than this
project had.** The gates caught real failures that agents produce: tests that
pass vacuously, contracts that drift between lanes, context lost across task
boundaries, and secrets showing up in tool output. Those would have been
expensive to find later. But each gate took 6–26 minutes, and there were
several per milestone. In a 24-hour build that overhead competes directly with
building features. The value is real; the frequency needs to match the time
available.

**"Simpler" still wasn't small enough.** The intent was a narrower project that
applied what the course had covered, using the Jobfinder app as a reference for
its shape. The misjudgement was the size. Card search, combo lookup, rules
retrieval, a stateful judge, evaluation, observability, safeguards and three
interfaces form a full product, not a 24-hour slice. Even with CLI and MCP cut
to a stretch goal, ten milestones were never going to fit. A project for this
window needs to be one or two of those capabilities at most.

**Mana Leak won't be developed further.** It's an interesting app, and there's
plenty of open data to build on: card databases, rulings, combos and prices.
But plenty of existing apps already cover this space, so there's no strong
reason to keep going. The value of the exercise was the process: the agent
operating model, workmux orchestration, review gates, and what the e2e and
browser checks caught. That carries over to the next project. The product
doesn't need to.

## If doing it again

- Cap design review at one round before code, and let PRDs and milestone
  reviews catch the rest.
- Use one review gate per milestone (on the plan) instead of three. Put the
  remaining time into the e2e and browser checks, which found the most
  consequential bugs.
- Make the per-worktree test database and the "never print resolved
  configuration" rule part of the first scaffold, not a mid-build lesson.
