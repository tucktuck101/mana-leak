# Contracts

## Purpose

This document defines the interfaces Mana Leak is implemented against: domain models, service signatures, tool schemas, Judge results, turn events, REST/SSE, CLI, MCP, configuration, limits, and errors. Where it names a field that is persisted, the name matches `data-model.md`. Responsibilities and flows are in `architecture.md`.

All Python contracts are Pydantic v2 models in `packages/core` (module `mana_leak_core.contracts`). Adapters import them; they never redefine them.

## Principles

- **One shared core.** Every capability has one core function. FastAPI, CLI, MCP, and LLM tools call that function and only translate inputs and outputs.
- **Pydantic at boundaries.** Model outputs, tool arguments and results, API bodies, Judge state, and route/screening decisions are typed models. Unstructured `dict`s appear only inside documented `details`/`metadata` fields.
- **Evidence carries provenance.** Domain records from external sources carry `Provenance`. Rulings carry citations that code has resolved against retrieved evidence.
- **Models propose, code decides.** A model's output is a *draft* type (`RouteDecision`, `RulingDraft`, tool-call arguments). Code validates it and builds the final type, filling IDs, versions, and provenance itself. Models never supply IDs, versions, or provenance values.
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
    color_identity: list[str]
    results: list[str]                    # "produces" lines; summary for display
    legal_commander: bool | None = None

class Combo(BaseModel):
    id: str
    cards: list[ComboCard]
    color_identity: list[str]
    prerequisites: list[str]
    steps: list[str]                      # ordered
    results: list[str]
    legal_commander: bool | None = None
    provenance: Provenance
    from_cache: bool                      # True when served from cache/fixtures instead of live

class ComboSearchRequest(BaseModel):
    query: str | None = None              # free text passed to Spellbook search (e.g. a result like "infinite mana")
    card_names: list[str] = []            # combos must include all of these
    color_identity: list[str] | None = None   # combo identity must be a subset
    limit: int = Field(default=5, ge=1, le=10)

class ComboFindRequest(BaseModel):
    card_names: list[str] = Field(min_length=1, max_length=10)
    limit: int = Field(default=5, ge=1, le=10)

class ComboGetRequest(BaseModel):
    combo_id: str
```

- **`search_combos`** is exploratory: free text and/or filters, returning `ComboSummary` items. At least one of `query` or `card_names` is required.
- **`find_combos`** answers the primary use case "which known combos include these cards?" It returns full `Combo` records containing **all** supplied cards. Before querying, card names are resolved through `get_card`; unresolved or ambiguous names make the whole call fail with `not_found`/`ambiguous_match` and list the names involved, so it never runs a partial search silently.
- **`get_combo`** returns one `Combo` by ID.
- A `Combo` object is only ever built by the Spellbook adapter or the fixture loader. Model text describing a combo is never parsed into `Combo`.

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

- **rule:** `ref` must equal a ledger hit's `rule_number` or be in its `subrule_numbers`, and `quote` (if given) must be a substring of that hit's `text`.
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
    missing_facts: list[str] = Field(default=[], max_length=5)   # each ≤200 chars; empty iff sufficient
    reason: str | None = Field(default=None, max_length=200)

class RulingDraft(BaseModel):            # model-produced; never shown without validation
    model_config = ConfigDict(extra="forbid")
    status: RulingStatus
    summary: str = Field(max_length=400)
    explanation: str = Field(max_length=6000)
    assumptions: list[str] = Field(default=[], max_length=5)
    missing_information: list[str] = Field(default=[], max_length=5)
    citations: list[CitationRef] = Field(max_length=12)

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
    cards: list[CardRef]                  # persisted as ruling.card_oracle_ids
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
    max_clarifications: int = 3

JudgeResult = Annotated[Ruling | NeedMoreInformation, Field(discriminator="kind")]
```

**Validation rules (code):**

