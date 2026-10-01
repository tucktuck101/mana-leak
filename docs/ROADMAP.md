# Roadmap

## Purpose

This roadmap sets the milestone-level delivery sequence for Mana Leak. It orders the implementation of the settled design (`vision.md`, `architecture.md`, `data-model.md`, `contracts.md`) without redesigning it.

This roadmap is the overall implementation plan for Mana Leak. Each milestone is a PRD-sized delivery unit. PRDs define what is built (requirements and acceptance criteria); PRD plans define how (work packages, sequence, tests, tasks). Neither belongs here.

```text
vision · architecture · data-model · contracts
        ↓
     ROADMAP
        ↓
       PRD
        ↓
    PRD plan
```

## Delivery strategy

Mana Leak is delivered as a walking skeleton that grows one capability at a time, not as architectural layers. The full product — persisted, streamed, browser-based chat — exists from the first milestone; every later milestone ships a complete capability through that same browser chat and leaves the product demonstrably more capable than before:

```text
a working chat product exists
→ every turn is observable
→ cards work
→ combos work
→ rules are explained with citations
→ rulings are judged with citations
→ ambiguity resolves through clarification
→ the system resists adversarial and runaway input
→ quality and safety become measurable
→ the demo is hardened
```

Each milestone builds only the persistence, contracts, and interfaces its own capability needs beyond what already exists.

This is a solo build with no fixed per-milestone time budget. The checkpoint rules below fire on one observable, non-clock signal instead of hours or a feeling of being behind: **a milestone's PRD plan has already had to be re-planned once to fit its remaining work.** The solo builder checks for that signal at every milestone's `Complete` transition, which is also when the next checkpoint below is reached.

## Roadmap summary

| Milestone | Outcome | Depends on | Status |
|---|---|---|---|
| M1 — Walking skeleton | A persisted, resumable, streamed chat product in the browser, with a plain model answer | Design docs, repository scaffold | Not Started |
| M2 — Observability | Every chat turn traced in self-hosted Langfuse, degrading safely when it is down | M1 | Not Started |
| M3 — Cards | Deterministic search and inspection of current card data, reachable in chat | M1 | Not Started |
| M4 — Combos | Discover and explain known Commander Spellbook combos in chat | M3 | Not Started |
| M5 — Rules explanations | Cited, validated natural-language rules explanations and exact rule text, in chat | M1, M3, M4 | Not Started |
| M6 — Structured rulings | Structured, cited legality rulings, with force-ask on unstated assumptions | M5; M4 for combos | Not Started |
| M7 — Stateful Judge | Multi-turn clarification resolves missing game state | M6 | Not Started |
| M8 — Safeguards & limits | Screening and execution limits hardened against adversarial input | M7 | Not Started |
| M9 — Evaluation | Measured, gated behaviour across all suites | M8 | Not Started |
| M10 — Demo hardening | Repeatable full demo from a clean local start | M9 | Not Started |

Status vocabulary: `Not Started`, `In Progress`, `Complete`, `Blocked`. The completed repository scaffold and design documents do not count toward M1.

## Milestone progression

| Milestone | Tangible increment |
|---|---|
| M1 | Chat with an AI in the browser, reload, continue |
| M2 | Inspect every chat turn in Langfuse |
| M3 | Ask about cards in chat |
| M4 | Find and explain known combos in chat |
| M5 | Get validated, cited rules explanations and exact rule text in chat |
| M6 | Get structured, cited legality rulings in chat |
| M7 | Resolve missing game state through clarification in chat |
| M8 | Adversarial and runaway input are safely handled |
| M9 | Measure correctness and safety across all suites |
| M10 | Repeat the reliable full demo |

## Dependency view

```mermaid
flowchart LR
    M1[M1 Walking skeleton]
    M2[M2 Observability]
    M3[M3 Cards]
    M4[M4 Combos]
    M5[M5 Rules explanations]
    M6[M6 Structured rulings]
    M7[M7 Stateful Judge]
    M8[M8 Safeguards and limits]
    M9[M9 Evaluation]
    M10[M10 Demo hardening]
    S1[S1 Agent interfaces - stretch]

    M1 --> M2
    M1 --> M3
    M1 --> M5
    M3 --> M4
    M3 --> M5
    M4 --> M5
    M5 --> M6
    M6 --> M7
    M7 --> M8
    M8 --> M9
    M9 --> M10
    M4 -.-> S1
    M7 -.-> S1
```

M2 depends only on M1. If its exit criterion is not met on the first attempt, mark M2 `Blocked`, continue to M3 with tracing disabled, and retry M2 at Checkpoint D before M9 needs it; the execution order below is otherwise M1 → M10.

---

## M1 — Walking skeleton

### Outcome
A persisted, resumable, streamed chat conversation works end to end in the browser, with a plain model answer and no domain capability yet.

### Why this milestone exists
This proves the full vertical slice — database, API, streaming, model gateway, and web UI — before any domain capability is added, so every later milestone adds a capability to an already-working product instead of integrating one for the first time at the end.

