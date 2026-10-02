# PRD — M3: Cards

## Status

Proposed

## Roadmap source

- Milestone: M3 — Cards (`docs/ROADMAP.md`)
- Roadmap outcome: "Mana Leak can deterministically search, look up, and inspect current Magic card data, and answer card questions through the router in the browser chat."
- Depends on: M1 (Walking skeleton)
- Demonstrable increment: "Ask about cards in the browser chat — find and inspect real current cards, including multi-face cards."
- Open PRD inputs resolved here: M2-08, M2-09, M2-10, M2-11 (`docs/ROADMAP.md` → Decisions & deviations → Round 3 → Open PRD inputs)

## 1. Problem / context

M1 proved the walking skeleton and M2 made it observable, but every turn still resolves to one hardcoded path. `_run_turn` builds its `TraceContext` with `route=Route.other` unconditionally and never calls a router (`packages/core/src/mana_leak_core/orchestrator.py`); `TurnResult` is still M1/M2's placeholder, `type(None)` (`contracts/events.py`); the `card` table does not exist (`db/models.py`'s module docstring: "Every other table in `data-model.md` arrives with the milestone that needs it" — `card` is M3's); and although the model gateway's `complete()` already accepts a `tools` parameter and plumbs it into the LiteLLM call when given one (`gateway.py` → `_build_kwargs`), no caller has ever passed it — M1/M2's one `other`-route call is the only call either milestone makes. Cards are the base evidence every later capability needs — combos are made of cards, rulings are about cards (`docs/ROADMAP.md` → M3 → Why this milestone exists) — and this is also the first milestone to exercise the router and the bounded tool-loop pattern every later route (`combos`, and `judge`'s own code-orchestrated evidence gathering) reuses (`docs/architecture.md` → Core components → Router, Tool runner).

Introducing that first router call and tool loop exposes three gaps M2's own milestone review already found and logged against M3 (`docs/ROADMAP.md` → Decisions & deviations → Round 3 → Open PRD inputs): the turn-level Langfuse trace currently fixes its route before the trace opens, but once a router exists the route is not known until after its own model call — which still has to nest under that same trace (M2-08); every model-call generation is named only by the calling model, which stops being distinguishing once one turn can contain a router decision, several tool calls, and a final answer (M2-09); a tool-calling response's generation is recorded with an effectively empty output because `ModelResponse`/`ModelChunk` carry no `tool_calls` field today (M2-10); and the tool loop must keep every `complete()` call sequential inside the turn's single SSE producer task, parallelising only the tool calls' own I/O, continuing the task-affinity invariant `gateway.py`'s per-turn budget contextvar and `tracing.py`'s ambient span nesting already depend on (M2-11).

The `other` route also changes shape now that routing exists. M1 shipped it as a plain, general-purpose answer (`OTHER_SYSTEM_PROMPT`: "Answer the user briefly and helpfully... steer the user toward asking about a specific card, a combo, or a rules interaction") because it was the only route there was. `docs/ROADMAP.md` → Decisions & deviations → Round 3 → R3-3 records that from M3 onward `other` narrows to the contracts' fixed short scope-steering answer, with no tools — and the orchestrator's own existing code comment already quotes that target behaviour ("a single short model answer steering the user back to cards/combos/rules questions, with no tools", `contracts.md` → Turn orchestration step 6) even though the M1 prompt string still tries to help with the off-topic content itself.

Finally, the browser has nothing to show for any of this yet: `ChatView.tsx`'s `final`-event handling reads only `text`/`error` and never `result`, and its history-reload path (`mergeHistory`) copies only `content`, never `payload` — both will carry structured data for the first time once `TurnResult` has a real variant.

## 2. Outcome

A user can ask about Magic cards in the browser chat and get back real, current Oracle card data — found by exact name, a specific card face, or a deterministic fuzzy match, or searched by type, colour identity, Commander legality, mana value, keyword, or text — grounded in locally ingested Scryfall data rather than the model's memory, with every card carrying its source and import version. An off-topic message instead gets a short, fixed reply steering the player back to cards, combos, or rules questions, never a general-purpose answer. Reaching this from a fresh checkout starts with an empty `card` table: an operator runs `mana-leak ingest cards` once before the chat can answer anything about cards. Consistent with the roadmap outcome above.

## 3. Scope

### In scope