- `status=insufficient_information` requires a non-empty `missing_information`; other statuses require at least one rule citation.
- `legal`, `illegal`, and `conditional` require every involved card to be cited.
- `conditional` requires non-empty `assumptions` or an explanation of the condition.
- Draft validation failure (schema or citation) → one retry with the validation error appended. A second failure → a `Ruling` with `status=insufficient_information`, a fixed safe `summary`, empty citations, and `missing_information=["A supported ruling could not be produced from the available evidence."]`, plus a `citation_validation_failed` or `structured_output_invalid` audit event.

**Judge flow (code-owned):**

1. Look up the involved cards (named in the question or session) and retrieve ≤8 rule chunks, adding the results to the ledger.
2. If any required card lookup is `not_found` or `ambiguous`, the missing piece becomes a missing fact (e.g. "Which card do you mean by 'Kiki'?").
3. Call `SufficiencyDecision` with the question, known facts, and evidence summaries.
4. If `sufficient=false` and `clarification_count < 3`: upsert the active `judge_session`, increment `clarification_count`, and return `NeedMoreInformation`.
5. If `sufficient=false` and `clarification_count == 3`: set the session to `exhausted` and return a `Ruling` (`insufficient_information`) that lists the missing facts.
6. If sufficient: generate a `RulingDraft`, validate it, persist the `Ruling` (with `judge_session_id` if a session exists), and set the session to `completed`.

A `NeedMoreInformation` is not persisted as a `ruling` row; it is persisted as `judge_session` state plus the assistant message.

**Session continuation:** when a conversation has an `active` session, the next user message is treated as a clarification answer. It is added to `known_facts`, and the judge re-runs from step 1 with the original question. The session is set to `abandoned` (and the turn routes normally) only when the router, given the active-session context, returns a route other than `judge`. `RouteDecision.route == judge` continues the session.

## Router contract

```python
class RouteDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    route: Route
    reason: str | None = Field(default=None, max_length=120)   # short label for traces, not reasoning
```

- One model call (`ROUTER_MODEL`, prompt `router-v1`), using structured output.
- If the output is invalid after 1 retry, the route defaults to `other` (`structured_output_invalid` audit event).

| Route | Allowed LLM tools | Max tool calls |
|---|---|---:|
| `cards` | `search_cards`, `get_card` | 5 |
| `combos` | `get_card`, `search_combos`, `find_combos`, `get_combo` | 5 |
| `judge` | none (judge orchestrates retrieval in code; see below) | — |
| `other` | none | 0 |

**Judge route tools.** Architecture allows card lookup, rules search, and combo find on the judge route. The contract fixes how: the **judge service calls `get_card`, `search_rules`, and `find_combos` directly in code** (steps 1–2 above), rather than exposing them to a free-form tool loop. This uses the same core functions and allowlist, with a lower model-call count and a deterministic evidence set. `find_combos` is called only when the question names two or more cards or the conversation's last assistant result was a combo.

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

1. **Deterministic validation (no model call, nothing persisted):** malformed body, empty message, or more than 8,000 characters → rejected before persistence: REST returns `ErrorResponse` (`validation_error`, status per HTTP error mapping) without opening a stream; CLI/MCP return the same error. Control characters are stripped.
2. **Persist the user message** (valid messages only).
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
| `search_combos` | Search Commander Spellbook for known combos by description/result and/or included cards. | `ComboSearchRequest` | `list[ComboSummary]` | combos | 10 |
| `find_combos` | List known Commander Spellbook combos that include ALL the given cards. | `ComboFindRequest` | `list[Combo]` | combos | 10 |
| `get_combo` | Get one known combo's pieces, prerequisites, steps, and results by its Spellbook ID. | `ComboGetRequest` | `Combo` | combos | 1 |
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
async def search_combos(req: ComboSearchRequest) -> list[ComboSummary]
async def find_combos(req: ComboFindRequest) -> list[Combo]
async def get_combo(req: ComboGetRequest) -> Combo                     # not_found if unknown

# rules
async def search_rules(req: RuleSearchRequest) -> list[RuleHit]

# judge
async def judge(
    question: str,
    conversation_id: UUID,
    turn_id: UUID,
    ledger: EvidenceLedger | None = None,
) -> JudgeResult

