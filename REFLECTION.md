# Mana Leak — a 24-hour buildathon

**Date:** 2026-10-01 to 2026-10-02

## What this was meant to be

The goal was a 24-hour solo build: something "simpler" that pulled together what
I'd learned on the course, using the Jobfinder app as a reference for its shape.
I picked Mana Leak, an assistant for Commander players that searches cards,
finds known combos, and explains rules interactions with citations to the
Comprehensive Rules rather than answering from model memory.

The clock ran from the first commit at 13:58 on 1 October to the freeze at 15:22
the next day. That's about 25.5 hours of wall clock, including a gap of roughly
six and a half hours overnight. At the freeze, two of the ten planned milestones
were complete, M3 had stopped at its PRD review, and there were 135 commits on
`master`.

So the honest version up front: I built a well-tested walking skeleton with
observability, and none of the Magic-specific behaviour that makes it Mana Leak.

## What actually exists

M1 is the walking skeleton. A Next.js chat UI talks to FastAPI only through an
`/api/*` proxy, so the SSE stream passes through untouched. Behind that sits a
shared Python core with `process_turn`, a model gateway over LiteLLM and
OpenRouter, and Postgres through Alembic. The gateway enforces its own timeouts
and per-turn call cap, and turns reasoning off by default. Conversations survive
reloads and restarts, a mid-stream abort keeps the partial answer, and a second
message during a turn gets a 409. `make e2e` covers health, streaming, restarts
and the abort, and there's a Playwright browser test as well.

M2 added self-hosted Langfuse: the web app, worker, ClickHouse, Redis and MinIO,
all on the Postgres I already had. Only the Langfuse UI is published, on
127.0.0.1:3001, and the keys are provisioned headlessly through
`LANGFUSE_INIT_*`, so there's no clicking through a setup screen. Every turn is
one trace (trace ID is the turn, session is the conversation), and each model
call sits under it with real token counts and the cost OpenRouter reports. A
message containing a secret fails before anything gets traced. If Langfuse is
down, hung, or has the wrong keys, the app keeps answering and `/health` reports
`unavailable`.

Around that there's about 8,900 lines of Python and TypeScript, 176 Python tests
and 19 web tests, and per-worktree test schemas so parallel agents can't trample
each other's test data.

The documentation set is big for a 24-hour project: about 64,000 words across
the vision, architecture, data model, contracts, roadmap, a PRD standard, the
PRDs and plans, and an `AGENTS.md` operating model.

What doesn't exist yet: card search, combos, rules retrieval, the judge,
clarification, safeguards, evals, demo hardening, and the CLI and MCP stretch.

## How I ran it

The pattern stayed the same the whole way through. I wrote the design documents
first, then put them through three rounds of adversarial review with Fable, with
the repairs fanned out to one agent per file. From there I reorganised the
roadmap around a walking skeleton, so every milestone would leave something I
could actually demo.

Each milestone then went through the same gates. It got a PRD, which Fable
reviewed. It got a PRD plan, which Fable also reviewed. The plan was run as
workmux lanes, each a git worktree with its own agent and a fixed set of files
it owned, and the orchestrator merged them one at a time after mechanical
checks. Finally, Fable did a milestone review before anything was marked
Complete.

Product and architecture questions came to me and went into the roadmap's
Decisions & deviations section. For the last stretch I stepped away, and a Fable
agent stood in for me on those calls, logging each one in a gitignored
`afk-decisions.md`.

## Hour by hour

This comes from the commit timestamps (NZDT). Commit counts show where activity
landed, not effort: review rounds and lane runs often went 20–30 minutes without
a commit and then produced several at once.