- Ingestion of Scryfall-derived Oracle card data into the new `card` table (`data-model.md` → `card`), via the `ingest_cards()` core function and its first CLI surface, `mana-leak ingest cards [--file PATH]` (`contracts.md` → CLI; Core service interfaces → ingestion), fetching Scryfall's live bulk-data listing when no local file is given.
- Provenance and source-version awareness on every ingested and served card record (`Provenance`; `data-model.md` → Provenance fields), and an `IngestReport` for every ingestion run.
- Structured card search over the complete filter set `CardSearchRequest` defines, and exact/face-name/fuzzy card lookup (`CardLookupResult`), with ambiguity reported explicitly rather than guessed (`contracts.md` → Card contracts).
- Direct REST access to both (`POST /cards/search`, `GET /cards/{identifier}`), alongside the chat-facing `search_cards`/`get_card` LLM tools — one shared core, several adapters.
- Intent routing between `cards` and `other` (`RouteDecision`, one bounded model call), and the bounded tool loop for the `cards` route: allowlist enforcement, the 5-call tool cap and its final-answer reservation, and truncation of oversized tool output (`contracts.md` → Router contract, Tool contracts, Operational limits).
- `other` narrowing from M1's plain general answer to the contracts' fixed short scope-steering reply, with no tools (`docs/ROADMAP.md` → Decisions & deviations → R3-3).
- The browser rendering a `cards`-route turn's structured `CardsResult` (`contracts.md` → Turn orchestration) alongside its streamed text, live and after a reload.
- Resolving M2-08 through M2-11: the turn trace's route is decided after it opens, model-call/tool-call spans become distinguishable, tool-calling generations record their proposed tool calls, and `complete()` calls stay sequential while tool I/O may run concurrently.
- Rebuilding model context to include previously persisted tool messages, which M1's context assembly explicitly skips today because none existed yet (`orchestrator.py` → `_model_messages`).
- `GET /health`'s `card_source_version` field reporting the real last-ingested version once ingestion has run, replacing M1/M2's hardcoded `null`.

### Out of scope

- Combos, rules retrieval, judging, and any routing beyond `cards`/`other` (`docs/ROADMAP.md` → M3 → Out of scope) — the `Route` enum's `combos`/`judge` members stay unimplemented until M4/M5.
- Card images: Scryfall provides them, but `data-model.md`'s `card` table never stores artwork, and none is fetched, stored, or displayed.
- Any query-language parser beyond the fixed `CardSearchRequest` filter set (`docs/vision.md` → Non-goals: "A complete implementation of the Scryfall query language"); there is no free-form Scryfall search syntax.
- Runtime Scryfall calls of any kind: ingestion is the only time this product talks to Scryfall (`contracts.md` → External adapters → Scryfall ingestion: "Ingestion only; no runtime Scryfall calls").
- The Stretch S1 user/agent CLI (`cards search`/`cards get`/…) and the MCP server — only the operational `ingest cards` command ships this milestone (`contracts.md` → CLI → Cross-interface parity).
- The eval harness, the `routing_tool`/`tool_success` gates, and `eval_case`/`eval_run` persistence — introduced at M5/M9 (`docs/ROADMAP.md` → Decisions & deviations → Round 3 → R3-1).
- Judge-session active-check in turn orchestration (M7) and model-based screening (M8): every valid message this milestone still proceeds as `clear`, exactly as in M1/M2.

## 4. Functional requirements