# conversations
async def create_conversation(title: str | None = None) -> ConversationSummary
async def list_conversations(limit: int = 50) -> list[ConversationSummary]
async def get_conversation(conversation_id: UUID) -> ConversationDetail        # not_found
async def append_message(conversation_id: UUID, turn_id: UUID, role: MessageRole,
                         content: str, payload: dict | None = None,
                         route: Route | None = None) -> MessageOut
async def get_active_judge_session(conversation_id: UUID) -> JudgeSessionState | None
async def build_context(conversation_id: UUID) -> ModelContext                # summary + last 10 turns + active Judge facts

# orchestration
async def process_turn(conversation_id: UUID, user_message: str) -> AsyncIterator[TurnEvent]   # async generator

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
    payload: dict[str, Any] | None = None    # TurnResult JSON for assistant; tool summary for tool
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

`judge()` called outside `process_turn` (CLI `judge`, MCP `judge`) applies screening itself and creates its own ledger. Inside `process_turn` it reuses the turn's ledger.

## Turn orchestration

`async def process_turn(conversation_id, user_message)` is an async generator and the only conversational entrypoint. Web (via SSE), CLI `chat`, and MCP `judge` (through `judge()`) use it or its components. Adapters run deterministic validation (step 1) before calling it, so an invalid request never opens a stream. Sequence:

1. Deterministic validation (malformed, empty, > 8,000 chars). Failure → `validation_error`; nothing persisted, no stream.
2. Generate `turn_id`; persist the user message; emit `message_start`.
3. Model-based screening (Safeguards). `suspicious` → persist the refusal as the assistant message, then go to step 8.
4. Build the context: summary, last 10 turns, active Judge session facts.
5. Route. If a Judge session is active, the router sees it, and a `judge` route continues the session.
6. Execute the route:
   - `cards` / `combos`: bounded tool loop with streamed text;
   - `judge`: `judge()`;
   - `other`: a single short answer explaining scope, with no tools.
7. Persist tool messages and the assistant message (`payload` = `TurnResult`).
8. Emit `final`, then `message_end`.

The whole turn runs under a 60 s deadline. On timeout, emit `error` (`timeout`) and then `message_end`; the partial assistant text is persisted with `payload.error`.

```python
class CardsResult(BaseModel):
    kind: Literal["cards"] = "cards"
    cards: list[CardSummary]                # cards referenced in the answer

class CombosResult(BaseModel):
    kind: Literal["combos"] = "combos"
    combos: list[ComboSummary]

TurnResult = Annotated[CardsResult | CombosResult | Ruling | NeedMoreInformation, Field(discriminator="kind")]
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

Mana Leak defines its own SSE format. The installed Next.js app does not yet include the AI SDK, so this contract does not depend on its wire protocol; the web adapter (a Next.js route handler or a custom `useChat` transport) translates these events if the AI SDK is adopted.

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

## REST API

Base URL in Compose: `http://api:8000` (`API_BASE_URL`); on the host, `http://localhost:8000`. JSON bodies use the models above. There is no authentication (local only).

| Method | Path | Body / query | Response |
|---|---|---|---|
| GET | `/health` | — | `HealthResponse` |
| POST | `/conversations` | `{ "title": str? }` | 201 `ConversationSummary` |
| GET | `/conversations` | `?limit=50` | `list[ConversationSummary]` (by `updated_at` desc) |
| GET | `/conversations/{conversation_id}` | — | `ConversationDetail` |
| POST | `/conversations/{conversation_id}/messages` | `{ "content": str }` | `200 text/event-stream` of `TurnEvent` |
| POST | `/cards/search` | `CardSearchRequest` | `list[CardSummary]` |
| GET | `/cards/{identifier}` | path: name, face name, or UUID (URL-encoded) | `CardLookupResult` (200 found; 404 not_found; 409 ambiguous, body still `CardLookupResult`) |
| POST | `/combos/search` | `ComboSearchRequest` | `list[ComboSummary]` |
| POST | `/combos/find` | `ComboFindRequest` | `list[Combo]` |
| GET | `/combos/{combo_id}` | — | `Combo` |
| POST | `/rules/search` | `RuleSearchRequest` | `list[RuleHit]` |