### Scope
- Docker Compose stack: Postgres with Alembic migrations for `conversation`, `message`, and `audit_event` (so every later milestone's audit events have a table to write to from the day they're added).
- The FastAPI SSE endpoint and the `process_turn` turn-orchestration skeleton.
- The model gateway (LiteLLM/OpenRouter) with configuration; it enforces the per-turn model-call cap and the turn timeout, emitting `limit_reached` via `emit_audit_event` when either fires — the budget every later milestone's limit rules build on.
- The Next.js chat UI behind a `/api/*` route-handler proxy to `API_BASE_URL`, with SSE streaming passthrough; the browser never calls the API directly.
- Persisted, resumable conversations.
- Every message resolves to the `other` route with a plain, general streamed model answer (no scope-steering yet — that narrows once routing exists, M3); no routing logic and no tools yet.
- A `/health` endpoint, so Compose health checks never block on a route that doesn't exist.

### Depends on
Repository scaffold and the design documents.

### Demonstrable increment
Chat with an AI in the browser, reload the page, and continue the conversation.

### Success evidence
- SSE events stream a plain assistant answer to the browser in the documented order.
- Conversation and message history persist across reload and container restart.
- `/health` reports accurately, and Compose brings up postgres, api, and web cleanly from a fresh checkout.
- Every message gets a plain model response; no domain routing exists yet.
- A turn that exceeds the model-call cap or turn timeout fails in a structured way and is recorded as an `audit_event`.

### Out of scope
Cards, combos, rules, judging, routing beyond `other`, tool use, observability, CLI, MCP.

---

## M2 — Observability

### Outcome
Every chat turn is traced end to end in self-hosted Langfuse, correlated by conversation, degrading safely when Langfuse is unavailable.

### Why this milestone exists
Tracing needs to be live before any domain capability is added, so everything from M3 onward is debuggable from the moment it ships instead of retrofitted after the whole system is built. Proving the stack, its headless key provisioning, and its no-op degrade on the simplest possible turn removes that risk before tool calls, retrieval, and judging raise the stakes.

### Scope
- A self-hosted Langfuse stack in local Compose, following the official stack, on its own database; there is no Langfuse Cloud fallback.
- Headless provisioning via Langfuse's `LANGFUSE_INIT_*` env vars (org, project, public+secret key) so the app's own `LANGFUSE_PUBLIC_KEY`/`LANGFUSE_SECRET_KEY` match on first boot, with no manual UI step.
- Conversation-as-session correlation, with a trace for every turn.
- No-op degradation when Langfuse is unavailable; chat answers are unaffected.

### Depends on
M1.

### Demonstrable increment
Have a conversation in the browser chat, then inspect every turn of it in Langfuse as one session.

### Success evidence
- Exit criterion: a chat turn appears as a trace under its conversation's session in local self-hosted Langfuse, provisioned headlessly with no UI step. If this does not hold after the first attempt, mark M2 `Blocked`, continue to M3 with tracing disabled, and retry at Checkpoint D.
- A fresh-volume `docker compose up` yields traces with no manual click-through.
- Stopping Langfuse does not affect chat answers.

### Out of scope
Domain capabilities (cards, combos, rules, judging — not shipped yet); tool-, retrieval-, and Judge-specific trace detail, added as each capability ships; CLI, MCP; custom dashboards or alerting.

---

## Checkpoint A — Product skeleton

