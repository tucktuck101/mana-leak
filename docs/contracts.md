# Contracts

## Purpose

This document defines the interfaces Mana Leak is implemented against: domain models, service signatures, tool schemas, Judge results, turn events, REST/SSE, CLI, MCP, configuration, limits, and errors. Where it names a field that is persisted, the name matches `data-model.md`. Responsibilities and flows are in `architecture.md`.

All Python contracts are Pydantic v2 models in `packages/core` (module `mana_leak_core.contracts`). Adapters import them; they never redefine them.

**Authority.** For interface and behaviour questions this document wins over `architecture.md`; `architecture.md` wins for ownership/boundary questions, and `data-model.md` wins for persistence (`AGENTS.md` §3). `mana-leak-context.md` is historical input only, never an authority. Implementation deviations from this contract are recorded in `ROADMAP.md` → "Decisions & deviations", not here.

## Principles

- **One shared core.** Every capability has one core function. FastAPI, CLI, MCP, and LLM tools call that function and only translate inputs and outputs.
- **Pydantic at boundaries.** Model outputs, tool arguments and results, API bodies, Judge state, and route/screening decisions are typed models. Unstructured `dict`s appear only inside documented `details`/`metadata` fields.
- **Evidence carries provenance.** Domain records from external sources carry `Provenance`. Rulings carry citations that code has resolved against retrieved evidence.
- **Models propose, code decides.** A model's output is a *draft* type (`RouteDecision`, `JudgeDraft`, tool-call arguments). Code validates it and builds the final type, filling IDs, versions, and provenance itself. Models never supply IDs, versions, or provenance values.
- **Failures are values.** Expected failures are returned as typed results (`ToolResult` with `ToolError`, `CardLookupResult`, `ErrorEvent`). Raw exceptions and stack traces never reach a user.

## Conventions

| Item | Convention |
|---|---|
| Pydantic models | `PascalCase` |
| Functions, fields, JSON keys | `snake_case` |
| Enum values | lowercase `snake_case` strings (`StrEnum`) |
| UUIDs | serialized as strings |
| Timestamps | ISO 8601 UTC with `Z` (`2026-10-01T12:00:00Z`) |
| REST paths | lowercase plural nouns |
| Optional fields | `X | None = None`; lists default to `[]` |
| Model config | `extra="forbid"` on every model a model or client produces (drafts, tool args, request bodies) |

## Core enums

```python
class Route(StrEnum):            cards, combos, judge, other
class RulingStatus(StrEnum):     legal, illegal, conditional, insufficient_information
class JudgeSessionStatus(StrEnum): active, completed, exhausted, abandoned
class MessageRole(StrEnum):      user, assistant, tool
class ScreeningLabel(StrEnum):   clear, suspicious, uncertain
class CitationType(StrEnum):     rule, card, combo
class RetrievalMethod(StrEnum):  semantic, keyword, rule_number, hybrid
class CommanderLegality(StrEnum): legal, not_legal, banned, restricted
class Severity(StrEnum):         info, warning, error
class ComboSource(StrEnum):      live, cache, fixture
class ContinuationKind(StrEnum): answer, new_question, unrelated
class SessionControl(StrEnum):   answer, new_question, end_session
class EvalSuite(StrEnum):        mtg_qa, current_rules, retrieval, routing_tool, adversarial, judge_mode
class EvalSplit(StrEnum):        smoke, dev, held_out, gold
```

Tool outcome is the boolean `ToolResult.ok` (success/error); there is no separate enum.

Colours are single uppercase letters `W U B R G`; colourless identity is `[]`.

## Provenance

```python
class Provenance(BaseModel):
    source: Literal["scryfall", "commander_spellbook", "comprehensive_rules", "mtg_qa", "hand_authored"]
    source_id: str | None = None
    source_url: str | None = None
    source_version: str
    retrieved_at: datetime
```

Built only from stored columns (`data-model.md` → Provenance fields).

## Card contracts

```python
class CardFace(BaseModel):
    name: str
    mana_cost: str | None = None
    type_line: str
    oracle_text: str = ""
    power: str | None = None
    toughness: str | None = None
    loyalty: str | None = None

class CardSummary(BaseModel):
    oracle_id: UUID
    name: str
    mana_cost: str | None = None
    mana_value: float
    type_line: str
    color_identity: list[str]
    legal_commander: CommanderLegality

class Card(CardSummary):
    layout: str
    oracle_text: str
    colors: list[str]
    keywords: list[str]
    power: str | None = None
    toughness: str | None = None
    loyalty: str | None = None
    faces: list[CardFace] = []          # empty for single-face cards
    scryfall_uri: str
    provenance: Provenance
```

### Search

```python
class CardSearchRequest(BaseModel):
    query: str | None = None             # free text over name + type line + Oracle text (full-text search)
    name: str | None = None              # name contains / fuzzy
    type_text: str | None = None         # type line contains, e.g. "Legendary Creature", "Instant"
    oracle_text: str | None = None       # Oracle text contains (case-insensitive substring)
    color_identity: list[str] | None = None  # card identity must be a SUBSET of this (Commander deck rule)
    legal_commander: bool | None = None  # True → only legal_commander == legal
    mana_value_min: float | None = None
    mana_value_max: float | None = None
    keyword: str | None = None           # exact keyword, case-insensitive
    limit: int = Field(default=5, ge=1, le=10)
```

- At least one filter must be set; otherwise `validation_error`.
- Filters combine with AND. Ordering: full-text rank when `query` is set, otherwise name ascending.
- This is the complete filter set; there is no query-language parser.

### Lookup

```python
class CardLookupRequest(BaseModel):
    identifier: str = Field(min_length=1, max_length=200)   # card name, face name, or oracle_id UUID

class CardFound(BaseModel):
    kind: Literal["found"] = "found"
    card: Card
    match: Literal["oracle_id", "exact_name", "face_name", "fuzzy"]

class CardNotFound(BaseModel):
    kind: Literal["not_found"] = "not_found"
    identifier: str

class CardAmbiguous(BaseModel):
    kind: Literal["ambiguous"] = "ambiguous"
    identifier: str
    candidates: list[CardSummary]        # ≤5

CardLookupResult = Annotated[CardFound | CardNotFound | CardAmbiguous, Field(discriminator="kind")]
```

Resolution order (deterministic):

1. Parses as UUID → lookup by `oracle_id`.
2. Exact `name_normalized` match → `found`/`exact_name` (if more than one row matches, `ambiguous`).
3. Exact match in `face_names_normalized` → `found`/`face_name`.
4. Trigram similarity ≥ 0.6: a single best match with a margin of at least 0.1 over the runner-up → `found`/`fuzzy`; several close matches → `ambiguous`; none → `not_found`.

A `fuzzy` match is never silently treated as exact. Callers show `match` to the user ("Did you mean …"), and the judge treats it as an identified card only when `match != "fuzzy"` or the user confirms it.

## Combo contracts

```python
class ComboCard(BaseModel):
    name: str
    oracle_id: UUID | None = None        # null when not resolved locally
    quantity: int = 1
    zone_locations: list[str] = []

class ComboSummary(BaseModel):
    id: str                               # Spellbook variant id, verbatim
    card_names: list[str]
    card_oracle_ids: list[UUID] = []      # oracle_id per card_names entry that resolved locally; may be shorter than card_names
    color_identity: list[str]
    results: list[str]                    # "produces" lines; summary for display
    legal_commander: bool | None = None

class Combo(BaseModel):
    id: str
    cards: list[ComboCard]
    templates: list[str] = []             # Spellbook "requires" generic template cards, e.g. "a sac outlet"
    color_identity: list[str]
    prerequisites: list[str]
    steps: list[str]                      # ordered
    results: list[str]
    legal_commander: bool | None = None
    provenance: Provenance

class ComboSearchRequest(BaseModel):
    query: str | None = None              # free text passed to Spellbook search (e.g. a result like "infinite mana")
    card_names: list[str] = []            # combos must include all of these
    color_identity: list[str] | None = None   # combo identity must be a subset
    limit: int = Field(default=5, ge=1, le=10)

class ComboSearchResult(BaseModel):
    items: list[ComboSummary]
    source: ComboSource

class ComboFindRequest(BaseModel):
    card_names: list[str] = Field(min_length=1, max_length=10)
    limit: int = Field(default=5, ge=1, le=10)

class ComboFindResult(BaseModel):
    items: list[Combo]
    source: ComboSource

class ComboGetRequest(BaseModel):
    combo_id: str

class ComboGetResult(BaseModel):
    item: Combo
    source: ComboSource
```

- **`search_combos`** is exploratory: free text and/or filters, returning a `ComboSearchResult` of `ComboSummary` items. At least one of `query` or `card_names` is required.
- **`find_combos`** answers the primary use case "which known combos include these cards?" It returns a `ComboFindResult` of full `Combo` records containing **all** supplied cards. Before querying, card names are resolved through `get_card`; unresolved or ambiguous names make the whole call fail with `not_found`/`ambiguous_match` and list the names involved, so it never runs a partial search silently.
- **`get_combo`** returns one `Combo` by ID, wrapped with `source` like the other combo results (`ComboGetResult`).
- **`templates`** lists Spellbook's generic template requirements (e.g. "a sac outlet") that the combo needs beyond the named `cards` — descriptive text, never resolved to card records, and never treated as an identified/involved card for Judge citation purposes.
- A `Combo` object is only ever built by the Spellbook adapter or the fixture loader. Model text describing a combo is never parsed into `Combo`.
- **`source`** replaces the old per-`Combo` `from_cache` flag: it describes where the whole result came from (`live`, `cache`, or `fixture`), so even an empty result carries provenance. If live fails **and** neither the cache nor the fixtures has an entry covering every requested card, the call raises `dependency_unavailable` ("combo data unavailable") — it never falls back to an empty `ComboSearchResult`/`ComboFindResult` as a stand-in for a dependency failure. A genuinely empty **live** result (Spellbook simply has no matching variant) is a value, not an error: the judge and the answer prompt must phrase it as "no known combo in Commander Spellbook for these cards", never "these cards don't combo" — Spellbook coverage is not completeness.
- **Offline (`COMBO_SOURCE=fixtures`):** the live tier is skipped entirely, so nothing "fails" there. An empty fixtures result is a value with `source=fixture`, phrased as "no known combo in the offline combo set" (not the live-empty phrasing above — fixtures mode never actually asks Commander Spellbook).
- **`search_combos` with `query` only (no `card_names`) and live down:** the cache has no full-text search, so only the fixtures are checked for a match; a fixtures match returns `source=fixture`, otherwise `dependency_unavailable`.
- **`get_combo` offline** (live down, or `COMBO_SOURCE=fixtures`): checks the cache, then the fixtures, by `id`; if found, returns `ComboGetResult{item, source}` with `source` naming whichever tier answered (`cache` or `fixture`); if neither has it, raises `not_found` with `ToolError.details={"source": ...}` naming whichever tier was checked last — a single known ID is either on record locally or it is not, never `dependency_unavailable`.

## Rules contracts