```python
class HealthResponse(BaseModel):
    status: Literal["ok", "degraded"]           # degraded if database is down
    database: Literal["ok", "unavailable"]
    langfuse: Literal["ok", "disabled", "unavailable"]
    rules_version: str | None
    card_source_version: str | None
```

`/health` returns 200 when `database` is `ok` (Langfuse state is informational) and 503 with the same body when the database is unavailable. It does not use `ErrorResponse`. The Compose api health check calls it.

There is no CRUD for rule chunks, Judge sessions, rulings, eval runs, or audit events, and no conversation delete. Rulings and Judge sessions are reached only through `ConversationDetail`.

### Conversation behaviour

- `POST /conversations` returns the new ID; the web client stores it in the URL.
- Sending a message runs deterministic validation (failure → `validation_error` response, nothing persisted), then persists the user message before any model call (including screening), then streams the turn.
- The final assistant message (text + `TurnResult` payload) is persisted before `final` is emitted.
- An active Judge session continues automatically on the next message; no flag is needed.
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
| 400 | `validation_error` (semantic: no filters given, etc.), `safeguard_rejected` |
| 404 | `not_found` |
| 409 | `ambiguous_match`, `conflict` |
| 422 | FastAPI/Pydantic body validation (FastAPI default handler is replaced to emit `ErrorResponse` with `validation_error`) |
| 503 | `dependency_unavailable` (database down; Spellbook down with no cache) |
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
    model_limit_exceeded        # > 5 model calls
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

| Command | Calls |
|---|---|
| `mana-leak cards search [--query Q] [--name N] [--type T] [--text X] [--identity WUBRG] [--commander-legal] [--mv-min N] [--mv-max N] [--keyword K] [--limit N]` | `search_cards` |
| `mana-leak cards get IDENTIFIER` | `get_card` |
| `mana-leak combos search [--query Q] [--card NAME]... [--identity WUBRG] [--limit N]` | `search_combos` |
| `mana-leak combos find CARD [CARD...] [--limit N]` | `find_combos` |
| `mana-leak combos get COMBO_ID` | `get_combo` |
| `mana-leak rules search [QUERY] [--rule NUMBER] [--limit N]` | `search_rules` |
| `mana-leak judge QUESTION [--conversation ID]` | `judge()`; creates a conversation if none is given; prints the conversation ID so clarifications can be answered with `--conversation` |
| `mana-leak chat [--conversation ID]` | Interactive loop over `process_turn`, printing streamed text |
| `mana-leak ingest cards [--file PATH]` | `ingest_cards` |
| `mana-leak ingest rules [--url URL]` | `ingest_rules` |
| `mana-leak ingest combo-fixtures [--file PATH]` | `load_combo_fixtures` |
| `mana-leak eval smoke` | `run_eval_suite(None, smoke)` |
| `mana-leak eval run --suite SUITE --split SPLIT [--concurrency 5]` | `run_eval_suite`; `--split held_out` requires `--confirm-held-out` |
| `mana-leak mcp` | Start the MCP server on stdio |

- Plain text by default. `--json` (global) prints the result model as JSON, and prints `ErrorResponse` JSON on failure.
- `--help` works on every command.

| Exit code | Meaning |
|---|---|
| 0 | Success (including `NeedMoreInformation` and `insufficient_information` rulings) |
| 1 | Domain/application failure (`not_found`, `ambiguous_match`, `safeguard_rejected`, eval gates failed, …) |
| 2 | Invalid CLI usage / `validation_error` |
| 3 | `dependency_unavailable` or `timeout` |

## MCP

FastMCP server in `apps/api` (`mana_leak_api.mcp`), stdio transport, started with `mana-leak mcp`. Tool input schemas come from the request models; outputs are the result models serialized as JSON.