- **FR-1** — The system shall ingest the Scryfall Oracle Cards bulk-data file into the `card` table via `ingest_cards(bulk_path: Path | None = None) -> IngestReport`, exposed as `mana-leak ingest cards [--file PATH]`. With no `--file`, it shall fetch the `oracle_cards` entry from `GET https://api.scryfall.com/bulk-data`, download the gzipped JSONL archive at that entry's `jsonl_download_uri`, and send an explicit `User-Agent` and `Accept: application/json` header on every request to `api.scryfall.com` (`contracts.md` → External adapters → Scryfall ingestion; verified live, Risks below). With `--file`, it shall read a local bulk file instead of fetching one.
- **FR-2** — When ingestion completes successfully, the system shall upsert each imported card by `oracle_id`, excluding the non-game layouts `data-model.md`'s `card` table defines, set every row's provenance (`source="scryfall"`, `source_id` = the representative printing's Scryfall `id`, `source_version` = the bulk file's `updated_at`, `retrieved_at` = import time), delete any previously-imported card absent from the new file, and return an `IngestReport` with insert/update/delete/skip counts and duration.
- **FR-3** — When the bulk-data listing request or the archive download fails (network error, non-2xx response, or an archive that cannot be decompressed/parsed), `ingest_cards` shall raise a typed failure rather than upserting or deleting any `card` row from a partial result, and previously ingested card data shall remain exactly what `search_cards`/`get_card` serve until a later ingestion succeeds (`docs/architecture.md` → Failure and degradation: "Source refresh unavailable → Use previously ingested card and rules data").
- **FR-4** — The system shall provide `search_cards(CardSearchRequest) -> list[CardSummary]`, implementing every filter the contract defines — free-text `query` over name/type/Oracle-text; `name`/`type_text`/`oracle_text` substring filters; `color_identity` as a subset of the card's own identity (the Commander deck-building rule); `legal_commander=True` restricting results to cards whose own `legal_commander` reads `legal`; `mana_value_min`/`max`; exact, case-insensitive `keyword` — combined with AND, ordered by full-text rank when `query` is set and by name otherwise, rejecting a request with no filter set as `validation_error`; exposed at `POST /cards/search` and as the `search_cards` LLM tool on the `cards` route.
- **FR-5** — The system shall provide `get_card(CardLookupRequest) -> CardLookupResult`, resolving an identifier in order — a UUID as `oracle_id`; else an exact `name_normalized` match; else an exact match in `face_names_normalized`; else a trigram fuzzy match (similarity ≥ 0.6, a single best match needing a margin of at least 0.1 over the runner-up) — and returning `CardFound` (with its `match` kind), `CardAmbiguous` (≤5 candidates), or `CardNotFound` as an ordinary value, never raising for an absent or ambiguous name; exposed at `GET /cards/{identifier}` (always 200, the same discriminated-value behaviour) and as the `get_card` LLM tool on the `cards` route (also allowlisted for `combos` once M4 ships it).
- **FR-6** — For a multi-face card (`transform`/`modal_dfc`/`split`/`adventure`/… layouts), `Card`/`CardFound` shall expose every face's own name, mana cost, type line, Oracle text, and power/toughness/loyalty (`faces: list[CardFace]`), and a lookup by any single face's name shall resolve to the owning logical card with `match="face_name"`.
- **FR-7** — When a turn has no forced route (no Judge session exists until M7, so that branch of Turn orchestration step 5 never applies yet), the system shall classify it with one bounded `RouteDecision` model call (`ROUTER_MODEL`, prompt `router-v1`) into `cards` or `other`. This milestone's router is scoped to those two outcomes only; a route value that fails schema validation after its one retry, or that names a route not yet implemented (`combos`/`judge`), shall default to `other` the same way (`contracts.md` → Router contract: "If the output is invalid after 1 retry, the route defaults to `other`"; the conservative reasoning for the second case is recorded under Constraints, below).
- **FR-8** — For the `cards` route, the system shall run a bounded tool-calling loop exposing exactly `search_cards` and `get_card` to the model, validate every call's arguments against its request model, execute the matching core function, return a `ToolResult` envelope — never a raw exception — to the model, emit `tool_start`/`tool_end` SSE events for every call, and persist a `tool`-role message per call storing its validated arguments and the returned records' IDs (`data-model.md` → `message.payload`: `{tool, args, ok, result_ids, error}`).
- **FR-9** — When the model's tool call names a tool outside the route's allowlist, or an unknown tool, the system shall not execute it, shall return `ToolResult(ok=False, error=ToolError(code="tool_not_allowed"))` to the model, and shall record a `tool_rejected` audit event, continuing the loop within its remaining budget.
- **FR-10** — The system shall cap the `cards` tool loop at 5 executed tool calls per turn; a further attempted call shall not execute, the model shall receive `ToolResult(ok=False, error=ToolError(code="tool_limit_exceeded"))`, and the loop shall still spend its next call producing a final answer from whatever card evidence has already been gathered, per the same reservation rule that stops the loop a call early once only one model call remains in the turn's budget (`contracts.md` → Operational limits: "the tool loop reserves its final answer").
- **FR-11** — When a tool result's model-facing JSON would exceed 8,000 characters, the system shall cut long text fields with an ellipsis and then drop trailing list items until the representation is at or under the cap, set `ToolResult.truncated=True`, and keep the full, untruncated result backing the persisted tool message's record IDs.
- **FR-12** — When the `cards` route's tool loop produces its final answer, the system shall persist and stream a `CardsResult` (`TurnResult.kind="cards"`, carrying the `CardSummary` records the answer actually refers to) as the turn's `Final.result` and the persisted assistant message's `payload`, replacing the `TurnResult = type(None)` placeholder M1/M2 still use.
- **FR-13** — When the router selects `other` (directly, or by the FR-7 default), the system shall produce one short, fixed-intent, tool-free model answer that steers the user toward card, combo, or rules questions, and shall not attempt to answer the off-topic content itself — replacing M1's general-purpose `other` answer.
- **FR-14** — The browser chat shall render a `cards`-route turn's `CardsResult` — the `CardSummary` fields the contract defines (name, mana cost, mana value, type line, colour identity, Commander legality) — alongside its streamed text, both for a turn just completed and, after a reload, from `GET /conversations/{id}`'s persisted `payload` (`contracts.md` → Conversation behaviour: "The client renders `payload` for structured results"), replacing `ChatView.tsx`'s current behaviour of reading only a `final` event's `text`/`error` and never its `result`.
- **FR-15** — `GET /health`'s `card_source_version` shall report the most recently completed ingestion's `source_version` once `ingest cards` has run at least once, replacing M1/M2's hardcoded `null`; it shall remain `null` before any ingestion.
- **FR-16** — The system shall add the `mana-leak` console script (`[project.scripts] mana-leak = "mana_leak_api.cli:app"`, built with Typer), following the CLI's documented conventions (plain text by default, global `--json`, `--help` on every command, the documented exit-code table), with `ingest cards` as its first command.
- **FR-17** — Once a conversation contains persisted `tool`-role messages, the system shall include them when rebuilding the model-facing context for a later turn in that conversation, replacing M1's explicit skip ("No tool messages exist in M1; empty assistant rows come from a turn that was aborted before any text arrived" — `orchestrator.py` → `_model_messages`), so a resumed or continued conversation lets the model see what was already looked up (`data-model.md` → `message`: tool messages are persisted "so that a resumed conversation can rebuild the last 10 turns with what the model actually saw").

## 5. Non-functional requirements