| Hour | Commits | What happened |
|---|---:|---|
| 10-01 13–14 | 10 | Repo scaffold (uv workspace, Next.js, Compose, pgvector Postgres); `.gitignore` fix; vision written. |
| 15 | 3 | Architecture; data model; roadmap replaces the build-plan model; `AGENTS.md` added. |
| 16 | 4 | Design review round 1 (56 findings) and repairs; session-resume rule and `HANDOFF.md`; usage-budget rule. |
| 17 | 0 | Round 1 repairs and round 2 review in flight. |
| 18 | 2 | Round 2 repairs: roadmap reorganised as a walking skeleton; session controls; rules explanations. |
| 19 | 12 | Round 3 repairs and design gate closed (CLI/MCP to stretch); dev model chosen on cost; M1 LLM spike; PRD standard and template; M1 PRD; workmux config and rules. |
| 20 | 18 | M1 plan, Fable plan review and fixes; wave 1 merged (contracts, Compose, web); a test leaked the API key and was fixed; wave 2 merged. |
| 21 | 12 | Wave 3 (turn orchestration, API); fix lanes (audit await, real-API tests); WP8 e2e. |
| 22 | 11 | Disk full broke e2e (Docker pruned); browser walkthrough found two web bugs; fixes. |
| 23 | 9 | M1 milestone review (2 MAJOR); four fix lanes; fresh-volume boot. |
| 10-02 00 | 5 | Pre-hydration click bug found and fixed with a Playwright test; M1 Complete; M2 PRD drafted. |
| 01 | 2 | M2 PRD reviewed and approved; M2 plan drafted. |
| 02–07 | 0 | Overnight gap; Fable quota reset. |
| 08 | 2 | M2 plan decisions (test schemas, Opus lanes); plan under Fable review. |
| 09 | 5 | M2 plan review (9 MAJOR) and fixes; wave 1 dispatched. |
| 10 | 5 | WP1 and WP3 merged; WP2 froze on `docker run` and was relaunched; wave 2 dispatched early. |
| 11 | 16 | WP4, WP2 and WP6 merged; a lane leaked secrets via `docker compose config`, so a new `AGENTS.md` rule went in; WP5 merged. |
| 12 | 4 | WP7 e2e merged; e2e on master failed (shell keys overrode Compose); fixed; Langfuse UI checked. |
| 13 | 9 | M2 milestone review (1 MAJOR, 10 MINOR); fix lanes; AFK mode set up. |
| 14 | 5 | Fresh-volume boot failed (ClickHouse start window) and was fixed; M2 Complete; M3 PRD drafted and reviewed. |
| 15 | 1 | M3 PRD repairs in flight; frozen. |

Roughly, the active hours split like this:

| Activity | Share of active time |
|---|---:|
| Design docs and their review rounds | ~25% (13:58–20:00 on day one) |
| M1 | ~25% |
| M2 | ~35% |
| M3 PRD | ~10% |
| Process rules, handoff and orchestration | ~5% |

## Six hours of design before any code

The first six hours went on documents. Three review rounds over roughly 6,000
lines of design produced 56 findings, then 27, then 33, and each repair round
created new problems of its own: in round 2, four of the five major findings
came from round 1's fixes. Four hours in I had no code at all, and that was the
point where I closed the design gate after round 3 and pushed the remaining
minor findings into the PRDs.

The design did hold up afterwards, so it wasn't wasted. But a lot of what it
settled could have been worked out during M1 and M2, with real code to test the
decisions against instead of more documents.

## Four gates per milestone

Once building started, every milestone paid a fixed cost before a single lane
ran: a PRD, its review, a plan, and its review. That came to about 1.5–2 hours
per milestone. Two milestones landed in about 15 active hours, so at that rate
the other eight were never going to fit.

On top of that was a lot of waiting that doesn't show up anywhere:

- Fable reviews took 6–26 minutes each, and there were about twelve of them.
- Lanes took 10–35 minutes.
- `make e2e` took 4–6 minutes, more once the Langfuse stack was in it.
- DeepSeek takes about 11 seconds to the first token on every model call.
- One lane sat frozen for about an hour on a `docker run` during an image pull
  before I noticed and relaunched it.

## What the gates and the real stack caught

The review gates earned their keep. They caught bugs that no test I'd written
would have:

- the 8-call cap couldn't be enforced through the API, because the counter lived
  in per-task context and every SSE event ran in a new task;
- an OpenTelemetry context crossed from one task to another;
- litellm's final usage-only chunk would have crashed every streamed turn;
- the Langfuse SDK's client reuse made the secret-leak test pass without
  checking anything;
- a schema search path that would have isolated nothing.

Running the real stack caught a different set. The dropped final chunk in the
UI, Send and Stop sharing one button slot, a click before hydration submitting
the form natively and navigating away, my shell's Langfuse keys overriding
Compose, and ClickHouse needing longer than 51 seconds on its first boot all
only showed up end to end. Each one now has a regression test.