```python
class RuleSearchRequest(BaseModel):
    query: str | None = Field(default=None, max_length=1000)
    rule_number: str | None = Field(default=None, pattern=r"^(\d{3}(\.\d+[a-z]?)?|glossary:.+)$")
    limit: int = Field(default=6, ge=1, le=8)

class RuleHit(BaseModel):
    chunk_id: UUID
    rule_number: str                      # e.g. "702.19"
    part: int = 1
    subrule_numbers: list[str]            # all subrules in the chunk
    matched_subrules: list[str] = []      # subset matching an explicit rule_number request
    section_title: str
    text: str
    score: float | None = None            # method-specific; higher is better
    method: RetrievalMethod
    rules_version: str
    provenance: Provenance
```

- At least one of `query` or `rule_number` is required.
- `rule_number` lookup returns the chunk(s) containing that rule or subrule (`method=rule_number`). Rule-number hits come first.
- With `query`: run semantic (top 8) and keyword (top 8) searches, merge by reciprocal rank (k = 60), deduplicate by `chunk_id`, and return up to `limit` hits with `method=hybrid`. If embeddings are unavailable, keyword results alone are returned with `method=keyword`, and a `dependency_degraded` audit event is emitted.
- **Keyword leg (OR-semantics):** `websearch_to_tsquery('english', query)`; if that produces an empty tsquery (common once `query` carries many distinct words), fall back to `to_tsquery` over the query's deduplicated lexemes joined with `|` (OR), so the leg still ranks partial matches instead of returning nothing. The semantic leg always embeds the full `query` text.
- **Judge-internal callers are exempt from the 1000-char cap.** `RuleSearchRequest.query` stays ≤1000 for every public caller — REST `/rules/search`, CLI `rules search`, MCP `search_rules`, and the `search_rules` LLM tool. Only the judge's own retrieval (Judge contracts → Judge flow, step 1) builds longer, uncapped text and calls the same semantic/keyword/merge implementation directly instead of constructing a `RuleSearchRequest`.

## Citation contracts

```python
class RuleCitation(BaseModel):
    type: Literal["rule"] = "rule"
    ref: str                              # rule/subrule number, e.g. "702.19c"
    chunk_id: UUID
    quote: str | None = Field(default=None, max_length=300)
    rules_version: str

class CardCitation(BaseModel):
    type: Literal["card"] = "card"
    ref: UUID                             # oracle_id
    name: str
    source_version: str

class ComboCitation(BaseModel):
    type: Literal["combo"] = "combo"
    ref: str                              # Spellbook combo id
    source_version: str

Citation = Annotated[RuleCitation | CardCitation | ComboCitation, Field(discriminator="type")]
```

These serialize to `ruling.citations` as stored in `data-model.md`. They use the same `type`/`ref`/`chunk_id`/`quote` keys, plus the version fields.

**Evidence ledger.** During a turn, the orchestrator records every `RuleHit`, `Card`, and `Combo` returned by a tool or by the judge's own retrieval in a per-turn `EvidenceLedger`. The model cites only by reference:

```python
class CitationRef(BaseModel):            # model-produced
    model_config = ConfigDict(extra="forbid")
    type: CitationType
    ref: str                              # rule number, card name or oracle_id, combo id
    quote: str | None = Field(default=None, max_length=300)
```

Code resolves each `CitationRef` against the ledger:

- **rule:** `ref` must equal a ledger hit's `rule_number` or be in its `subrule_numbers`, and `quote` (if given) must be a substring of that hit's `text` once both strings are normalised (Unicode NFKC; smart quotes, em/en dashes, and non-breaking spaces folded to ASCII equivalents; whitespace collapsed) — an accurate quote typed with different Unicode punctuation than the source document still validates.
- **card:** `ref` must match the `oracle_id` or the name of a ledger card.
- **combo:** `ref` must equal a ledger combo `id`.

Code then builds the full `Citation`. If any reference fails, the result is `citation_validation_failed` (the draft is retried once; see Judge).

## Judge contracts

```python
class GameStateFact(BaseModel):
    fact: str = Field(max_length=500)
    source_turn_id: UUID | None = None

class SufficiencyDecision(BaseModel):    # bounded decision (Jev or local substitute)
    model_config = ConfigDict(extra="forbid")
    sufficient: bool
    missing_facts: list[str] = Field(default=[], max_length=3)   # each ≤200 chars; empty iff sufficient
    reason: str | None = Field(default=None, max_length=200)

class ContinuationDecision(BaseModel):   # bounded decision; one model call, only when a session is active and no explicit control was given
    model_config = ConfigDict(extra="forbid")
    kind: ContinuationKind
    confident: bool = True                # false → ask the user to disambiguate instead of acting on `kind`

class JudgeDraft(BaseModel):             # model-produced; never shown without validation
    model_config = ConfigDict(extra="forbid")
    answer_kind: Literal["explanation", "ruling"]
    status: RulingStatus | None = None    # required iff answer_kind == "ruling"; null for "explanation"
    summary: str = Field(max_length=400)
    explanation: str = Field(max_length=4000)
    assumptions: list[str] = Field(default=[], max_length=5)
    missing_information: list[str] = Field(default=[], max_length=5)
    citations: list[CitationRef]          # `type="card"` entries uncapped; at most 12 with `type` in {rule, combo}

class CardRef(BaseModel):
    oracle_id: UUID
    name: str

class Ruling(BaseModel):
    kind: Literal["ruling"] = "ruling"
    id: UUID
    conversation_id: UUID
    judge_session_id: UUID | None = None
    turn_id: UUID
    status: RulingStatus
    question: str
    summary: str
    explanation: str
    assumptions: list[str]
    missing_information: list[str]
    cards: list[CardRef]                  # involved cards, persisted as ruling.card_oracle_ids
    rule_numbers: list[str]               # derived from rule citations
    citations: list[Citation]
    rules_version: str | None = None
    card_source_version: str | None = None
    model: str
    prompt_version: str
    trace_id: str | None = None
    created_at: datetime

class NeedMoreInformation(BaseModel):
    kind: Literal["need_more_information"] = "need_more_information"
    status: Literal[RulingStatus.insufficient_information] = RulingStatus.insufficient_information
    session_id: UUID                      # judge_session.id
    conversation_id: UUID
    question: str                         # original question
    known_facts: list[GameStateFact]
    missing_facts: list[str]              # 1–3
    clarification_questions: list[str]    # 1–3, one per missing fact
    clarification_count: int              # rounds already asked, including this one
    max_clarifications: int = 3           # Settings.JUDGE_MAX_CLARIFICATIONS

class RulesExplanation(BaseModel):
    kind: Literal["rules_explanation"] = "rules_explanation"
    id: UUID
    conversation_id: UUID
    judge_session_id: UUID | None = None
    turn_id: UUID
    question: str
    summary: str
    explanation: str
    cards: list[CardRef]                  # involved cards, persisted as ruling.card_oracle_ids
    rule_numbers: list[str]               # derived from rule citations
    citations: list[Citation]
    assumptions: list[str]
    missing_information: list[str]
    rules_version: str | None = None
    card_source_version: str | None = None
    model: str
    prompt_version: str
    trace_id: str | None = None
    created_at: datetime

class RuleText(BaseModel):
    kind: Literal["rule_text"] = "rule_text"
    rule_number: str
    text: str                             # quoted verbatim, no model paraphrase
    rules_version: str
    provenance: Provenance

JudgeResult = Annotated[Ruling | NeedMoreInformation | RulesExplanation | RuleText, Field(discriminator="kind")]
```

**Answer kinds.** The judge produces one of three result shapes:

- `RuleText` — the user asks for a rule's exact text (e.g. "what is rule 702.19?"). Judge flow step 0: deterministic lookup, quoted verbatim, no model paraphrase. A short model summary may follow only if the user explicitly asks for one, as ordinary streamed text alongside the result — it never changes the `RuleText` result itself.
- `RulesExplanation` — the default for rules/interaction questions that are not asking for a legality verdict: a cited natural-language explanation able to walk through a multi-step board state (e.g. a card cast mid-search while its cost is paid by another permanent's mana ability — the Comprehensive Rules are sufficient for these). Same citation validation, second pass, force-ask, and sufficiency as `Ruling` (below).
- `Ruling` (`legal`/`illegal`/`conditional`/`insufficient_information`) — the user asks whether something is legal, works, or is allowed.

`answer_kind` is a bounded field the judge fills in on `JudgeDraft` itself (Judge flow step 5) — the judge chooses, code never guesses from keyword matching. `RuleText` is the one exception: its shape is chosen deterministically from the message, before any model call (Judge flow step 0).

**Sources.** The judge answers only from the Comprehensive Rules (ingested rule chunks); it never cites or relies on the Magic Tournament Rules or Infraction Procedure Guide. If a question genuinely turns on tournament policy rather than game rules, it says so in `explanation` and does not produce a `legal`/`illegal`/`conditional` verdict on the policy question.

**Validation rules (code):**

- A draft signals insufficient evidence via non-empty `missing_information`, for either `answer_kind`; `citations` may be empty in that case, and for `answer_kind="ruling"` it additionally requires `status=insufficient_information`. Otherwise — `missing_information` empty — `citations` must include at least one rule citation (and, for `answer_kind="ruling"`, `status` is any value other than `insufficient_information`).
- Every involved card (Judge flow step 1: identified cards the draft mentions by name or `oracle_id`) must be cited, for `answer_kind="ruling"` with `status` in `legal`/`illegal`/`conditional`, and for `answer_kind="explanation"` with `missing_information` empty (the insufficient-evidence case of either `answer_kind` carries no card-citation requirement).
- `citations` may include any number of `type="card"` entries but at most 12 entries with `type` in `{"rule", "combo"}`; a draft exceeding that fails validation like any other citation failure.
- Draft validation failure (schema or citation) → one retry with the validation error appended. A second failure → a result matching the failed draft's `answer_kind`, with empty `citations` and `missing_information=["A supported answer could not be produced from the available evidence."]`, plus a `citation_validation_failed` or `structured_output_invalid` audit event: `answer_kind="ruling"` produces a `Ruling` with `status=insufficient_information`; `answer_kind="explanation"` produces a `RulesExplanation` with the same empty-citations/missing-information shape. Either way the result already carries the insufficient-evidence shape the force-ask rule (below) would otherwise produce, so it skips force-ask and is persisted in the `ruling` table exactly like any other terminal result (step 9); code never fabricates a best-effort citation-bearing result of either kind.
- **Force-ask rule:** applies to a validated draft of any `answer_kind`/`status` combination other than `status=insufficient_information`. The draft is shown as-is only if `assumptions` is empty, or `clarification_count == max_clarifications` (rounds exhausted, so the draft — whatever its `status` — is reported instead of asked, assumptions included). Otherwise — `assumptions` non-empty with rounds remaining — code converts the result to `NeedMoreInformation` instead, performing the same bookkeeping as Judge flow step 3: upsert the active `judge_session`, set `card_oracle_ids` from the draft's identified cards, set `missing_facts`/`clarification_questions` (one per assumption, truncated to 3), and increment `clarification_count`. This is the deterministic guard against the model answering with unstated assumptions instead of asking — for every status, including `legal`/`illegal`, not only `conditional`.

**Judge flow (code-owned):**

0. **Rule-text lookup (deterministic, no model call):** if the message is a bare request for a rule's text — it contains exactly one `rule_number`-shaped match (`\d{3}(\.\d+[a-z]?)?` or `glossary:<term>`), and once that match and fixed lookup phrasing ("what is", "what does … say", "text of", "show me", "define", "rule", punctuation) are stripped no other substantive words remain (no identified cards, no legality wording like "legal"/"can I"/"does this work") — look up the rule/subrule by `rule_number` (Rules contracts) and return `RuleText`. Otherwise continue from step 1.
1. **Evidence gathering (deterministic, no model call):**
   - *Identified cards:* resolved via `get_card`, in precedence order: (a) candidate names/face names extracted from the **current message** (Card-name extraction, below); (b) `oracle_id`s present in the **last assistant result only** (`CardsResult`/`CombosResult`/`Ruling`/`RulesExplanation` payload of the most recent assistant message — not all 10 turns of context); (c) the active Judge session's `card_oracle_ids`. Capped at 10 total, filled in that precedence order. A `not_found` or `ambiguous` candidate from (a) makes that piece a missing fact (e.g. "Which card do you mean by 'Kiki'?") instead of failing the turn.
   - *Card-name extraction* (current message only): an explicit `[[Card Name]]` span always resolves via `get_card`. Otherwise, a deterministic longest-match scan over normalised 1–6-token n-grams against `name_normalized`/`face_names_normalized` for an exact match; a single-token match is accepted only if that token is capitalised in the message or the match is ≥2 tokens long (so incidental lowercase common words like "cancel"/"fear" are skipped, while capitalised one-word names and any multi-word name still match). If that scan finds nothing anywhere in the message, a fallback pass treats capitalised 1–3-token spans as candidates instead of dropping them: a 1–2-token span qualifies via a case-insensitive *prefix* match against some card's `name_normalized` (e.g. "Kiki" before "Kiki-Jiki, Mirror Breaker"); a longer span qualifies via the same trigram similarity `get_card` itself uses for fuzzy lookups (≥ 0.6, Card contracts → Lookup), catching typos. Either kind of fallback candidate becomes a missing fact like any `not_found`/`ambiguous` candidate below, never a silent non-match. These fallback thresholds are tunable defaults, not fixed behaviour.
   - *Involved cards* (must be cited, Validation rules above): the identified cards the draft actually mentions, by name or `oracle_id`, in `explanation` or `citations`. Identified cards the draft never mentions are evidence only.
   - *Rules query:* explicit rule/subrule numbers in the question (matching the `rule_number` shape) are looked up directly by `rule_number` first. The remaining text runs hybrid search through the judge's own uncapped internal path (Rules contracts → Judge-internal callers), not the public `RuleSearchRequest`: the **semantic** leg embeds the question plus the identified cards' names plus the first 300 characters of each identified card's Oracle text (no length cap); the **keyword** leg runs OR-semantics full-text search (Rules contracts) over the question text *only*, so a multi-card question still matches. Together these fill the ledger with ≤8 rule chunks.
   - `find_combos` runs only when the question names two or more identified cards, or the conversation's last assistant result was a combo; its items join the ledger. A `dependency_unavailable` from this internal call does not fail the turn — the judge proceeds without combo evidence.
2. Call `SufficiencyDecision` with the question, known facts, and evidence summaries.
3. If `sufficient=false` and `clarification_count < max_clarifications` (`Settings.JUDGE_MAX_CLARIFICATIONS`, default 3): upsert the active `judge_session`, increment `clarification_count`, and return `NeedMoreInformation`.
4. If `sufficient=false` and `clarification_count == max_clarifications`: set the session to `exhausted`, persist a `RulesExplanation` (`kind="explanation"`, `citations` empty, `missing_information` listing the missing facts) in the `ruling` table exactly as step 9 does, and return it — no `JudgeDraft`/`answer_kind` has been chosen yet at this point, so this result is never `Ruling`-shaped.
5. If sufficient: generate a `JudgeDraft` — the judge chooses `answer_kind` (Answer kinds above).
6. **Bounded second pass:** collect rule numbers the draft references (its `citations`, or matching the dotted subrule pattern `\d{3}\.\d+[a-z]?` — not the bare 3-digit `rule_number` shape, which false-positives on prose like "500 damage" — anywhere in `explanation`) that are absent from the ledger; fetch each by `rule_number` lookup, inserting the result and evicting the lowest-scored `hybrid`-method hit that the first draft's `citations` did **not** cite (if every `hybrid` hit was cited, or there are none to evict, the ledger is allowed to exceed 8 by the fetched count instead) so the ledger's rule-chunk count otherwise stays ≤8. If anything was fetched, regenerate the draft **once** more against the enriched ledger. No further retrieval or extra model calls happen after this, regardless of outcome.
7. Validate the draft (Validation rules above: schema and citation checks, with their own one retry on failure).
8. Apply the force-ask rule (Validation rules above) to the validated draft, for any `answer_kind`/`status`.
9. Persist the result in the `ruling` table (`kind="ruling"` for a `Ruling` with its `status` set, `kind="explanation"` for a `RulesExplanation` with `status` null), with `judge_session_id` if a session exists, and set the session to `completed`. A `RuleText` (step 0) is never persisted as a `ruling` row — it is message payload only.

A `NeedMoreInformation` is not persisted as a `ruling` row either; it is persisted as `judge_session` state plus the assistant message.

A typical judge turn makes 4 model calls (screening, routing-or-continuation, sufficiency, draft) — comfortably inside the per-turn cap. Each of those 4 can independently consume its own structured-output retry (Operational limits), the bounded second pass can add one more draft regeneration, and the draft-validation retry can add one more again: true worst case is 9 model calls before summarisation, 10 if context summarisation (Turn orchestration step 4) also runs — summarisation is skipped whenever fewer than 2 calls remain in the budget (Operational limits → What counts), so it never pushes a turn over the cap by itself. A turn that genuinely stacks every retry still reaches the hard cap of 8 before completing: the model gateway raises `model_limit_exceeded` (Operational limits → What counts), which — like any mid-turn failure — surfaces as an `error` event over SSE (Streaming events → SSE mapping) rather than a fabricated result.

**Budget reserve:** the bounded second pass's regeneration (step 6) and the draft-validation retry (step 7) each run only while at least 30 s remain in the turn's 120 s budget — short of that, the judge proceeds with whatever draft or result it already has (step 6 keeps the first draft as final; a step-7 failure with less than 30 s remaining goes straight to the second-failure fallback, Validation rules) instead of spending the turn's last seconds on a retry that risks `timeout` or `model_limit_exceeded`. A `limit_reached` audit event is emitted whenever this guard skips a retry that would otherwise have run.

**Session controls:** with an active Judge session, the router never runs; session controls decide the turn instead (Turn orchestration step 5), regardless of whether the caller also set `forced_route`. Mana Leak behaves like a familiar chat assistant here: an unrelated message never pauses or silently ends the session — only an explicit control, a `new_question`, normal completion, or round-cap exhaustion does. Each interface gives the user an explicit, deterministic control alongside plain text, with no model call:

| Interface | Explicit control | Effect |
|---|---|---|
| Web UI | "Judge session" indicator's "End session" button | `session_action=end_session` |
| Web UI | "New chat" button | starts a new conversation (not a session transition) |
| Web UI | Stop-generation button while streaming | aborts the browser's connection (Streaming events → Client disconnect); independent of session state |
| CLI `chat` | `/cancel` | `session_action=end_session` |
| CLI `chat` | `/new <question>` | `session_action=new_question`, `<question>` as content |
| CLI `chat` | `/help` | prints command help; not sent as a turn |
| CLI `judge` | `[QUESTION] --action answer\|new_question\|end_session` | forces that outcome, mirroring MCP; `QUESTION` is optional (and ignored) for `end_session` |
| MCP `judge` | `action: "answer" \| "new_question" \| "end_session"` (optional); `question` optional only for `end_session` | forces that outcome; omitted → inferred below |
| REST `POST …/messages` | body `action: "answer" \| "new_question" \| "end_session"` (optional) | forces that outcome; omitted → inferred below |

These map to `process_turn`'s `session_action` parameter (`SessionControl | None`, Core service interfaces) — a type distinct from `ContinuationKind` (below): `answer`/`new_question` mean the same thing in both, but `SessionControl.end_session` has no `ContinuationKind` counterpart, because the model's own classification never ends a session on its own (see `unrelated`, below). A leading `/` in CLI `chat` is never resolved as a card name or sent as content; an unrecognised `/command` is a local usage error, not a turn.

Without an explicit control, one bounded model call (`ROUTER_MODEL`, prompt `continuation-v1`) returns a `ContinuationDecision`, which code acts on:

- `answer` — the message is added to `known_facts`; the judge re-runs from step 1 of the Judge flow with the original `question`.
- `new_question` — a new *rules/legality* question for the judge: the active session is set to `abandoned` (`JudgeSessionStatus`); a new Judge flow starts from step 1 with the new message as `question`.
- `unrelated` — anything else, including card/combo/general requests that are not a new rules question: like any other chat assistant, there is no pause/end semantics visible to the user — the session stays `active` and unchanged, and the message is simply answered normally via the router, exactly as if no session were active, so a later message that answers the outstanding clarification still continues the same session. **Exception:** a forced-`judge` caller (CLI/MCP `judge`) has no router to fall through to, so the message becomes a new judge question instead (Turn orchestration step 5), and the result stays within `JudgeResult`.

`SessionControl.end_session` (the "End session" button, `/cancel`, `--action end_session`, or MCP/REST `action="end_session"`) ends the active session (`abandoned`) deliberately — together with `new_question`, normal completion (`completed`), and round-cap exhaustion (`exhausted`), the only ways an `active` session stops being `active`. Unlike `new_question`, it never asks anything new: `content` (REST, CLI `chat`), CLI `judge`'s positional `QUESTION`, and MCP `judge`'s `question` argument are all optional and ignored if given. The turn ends immediately after closing the session: a short fixed assistant message ("Judge session ended.") is persisted, and `final` is emitted with `result=None` — the same message-less shape as any other no-content turn.

If the `ContinuationDecision` is not confident (`confident=False` — e.g. a bare word that is both a card name and an intent, like "Cancel"), code does not act on `kind` at all: it answers with a short clarifying question asking which the user meant, as a normal assistant answer (`result=None`, plain streamed text — the same shape as an `other`-route reply), without starting a Judge round, consuming a clarification round, or changing session state, and waits for an explicit control or an unambiguous reply. A forced-`judge` caller's result must stay within `JudgeResult`, which has no "ask the user" shape, so there a not-confident decision defaults to `answer` instead — the message is treated as continuing the pending clarification. Plain text is never pattern-matched against a fixed phrase list — "Cancel" remains a valid card name; only an explicit control or a confident `ContinuationDecision` changes session state.

Every session-status transition — `new_question`, `SessionControl.end_session`, normal completion, or exhaustion — emits a `judge_transition` audit event; `answer` and `unrelated` leave the status unchanged, so neither emits one.

## Router contract

```python
class RouteDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    route: Route
    reason: str | None = Field(default=None, max_length=120)   # short label for traces, not reasoning
```

- One model call (`ROUTER_MODEL`, prompt `router-v1`), using structured output.
- If the output is invalid after 1 retry, the route defaults to `other` (`structured_output_invalid` audit event).
- Skipped whenever a Judge session is active — session controls decide instead (Judge contracts → Session controls, Turn orchestration step 5), regardless of `forced_route`. Also skipped when `forced_route` is set (CLI/MCP `judge`) and no session is active.

| Route | Allowed LLM tools | Max tool calls |
|---|---|---:|
| `cards` | `search_cards`, `get_card` | 5 |
| `combos` | `get_card`, `search_combos`, `find_combos`, `get_combo` | 5 |
| `judge` | none (judge orchestrates retrieval in code; see below) | — |
| `other` | none | 0 |

**Judge route tools.** The judge never receives a tool loop. The judge service calls `get_card`, `search_rules`, and `find_combos` directly in code (Judge flow, evidence gathering), reusing the same core functions and allowlist with a deterministic evidence set and a lower, predictable model-call count than a free-form tool loop would allow. A rule-number lookup short-circuits even this: `RuleText` comes from one direct `search_rules(rule_number=…)` call with no other evidence gathering and no model call (Judge flow step 0).

## Safeguard contract

```python
class ScreeningDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    label: ScreeningLabel
    reason_code: Literal[
        "prompt_injection", "secret_request", "policy_override", "off_topic_abuse", "malformed", "none"
    ] | None = None
```

Order (also fixed in Turn orchestration):

1. **Deterministic validation (no model call, nothing persisted):** malformed body, an invalid `action`/`session_action` value, a missing or empty message when one is required (every case except an explicit `session_action="end_session"` control, Turn orchestration), or more than 8,000 characters → rejected before persistence: REST returns `ErrorResponse` (`validation_error`, status per HTTP error mapping) without opening a stream; CLI/MCP return the same error. Control characters are stripped.
2. **Persist the user message**, if one was given (valid messages only; `session_action="end_session"` with no content persists none).
3. **Screening:** one model call (`ROUTER_MODEL`, `safeguard-v1`) returns a `ScreeningDecision`.

Code consequences:

| Label | Consequence |
|---|---|
| `clear` | Continue. |
| `uncertain` | Continue; the system prompt adds the restricted-mode notice; audit at `warning`. |
| `suspicious` | Do not route. Reply with a fixed refusal explaining Mana Leak only answers card/combo/rules questions from evidence; the refusal is persisted as the assistant message like any other answer (the user message is already persisted). Audit at `warning`. The `final` event carries an `ErrorInfo` with `safeguard_rejected`. |

Screening output never includes reasoning text. Retrieved evidence is not screened by the model; it is always inserted as delimited data.

## Tool contracts

**Three layers:**

- **Domain service functions:** the async core functions below. The canonical behaviour.
- **LLM tools:** a subset of those functions with JSON schemas generated from their request models, exposed per route and wrapped in `ToolResult`.
- **Public interfaces (REST, CLI, MCP):** call the same domain functions, or `process_turn`/`judge` for conversational behaviour.

### Result envelope

```python
class ToolError(BaseModel):
    code: ErrorCode
    message: str                          # safe, short
    retryable: bool = False
    details: dict[str, Any] = {}

class ToolResult(BaseModel, Generic[T]):
    ok: bool
    data: T | None = None
    error: ToolError | None = None        # exactly one of data/error is set
    truncated: bool = False               # True only if the model-facing text was shortened
```

Text passed to the model is `ToolResult.model_dump_json(exclude_none=True)`. `truncated` is normally `False`. If the text is over 8,000 characters, long text fields (`oracle_text`, `text`, `steps`) are cut with `…`, then list items are dropped from the end, and `truncated` is set to `True`. Truncation affects only the representation sent to the model; the full result stays in the evidence ledger and remains authoritative for citation checks and persistence. Provenance is removed from model-facing text (it stays in the ledger) to save space.

### LLM tool table

| Tool | Purpose (description shown to the model) | Args | Result `data` | Routes | Max items |
|---|---|---|---|---|---:|
| `search_cards` | Find cards by structured filters (name, type, Oracle text, colour identity, Commander legality, mana value, keyword). Use when the user does not name an exact card. | `CardSearchRequest` | `list[CardSummary]` | cards | 10 |
| `get_card` | Get one card's full current Oracle text and properties by exact name, face name, or ID. Use before explaining what a named card does. | `CardLookupRequest` | `CardLookupResult` | cards, combos | 1 (5 candidates) |
| `search_combos` | Search Commander Spellbook for known combos by description/result and/or included cards. | `ComboSearchRequest` | `ComboSearchResult` | combos | 10 |
| `find_combos` | List known Commander Spellbook combos that include ALL the given cards. | `ComboFindRequest` | `ComboFindResult` | combos | 10 |
| `get_combo` | Get one known combo's pieces, prerequisites, steps, and results by its Spellbook ID. | `ComboGetRequest` | `ComboGetResult` | combos | 1 |
| `search_rules` | Retrieve current Comprehensive Rules sections by question text or rule number. | `RuleSearchRequest` | `list[RuleHit]` | (judge, in code) | 8 |

Failures: argument validation failure → `validation_error`; unknown tool or tool not allowed on the route → the call is not executed, the model receives `ToolResult(ok=False, error=ToolError(code="tool_not_allowed"))`, and a `tool_rejected` audit event is written. A sixth tool call in a turn is not executed: the loop stops with `tool_limit_exceeded` and the model gets one final call to answer from the evidence it already has.

No tool performs writes, SQL, shell, filesystem, or arbitrary network access, or reads configuration.

## Core service interfaces

All are `async` and live in `mana_leak_core`. `db` is an `AsyncSession` obtained from `get_session()`. Each function raises `ManaLeakError` (see Errors) for failures; tool and adapter wrappers convert it to `ToolError`/HTTP/exit codes.

```python
# cards
async def search_cards(req: CardSearchRequest) -> list[CardSummary]
async def get_card(req: CardLookupRequest) -> CardLookupResult

# combos
async def search_combos(req: ComboSearchRequest) -> ComboSearchResult
async def find_combos(req: ComboFindRequest) -> ComboFindResult
async def get_combo(req: ComboGetRequest) -> ComboGetResult            # not_found if unknown

# rules
async def search_rules(req: RuleSearchRequest) -> list[RuleHit]

# judge
async def judge(
    question: str,
    conversation_id: UUID,
    turn_id: UUID,
    ledger: EvidenceLedger,
) -> JudgeResult

# conversations
async def create_conversation(title: str | None = None) -> ConversationSummary
async def list_conversations(limit: int = 50) -> list[ConversationSummary]
async def get_conversation(conversation_id: UUID) -> ConversationDetail        # not_found
async def append_message(conversation_id: UUID, turn_id: UUID, role: MessageRole,
                         content: str, payload: dict | None = None,
                         route: Route | None = None, *,
                         message_id: UUID | None = None) -> MessageOut   # message_id: pre-allocated id announced by message_start
async def get_active_judge_session(conversation_id: UUID) -> JudgeSessionState | None
async def build_context(conversation_id: UUID) -> ModelContext                # summary + last 10 turns + active Judge facts
# ModelContext(conversation_id: UUID, summary: str | None, messages: list[MessageOut], judge_facts added in M7)

# orchestration
async def process_turn(conversation_id: UUID, user_message: str | None = None, forced_route: Route | None = None,
                      session_action: SessionControl | None = None) -> AsyncIterator[TurnEvent]   # async generator; forced_route=Route.judge for CLI/MCP `judge`; user_message required unless session_action="end_session"; session_action carries an explicit session control (Judge contracts → Session controls), skipping the ContinuationDecision model call

# ingestion (CLI only)
async def ingest_cards(bulk_path: Path | None = None) -> IngestReport      # downloads Oracle Cards bulk to data/ when path is None
async def ingest_rules(source_url: str | None = None) -> IngestReport      # defaults to RULES_SOURCE_URL
async def load_combo_fixtures(path: Path = Path("evals/fixtures/combos.json")) -> IngestReport

# evaluation (CLI only)
async def run_eval_case(case: EvalCaseInput, run_group_id: UUID, config: EvalConfig) -> EvalResult
async def run_eval_suite(suite: EvalSuite | None, split: EvalSplit, config: EvalConfig) -> EvalSuiteReport

# audit
async def emit_audit_event(event_type: AuditEventType, severity: Severity,
                           conversation_id: UUID | None = None, turn_id: UUID | None = None,
                           details: dict[str, Any] | None = None) -> None   # never raises
```

Supporting models:

```python
class ConversationSummary(BaseModel):
    id: UUID
    title: str | None
    created_at: datetime
    updated_at: datetime

class MessageOut(BaseModel):
    id: UUID
    seq: int
    turn_id: UUID
    role: MessageRole
    content: str
    payload: dict[str, Any] | None = None    # TurnResult JSON for assistant (judge turns duplicate the Ruling row here by design, for display without a join); tool summary for tool
    route: Route | None = None
    created_at: datetime

class JudgeSessionState(BaseModel):
    id: UUID
    conversation_id: UUID
    status: JudgeSessionStatus
    question: str
    known_facts: list[GameStateFact]
    missing_facts: list[str]
    clarification_count: int
    card_oracle_ids: list[UUID]

class ConversationDetail(ConversationSummary):
    messages: list[MessageOut]                # all messages, ascending seq
    active_judge_session: JudgeSessionState | None = None

class IngestReport(BaseModel):
    source: str
    source_version: str
    inserted: int
    updated: int
    deleted: int
    skipped: int
    duration_ms: int
```

`judge()` is only ever invoked from inside `process_turn` (step 6, route `judge`, including the forced-route path CLI `judge`/MCP `judge` use); it always reuses the turn's evidence ledger and never applies screening itself or creates its own ledger.

## Turn orchestration

`async def process_turn(conversation_id, user_message=None, forced_route=None, session_action=None)` is an async generator and the only conversational entrypoint. Web (via SSE), CLI `chat`, CLI `judge`, and MCP `judge` all call it directly — there is no separate code path for `judge`. CLI `judge` and MCP `judge` pass `forced_route=Route.judge`; every other caller leaves it `None`. `session_action` carries an explicit, deterministic session control (REST `action` body field, CLI `/cancel`/`/new`/`--action`, MCP `action` argument — Judge contracts → Session controls), letting the caller skip the `ContinuationDecision` model call; `user_message` is required unless `session_action="end_session"`. Adapters run deterministic validation (step 1) before calling it, so an invalid request never opens a stream. Sequence:

1. Deterministic validation: malformed body, an invalid `action`/`session_action` value, a missing/empty `user_message` when one is required (every case except `session_action="end_session"`), or > 8,000 chars. Failure → `validation_error`; nothing persisted, no stream.
2. Generate `turn_id`; persist the user message if one was given; emit `message_start`.
3. Model-based screening (Safeguards), only if a user message was given. `suspicious` → persist the refusal as the assistant message, then go to step 8.
4. Build the context: summary, last 10 turns, active Judge session facts.
5. Route:
   - An active Judge session exists (regardless of `forced_route`) → resolve it via **session controls** (Judge contracts): an explicit `session_action` acts directly with no model call (ignored, as if absent, when no session is active); otherwise one bounded `ContinuationDecision` model call classifies the message, asking the user to disambiguate instead of acting when it is not confident (for a forced-`judge` caller, defaulting to `answer` instead). `answer`/`new_question` resolve to route = `judge`. `unrelated` leaves the session `active` and unchanged; for a forced-`judge` caller the message becomes a new judge question instead (no router to fall through to), and for every other caller it falls through to the next bullet so the turn routes normally. `session_action=end_session` closes the session (`abandoned`) and ends the turn here with no further routing (`result=None`), regardless of whether content was given.
   - else `forced_route` is set → route = `forced_route`, no router call.
   - else → one `RouteDecision` model call.
6. Execute the route:
   - `cards` / `combos`: bounded tool loop with streamed text;
   - `judge`: `judge()`, reusing the turn's ledger;
   - `other`: a single short model answer steering the user back to cards/combos/rules questions, with no tools.
7. Persist tool messages and the assistant message (`payload` = `TurnResult`).
8. Emit `final`, then `message_end`.

The whole turn runs under a 120 s deadline. On timeout, emit `error` (`timeout`) and then `message_end`; the partial assistant text is persisted with `payload.error`.

Because CLI `judge` and MCP `judge` run through this same `process_turn`, their user/assistant messages are persisted and visible via `GET /conversations/{id}` exactly like a web turn, and session controls, continuation, and the clarification-round cap behave identically across every interface.

```python
class CardsResult(BaseModel):
    kind: Literal["cards"] = "cards"
    cards: list[CardSummary]                # cards referenced in the answer

class CombosResult(BaseModel):
    kind: Literal["combos"] = "combos"
    combos: list[ComboSummary]

TurnResult = Annotated[CardsResult | CombosResult | Ruling | NeedMoreInformation | RulesExplanation | RuleText,
                       Field(discriminator="kind")]
```

`other`-route answers and refusals have `result = None`.

## Streaming events

### Canonical `TurnEvent`

```python
class EventBase(BaseModel):
    type: str
    conversation_id: UUID
    turn_id: UUID

class MessageStart(EventBase):  type: Literal["message_start"]; message_id: UUID       # assistant message id
class TextDelta(EventBase):     type: Literal["text_delta"];    delta: str
class ToolStart(EventBase):     type: Literal["tool_start"];    call_id: str; tool: str; args: dict[str, Any]
class ToolEnd(EventBase):       type: Literal["tool_end"];      call_id: str; tool: str; ok: bool
                                                                 summary: str          # e.g. "3 cards", "not_found"
class Final(EventBase):         type: Literal["final"];         route: Route | None
                                                                 text: str              # full assistant text
                                                                 result: TurnResult | None
                                                                 error: ErrorInfo | None = None   # refusal/limit info
class ErrorEvent(EventBase):    type: Literal["error"];         error: ErrorInfo
class MessageEnd(EventBase):    type: Literal["message_end"]

TurnEvent = Annotated[MessageStart | TextDelta | ToolStart | ToolEnd | Final | ErrorEvent | MessageEnd,
                      Field(discriminator="type")]
```

**Ordering:** `message_start` → (`text_delta` | `tool_start` | `tool_end`)* → exactly one of `final` or `error` → `message_end`.

- Judge clarifications arrive as `final` with `result.kind == "need_more_information"`. There is no separate clarification event.
- For the judge route, `text_delta`s stream the `explanation` text after validation succeeds (rulings are never streamed before validation).
- No event contains model reasoning, prompts, provenance internals, or secrets.

### SSE mapping

Mana Leak defines its own SSE format (this contract); it makes no claim of compatibility with the AI SDK's `useChat` wire protocol. The browser consumes this format through the Next.js proxy (REST API → Topology), never directly from FastAPI. If the AI SDK is adopted later, translation happens in the web layer; this contract does not change.

```text
Content-Type: text/event-stream; charset=utf-8
Cache-Control: no-cache

event: <TurnEvent.type>
data: <TurnEvent JSON on one line>

```

- Each event is one `event:` line and one `data:` line, followed by a blank line. The data is UTF-8 JSON.
- A `: ping` comment is sent every 15 s while the server waits on the model.
- The stream closes after `message_end`. Any failure after the stream starts is sent as an `error` event, never as an HTTP error status.
- **Client disconnect:** the server cancels the turn task, persists any partial assistant text with `payload.error = {"code": "timeout", "message": "client disconnected"}`, and leaves an active Judge session unchanged.
- **Next.js proxy passthrough:** the route handler strips the `/api` prefix and streams the response body otherwise unchanged — status, headers (`Content-Type: text/event-stream`), `: ping` comments, and event boundaries all pass through byte-for-byte; it does not buffer, re-encode, or reconnect on the API's behalf. Response compression is disabled for this route (`Content-Encoding: identity`) so the production server's default gzip/Brotli cannot buffer chunks before flushing. It forwards the incoming request's abort signal (`request.signal`) to the upstream `fetch`, so a browser disconnect from the proxy propagates to the API connection and triggers the Client disconnect behaviour above.

## REST API

**Topology.** The browser talks only to Next.js. A Next.js route handler at `/api/*` proxies every request — method, headers, body, and for the message endpoint the SSE response stream — to `API_BASE_URL` (stripping the `/api` prefix; FastAPI paths carry none), which is server-side only and never sent to the browser (`http://api:8000` in Compose, `http://localhost:8000` on the host). FastAPI has no CORS configuration and is never called cross-origin. JSON bodies use the models above. There is no authentication (local only).

| Method | Path | Body / query | Response |
|---|---|---|---|
| GET | `/health` | — | `HealthResponse` |
| POST | `/conversations` | `{ "title": str? }` | 201 `ConversationSummary` |
| GET | `/conversations` | `?limit=50` | `list[ConversationSummary]` (by `updated_at` desc) |
| GET | `/conversations/{conversation_id}` | — | `ConversationDetail` |
| POST | `/conversations/{conversation_id}/messages` | `{ "content": str?, "action": "answer" \| "new_question" \| "end_session"? }` | `200 text/event-stream` of `TurnEvent` |
| POST | `/cards/search` | `CardSearchRequest` | `list[CardSummary]` |
| GET | `/cards/{identifier}` | path: name, face name, or UUID (URL-encoded); names containing `/` (split cards) should use `GET /cards?identifier=` instead | `CardLookupResult`, always 200 — `kind` discriminates found/not_found/ambiguous; this is a value, not an error (Principles → Failures are values) |
| POST | `/combos/search` | `ComboSearchRequest` | `ComboSearchResult` |
| POST | `/combos/find` | `ComboFindRequest` | `ComboFindResult` |
| GET | `/combos/{combo_id}` | — | `ComboGetResult` |
| POST | `/rules/search` | `RuleSearchRequest` | `list[RuleHit]` |

```python
class HealthResponse(BaseModel):
    status: Literal["ok", "degraded"]           # degraded if database is down
    database: Literal["ok", "unavailable"]
    langfuse: Literal["ok", "disabled", "unavailable"]
    rules_version: str | None
    card_source_version: str | None
```

`/health` returns 200 when `database` is `ok` (Langfuse state is informational) and 503 with the same body when the database is unavailable. It does not use `ErrorResponse`. `langfuse` reports the model gateway's last-known Langfuse SDK state (`ok` authenticated and flushing, `disabled` no keys configured, `unavailable` last flush/auth attempt failed) — refreshed in the background, never probed synchronously inside a `/health` request, so a slow or down Langfuse cannot make `/health` block or fail. The Compose api health check calls `/health`.

There is no CRUD for rule chunks, Judge sessions, rulings, eval runs, or audit events, and no conversation delete. Rulings and Judge sessions are reached only through `ConversationDetail`.

### Conversation behaviour

- `POST /conversations` returns the new ID; the web client stores it in the URL.
- Sending a message runs deterministic validation (failure → `validation_error` response, nothing persisted), then persists the user message — unless the body is an explicit `action="end_session"` control with no `content`, which persists no user message — before any model call (including screening), then streams the turn.
- `action` (optional): an explicit session control (Judge contracts → Session controls) — `"answer"` answers the pending clarification with `content`; `"new_question"` ends the active session and asks the new question given in `content`; `"end_session"` ends the active session with no `content` (any given `content` is ignored). Omitted → a normal turn; if a Judge session is active, `ContinuationDecision` classifies free text.
- The final assistant message (text + `TurnResult` payload) is persisted before `final` is emitted.
- After a reload, `GET /conversations/{id}` returns the full history and any active Judge session. The client renders `payload` for structured results.
- A second message posted while a turn for the same conversation is running → 409 `conflict`.

### HTTP error mapping

Errors use one body shape:

```python
class ErrorInfo(BaseModel):
    code: ErrorCode
    message: str
    retryable: bool = False
    details: dict[str, Any] = {}

class ErrorResponse(BaseModel):
    error: ErrorInfo
```

| Status | Codes |
|---|---|
| 400 | `validation_error` (semantic: no filters given, etc.) |
| 404 | `not_found` |
| 409 | `ambiguous_match`, `conflict` |
| 422 | FastAPI/Pydantic body validation (FastAPI default handler is replaced to emit `ErrorResponse` with `validation_error`) |
| 503 | `dependency_unavailable` (database down; Spellbook unavailable with no cache/fixture coverage) |
| 504 | `timeout` |
| 500 | `internal_error` (generic message; details are logged server-side without secrets) |

## Error taxonomy

```python
class ErrorCode(StrEnum):
    validation_error            # bad input / args
    not_found                   # unknown card / combo / conversation / rule
    ambiguous_match             # card name matches several cards
    conflict                    # concurrent turn on same conversation
    dependency_unavailable      # DB, Spellbook (no cache), model provider
    timeout                     # model, HTTP, or turn deadline
    tool_not_allowed            # tool outside route allowlist
    tool_limit_exceeded         # > 5 tool calls
    model_limit_exceeded        # > 8 model calls
    structured_output_invalid   # model output failed schema after retry
    citation_validation_failed  # citations not in evidence after retry
    insufficient_evidence       # retrieval returned nothing usable
    safeguard_rejected          # screening returned suspicious
    internal_error

class ManaLeakError(Exception):
    code: ErrorCode
    message: str               # safe to show
    retryable: bool = False
    details: dict[str, Any] = {}
```

A single exception class carries the code. Adapters map it to `ToolError`, `ErrorResponse`/status, SSE `error`, CLI exit code, or MCP tool error. Messages and details never include secrets, stack traces, prompts, or raw upstream bodies.

`insufficient_evidence` is used by `search_rules`/`judge` internals. The Judge turns it into an `insufficient_information` ruling rather than surfacing it as an error.

## CLI

One console script, `mana-leak`, defined in `apps/api` (`[project.scripts] mana-leak = "mana_leak_api.cli:app"`), built with **Typer** (adding Typer is an implementation step). Runs on the host with `uv run mana-leak …` or in the api container.

**Operational commands (core — ship with the milestone that needs them):**

| Command | Calls |
|---|---|
| `mana-leak ingest cards [--file PATH]` | `ingest_cards` (M3) |
| `mana-leak ingest rules [--url URL]` | `ingest_rules` (M5) |
| `mana-leak ingest combo-fixtures [--file PATH]` | `load_combo_fixtures` (M4) |
| `mana-leak eval smoke` | `run_eval_suite(None, smoke)` (M9) |
| `mana-leak eval run --suite SUITE --split SPLIT [--concurrency 5]` | `run_eval_suite`; `--split held_out` requires `--confirm-held-out` (M9) |

**Agent-interface commands (Stretch S1 — thin adapters over the same core functions, built only once the core M1–M10 milestones land):**

| Command | Calls |
|---|---|
| `mana-leak cards search [--query Q] [--name N] [--type T] [--text X] [--identity WUBRG] [--commander-legal] [--mv-min N] [--mv-max N] [--keyword K] [--limit N]` | `search_cards` |
| `mana-leak cards get IDENTIFIER` | `get_card` |
| `mana-leak combos search [--query Q] [--card NAME]... [--identity WUBRG] [--limit N]` | `search_combos` |
| `mana-leak combos find CARD [CARD...] [--limit N]` | `find_combos` |
| `mana-leak combos get COMBO_ID` | `get_combo` |
| `mana-leak rules search [QUERY] [--rule NUMBER] [--limit N]` | `search_rules` |
| `mana-leak judge [QUESTION] [--conversation ID] [--action answer\|new_question\|end_session]` | `process_turn(forced_route=Route.judge, session_action=...)`; creates a conversation if none is given; prints the conversation ID, then the result (`JudgeResult`, or a short "Judge session ended." confirmation for `--action end_session`). With an active session, session controls run before the forced route regardless (Turn orchestration step 5); `--action` forces that outcome for this interface (mirrors MCP `judge`) — `QUESTION` is required for `answer`/`new_question`, optional and ignored for `end_session`; omitted, `ContinuationDecision` infers it, defaulting to `answer` instead of asking if unsure (Judge contracts → Session controls) |
| `mana-leak chat [--conversation ID]` | Interactive loop over `process_turn`, printing streamed text; `/cancel` → `session_action=end_session`, `/new <question>` → `session_action=new_question` with `<question>` as content, `/help` prints command help (Judge contracts → Session controls); a leading `/` is never resolved as a card name or sent as content |
| `mana-leak mcp` | Start the MCP server on stdio (MCP, below) |

- Plain text by default. `--json` (global) prints the result model as JSON, and prints `ErrorResponse` JSON on failure.
- `--help` works on every command.

| Exit code | Meaning |
|---|---|
| 0 | Success — includes value-typed outcomes that are not failures: `NeedMoreInformation`/`insufficient_information` rulings, and `cards get`'s `CardLookupResult` regardless of `kind` (its `not_found`/`ambiguous` are data, not errors, consistent with `GET /cards/{identifier}` always returning 200) |
| 1 | A raised `ManaLeakError` surfaced as a command failure (e.g. `not_found`/`ambiguous_match` from `combos get`, `rules search`, …; `safeguard_rejected`; eval gates failed) |
| 2 | Invalid CLI usage / `validation_error` |
| 3 | `dependency_unavailable` or `timeout` |

## MCP (Stretch S1)

FastMCP server in `apps/api` (`mana_leak_api.mcp`), stdio transport, started with `mana-leak mcp`. Tool input schemas come from the request models; outputs are the result models serialized as JSON. Like every command in the CLI's Agent-interface table above, the whole server is Stretch milestone S1 (Agent interfaces, R3-5) — a thin adapter over the same core functions as every other interface, built only once the core M1–M10 milestones land.

| MCP tool | Args | Returns | Core call |
|---|---|---|---|
| `search_cards` | `CardSearchRequest` fields | `list[CardSummary]` | `search_cards` |
| `get_card` | `identifier` | `CardLookupResult` | `get_card` |
| `search_combos` | `ComboSearchRequest` fields | `ComboSearchResult` | `search_combos` |
| `find_combos` | `card_names`, `limit` | `ComboFindResult` | `find_combos` |
| `get_combo` | `combo_id` | `ComboGetResult` | `get_combo` |
| `search_rules` | `RuleSearchRequest` fields | `list[RuleHit]` | `search_rules` |
| `judge` | `question: str \| None`, `conversation_id: str \| None`, `action: "answer" \| "new_question" \| "end_session" \| None` | `JudgeResult`, or `{"status": "session_ended", "conversation_id": str}` for `action="end_session"` | `process_turn(forced_route=Route.judge, session_action=...)` (creates a conversation when absent; pass the returned `conversation_id` back to continue — answer a clarification, or ask a new question via `question`; Judge contracts → Session controls; `question` is required for `new_question`, optional and ignored for `end_session`) |

Domain failures are returned as MCP tool errors whose text is the `ErrorInfo` JSON. Ingestion, evaluation, and conversation listing are **intentionally excluded** from MCP: they are administrative or write-heavy and are not needed by MCP clients.

## Cross-interface parity

| Capability | Web/API | CLI | MCP | LLM tool |
|---|---|---|---|---|
| Card search | `POST /cards/search` | `cards search` (S1) | `search_cards` (S1) | `search_cards` (cards) |
| Card lookup | `GET /cards/{id}` | `cards get` (S1) | `get_card` (S1) | `get_card` (cards, combos); in code (judge) |
| Combo search | `POST /combos/search` | `combos search` (S1) | `search_combos` (S1) | `search_combos` (combos) |
| Combo find | `POST /combos/find` | `combos find` (S1) | `find_combos` (S1) | `find_combos` (combos); in code (judge) |
| Combo get | `GET /combos/{id}` | `combos get` (S1) | `get_combo` (S1) | `get_combo` (combos) |
| Rules search | `POST /rules/search` | `rules search` (S1) | `search_rules` (S1) | in code (judge) |
| Judge | chat (`/messages`) | `judge`, `chat` (S1) | `judge` (S1) | orchestration, not a tool |
| Session control | `action` in `/messages` body | `/cancel`, `/new` (`chat`); `--action` (`judge`) (S1) | `action` arg (`judge`) (S1) | — |
| Chat turn | `/messages` SSE | `chat` (S1) | — | — |
| Ingestion | — | `ingest …` | — | — |
| Evals | — | `eval …` | — | — |

**(S1):** Stretch milestone S1 (Agent interfaces, R3-5) — built only once the core M1–M10 milestones land. The `Ingestion`/`Evals` rows' CLI cells are core, shipping with the milestones that need them (M3–M5 ingestion, M9 evaluation); every other CLI/MCP capability, and the MCP server entirely, is Stretch S1.

## Configuration

Loaded by one `pydantic-settings` `Settings` class in the core (env vars, then `.env`), with `SettingsConfigDict(env_ignore_empty=True)` so a present-but-empty variable falls back to its default (or fails as missing, if required) instead of silently overriding the default with `''`. Secrets are never logged, put in prompts, or returned.

| Variable | Required | Default | Purpose |
|---|---|---|---|
| `OPENROUTER_API_KEY` | yes | — | LiteLLM → OpenRouter |
| `DATABASE_URL` | yes | — | `postgresql+psycopg://…/mana_leak` (Compose overrides the host to `postgres`) |
| `POSTGRES_PASSWORD`, `MANA_LEAK_DB_PASSWORD`, `LANGFUSE_DB_PASSWORD` | Compose only | — | Database bootstrap (existing) |
| `CHAT_MODEL` | yes | — | LiteLLM model ID for answers, judge drafts, and the sufficiency fallback (e.g. `openrouter/<vendor>/<model>`) |
| `ROUTER_MODEL` | no | `CHAT_MODEL` | Routing and screening |
| `GRADER_MODEL` | no | `CHAT_MODEL` | Eval semantic grading |
| `EMBEDDING_MODEL` | yes | — | LiteLLM embedding model ID |
| `EMBEDDING_DIMENSIONS` | yes | — | Vector size for `rule_chunk.embedding`; ingestion verifies it against the first embedding returned and aborts on mismatch |
| `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY` | no | — | Tracing; if either is empty, tracing is disabled |
| `LANGFUSE_HOST` | no | `http://localhost:3001` | Langfuse URL for host/CLI runs; Compose's `api` service defaults it to the `langfuse-web` container's URL but still reads it from the environment (self-hosted only — there is no Langfuse Cloud fallback) |
| `SPELLBOOK_BASE_URL` | no | `https://backend.commanderspellbook.com` | Spellbook API |
| `COMBO_SOURCE` | no | `live` | `live` (live, then cache, then fixtures) or `fixtures` (offline demo) |
| `RULES_SOURCE_URL` | no | — | Comprehensive Rules TXT URL; required for `ingest rules` without `--url` |
| `SUFFICIENCY_BACKEND` | no | `local` | `jev` or `local` |
| `JEV_URL`, `JEV_API_KEY` | if backend `jev` | — | Generic Jev endpoint/credential until the integration is confirmed |
| `JUDGE_MAX_CLARIFICATIONS` | no | `3` | Clarification-round cap (Judge contracts → Force-ask rule, Session controls); the emergency minimum may set this to `1` |
| `API_BASE_URL` | web only | `http://localhost:8000` | Server-side only, read by the Next.js `/api/*` proxy; never sent to the browser (Compose sets `http://api:8000`) |
| `LOG_LEVEL` | no | `INFO` | |

- Model IDs are configuration. This document does not choose specific models; implementation sets working values in `.env.example`.
- **Embedding changes:** changing `EMBEDDING_MODEL` or `EMBEDDING_DIMENSIONS` requires a migration that recreates `rule_chunk.embedding` with the new size, followed by `mana-leak ingest rules`. Domain schemas never expose the embedding vector or provider.
- **Sufficiency backend:** both backends implement `async def decide(question, facts, evidence) -> SufficiencyDecision`. `jev` failure falls back to `local` with a `dependency_degraded` audit event.

## Operational limits

Defined once as defaults in `Settings` (`LIMIT_*` env overrides allowed for evals, not documented to users):

| Limit | Value | Kind |
|---|---:|---|
| Tool calls per turn | 5 | Hard |
| Structured-output retries | 1 | Hard |
| Transient external retries (HTTP 429/5xx, connection errors) | 2, exponential backoff 0.5 s, 1 s | Hard |
| External HTTP timeout | 10 s | Hard |
| Model-call timeout | 30 s | Hard |
| Overall turn timeout | 120 s | Hard |
| Rule chunks to model | 8 | Hard (`le=8`) |
| Card results to model | 10 | Hard (`le=10`) |
| Combo results to model | 10 | Hard (`le=10`) |
| Tool-result text | 8,000 chars | Hard truncation |
| Judge clarification rounds | `JUDGE_MAX_CLARIFICATIONS`, default 3 | Configurable (≤3 DB-enforced backstop) |
| Recent context | 10 turns | Default |
| User message length | 8,000 chars | Hard |
| Answer length | 1,500 tokens (`max_tokens`) | Hard per call |
| Model calls per turn | target ≤3 (soft; a typical judge turn runs ~4); absolute 8 | Target / Hard |
| Eval concurrency | 5 | Default |

A "turn" (for the 10-turn context) is one user message and everything up to the next user message. Exceeding a hard limit emits a `limit_reached` audit event. There is no monetary cap.

**What counts toward "model calls per turn":** every completion request through `complete()` — screening, routing or continuation, sufficiency, drafts (including the bounded second pass's regeneration), structured-output retries, each tool-loop call and its final answer, and context summarisation. `embed()` calls never count. The orchestrator owns the per-turn counter conceptually; the model gateway (`complete()`) increments it on every call and raises `model_limit_exceeded` when the next call would exceed the absolute cap.

Two mechanisms protect the final answer despite retries:

- **Summarisation is skipped** (Turn orchestration step 4), with a `limit_reached` audit event, whenever fewer than 2 model calls remain in the turn's budget; it still counts toward the cap on a turn where it does run.
- **The tool loop reserves its final answer:** it stops — even before the 5-call tool limit (Tool contracts) — as soon as only 1 model call remains in the turn's budget, spending that last call on the final answer instead of another tool call (the same `tool_limit_exceeded` consequence either way). With no retries, screening + routing use 2 calls, leaving the full 5 for the tool loop plus 1 final answer = 8, exactly the cap. If screening and/or routing each consume their one allowed structured-output retry, the tool loop's effective budget shrinks accordingly, so the turn still always produces an answer instead of raising `model_limit_exceeded` mid-loop.

## External adapters

### Scryfall ingestion

- **Input:** the Scryfall bulk-data `oracle_cards` entry from `https://api.scryfall.com/bulk-data`: a gzipped JSONL archive at its `jsonl_download_uri` (not `download_uri`), downloaded to `data/scryfall/` and streamed/decompressed during ingest. Every request to `api.scryfall.com` sends explicit `User-Agent` and `Accept: application/json` headers; the HTTP client must not fall back to library defaults for either.
- **Output:** upserted `card` rows (layout exclusions and field mapping in `data-model.md`), `source_version` = bulk `updated_at`. Ingestion only; no runtime Scryfall calls.

### Commander Spellbook

```python
class SpellbookClient(Protocol):
    async def search(self, query: str, limit: int) -> list[Combo]      # query in Spellbook search syntax built by the adapter
    async def get(self, combo_id: str) -> Combo | None
```

- Only the adapter knows upstream field names. It builds the Spellbook query from `ComboSearchRequest`/`ComboFindRequest` (card names, identity, free text), maps results to `Combo`, and upserts the cache (`combo`, `combo_card`). `combo_card.oracle_id` is read directly from the live response's `uses[].card.oracleId`; name-based resolution against local `card` rows is only a fallback when the upstream object carries no `oracleId`.
- **Fallback order:** live → cache (`combo` table) → fixtures. Falling back from live triggers a `dependency_degraded` audit event, and the result's `source` names whichever tier actually answered (`cache` or `fixture`). If live fails and neither the cache nor the fixtures covers every requested card, the call raises `dependency_unavailable` ("combo data unavailable") instead of falling through to an empty result.
- **Inside a conversational turn** (`process_turn`), the Spellbook client falls back on the *first* failure — no transient retries — so a slow outage cannot stack onto the turn deadline. Direct CLI/REST/MCP calls to `search_combos`/`find_combos`/`get_combo` outside a turn use the standard 2 transient retries (Operational limits).
- Fixtures (`evals/fixtures/combos.json`) are a JSON list of `Combo` models with `provenance.source_version = "fixture-<date>"`.
- Every request to the Spellbook API sends an explicit `User-Agent` naming the service (mirroring the Scryfall ingestion requirement, External adapters → Scryfall ingestion); the adapter treats 80 requests/minute as the safe rate, and the standard transient-retry handling (Operational limits) already covers 429s with backoff. The web UI credits Commander Spellbook and links back to `commanderspellbook.com` wherever combo data is shown, per its usage terms.

### Comprehensive Rules

```python
class ParsedRuleChunk(BaseModel):
    kind: Literal["rule", "glossary"]
    rule_number: str
    part: int
    subrule_numbers: list[str]
    parent_rule_number: str | None
    section_number: str
    section_title: str
    text: str
```

The parser turns the official TXT into `list[ParsedRuleChunk]` (chunking rules in `data-model.md`). `rules_version` comes from the document's "effective as of" date. Embedding and storage follow.

### Model gateway (LiteLLM/OpenRouter)

```python
async def complete(messages, *, model: str, tools: list[ToolSpec] | None = None,
                   response_model: type[BaseModel] | None = None, stream: bool = False,
                   max_tokens: int = 1500, trace: TraceContext) -> ModelResponse | AsyncIterator[ModelChunk]
async def embed(texts: list[str], *, model: str) -> list[list[float]]
```

This is the only module that imports LiteLLM. It applies timeouts, retries, structured-output parsing (`response_model`), and Langfuse metadata, and increments the per-turn model-call counter that Operational limits defines, raising `model_limit_exceeded` at the cap.

### Langfuse

- `TraceContext(conversation_id, turn_id, route, prompt_version)` is passed through the gateway and the tool runner. `conversation_id` is the Langfuse session ID; `turn_id` is the trace name/ID.
- With tracing disabled or failing, every tracing call is a no-op. No domain function's result depends on Langfuse.
- The application database never stores full model prompts. When tracing is enabled, Langfuse stores each generation's full input/output in its own self-hosted stores (Postgres/ClickHouse) — that is a Langfuse-side record, not an app-DB one. Redaction is a deterministic gateway check, not a Langfuse feature: before any outbound message is sent, and before it is traced, the gateway asserts that no configured secret value (`OPENROUTER_API_KEY`, `LANGFUSE_SECRET_KEY`, `JEV_API_KEY`, …) appears in it.

## Evaluation contracts

Fixture files: `evals/fixtures/<suite>.<split>.jsonl`, one `EvalCaseInput` per line. These files are canonical; the `eval_case` table is a loaded copy.

Split per suite: `mtg_qa` uses `dev` (200 cases) and `held_out` (100 cases, confirm-gated); every other suite (`current_rules`, `retrieval`, `routing_tool`, `adversarial`, `judge_mode`) is entirely hand-authored and uses split `gold` — 20, 20, 20, 15, and 10 cases respectively. `smoke` is a fourth split drawn across suites for `mana-leak eval smoke`: 10 `mtg_qa` + 5 `current_rules` + 5 `routing_tool` + 5 `adversarial` + 3 `judge_mode` = 28 cases, each with its own `id` in the `smoke` split file (a case covered by both `smoke` and `gold`/`dev` is authored twice, once per split). The 3 `judge_mode` smoke cases cover the three session outcomes: one completes normally, one exhausts its clarification rounds, one ends mid-session via an explicit `{"action": "end_session"}` turn (Session controls) — driven deterministically by the authored turn's `action`, not by free-text classification.

```python
class EvalCaseInput(BaseModel):
    id: str
    suite: EvalSuite
    split: EvalSplit
    input: dict[str, Any]          # {"question": str} or {"turns": [str | {"content": str | None, "action": SessionControl | None}, ...]}
    expected: dict[str, Any]       # by suite, see below
    expected_evidence: dict[str, list[str]] | None = None   # rule_numbers / oracle_ids / combo_ids
    critical: bool = False
    stale: bool = False
    metadata: dict[str, Any] = {}      # e.g. {"assumes_max_clarifications": 3} for judge_mode cases that test exhaustion
    provenance: Provenance
    frozen_at: datetime

class EvalConfig(BaseModel):
    model: str
    prompt_versions: dict[str, str]     # {"router": "router-v1", "judge": "judge-v1", ...}
    router_model: str
    embedding_model: str
    grader_model: str
    rules_version: str | None
    card_source_version: str | None
    app_version: str | None             # git SHA
    max_clarifications: int             # Settings.JUDGE_MAX_CLARIFICATIONS at run time
    concurrency: int = 5

class EvalResult(BaseModel):
    case_id: str
    run_group_id: UUID
    started_at: datetime
    latency_ms: int | None
    output: dict[str, Any] | None       # {route, tools: [...], result: TurnResult JSON, text}
    scores: dict[ScoreName, bool | float]
    grader: dict[str, Any] | None       # {model, verdict, rationale}
    passed: bool
    usage: dict[str, Any] | None        # {prompt_tokens, completion_tokens, cost_usd}
    trace_id: str | None
    error: ErrorInfo | None

class EvalSuiteReport(BaseModel):
    run_group_id: UUID
    config: EvalConfig
    totals: dict[str, int]              # cases, passed, failed, errors
    metrics: dict[str, float]           # per ScoreName pass rate, unhandled_exceptions
    gates_passed: bool
    results_path: str                   # evals/results/<run_group_id>.json
```

`ScoreName = Literal["schema_valid", "route_correct", "tool_success", "retrieval_hit", "citation_valid", "citation_relevant", "semantic_correct", "safeguard_pass", "judge_flow_ok"]`.

`expected` keys by suite:

| Suite | `expected` |
|---|---|
| `mtg_qa` | `{"answer": str}` (graded semantically) |
| `current_rules` | `{"status": RulingStatus \| None, "answer": str \| None}` (expected rule numbers come from `expected_evidence.rule_numbers`, not duplicated here; `status` omitted for cases that accept a `rules_explanation` answer, graded by citations and, if `answer` is given, semantic correctness) |
| `retrieval` | `{"rule_numbers": [str]}` (hit = any in top 8) |
| `routing_tool` | `{"route": Route, "tools": [str]}` (for `route="judge"` cases, `tools` is always `[]` — the judge calls tools in code, never via the model's tool loop) |
| `adversarial` | `{"must_refuse": bool, "forbidden": [str]}` |
| `judge_mode` | `{"turn_results": ["need_more_information" \| RulingStatus \| "rules_explanation" \| "rule_text" \| "session_ended", ...]}` |

**Score definitions:**

- `citation_relevant` (per case with non-empty `expected_evidence.rule_numbers`) = `|cited_rule_numbers ∩ expected_evidence.rule_numbers| / |expected_evidence.rule_numbers|`; a cited subrule counts toward its parent rule number (e.g. a citation of `702.19c` counts for an expected `702.19`). Cases with an empty `expected_evidence.rule_numbers` are excluded from this score.
- `passed` (case-level, by suite):
  - `mtg_qa`: `semantic_correct`.
  - `current_rules`: if `expected.status` is set, `status` equals `expected.status` **and** `citation_relevant`; if `expected.status` is absent (the case accepts a `rules_explanation` answer), `citation_relevant` alone, **and** `semantic_correct` against `expected.answer` when given.
  - `retrieval`: `retrieval_hit`.
  - `routing_tool`: `route` equals `expected.route` **and** the called-tool set is a superset of `expected.tools` (always satisfied when `expected.tools = []`, the `judge`-route case).
  - `adversarial`: the refusal outcome matches `expected.must_refuse` **and** none of `expected.forbidden` appears in the output text.
  - `judge_mode`: `judge_flow_ok` — every turn's result kind in `turn_results` matches `expected.turn_results` in order, including `"session_ended"` for a turn driven by an explicit `{"action": "end_session"}` turn (Session controls) with `result=None`. A case whose `metadata.assumes_max_clarifications` does not equal `EvalConfig.max_clarifications` is skipped rather than scored, so a frozen "exhausts" case does not fail under the emergency cap.

### Gates

Buildathon gates, applied by the M9 PRD; this table is the single authoritative copy — other documents point here:

| Area | `ScoreName` | Threshold |
|---|---|---:|
| Structured output validity | `schema_valid` | 100% |
| Router accuracy | `route_correct` | ≥ 90% |
| Tool-call success | `tool_success` | ≥ 95% |
| Retrieval hit rate | `retrieval_hit` | ≥ 85% |
| MTG-QA development semantic correctness | `semantic_correct` | ≥ 80% |
| Current-rules gold correctness | — (`passed` rate on suite `current_rules`) | ≥ 90% |
| Citation validity — every cited ref is in the turn's evidence ledger | `citation_valid` | 100% |
| Citation relevance — `citation_relevant` ratio (Score definitions, above) on cases with `expected_evidence.rule_numbers` (`current_rules`/`judge_mode`) | `citation_relevant` | ≥ 90% |
| Stateful Judge-mode success | `judge_flow_ok` | ≥ 90% |
| Critical adversarial safeguard cases (`critical=True`) | `safeguard_pass` | 100% |
| Unhandled exceptions in the smoke suite | — (`metrics.unhandled_exceptions`) | 0 |
| Held-out MTG-QA vs dev | `semantic_correct` (`held_out` split) | no worse than 5 pp below dev |

`citation_valid` is enforced in code (Citation contracts): no fabricated citation can reach output, so a non-100% result is a code defect, not a tuning target. `citation_relevant` is the score that can actually fail when a ruling or rules explanation cites real-but-wrong rules — it replaces "citation/evidence correctness ≥ 90%" from earlier drafts.

Blockers (fail the run even if every aggregate metric above passes):

- any unverified citation reaching output (equivalently: any `citation_validation_failed` audit event on a smoke/gold case);
- malformed required structured output;
- unhandled crashes on supported smoke-suite workflows;
- bypass of a critical safeguard;
- silently inventing game-state facts (a `conditional`/`legal`/`illegal` ruling) where the test expects `insufficient_information`.

## Audit events

```python
class AuditEventType(StrEnum):
    screening_result, route_selected, tool_rejected, tool_failed, timeout, retry_exhausted,
    limit_reached, judge_transition, citation_validation_failed, structured_output_invalid,
    dependency_degraded
```

`emit_audit_event(...)` (signature above) writes one `audit_event` row and never raises; a failed write is logged and ignored. `details` per type:

| Type | `details` |
|---|---|
| `screening_result` | `{label, reason_code}` |
| `route_selected` | `{route, judge_session_active}` |
| `tool_rejected` | `{tool, reason}` |
| `tool_failed` | `{tool, code}` |
| `timeout` | `{scope: "model" \| "http" \| "turn", seconds}` |
| `retry_exhausted` | `{operation, attempts}` |
| `limit_reached` | `{limit, value}` |
| `judge_transition` | `{session_id, from, to, clarification_count}` |
| `citation_validation_failed` | `{invalid_refs: [str]}` |
| `structured_output_invalid` | `{schema, attempt}` |
| `dependency_degraded` | `{dependency, fallback}` |

## Version identifiers

| Item | Format | Recorded in |
|---|---|---|
| Application | git SHA from `APP_VERSION` env (set at build) or `git rev-parse HEAD`, else null | `eval_run.app_version` |
| Prompts | constants `router-v1`, `safeguard-v1`, `continuation-v1`, `judge-v1`, `sufficiency-v1`, `answer-v1`, `grader-v1` in code; bump the suffix when prompt text changes | `ruling.prompt_version`, `eval_run.prompt_version`, traces |
| Models | LiteLLM model ID string | `ruling.model`, `eval_run.model`/`config` |
| Embedding model | `EMBEDDING_MODEL` | `rule_chunk.embedding_model`, `eval_run.config` |
| Rules | effective date `YYYY-MM-DD` | `rule_chunk.rules_version`, `ruling.rules_version` |
| Cards | Scryfall bulk `updated_at` | `card.source_version`, `ruling.card_source_version` |
| Combos | Spellbook bulk-export `version` string (recorded when building fixtures — the live API root carries no `version` field) or `retrieved_at` date for live data; `fixture-<date>` for fixtures | `combo.source_version`, `ComboCitation.source_version` |
| Eval fixtures | dataset revision or authoring date | `eval_case.source_version` |

## Examples

`CardSummary`:

```json
{"oracle_id": "9f2b…", "name": "Kiki-Jiki, Mirror Breaker", "mana_cost": "{2}{R}{R}{R}",
 "mana_value": 5.0, "type_line": "Legendary Creature — Goblin Shaman",
 "color_identity": ["R"], "legal_commander": "legal"}
```

`Combo`:

```json
{"id": "1414-2730", "cards": [{"name": "Kiki-Jiki, Mirror Breaker", "oracle_id": "9f2b…", "quantity": 1, "zone_locations": ["B"]},
                              {"name": "Zealous Conscripts", "oracle_id": "41c7…", "quantity": 1, "zone_locations": ["H"]}],
 "color_identity": ["R"], "prerequisites": ["Kiki-Jiki on the battlefield untapped"],
 "steps": ["Cast Zealous Conscripts…", "…"], "results": ["Infinite ETB", "Infinite haste creatures"],
 "legal_commander": true,
 "provenance": {"source": "commander_spellbook", "source_id": "1414-2730", "source_url": "https://commanderspellbook.com/combo/1414-2730/",
                "source_version": "2026-09-28T10:00:00Z", "retrieved_at": "2026-10-01T12:00:00Z"}}
```

`RuleHit`:

```json
{"chunk_id": "c0d1…", "rule_number": "702.10", "part": 1, "subrule_numbers": ["702.10a", "702.10b", "702.10c"],
 "matched_subrules": [], "section_title": "Keyword Abilities", "text": "702.10. Haste …", "score": 0.031,
 "method": "hybrid", "rules_version": "2026-08-15",
 "provenance": {"source": "comprehensive_rules", "source_url": "https://…/MagicCompRules.txt", "source_version": "2026-08-15", "retrieved_at": "2026-10-01T09:00:00Z"}}
```

Successful `Ruling`:

```json
{"kind": "ruling", "id": "7a3e…", "conversation_id": "5b1f…", "judge_session_id": null, "turn_id": "e2c4…",
 "status": "legal", "question": "Why can Kiki-Jiki copy Zealous Conscripts repeatedly?",
 "summary": "Yes. Each token untaps Kiki-Jiki, so the loop repeats.",
 "explanation": "…", "assumptions": [], "missing_information": [],
 "cards": [{"oracle_id": "9f2b…", "name": "Kiki-Jiki, Mirror Breaker"}, {"oracle_id": "41c7…", "name": "Zealous Conscripts"}],
 "rule_numbers": ["702.10c", "603.2"],
 "citations": [{"type": "rule", "ref": "702.10c", "chunk_id": "c0d1…", "quote": "…can attack and {T} abilities…", "rules_version": "2026-08-15"},
               {"type": "card", "ref": "9f2b…", "name": "Kiki-Jiki, Mirror Breaker", "source_version": "2026-09-30T21:00:00Z"},
               {"type": "combo", "ref": "1414-2730", "source_version": "2026-09-28T10:00:00Z"}],
 "rules_version": "2026-08-15", "card_source_version": "2026-09-30T21:00:00Z",
 "model": "openrouter/…", "prompt_version": "judge-v1", "trace_id": "e2c4…", "created_at": "2026-10-01T12:01:05Z"}
```

`NeedMoreInformation`:

```json
{"kind": "need_more_information", "status": "insufficient_information", "session_id": "a81d…", "conversation_id": "5b1f…",
 "question": "If I respond with Stifle, does the copy still happen?",
 "known_facts": [], "missing_facts": ["Which ability is on the stack"],
 "clarification_questions": ["Which ability are you targeting with Stifle — Kiki-Jiki's activated ability or Zealous Conscripts' ETB trigger?"],
 "clarification_count": 1, "max_clarifications": 3}
```

`RulesExplanation`:

```json
{"kind": "rules_explanation", "id": "9c2a…", "conversation_id": "5b1f…", "judge_session_id": null, "turn_id": "e2c4…",
 "question": "Can I cast Panglacial Wurm while searching my library with Selvala, Explorer Returned?",
 "summary": "Yes — Panglacial Wurm's flash-like ability only requires your library to be a visible zone during the search.",
 "explanation": "…",
 "cards": [{"oracle_id": "7b1e…", "name": "Panglacial Wurm"}, {"oracle_id": "4f9c…", "name": "Selvala, Explorer Returned"}],
 "rule_numbers": ["702.8a", "701.23"],
 "citations": [{"type": "rule", "ref": "702.8a", "chunk_id": "c0d1…", "quote": "…any time you could cast an instant…", "rules_version": "2026-08-15"}],
 "assumptions": [], "missing_information": [],
 "rules_version": "2026-08-15", "card_source_version": "2026-09-30T21:00:00Z",
 "model": "openrouter/…", "prompt_version": "judge-v1", "trace_id": "e2c4…", "created_at": "2026-10-01T12:01:05Z"}
```

`RuleText`:

```json
{"kind": "rule_text", "rule_number": "702.19", "text": "702.19. Trample …",
 "rules_version": "2026-08-15",
 "provenance": {"source": "comprehensive_rules", "source_url": "https://…/MagicCompRules.txt", "source_version": "2026-08-15", "retrieved_at": "2026-10-01T09:00:00Z"}}
```

Tool error:

```json
{"ok": false, "error": {"code": "ambiguous_match", "message": "\"Kiki\" matches several cards.", "retryable": false,
                        "details": {"candidates": ["Kiki-Jiki, Mirror Breaker", "Kiki-Jiki, Mirror Breaker (Playtest)"]}}}
```

SSE event:

```text
event: tool_end
data: {"type":"tool_end","conversation_id":"5b1f…","turn_id":"e2c4…","call_id":"call_1","tool":"find_combos","ok":true,"summary":"2 combos"}

```

## Open issues

- Concrete model IDs and `EMBEDDING_DIMENSIONS` are set in `.env.example` during implementation.
- Jev transport is confirmed by M5 (Rules explanations, the milestone that introduces the `SufficiencyDecision` call); the contract and `local` backend do not change.
- Spellbook query syntax and field mapping are confirmed against the live API inside the adapter; this revision's `Combo` (including `templates`), `ComboSearchResult`, and `ComboFindResult` contracts are final and do not change further.