- **NFR-1** — Bounded execution / degradation: the `cards` tool loop never exceeds 5 executed tool calls or lets a single tool result exceed the model-facing 8,000-character cap (FR-10, FR-11); a tool call's own execution failure (e.g. a transient database error) is returned to the model as a structured `ToolResult` with a `tool_failed` audit event, never an unhandled exception that crashes the turn (`docs/architecture.md` → Failure and degradation: "Tool error → Returned to the model as a structured tool result; the loop continues within limits"); and an ingestion failure never partially clears or corrupts the `card` table (FR-3).
- **NFR-2** — Provenance: every `Card`/`CardSummary`/`CardFound`/`CardAmbiguous` candidate the system serves — through `search_cards`, `get_card`, either REST endpoint, or either LLM tool — carries its `Provenance`/`source_version`, never a bare record with no source (`data-model.md` → Provenance fields: "Importers must fail rather than write external data without `source`, `source_version`, and `retrieved_at`").
- **NFR-3** — Determinism: `search_cards`/`get_card`'s matching, ranking, and resolution order are ordinary deterministic code paths — an identical request returns identical results — and no model generates, infers, or rewrites card data (`docs/architecture.md` → Card service: "No model generates or rewrites card data, and card search does not use embeddings"; Deterministic versus model-owned).
- **NFR-4** — Observability (M2-08): the system shall open the turn-level Langfuse trace before the route is known — `TraceContext.route` starts `None` — and update the open trace's recorded route once the router decision completes, so the router's own model-call generation still nests as the trace's child despite the route being undecided when the trace opened (`docs/ROADMAP.md` → Open PRD inputs → M3 — M2-08).
- **NFR-5** — Observability (M2-09): the system shall give each model-call generation and each tool-call span within a turn's trace a name that distinguishes it — the router decision, each individual tool call, the final answer — rather than every child observation sharing only the calling model's name (`docs/ROADMAP.md` → Open PRD inputs → M3 — M2-09).
- **NFR-6** — Observability (M2-10): the model gateway shall carry the model's proposed tool calls on `ModelResponse`/`ModelChunk`, and a tool-calling call's recorded generation output shall include both the answer content and the proposed tool calls, rather than today's empty/text-only output (`docs/ROADMAP.md` → Open PRD inputs → M3 — M2-10).
- **NFR-7** — Observability (M2-11): within one turn, every `complete()` call shall still run sequentially inside the turn's single SSE producer task — preserving the per-turn model-call budget and the trace's ambient nesting, both of which `gateway.py`/`tracing.py` already document as depending on one task driving the whole turn — and only the non-model I/O of executing several tool calls the model proposed in one response may run concurrently (`docs/ROADMAP.md` → Open PRD inputs → M3 — M2-11).

## 6. Interfaces and contracts affected

| Surface | Reference | Change |
|---|---|---|
| `card` table | `data-model.md` → `card` | first introduced |
| `Card`, `CardFace`, `CardSummary` | `contracts.md` → Card contracts | first introduced |
| `Provenance` | `contracts.md` → Provenance | first populated (type existed, unused until now) |
| `CardSearchRequest` / `search_cards` | `contracts.md` → Card contracts → Search; Core service interfaces | first introduced |
| `CardLookupRequest` / `CardLookupResult` (`CardFound`/`CardNotFound`/`CardAmbiguous`) / `get_card` | `contracts.md` → Card contracts → Lookup; Core service interfaces | first introduced |
| `POST /cards/search`, `GET /cards/{identifier}` | `contracts.md` → REST API | first introduced |
| `IngestReport` / `ingest_cards()` | `contracts.md` → Core service interfaces (ingestion) | first introduced |
| `mana-leak` CLI, `ingest cards` | `contracts.md` → CLI | first introduced |
| `RouteDecision` / Router contract | `contracts.md` → Router contract | first introduced (`cards`/`other` only; `combos`/`judge` rows unimplemented until M4/M5) |
| `ToolResult`/`ToolError` envelope; `search_cards`/`get_card` LLM tool rows | `contracts.md` → Tool contracts | first introduced |
| `TurnEvent.ToolStart`/`ToolEnd` | `contracts.md` → Streaming events | implements (type defined since M1, first real emission) |
| `TurnResult` / `CardsResult` | `contracts.md` → Turn orchestration | first introduced (replaces the `type(None)` placeholder) |
| `ErrorCode.tool_not_allowed` / `tool_limit_exceeded` / `structured_output_invalid` | `contracts.md` → Error taxonomy | first introduced (code-enum extension) |
| Web chat rendering of `CardsResult` | `contracts.md` → Conversation behaviour ("the client renders `payload`"); `architecture.md` → web responsibility | first introduced |
| `GET /health` → `card_source_version` | `contracts.md` → REST API → `HealthResponse` | implements (hardcoded `null` stub replaced) |
| `TraceContext.route` | `contracts.md` → External adapters → Langfuse | implements (M2-08: becomes undecided-until-routed) |
| Tracing span-naming; tool-call generation output | `contracts.md` → External adapters → Langfuse; Model gateway | first introduced (M2-09 / M2-10) |
| Model-context rebuild (`build_context` / model-message assembly) | `contracts.md` → Core service interfaces (`build_context`) | implements (tool messages no longer skipped) |