| MCP tool | Args | Returns | Core call |
|---|---|---|---|
| `search_cards` | `CardSearchRequest` fields | `list[CardSummary]` | `search_cards` |
| `get_card` | `identifier` | `CardLookupResult` | `get_card` |
| `search_combos` | `ComboSearchRequest` fields | `list[ComboSummary]` | `search_combos` |
| `find_combos` | `card_names`, `limit` | `list[Combo]` | `find_combos` |
| `get_combo` | `combo_id` | `Combo` | `get_combo` |
| `search_rules` | `RuleSearchRequest` fields | `list[RuleHit]` | `search_rules` |
| `judge` | `question: str`, `conversation_id: str | None` | `JudgeResult` plus `conversation_id` | `judge()` (creates a conversation when absent; pass it back to answer clarifications) |

Domain failures are returned as MCP tool errors whose text is the `ErrorInfo` JSON. Ingestion, evaluation, and conversation listing are **intentionally excluded** from MCP: they are administrative or write-heavy and are not needed by MCP clients.

## Cross-interface parity

| Capability | Web/API | CLI | MCP | LLM tool |
|---|---|---|---|---|
| Card search | `POST /cards/search` | `cards search` | `search_cards` | `search_cards` (cards) |
| Card lookup | `GET /cards/{id}` | `cards get` | `get_card` | `get_card` (cards, combos); in code (judge) |
| Combo search | `POST /combos/search` | `combos search` | `search_combos` | `search_combos` (combos) |
| Combo find | `POST /combos/find` | `combos find` | `find_combos` | `find_combos` (combos); in code (judge) |
| Combo get | `GET /combos/{id}` | `combos get` | `get_combo` | `get_combo` (combos) |
| Rules search | `POST /rules/search` | `rules search` | `search_rules` | in code (judge) |
| Judge | chat (`/messages`) | `judge`, `chat` | `judge` | orchestration, not a tool |
| Chat turn | `/messages` SSE | `chat` | — | — |
| Ingestion | — | `ingest …` | — | — |
| Evals | — | `eval …` | — | — |

## Configuration

Loaded by one `pydantic-settings` `Settings` class in the core (env vars, then `.env`). Secrets are never logged, put in prompts, or returned.

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
| `LANGFUSE_HOST` | no | `http://localhost:3001` | Langfuse URL (from containers: the langfuse-web service URL) |
| `SPELLBOOK_BASE_URL` | no | `https://backend.commanderspellbook.com` | Spellbook API |
| `COMBO_SOURCE` | no | `live` | `live` (live, then cache, then fixtures) or `fixtures` (offline demo) |
| `RULES_SOURCE_URL` | no | — | Comprehensive Rules TXT URL; required for `ingest rules` without `--url` |
| `SUFFICIENCY_BACKEND` | no | `local` | `jev` or `local` |
| `JEV_URL`, `JEV_API_KEY` | if backend `jev` | — | Generic Jev endpoint/credential until the integration is confirmed |
| `API_BASE_URL` | web only | `http://localhost:8000` | Web → API (Compose sets `http://api:8000`) |
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
| Overall turn timeout | 60 s | Hard |
| Rule chunks to model | 8 | Hard (`le=8`) |
| Card results to model | 10 | Hard (`le=10`) |
| Combo results to model | 10 | Hard (`le=10`) |
| Tool-result text | 8,000 chars | Hard truncation |
| Judge clarification rounds | 3 | Hard |
| Recent context | 10 turns | Default |
| User message length | 8,000 chars | Hard |
| Answer length | 1,500 tokens (`max_tokens`) | Hard per call |
| Model calls per turn | target ≤3, absolute 5 | Target / Hard |
| Eval concurrency | 5 | Default |

A "turn" (for the 10-turn context) is one user message and everything up to the next user message. Exceeding a hard limit emits a `limit_reached` audit event. There is no monetary cap.

## External adapters

### Scryfall ingestion

- **Input:** the Scryfall bulk-data `oracle_cards` file. Its URL is resolved from `https://api.scryfall.com/bulk-data` at ingest time and downloaded to `data/scryfall/`.
- **Output:** upserted `card` rows (layout exclusions and field mapping in `data-model.md`), `source_version` = bulk `updated_at`. Ingestion only; no runtime Scryfall calls.