Workmux with single-owner files also worked well. Up to four agents ran in
parallel with almost no merge conflicts, and when a lane needed a file it didn't
own, it stopped and asked instead of quietly editing it.

The decisions trail is solid too. Everything I decided is in the roadmap's
Decisions & deviations, so it can be recovered without this conversation.

## Where it went wrong

The scope is the obvious one: two of ten milestones. The process was tuned for
getting each milestone right, not for getting through 24 hours. My own docs said
"complete over broad" and I'd said speed was key, and the ceremony around each
milestone worked against both.

Secrets ended up in agent transcripts three times, although never in git:

1. A failing test printed the real `OPENROUTER_API_KEY` in pytest's diff.
2. A lane ran `docker compose config`, which printed four resolved values.
3. My shell had Langfuse keys for an unrelated project, and Compose preferred
   them over `.env`.

Each one led to a fix and a rule: `SecretStr` for settings, tests that ignore
the real environment, an `AGENTS.md` rule against printing resolved config, and
Compose taking the app's Langfuse keys only from the `LANGFUSE_INIT_*` pair. The
OpenRouter key and the app database password still need rotating.

Some tests passed while the product was broken. Mocks hid an audit write that
was never awaited. Lanes sharing one test database corrupted each other's runs.
The unit tests never drove the real browser. All three were found later than
they could have been.

The machine itself cost about an hour: the disk filled and crashed Postgres
mid-test, image pulls were slow, and ClickHouse was slow to start cold. None of
that is a Mana Leak bug, but it still came out of the 24 hours.

The orchestration had its own slips:

- a backtick in a prompt string ran an `npm` command;
- progress table updates hit the wrong lines;
- a merge was retried before anyone worked out why it failed;
- one lane's end-to-end run passed only because it unset the Langfuse keys in
  its own shell, which hid the key bug until master's run.

## What I'm taking away

Don't get caught up in the design phase. A short design pass followed by a
running skeleton is the better trade at this timescale, because the code tests
the decisions faster than another review round does.

Agentic review gates are worth it. This set of rules caught failure modes that
agents really do produce: tests that pass without testing, contracts drifting
between parallel lanes, context lost across task boundaries, and secrets leaking
into tool output. Those would have been expensive to find later. But the gates
took more time than this project had. The value is there; how often I run them
has to fit the time available.

"Simpler" still wasn't small enough. I wanted something narrower that focused on
what I'd learned, with Jobfinder as the reference. What I didn't see was that
card search, combos, rules retrieval, a stateful judge, evals, observability,
safeguards and multiple interfaces add up to a whole product, not a 24-hour
slice. Even after moving CLI and MCP to a stretch goal, ten milestones were
never going to fit. For a window like this I'd pick one or two of those
capabilities at most.

I'm not going to keep developing Mana Leak. It's a cool idea, and there's plenty
of open data to build on: card databases, rulings, combos and prices. But there
are already a lot of apps doing this. What I'm keeping is the process: the agent
operating model, the workmux orchestration, the review gates, and the habit of
proving things end to end. That carries straight over to the next project.

If I ran this again:

- one design review round before code, and let the PRD and milestone reviews
  catch the rest;
- one review gate per milestone, on the plan, with the saved time going into the
  end-to-end and browser checks that found the worst bugs;
- the per-worktree test database and the "never print resolved config" rule in
  the first scaffold, not learned halfway through.

## Loose ends

- The M3 contract and data-model edits from its PRD review are committed
  separately, labelled frozen and unreviewed. The M3 PRD itself still has
  round-1 findings unapplied; its repair agent was stopped.
- The M3 decisions the Fable stand-in made for me are in `afk-decisions.md`,
  which is local only and gitignored. I haven't reviewed them.
- `OPENROUTER_API_KEY` (exposed twice) and `MANA_LEAK_DB_PASSWORD` (exposed
  once) still need rotating.
- The review reports and stand-in decisions are in `~/.omp-tmp/`, outside the
  repo.
- The stack is stopped (`docker compose down`), with its data volumes kept.

Drafted with my orchestrating agent from the commit history and session record.
The takeaways are mine.