After M2, Mana Leak is a real product with no domain capability yet: conversations persist and stream through the browser chat, every turn gets a plain model answer, and every turn is traceable in Langfuse (or M2 is `Blocked` and tracing is retried at Checkpoint D, per M2's exit criterion).

---

## M3 — Cards

### Outcome
Mana Leak can deterministically search, look up, and inspect current Magic card data, and answer card questions through the router in the browser chat.

### Why this milestone exists
Cards are the base evidence for every later capability — combos are made of cards, and rulings are about cards — and the first real domain route proves the router and bounded tool-loop pattern every later route reuses.

### Scope
- Ingestion of Scryfall-derived Oracle card data into local storage, and the persistence it needs; CLI access via `ingest cards`.
- Structured card search over the defined filter set; lookup by name, face name, and identifier, with ambiguity reported explicitly rather than guessed.
- Card provenance and source-version awareness.
- Intent routing (`cards`|`other`), with the bounded tool loop enforcing the per-turn tool-call cap and truncating runaway tool output, and a `cards`-route tool allowlist.
- `other` narrows from M1's plain general answer to the contracts' fixed short scope-steering answer (no tools), now that routing exists.

### Depends on
M1.

### Demonstrable increment
Ask about cards in the browser chat — find and inspect real current cards, including multi-face cards.

### Success evidence
- Correct results for known cards by exact name, face name, and identifier.
- Ambiguous or misspelled names produce explicit candidates or a flagged fuzzy match.
- Structured filters (type, identity, legality, mana value, keyword, text) behave as defined.
- Every card carries provenance and a source version.
- The router selects `cards` for card questions and `other` otherwise; tools outside the allowlist never execute; a tool-call cap breach fails in a structured way, not a raw error.
- An off-topic message gets the short scope-steering answer, not a general-purpose reply.

### Out of scope
Combos, rules retrieval, judging, multi-route reasoning beyond `cards`/`other`.

---

## M4 — Combos

### Outcome
Mana Leak can find and present known Commander Spellbook combos from structured combo data, through the router in the browser chat.

### Why this milestone exists
Combo discovery is the second step of the primary demo journey. It reuses card resolution from M3 and adds the only live external runtime dependency, so its fallback strategy is proven early.

### Scope
- Commander Spellbook integration behind an adapter boundary.
- Combo search, lookup by identifier, and finding combos that include all supplied cards; pieces, prerequisites, ordered steps, and results, with provenance.
- Local caching and an offline fixture fallback using the same domain contract; `source: live|cache|fixture` on every result.
- The router gains `combos`; the tool-loop allowlist extends to it.
- CLI access via `ingest combo-fixtures`.

### Depends on
M3.

### Demonstrable increment
Find and explain a known combo in the browser chat — card → known combo → pieces, prerequisites, steps, results. The demo combo works even when Commander Spellbook is unreachable.

### Success evidence
- Known demo cards return their known combos with steps and results.
- Unresolvable or ambiguous card names fail explicitly instead of producing partial results.
- Fixture fallback serves the demo path with live access disabled, and results are marked by `source`.
- A live miss with no cache or fixture coverage returns `dependency_unavailable`, never an empty list.
- No combo is ever produced by a model.

### Out of scope
Natural-language explanation of why a combo works (M5/M6), rules legality, judging, EDHREC or popularity data.

---

## M5 — Rules explanations

### Outcome
Mana Leak retrieves current Comprehensive Rules evidence and answers rules questions with a validated, cited, natural-language explanation — or deterministic exact rule text — through a new `judge` route, refusing to answer when evidence or game state is insufficient.

### Why this milestone exists
The rules corpus is the highest-authority domain evidence. Shipping the first judge output here — real retrieval, a real evidence ledger, and real citation validation — proves the judge's core machinery on explanations, which need simpler semantics than a legality verdict, before M6 reuses the same machinery for the higher-stakes `legal`/`illegal` verdict, and before M7 adds sessions.

### Scope
- Ingestion of the current Comprehensive Rules with version and provenance; chunking that follows the rules' own hierarchy; CLI access via `ingest rules`.
- Embedding and semantic retrieval, keyword retrieval, and direct rule-number lookup; a hybrid merge with keyword-only fallback; retrieval is capped on every leg.
- The judge's internal rules query composes the question with identified card names and short Oracle excerpts; the public `RuleSearchRequest.query` stays length-bounded for other callers.
- The router gains `judge` alongside `cards`/`combos`/`other`; code-orchestrated evidence gathering (cards from M3, combos from M4, rules from this milestone) feeds the judge — no tools are exposed to the model.
- A bounded sufficiency decision: enough evidence and state to answer, or not.
- The turn's evidence ledger: code resolves each model-emitted `CitationRef` against retrieved evidence into a full `Citation` (`chunk_id`, `rules_version`); unresolvable refs are rejected.
- Citation validation (`citation_valid`) against the ledger, with safe failure and a documented retry on invalid or unverifiable citations; a bounded second pass fetches by rule number any citation the draft needs but the ledger lacks, then redrafts once.
- Judge output kinds: `RulesExplanation` (cited natural-language explanation, able to walk through multi-step board states, the default for rules/interaction questions) and `RuleText` (deterministic, verbatim rule lookup, no model paraphrase, for rule-number requests). `RulesExplanation` persists in the `ruling` table with a new `kind` column (`ruling`|`explanation`); `RuleText` is message-payload only.
- Pre-M7 rule: insufficient evidence or state returns a `RulesExplanation` with empty citations and non-empty `missing_information`, persisted in `ruling` (M7 replaces this with real clarification; M6 adds the equivalent `Ruling{status: insufficient_information}` for legality questions).
- Sources are the Comprehensive Rules only; a question that genuinely depends on tournament policy (MTR/IPG) is flagged as such, not ruled on.
- The retrieval evaluation suite and the harness to run it; `eval_case`/`eval_run` tables are created in this milestone's migration.

### Depends on
M1, M3, M4 (combo evidence).

### Demonstrable increment
Ask a rules question — including a multi-step interaction — in the browser chat and get a validated, cited explanation; ask for a rule's exact text and get it verbatim; ask something the current rules don't settle and get `insufficient_information` listing what's missing.

### Success evidence
- Rule-number lookup returns the correct rule or subrule, verbatim, with no model paraphrase.
- Natural-language queries surface the expected rules in the retrieval suite at the agreed hit-rate target.
- A demo multi-step interaction (for example, casting Panglacial Wurm mid-search while paying with Selvala, Explorer Returned) gets a correctly cited, step-by-step explanation.
- A citation that doesn't resolve against the ledger is rejected and follows the documented retry and safe-failure path; no persisted result carries an unverified citation.
- Questions without supporting evidence return `insufficient_information` rather than a guess from model memory.
- Every explanation or rule-text result records model, prompt, and rules version.
- Retrieval still returns results with embeddings disabled.

### Out of scope
Legality rulings (`Ruling`) and force-ask (M6); stateful clarification and sessions (M7).

---

## M6 — Structured rulings

### Outcome
Mana Leak adds structured, cited legality rulings alongside M5's rules explanations, reusing M5's evidence ledger and citation validation, and hardens both output kinds with force-ask, so neither can carry an unstated assumption past the user.

### Why this milestone exists
Legality tolerates less ambiguity than an explanation — a wrong `legal`/`illegal` is a worse failure than an imprecise explanation. M5 already made citations trustworthy; this milestone adds the higher-stakes verdict and the force-ask guard on top, before M7 adds session state.

### Scope
- `Ruling{status: legal|illegal|conditional|insufficient_information}`, chosen by the judge's `answer_kind` field alongside `RulesExplanation`/`RuleText`; reuses M5's evidence ledger, citation resolution, `citation_valid` validation, and bounded second pass.
- Ruling-specific validation beyond M5's shared citation check: `conditional` requires non-empty `assumptions`; `insufficient_information` is the only `Ruling` status allowed empty citations, and only together with non-empty `missing_information`.
- Force-ask: any draft — any status, not only `conditional` — with non-empty `assumptions` while clarification rounds remain converts to the pre-M7 `insufficient_information` result (a `Ruling{status: insufficient_information}` here, the same pattern as M5's `RulesExplanation` equivalent), with the same bookkeeping as a plain insufficiency; `missing_information` stays empty on every other status.

### Depends on
M5; M4 for combos.

### Demonstrable increment
Ask whether an interaction is legal in the browser chat and get a validated, cited ruling (`legal`, `illegal`, or `conditional`), or `insufficient_information` listing exactly what's missing — and M5's explanations now share the same evidence ledger.

### Success evidence
- Demo legality questions return validated rulings citing retrieved rules and cards.
- A ruling that cites unretrieved evidence is rejected and follows M5's documented retry and safe-failure path.
- A draft with assumptions never reaches the user as a bare `legal`/`illegal`/`conditional` answer while rounds remain.
- Every ruling records model, prompt, rules, and card versions.

### Out of scope
Stateful sessions and multi-turn clarification (M7).

---

## Checkpoint B — Evidence and judging foundation

After M6, cards, combos, cited rules explanations, exact rule text, and structured legality rulings all work through the shared core in the browser chat. Only stateful clarification, hardening, and measurement are left.

**Checkpoint rule:** if any milestone through M6 needed its PRD plan re-planned once already, apply the minimum acceptable increment (below) starting with M7.

---

## M7 — Stateful Judge

### Outcome
Mana Leak resolves rules questions that depend on missing game state across multiple conversational turns, replacing the pre-M7 `insufficient_information` shortcut with real clarification, and behaves like a normal chat assistant around it — an off-topic detour never silently derails an open ruling.

### Why this milestone exists
"Ask rather than guess" needs persistent conversations, already in place since M1, and a judge whose output is already trustworthy (M5, M6). This milestone closes the loop the pre-M7 rule left open.

### Scope
- Persistent Judge sessions tied to conversations; at most one active session per conversation; known and missing game-state facts; targeted clarification questions.
- Bounded clarification rounds (`JUDGE_MAX_CLARIFICATIONS`, default 3). Force-ask conversions (M6) now perform full session bookkeeping instead of the pre-M7 shortcut.
- Continuation: `ContinuationDecision{kind: answer|new_question|unrelated}`. `unrelated` covers anything else, including card, combo, or general requests: it is answered normally through the router and the session is left `active`, unchanged, so a later message that answers the outstanding clarification still continues it. `new_question` is a new rules or legality question: the old session is set to `abandoned` and a new judge flow starts. A not-confident classification asks a short clarifying question as a normal assistant answer, without consuming a round or touching session state. The session ends only on completion (`completed`), round-cap exhaustion (`exhausted`), a `new_question` (`abandoned`), or the user's explicit "End session" control (`abandoned`) — never by matching plain text against a phrase list.
- Session controls: a web "Judge session" indicator with "End session" and "New chat", plus a stop-generation control while streaming. Controls send explicit actions, not text.
- Clarification display and answering in the web UI.
- The Judge-mode evaluation suite.

### Depends on
M6.

### Demonstrable increment
In the browser chat: an ambiguous rules question → identify missing information → ask a targeted question → the user answers → resume reasoning → cited ruling. Insufficient after bounded rounds → `insufficient_information`. A mid-session card lookup is answered normally without derailing the judge session — the session stays active and a later answer to the clarification still completes it.

### Success evidence
- Judge-mode cases meet the agreed success target.
- Sessions survive reload and resume correctly.
- At most one session is active per conversation, and `JUDGE_MAX_CLARIFICATIONS` is never exceeded.
- The explicit "End session" control ends the session with no continuation call.
- A mid-session topic change is classified `new_question` or `unrelated` correctly, never silently folded into the wrong flow, and an `unrelated` classification never changes session state.

### Out of scope
Full game-state modelling, rules simulation; CLI/MCP session-control parity (S1).

---

## Checkpoint C — Core product

After M7, the complete primary journey works end to end in the browser:

```text
search/identify card
→ discover known combo
→ ask why it works
→ receive a cited rules explanation
→ ask explicitly whether it's legal (e.g. "Is this legal in Commander?")
→ receive a cited ruling
→ ask an ambiguous follow-up
→ clarification workflow
→ final ruling
```

Mana Leak should now feel like the intended product.

**Checkpoint rule:** if M7 needed its PRD plan re-planned once already, apply the minimum acceptable increment to M8–M9.

---

## M8 — Safeguards & limits

### Outcome
Mana Leak's execution is hardened against adversarial and runaway input, consistently across every route.

### Why this milestone exists
Safeguards matter most once every route exists (M7) and can be driven adversarially through the browser chat; hardening them now means the gates in M9 measure an already-hardened system.

### Scope
- Input validation and safeguard screening, with code-owned consequences rather than model-decided refusals.
- Adversarial hardening of the per-turn execution limits already enforced since M1 (model-call cap, turn timeout) and M3 (tool-call cap): consistent structured failures instead of raw errors, verified across every route.
- Audit coverage completed and adversarially verified: `limit_reached` (M1/M3), `dependency_degraded` (M4/M5), `citation_validation_failed` (M6), and `judge_transition` (M7) all land reliably in `audit_event`.
- Adversarial test cases, including the critical hard-gate cases.

### Depends on
M7.

### Demonstrable increment
An adversarial input (prompt injection, a secret request) is refused with a clear, structured response in the browser chat; a runaway turn is stopped by its limit with a controlled outcome.

### Success evidence
- Obvious prompt-injection and secret-request inputs are refused on every route.
- Limits stop runaway turns with controlled outcomes.
- Critical adversarial cases pass.
- Audit events are recorded for every screening, limit, and tool outcome.

### Out of scope
Evaluation reporting and gates (M9), new capabilities.

---

## Checkpoint D — Hardened candidate

After M8, every required capability exists through the browser chat and its execution is hardened against adversarial and runaway input. No new required capability is introduced after this point; remaining work is measurement and demo polish.

**Checkpoint rule:** if M8 needed its PRD plan re-planned once already — and so threatens M10's rehearsal time — apply M9's minimum acceptable increment first; M10 is never skipped.

---

## M9 — Evaluation

### Outcome
Mana Leak's important behaviours are measured and regression-gated across all suites, rather than judged only by demos.

### Why this milestone exists
The full behaviour set only exists once every capability (M3–M7) and safeguard (M8) ships. This milestone completes the remaining suites and applies the project's gates.

### Scope
- Deterministic selection and freezing of MTG-QA development and held-out sets, and current-rules gold cases.
- Completion of the retrieval, routing/tool, and Judge-mode suites.
- The smoke suite (28 cases: 10 mtg_qa, 5 current_rules, 5 routing_tool, 5 adversarial, 3 judge_mode).
- Deterministic and model-based grading, with versioned run records.
- The project's threshold and blocker gating (`contracts.md` → Evaluation contracts → Gates).
- Handling of stale historical cases without changing expected answers.
- CLI access to run evaluations (`eval smoke`, `eval run`).

### Depends on
M8.

### Demonstrable increment
A passing smoke run and measured results for routing, retrieval, rules correctness, citations, Judge behaviour, and safeguards — every case exercises a capability already reachable through the browser chat.

### Success evidence
- The smoke suite passes its gates with zero unhandled exceptions.
- Development-set and gold-set results meet the agreed thresholds.
- Critical adversarial cases pass at 100%; `citation_valid` is 100%; `citation_relevant` meets its gate on current_rules/judge_mode cases.
- The held-out set is kept out of development runs and used only for final evaluation.

### Out of scope
Running the full historical corpus, chasing marginal historical-QA gains once gates pass.

---

## M10 — Demo hardening

### Outcome
Mana Leak is a repeatable, reliable buildathon demonstration.

### Why this milestone exists
Integration defects surface last. A protected final pass makes the complete demo dependable from a clean start, regardless of how the earlier milestones went.

### Scope
- Clean local startup of the full Compose stack from a fresh checkout with a populated environment file.
- A reliable data bootstrap path that does not depend on downloads at startup.
- Verification of fallback paths (combos offline, keyword-only retrieval, Langfuse down).
- Final smoke and held-out evaluation; rehearsal of the primary demo in the browser chat (and across CLI/MCP too, if S1 has shipped).
- Secret and configuration review; removal of demo-breaking defects; README quick-start completion.

### Depends on
M9.

### Demonstrable increment
The complete agreed demo runs from beginning to end in the browser, without manual repair.

### Success evidence
- Two consecutive clean-start demo rehearsals succeed.
- Final gates pass, and the held-out result is within the agreed margin of the development result.
- No secrets appear in the repository, traces, logs, or errors.

### Out of scope
New product features.

---

## Roadmap and PRDs

Each milestone becomes one PRD, unless a milestone is clearly too large for one reviewable unit. In that case it may be split into a small number of tightly related PRDs that together deliver the milestone's increment.

A PRD expands the milestone's Outcome, Scope, Depends on, Demonstrable increment, Success evidence, and Out of scope into:

```text
problem/context
requirements (functional and non-functional)
acceptance criteria
interfaces affected
test/evaluation requirements
constraints
risks/fallbacks
```

A PRD must stay within its milestone's scope and the settled design documents. If it finds a design gap, the gap is reported and the relevant design document is updated deliberately, not redesigned inside the PRD.

After a PRD is reviewed and approved, a PRD plan derives work packages, components, technical sequence, tests, and tasks, and tracks their progress. Coding does not start directly from a roadmap milestone.

Location: `docs/prds/<milestone>-<slug>.md` for the PRD and `docs/prds/<milestone>-<slug>.plan.md` for its PRD plan (e.g. `docs/prds/M1-walking-skeleton.md`). A split milestone uses one pair per PRD (`M6a-…`, `M6b-…`).

## Execution model

```text
select next roadmap milestone
→ derive PRD
→ review PRD
→ derive PRD plan
→ execute PRD plan
→ validate milestone against its success evidence
→ update roadmap status
→ select next milestone
```

A milestone is `Complete` only when its demonstrable increment works and its success evidence holds. Unresolved demo-threatening blockers in the current milestone take priority over starting the next one.

## Minimum acceptable increment

If a checkpoint rule above is invoked, stop building the full version of the remaining milestones and build only the minimum acceptable increment below, in order, protecting M10.

- **M7** — One clarification round only (same session semantics; set `JUDGE_MAX_CLARIFICATIONS=1` instead of the default 3).
- **M8** — The critical hard-gate adversarial cases only; skip broader adversarial expansion.
- **M9** — The smoke suite plus the critical adversarial cases only, with reduced case counts elsewhere; skip the remaining development/held-out volume.

Record any minimum acceptable increment invoked in "Decisions & deviations" below.

## Cut strategy

If time runs short, cut in this order:

1. voice;
2. richer card-search functionality;
3. UI polish;
4. optional visual or deck analytics;
5. retrieval or reranking sophistication beyond required quality;
6. automated source-refresh convenience;
7. evaluation volume beyond the minimum useful gates.

These outcomes are not optional: card capability, Commander Spellbook combos, current rules retrieval and cited explanations, the structured evidence-grounded legality judge, citations, `insufficient_information` behaviour, stateful clarification, unified routing and tool boundaries, persistent streamed conversation, the browser experience, core evaluations and safeguards, Langfuse, and operational CLI (ingestion, evals).

When a required capability threatens the schedule, simplify its implementation rather than removing it.

## S1 — Agent interfaces (CLI + MCP)

### Outcome
A user/agent CLI and an MCP server reach parity with the web chat: thin adapters over `process_turn` and the domain services, with no duplicated domain logic.

### Why this milestone exists
Every required capability ships through the browser chat by M10 (see "Roadmap completion"); CLI and MCP are agent-facing conveniences — and a course requirement — layered on the same core afterward, not gates on the browser demo.

### Scope
- A complete CLI surface: `cards`, `combos`, `rules`, `judge`, and a `chat` command with `/cancel`, `/new <question>`, `/help` session controls, structured JSON output.
- The FastMCP server exposing six read tools (`search_cards`, `get_card`, `search_combos`, `find_combos`, `get_combo`, `search_rules`) plus `judge`, with an optional `action: "answer" | "new_question" | "end_session"` argument.
- CLI `judge` and MCP `judge` both call the shared turn path (`process_turn`, route forced to `judge`). An active session's continuation decision is checked first, before any forced route, so a clarification answer is never misread as a new question.
- Consistent errors and exit behaviour across interfaces; verification that no interface duplicates domain logic.

### Depends on
M4, M7.

### Demonstrable increment
The same card, combo, rules-explanation, rule-text, and ruling capabilities — including a full clarification round — demonstrated through the CLI and an MCP client, matching what already works in the browser chat.

### Success evidence
- Equivalent inputs give equivalent results across web, CLI, and MCP.
- MCP clients can find combos and obtain cited rulings and explanations.
- A CLI clarification answer continues the same session rather than opening a new one.
- Administrative operations are not exposed over MCP.

### Out of scope
New capabilities, remote MCP transport, authentication.

## Optional stretch roadmap

Stretch work starts only after M10's demo is reliable, and in this order:

1. **S1 — Agent interfaces (CLI + MCP)** (see above) — the course-required agent surface;
2. speech-to-text (preferred first voice stretch goal);
3. browser microphone input;
4. text-to-speech;
5. richer realtime voice interaction;
6. richer card-search and UI improvements.

## Roadmap completion

The roadmap is complete when Mana Leak demonstrates the product vision end to end in the browser:

```text
card
→ combo
→ rules explanation
→ current evidence
→ cited ruling
→ ambiguous state
→ clarification
→ final ruling
```

and also has persistent streamed web conversation from the first milestone, bounded routing and tool use, Langfuse observability, and measured evaluation and safeguard evidence, running reliably from a clean local start. Agent interfaces (CLI + MCP, S1) are optional stretch work layered on top once this is reliable.

## Decisions & deviations

Amendment rule: when implementation evidence contradicts a design document, record the deviation here first, then update the owning document deliberately (per the authority tie-break in `AGENTS.md` §3), then update any downstream documents that assumed the old behaviour.

### Round 1 — documentation repair pass

Historical record; items 4 and 10 are superseded by Round 2 below.

1. Judge tools: code orchestrates all evidence lookups; the judge route exposes no tools to the model.
2. Judge evidence: deterministic extraction of cards/rules from the current message and recent results; a bounded second pass fetches any rule numbers the draft cites but the ledger lacks, then redrafts once.
3. Conditional rulings: a `conditional` draft with non-empty assumptions and clarification rounds remaining converts to `NeedMoreInformation`; `conditional` is otherwise allowed only with no assumptions or when rounds are exhausted.
4. Continuation: an active Judge session skips the router; deterministic cancel phrases abandon without a model call; otherwise one bounded `ContinuationDecision` call resolves answer/new_question/abandon. CLI and MCP `judge` both go through `process_turn` with route forced to `judge`.
5. Web topology: a Next.js route handler proxies `/api/*` to `API_BASE_URL` server-side with SSE streaming passthrough; no CORS on FastAPI.
6. Streaming: Mana Leak keeps its own SSE format; no AI SDK protocol compatibility is claimed.
7. Empty combos: results carry `source: live|cache|fixture`; a live miss with no cache/fixture coverage returns `dependency_unavailable`, never an empty list.
8. Budgets: model-call cap 8 per turn (hard), 3 soft target; turn timeout 120 s (full limit list in `contracts.md`).
9. Smoke suite: 28 cases (10 mtg_qa + 5 current_rules + 5 routing_tool + 5 adversarial + 3 judge_mode); `citation_valid` gate 100%, new `citation_relevant` gate ≥90%; full gate table lives in `contracts.md` → Evaluation contracts → Gates.
10. Schedule: hour budget per milestone and the checkpoint/Emergency minimum rules above; Langfuse sequencing fixed to M10 (not an earlier "next infrastructure task").

### Round 2 — roadmap reorganisation and consistency repair

Historical record using round-2 numbering (M8 CLI + MCP, M9–M11); Round 3 below moved CLI + MCP to stretch S1 and renumbered to M1–M10, and replaced `leave_session` with `unrelated`.

1. Roadmap reorganised (R2-1): a walking skeleton — persistence, streaming, the model gateway, the browser chat, and a health endpoint — ships first as M1; Observability moves to M2; Cards, Combos, Rules explanations, Structured rulings, and Stateful Judge become M3–M7 in that order; CLI + MCP (M8), Safeguards & limits (M9), Evaluation (M10), and Demo hardening (M11) close the sequence. No hour budgets and no time-triggered checkpoints; every milestone ships through the browser chat and depends only on earlier milestones. This also retires the old M4/M5 scoping conflict, where the judge and "ask naturally" increments could not be reached without the conversation persistence and `process_turn` entrypoint that used to belong to a later milestone.
2. Turn orchestration order (R2-2): an active session's continuation decision is always checked before any `forced_route`; CLI/MCP `judge` results are `Ruling | NeedMoreInformation | RulesExplanation | RuleText`.
3. Identified vs involved cards (R2-3): identified cards = cards named in the message ∪ the last assistant result's cards ∪ the active session's `card_oracle_ids`, capped at 10; involved (citable) cards = identified cards the draft actually names; card citations don't count toward the 12-citation cap.
4. Force-ask (R2-4) applies to any non-`insufficient_information` status with non-empty `assumptions` while clarification rounds remain, not just `conditional`.
5. Session controls (R2-5): web gets a "Judge session" indicator with "End session"/"New chat" and a stop-generation control; CLI gets `/cancel`, `/new <question>`, `/help`; MCP `judge` gets an optional `action` argument. `ContinuationDecision.kind` is renamed `abandon` → `leave_session` (covers non-rules follow-ups, which route normally); the `judge_session.status` value `abandoned` is unchanged.
6. Rules answers (R2-6): rules/interaction questions default to a cited `RulesExplanation` (can walk through a multi-step board state); exact rule-number requests return `RuleText` (deterministic, verbatim, no paraphrase); legality questions return `Ruling`. `RulesExplanation` persists in `ruling` with a new `kind` column; `RuleText` is message-payload only. Sources are the Comprehensive Rules only; tournament-policy (MTR/IPG) questions are flagged, not ruled on.
7. Pre-M7 rule: until M7 ships sessions, insufficient evidence or state — including force-ask conversions — returns `insufficient_information` listing the missing facts, with no session row; M7 replaces this with real clarification and gives force-ask the same session bookkeeping as a plain insufficiency.
8. The clarification-round cap is expressed once as `JUDGE_MAX_CLARIFICATIONS` (default 3) so the minimum acceptable increment can set it to 1 without editing the contracts; the M8 minimum keeps all six MCP read tools and never cuts `ingest`/`eval smoke`/CLI `judge` (the old "four read tools" wording was never accurate — the contract has always defined six).
9. Checkpoint D (after M8) was added to protect M11's rehearsal: if M9 or M10's full scope is at risk, their minimum acceptable increment applies first, rather than relying on an hour-based trigger that no longer exists. The Langfuse Cloud emergency fallback is dropped from the minimum-acceptable-increment list now that Observability ships at M2, not at the end of the build.

### Development model choice

`CHAT_MODEL=openrouter/deepseek/deepseek-v4-flash` (router and grader default to it) is the development model, chosen for cost: OpenRouter lists it at $0.042 in / $0.084 out per million tokens, against $0.016 / $0.396 for `deepseek-v4.1-flash`. Output-heavy chat makes v4-flash cheaper. Both list `tools` and `structured_outputs` support. Model quality is compared in M9 (Evaluation); this is not a final choice.

### Round 3 — M5/M6 citation boundary, Langfuse exit criterion, ChatGPT-like continuation, and CLI/MCP as a stretch milestone

1. M5/M6 boundary (R3-1): M5 (Rules explanations) now owns the evidence ledger, citation resolution, citation validation (`citation_valid`), and the bounded second pass; its pre-M7 insufficiency outcome is a `RulesExplanation` with empty citations and non-empty `missing_information`, persisted in `ruling`. M6 (Structured rulings) adds the legality `Ruling` kind (`legal|illegal|conditional|insufficient_information`), ruling-specific validation, and force-ask on top of M5's shared machinery. M5 now depends on M4 for combo evidence. The eval harness's `eval_case`/`eval_run` tables move to M5's migration (the harness starts there); `audit_event` moves to M1's migration, with emission wired in from M1. Per-turn limits are enforced where they are needed, not introduced in one place: the model-call cap and turn timeout ship with the M1 gateway, the tool-call cap and truncation ship with M3's tool loop, retrieval caps ship with M5, and M8 (Safeguards) hardens and adversarially verifies all of them rather than introducing them.
2. Langfuse, self-hosted only (R3-2): the Langfuse Cloud fallback is removed everywhere. M2's exit criterion is a chat turn appearing as a trace under its conversation's session in local self-hosted Langfuse, with keys provisioned headlessly via Langfuse's `LANGFUSE_INIT_*` env vars so the app's own keys match with no UI step, and the app still answering when Langfuse is down. If the exit criterion isn't met on the first attempt, M2 is marked `Blocked` and retried at Checkpoint D rather than blocking M3.
3. `other` route (R3-3): M1 ships a plain, general model answer on `other` (walking-skeleton behaviour, no scope-steering yet); from M3 onward, once routing exists, `other` narrows to the contracts' fixed short scope-steering answer with no tools.
4. Chat behaves like ChatGPT/Claude (R3-4): an active Judge session is never silently abandoned or paused by an unrelated message — it stays `active`, and a later message answering the outstanding clarification still continues it. `ContinuationDecision.kind`'s `leave_session` is renamed `unrelated`. A session ends only on completion (`completed`), round-cap exhaustion (`exhausted`), a `new_question` (`abandoned`), or the user's explicit "End session"/`/cancel` control (`abandoned`). The not-confident outcome is a normal assistant answer (`TurnResult` `None`/plain text), not a typed result.
5. CLI + MCP become stretch milestone S1 (R3-5): old M8 (CLI + MCP) is removed from the core roadmap; Safeguards & limits, Evaluation, and Demo hardening move down to M8, M9, M10 (core = M1–M10). The operational CLI needed to build the product stays in the milestone that needs it (`ingest cards` M3, `ingest combo-fixtures` M4, `ingest rules` M5, `eval smoke`/`eval run` M9) — no other CLI command ships in a core milestone. **S1 — Agent interfaces (CLI + MCP)** is added after M10, first in the optional stretch roadmap: the user/agent CLI and the FastMCP server, as thin adapters over `process_turn`. The cut-strategy "not optional" list now reads "operational CLI (ingestion, evals)" in place of "CLI, MCP". Course-requirement risk: if the buildathon's rubric requires a working CLI/MCP submission, that is S1, and S1 is not guaranteed to ship within the core schedule — treat it as a near-term stretch goal, not an assumed deliverable.

Design review gate closed after round 3 with remaining MINOR findings logged as PRD inputs below.

**Open PRD inputs** (unresolved MINOR findings from the round-3 report `mana-leak-review-r3.md`; for the owning PRD to settle):
- M5 — DOC-205: decide whether the retrieval evaluation suite exercises the judge's internal composed query, the public `search_rules` path, or both; the two retrieve differently.
- M7 — DOC-213: specify the event/persistence shape of an explicit "End session" turn with no user content (does it persist an assistant message? does `Final` carry session status for the UI indicator?).
- S1 — DOC-211: on CLI/MCP, `end_session` currently behaves identically to `new_question` (both require and act on `question`); make `question` optional for `end_session`, and decide whether REST needs an explicit `answer` action for CLI/MCP parity.