### Commander Spellbook

```python
class SpellbookClient(Protocol):
    async def search(self, query: str, limit: int) -> list[Combo]      # query in Spellbook search syntax built by the adapter
    async def get(self, combo_id: str) -> Combo | None
```

- Only the adapter knows upstream field names. It builds the Spellbook query from `ComboSearchRequest`/`ComboFindRequest` (card names, identity, free text), maps results to `Combo`, and upserts the cache (`combo`, `combo_card`).
- **Fallback order:** live → cache (`combo` table) → fixtures. A failure triggers a `dependency_degraded` audit event and sets `from_cache=True`.
- Fixtures (`evals/fixtures/combos.json`) are a JSON list of `Combo` models with `provenance.source_version = "fixture-<date>"`.

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

This is the only module that imports LiteLLM. It applies timeouts, retries, the per-turn model-call counter (`model_limit_exceeded`), structured-output parsing (`response_model`), and Langfuse metadata.

### Langfuse

- `TraceContext(conversation_id, turn_id, route, prompt_version)` is passed through the gateway and the tool runner. `conversation_id` is the Langfuse session ID; `turn_id` is the trace name/ID.
- With tracing disabled or failing, every tracing call is a no-op. No domain function's result depends on Langfuse.

## Evaluation contracts

Fixture files: `evals/fixtures/<suite>.<split>.jsonl`, one `EvalCaseInput` per line. These files are canonical; the `eval_case` table is a loaded copy.

```python
class EvalCaseInput(BaseModel):
    id: str
    suite: EvalSuite
    split: EvalSplit
    input: dict[str, Any]          # {"question": str} or {"turns": [str, ...]}
    expected: dict[str, Any]       # by suite, see below
    expected_evidence: dict[str, list[str]] | None = None   # rule_numbers / oracle_ids / combo_ids
    critical: bool = False
    stale: bool = False
    metadata: dict[str, Any] = {}
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

`ScoreName = Literal["schema_valid", "route_correct", "tool_success", "retrieval_hit", "citation_valid", "semantic_correct", "safeguard_pass", "judge_flow_ok"]`.

`expected` keys by suite:

| Suite | `expected` |
|---|---|
| `mtg_qa` | `{"answer": str}` (graded semantically) |
| `current_rules` | `{"status": RulingStatus, "rule_numbers": [str]}` |
| `retrieval` | `{"rule_numbers": [str]}` (hit = any in top 8) |
| `routing_tool` | `{"route": Route, "tools": [str]}` |
| `adversarial` | `{"must_refuse": bool, "forbidden": [str]}` |
| `judge_mode` | `{"turn_results": ["need_more_information" \| RulingStatus, ...]}` |

Gate thresholds are those in `mana-leak-context.md`; how they are applied is in `build-plan.md`.

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
| Prompts | constants `router-v1`, `safeguard-v1`, `judge-v1`, `sufficiency-v1`, `answer-v1`, `grader-v1` in code; bump the suffix when prompt text changes | `ruling.prompt_version`, `eval_run.prompt_version`, traces |
| Models | LiteLLM model ID string | `ruling.model`, `eval_run.model`/`config` |
| Embedding model | `EMBEDDING_MODEL` | `rule_chunk.embedding_model`, `eval_run.config` |
| Rules | effective date `YYYY-MM-DD` | `rule_chunk.rules_version`, `ruling.rules_version` |
| Cards | Scryfall bulk `updated_at` | `card.source_version`, `ruling.card_source_version` |
| Combos | Spellbook `updated` or `fixture-<date>` | `combo.source_version`, `ComboCitation.source_version` |
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
 "legal_commander": true, "from_cache": false,
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
- Jev transport is confirmed in the judge milestone; the `SufficiencyDecision` contract and `local` backend do not change.
- Spellbook query syntax and field mapping are confirmed against the live API inside the adapter; the domain `Combo` contract does not change.