No required behaviour in this milestone conflicts with a settled contract; the one place this PRD extends a contract's own documented fallback (an unimplemented route defaulting to `other`, FR-7) is recorded as a conservative assumption under Constraints, not a redefinition.

## 7. Acceptance criteria

- **AC-1** — Given a fresh checkout with no ingested cards, when an operator runs `mana-leak ingest cards` (fetching Scryfall's live bulk data, or `--file` pointing at a downloaded archive), then the `card` table is populated from the archive, non-game layouts are excluded, every row carries `source="scryfall"`/`source_version`/`retrieved_at`, and the command prints an `IngestReport` with a non-zero `inserted` count and a recorded `duration_ms`.
- **AC-2 (failure/degradation)** — Given cards already ingested, when a later `mana-leak ingest cards` run cannot reach Scryfall or cannot parse the downloaded archive, then the command fails with a typed, structured error (exit code 3) instead of a stack trace, the `card` table is left exactly as it was before the attempt, and `search_cards`/`get_card` keep serving the previously ingested data unaffected.
- **AC-3** — Given the exact current name of an ingested card, when it is looked up (`get_card`/`GET /cards/{identifier}`), then it returns `CardFound` with `match="exact_name"`, the card's current Oracle text/type line/mana cost, and its `Provenance`/`source_version`.
- **AC-4** — Given the name of one face of a multi-face card (e.g. a transform or adventure card's back/secondary face), when it is looked up, then it resolves to the owning logical card with `match="face_name"`, and the returned `Card` exposes every face's own name, type line, and Oracle text.
- **AC-5 (boundary)** — Given a misspelled name close to exactly one real card, when it is looked up, then it returns `CardFound` with `match="fuzzy"` rather than being silently treated as an exact hit, so a caller can show "Did you mean …"; given a misspelling close to several cards, it returns `CardAmbiguous` instead.
- **AC-6 (boundary)** — Given a name that is ambiguous among several real cards, when it is looked up, then `CardAmbiguous` lists up to 5 explicit `CardSummary` candidates rather than guessing one.
- **AC-7 (failure)** — Given an identifier that matches no card by ID, exact name, face name, or fuzzy threshold, when it is looked up, then it returns `CardNotFound`, and no card information is invented in its place.
- **AC-8** — Given a search combining a type-line filter, a colour-identity subset, `legal_commander=True`, a mana-value range, and a keyword, when it runs (`search_cards`/`POST /cards/search`), then every returned card satisfies all of the filters (AND-combined) — including a `legal_commander` field that reads `legal` — and the set is ordered by full-text rank when `query` is given, else by name.
- **AC-9 (boundary)** — Given a `CardSearchRequest` with every filter field left unset, when it is submitted, then the call fails as `validation_error` rather than returning an unfiltered scan.
- **AC-10 (boundary)** — Given `limit` set below 1, above 10, or left at its default, when a search runs, then the result respects the contract's bounds (1–10, default 5) rather than returning an unbounded set.
- **AC-11** — Given any card record returned by `search_cards`, `get_card`, either REST endpoint, or either LLM tool, then it carries a non-null `Provenance`/`source_version`/`retrieved_at` — never a bare record with no source.
- **AC-12** — Given a message that names or clearly asks about a Magic card, when the turn is routed, then the router selects `cards` and the tool loop runs.
- **AC-13** — Given an off-topic message (no card, combo, or rules content), when the turn is routed, then the router selects `other`, and the assistant's reply is the short, fixed scope-steering message — it does not attempt to actually answer the off-topic question.
- **AC-14 (boundary/failure)** — Given the router's structured output fails schema validation after its one retry, or names a route this milestone does not implement (`combos`/`judge`), when the turn is processed, then the route defaults to `other` and the turn still answers normally, exactly as the existing invalid-output fallback already does.
- **AC-15** — Given a `cards`-route turn whose question needs a lookup, when the tool loop runs, then it calls `search_cards`/`get_card` with validated arguments, the SSE stream carries a matching `tool_start`/`tool_end` pair for each call, a `tool`-role message is persisted recording the call's arguments and the returned IDs, and the turn's `Final.result` is a `CardsResult` naming the cards the answer refers to.
- **AC-16 (boundary/failure)** — Given a tool call naming a tool outside `{search_cards, get_card}` (including a hallucinated/unknown name), when the loop processes it, then the call is not executed, the model receives a `tool_not_allowed` `ToolResult`, a `tool_rejected` audit event is recorded, and the loop continues within its remaining budget.
- **AC-17 (failure/degradation)** — Given an allowed tool call whose underlying core function itself fails unexpectedly (e.g. a transient database error), when the loop executes it, then the model receives a structured, unsuccessful `ToolResult` and a `tool_failed` audit event is recorded, the turn does not crash, and the loop continues within its remaining budget.
- **AC-18 (boundary)** — Given a `cards`-route turn whose tool loop has already executed 5 tool calls, when the model attempts a 6th, then that call is not executed, the model receives a `tool_limit_exceeded` `ToolResult`, and the turn still completes with a final answer grounded in the card evidence already gathered, rather than failing the turn or looping further.
- **AC-19 (boundary)** — Given a tool result whose model-facing JSON would exceed 8,000 characters, when it is returned to the model, then its long text fields are cut with an ellipsis and trailing list items are dropped until it is at or under the cap, `ToolResult.truncated=True`, and the persisted tool message's record IDs still reflect the full, untruncated result.
- **AC-20** — Given a conversation whose history already includes a `cards`-route turn's tool calls, when a later turn in that conversation rebuilds its model context, then the assembled messages include a representation of those earlier tool calls/results rather than skipping them, so a same-conversation follow-up about an already-looked-up card does not require re-searching from nothing.
- **AC-21** — Given a `cards`-route turn that completes with a `CardsResult`, when it is viewed in the browser chat, then the structured card fields (name, mana cost, mana value, type line, colour identity, Commander legality) are shown alongside the streamed prose, both immediately after the turn and after a page reload, via the conversation's persisted history.
- **AC-22 (boundary)** — Given a fresh checkout with no ingestion yet run, `GET /health` reports `card_source_version: null`; given `ingest cards` has completed at least once, it reports that ingestion's actual `source_version`.
- **AC-23 (observability)** — Given a `cards`-route turn, when its trace is inspected, then the turn-level trace opens before the route decision and is later shown with the decided route, and the router's own model call appears as a child generation of that same turn trace throughout.
- **AC-24 (observability)** — Given a `cards`-route turn with at least one tool call, when its trace is inspected, then the router's generation, each tool call, and the final answer are each identifiable by a distinguishing name, not only by the shared chat model name.
- **AC-25 (observability)** — Given a tool-calling model response, when its generation is recorded, then the traced output includes the proposed tool call(s), not an empty or text-only output.
- **AC-26 (observability/degradation)** — Given a model response that proposes more than one tool call at once, when the loop executes them, then the `complete()` calls in that turn still run strictly one at a time in the turn's single producer task, while the tool calls' own I/O may run concurrently.
- **AC-27 (demonstrable increment)** — A user asks about a real current card in the browser chat by name and gets back its current Oracle text with its data also shown structurally; asks about a multi-face card and gets both faces; asks an off-topic question and gets the short scope-steering reply instead of a general answer — all from a fresh checkout whose only setup step was running `mana-leak ingest cards` once.

## 8. Test and evaluation requirements

### Deterministic verification

- Ingestion success, provenance, layout exclusion, and `IngestReport` counts against a downloaded or fixture bulk file (AC-1); ingestion failure leaves existing `card` rows untouched (AC-2) — the live-Scryfall network path itself is an operational check (Risks, below).
- Lookup resolution order — exact name, face name, fuzzy, ambiguous, not-found (AC-3–AC-7).
- Search filter combinations, AND-semantics, ordering, empty-filter rejection, and `limit` bounds (AC-8–AC-10).
- Provenance present on every served record, across both core functions, both REST endpoints, and both LLM tools (AC-11).
- Router decision for a card question versus an off-topic message, and the invalid-or-unimplemented-route fallback (AC-12–AC-14).
- Tool loop: allowed-tool execution with SSE `tool_start`/`tool_end` and the persisted tool-message shape, and a `CardsResult` on `Final` (AC-15); allowlist rejection (AC-16); an allowed tool's own execution failure (AC-17); the 5-call cap and reserved final answer (AC-18); 8,000-character truncation (AC-19).
- Context rebuild including prior tool messages instead of skipping them (AC-20).
- `/health`'s `card_source_version` before and after ingestion (AC-22).
- Trace content: the turn span's route is set only after routing (AC-23); generation/span names distinguish router, tool calls, and the final answer (AC-24); a tool-calling generation's output carries `tool_calls` (AC-25); `complete()` calls stay strictly sequential in the turn's producer task even when the model proposes several tool calls at once (AC-26) — driven through `sse_stream`, the same way M2's own trace-content tests exercised the orchestrator rather than calling `process_turn` directly.
- Browser rendering of `CardsResult`, live and after reload (AC-21), and the end-to-end demonstrable increment through the browser chat (AC-27).

### Evaluation

None for this milestone — the eval harness, its `routing_tool`/`tool_success` scores, and the `eval_case`/`eval_run` tables are introduced at M5/M9 (`docs/ROADMAP.md`; `docs/contracts.md` → Evaluation contracts).

## 9. Constraints

- Operational limits (5 tool calls, 8 model calls hard / 3 soft, 8,000-character truncation, 8,000-character user-message cap, 10-turn context, 10-second external HTTP timeout, 2 transient retries) are defined once in `contracts.md` → Operational limits; this PRD does not redefine them.
- Route scope: only `cards`/`other` exist after this milestone; `combos`/`judge` remain unimplemented until M4/M5 (`docs/ROADMAP.md` → M3 → Out of scope).
- Scryfall ingestion stays CLI/offline-only; no runtime Scryfall call is ever made (`contracts.md` → External adapters → Scryfall ingestion: "Ingestion only; no runtime Scryfall calls"). `docker-compose.yml`'s existing `./data:/app/data` volume mount — already present, with a comment pre-anticipating this milestone ("Downloaded bulk card data (`ingest_cards`) ... kept on the host") — needs no change.
- No card images are ingested, stored, or displayed (`data-model.md` → `card`: "Not stored: ... artwork").
- Scryfall's data-usage guidelines (verified 2026-10-02, Risks below): no paywalling access to the data, no simply repackaging/republishing it without adding value, no implying Scryfall's endorsement. This product satisfies them by serving card data only through its own structured search/lookup and chat answer, never as a raw proxy of Scryfall's API.
- Recorded conservative assumption (`AGENTS.md` §3: "make the smallest conservative assumption and record it"): a router decision naming a not-yet-implemented route (`combos`/`judge`) is treated exactly like a structured-output validation failure and defaults to `other` (FR-7, AC-14), rather than being escalated as a product question — `contracts.md`'s own router fallback already establishes "default to `other`" as the safe behaviour for an output code cannot act on, and Source authority (`architecture.md`) places application code above any single model proposal.
- A bulk-data record that cannot be mapped to a valid `card` row is skipped and counted in `IngestReport.skipped`, rather than aborting the whole ingestion run — the contract already defines `skipped` as distinct from `inserted`/`updated`/`deleted` for exactly this purpose.
- The 24-hour-buildathon architectural constraints continue to apply: no agent framework, no new abstraction layer without a second concrete use, the tool loop stays plain code over LiteLLM (`docs/architecture.md` → Constraints).
- This milestone adds no Makefile target for ingestion; the documented access pattern is `uv run mana-leak ingest cards` on the host or `docker compose exec api mana-leak ingest cards` in Compose (`docs/architecture.md` → Local deployment).

## 10. Risks and fallbacks

| Risk | Impact | Design-supported fallback |
|---|---|---|
| Scryfall bulk-data endpoint/shape. Verified live 2026-10-02 against `GET https://api.scryfall.com/bulk-data/oracle-cards`: returns `{"object":"bulk_data","type":"oracle_cards","updated_at":"2026-10-01T21:01:57.842+00:00","uri":"https://api.scryfall.com/bulk-data/27bf3214-...","jsonl_download_uri":"https://data.scryfall.io/oracle-cards/oracle-cards-20261001210157.jsonl.gz","compressed_size":24595868}` — confirms the exact field names `contracts.md` already specifies (`jsonl_download_uri`, not `download_uri`; `updated_at`) and the gzipped-JSONL shape (`https://scryfall.com/docs/api/bulk-data`: "Each bulk file is a gzipped JSONL ... archive"). Separately verified (`https://scryfall.com/docs/api/`, "Required Headers"): all requests to `api.scryfall.com` must send a descriptive `User-Agent` and an `Accept` header, or Scryfall may block/throttle the generic defaults HTTP libraries send. | Ingest fails or is throttled, demo has no/stale card data | `docs/architecture.md` → Failure and degradation: "Source refresh unavailable → Use previously ingested card and rules data; startup never downloads sources" — ingest ahead of the demo, never at startup; re-run `ingest cards` once connectivity returns (FR-3, AC-2). |
| Scryfall's data-usage guidelines. Verified 2026-10-02 against `https://scryfall.com/docs/api/` ("Use of Scryfall Data and Images"): data is provided under the Wizards of the Coast Fan Content Policy for building additional *Magic* software; consumers may not paywall access, may not simply repackage/republish/proxy the data without adding value, and may not imply Scryfall's endorsement. No card images are stored or shown (`data-model.md` → `card`: "Not stored: ... artwork"), so the image-handling guidelines (crop/watermark/etc.) do not apply — only text/Oracle data is used. | A bare re-serving of Scryfall's own card text with no retrieval/chat value added could read as "simple repackaging" | Card data is always served through this product's own structured search/lookup and chat answer, combined with routing (and, from M4 on, combo/rules context) — never a raw proxy of Scryfall's API. Whether an explicit on-screen Scryfall/Wizards attribution notice is additionally warranted is unresolved (Open issues, below). |
| Trigram fuzzy-match threshold (`pg_trgm` similarity ≥ 0.6, margin ≥ 0.1) tuned against the full ~30k-entry Oracle Cards set, not a small sample | Misspelled names return the wrong card, or an unhelpfully long ambiguous list | `CardAmbiguous`/`CardNotFound` are explicit, inspectable value outcomes, never a silently-wrong answer (FR-5); the PRD plan's deterministic tests exercise the threshold against real ingested data before the demo, not synthetic fixtures alone. |

## 11. Dependencies

- Milestones: M1 (Walking skeleton) — `conversation`/`message`/`audit_event`, `process_turn`, the model gateway, and `/health` all already exist. M2 (Observability, Complete) — the Langfuse tracing module (`tracing.py`), the per-turn trace, and generation recording, which this PRD extends via NFR-4–NFR-7.
- Existing capabilities: the per-turn model-call budget contextvar and secret redaction (`gateway.py`); the single-producer-task SSE pattern every turn already runs inside (`sse.py`); `Settings` (`pydantic-settings`); the `Route`/`MessageRole`/`ErrorCode` enums and `TurnEvent` types already defined for forward compatibility; `OTHER_SYSTEM_PROMPT`'s M1 precedent, which this milestone replaces.
- External: the Scryfall bulk-data API (`api.scryfall.com` for the listing call, `data.scryfall.io` for the archive download) — ingestion-time only. No other new external service: Commander Spellbook arrives at M4; Langfuse is already running since M2 and needs no new configuration.

## 12. Traceability

| Requirement | Source | Acceptance criteria | Verification |
|---|---|---|---|
| FR-1 | ROADMAP M3 Scope; `contracts.md` → External adapters → Scryfall ingestion | AC-1 | deterministic test / operational check |
| FR-2 | `data-model.md` → `card`, Provenance fields; `contracts.md` → `IngestReport` | AC-1 | deterministic test |
| FR-3 | `architecture.md` → Failure and degradation | AC-2 | deterministic test |
| FR-4 | `contracts.md` → Card contracts → Search; REST API | AC-8, AC-9, AC-10, AC-11 | deterministic test |
| FR-5 | `contracts.md` → Card contracts → Lookup; REST API | AC-3, AC-5, AC-6, AC-7, AC-11 | deterministic test |
| FR-6 | `data-model.md` → `card` (`faces`); `contracts.md` → `CardFace` | AC-4, AC-27 | deterministic test |
| FR-7 | ROADMAP M3 Scope; `contracts.md` → Router contract | AC-12, AC-13, AC-14 | deterministic test |
| FR-8 | ROADMAP M3 Scope; `contracts.md` → Tool contracts | AC-15 | deterministic test |
| FR-9 | `contracts.md` → Tool contracts → Failures | AC-16 | deterministic test |
| FR-10 | `contracts.md` → Tool contracts; Operational limits | AC-18 | deterministic test |
| FR-11 | `contracts.md` → Result envelope | AC-19 | deterministic test |
| FR-12 | `contracts.md` → Turn orchestration (`CardsResult`) | AC-15, AC-21 | deterministic test |
| FR-13 | ROADMAP → Decisions & deviations R3-3 | AC-13 | deterministic test |
| FR-14 | `contracts.md` → Conversation behaviour; `architecture.md` → web | AC-21, AC-27 | deterministic test / operational check |
| FR-15 | `contracts.md` → REST API → `HealthResponse` | AC-22 | deterministic test |
| FR-16 | `contracts.md` → CLI | AC-1 | deterministic test |
| FR-17 | `data-model.md` → `message`; `contracts.md` → `build_context` | AC-20 | deterministic test |
| NFR-1 | `architecture.md` → Failure and degradation; `contracts.md` → Operational limits | AC-2, AC-17, AC-18, AC-19 | deterministic test |
| NFR-2 | `data-model.md` → Provenance fields | AC-11 | deterministic test |
| NFR-3 | `architecture.md` → Card service; Deterministic versus model-owned | AC-3, AC-8 | deterministic test |
| NFR-4 | ROADMAP → Open PRD inputs → M2-08 | AC-23 | deterministic test |
| NFR-5 | ROADMAP → Open PRD inputs → M2-09 | AC-24 | deterministic test |
| NFR-6 | ROADMAP → Open PRD inputs → M2-10 | AC-25 | deterministic test |
| NFR-7 | ROADMAP → Open PRD inputs → M2-11 | AC-26 | deterministic test |

## 13. Open issues / design gaps

- Scryfall's verified usage guidelines discourage simple repackaging and require not implying Scryfall's endorsement, and `contracts.md` already requires an explicit on-screen credit-and-link for the analogous Commander Spellbook data (`contracts.md` → External adapters → Commander Spellbook: "The web UI credits Commander Spellbook and links back to `commanderspellbook.com` ... per its usage terms"). No design document states an equivalent requirement for Scryfall card data. Whether the browser chat must also display an explicit Scryfall/Wizards-of-the-Coast attribution notice wherever card data is shown is a product decision for the user, not decided here.
- `architecture.md`'s "web" container responsibility row names only "structured judge answers (rulings, rules explanations, rule text) and citations" as the UI's structured-rendering duty; it predates `CardsResult` and does not mention tool-call progress. This PRD requires rendering `CardsResult` (FR-14), grounded in `contracts.md`'s more specific "the client renders `payload` for structured results," but leaves undecided whether the browser should also show a live progress indicator for `tool_start`/`tool_end` events (e.g. "Searching cards…") during a `cards`-route turn — a product/UX decision for the user.

## 14. Completion condition

This PRD is complete only when:

- every acceptance criterion holds;
- the required deterministic verification passes;
- the milestone's demonstrable increment works end to end: a user asks about cards — including a multi-face card — in the browser chat and sees both the current Oracle data and a short, fixed reply to an off-topic question, from a fresh checkout whose only setup step was `mana-leak ingest cards`;
- the required failure and degradation paths work: an ingestion failure leaves existing card data untouched and servable, a tool-call-cap breach still produces an answer, a disallowed/failing tool call is returned to the model as a structured result rather than crashing the turn, and an invalid or unimplemented router decision safely defaults to `other`;
- no BLOCKER or MAJOR review finding remains open.

Implementation existing is not sufficient.
