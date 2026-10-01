# PRD authoring standard

How a roadmap milestone becomes a PRD. Template: [`TEMPLATE.md`](TEMPLATE.md).

The planning chain, ownership, and review gate come from authoritative documents; this file only adds PRD-specific rules:

- Hierarchy and tie-breaks: `AGENTS.md` §3.
- Milestones, dependencies, outcomes, success evidence, and "Open PRD inputs": `docs/ROADMAP.md`.
- Review gate (review–repair cycle): `AGENTS.md` §49a.
- Design gaps and deviations: `docs/ROADMAP.md` → Decisions & deviations (amendment rule).

```text
vision · architecture · data-model · contracts
        ↓
     ROADMAP milestone      (sequence, outcome, scope, increment, evidence)
        ↓
       PRD                  (what must be built: requirements + acceptance criteria)
        ↓   review gate (AGENTS.md §49a)
    PRD plan                (how: work packages, sequence, files, tests, progress)
        ↓
  implementation
```

## PRD versus PRD plan

| Belongs in the PRD | Belongs in the PRD plan |
|---|---|
| Observable behaviour the milestone must deliver | Work packages and their order |
| Requirements (FR/NFR) and acceptance criteria (AC) | Files, modules, classes, functions to create or change |
| Which settled contracts/tables/events are touched | Technical tasks and checklists |
| What must be verified (deterministic tests vs evals) | The specific tests to write and run |
| Constraints, risks, and design-supported fallbacks | Progress state and the current step |

Name a specific interface or structure in the PRD only when `contracts.md`, `data-model.md`, or `architecture.md` already mandates it, and reference it rather than redefining it.

## Milestone → PRD

**Default: one milestone, one PRD** (`docs/ROADMAP.md` → Roadmap and PRDs). Ask first:

> Can this milestone be written as one coherent, reviewable PRD that delivers its single demonstrable increment?

If yes, write one PRD. Splitting is an exception that needs evidence.

### Split test

Consider a split only if at least one of these **materially** applies, and record which one in each split PRD:

1. **Independent outcomes**: the milestone contains several independently meaningful outcomes rather than one.
2. **Independent acceptance**: one part has substantially different acceptance criteria and can be completed and reviewed on its own.
3. **Specification dependency**: one part must be settled before another can be specified safely.
4. **Reviewability**: one combined PRD would be too large for a reviewer or implementing agent to reason about reliably.
5. **Distinct risk/evidence**: parts carry materially different risk, evidence, or evaluation requirements that warrant separate acceptance.
6. **Usable sub-increment**: completing one part yields a coherent usable increment, not merely an architectural layer.

These are **not** reasons to split; they belong in the PRD plan:

- several components, or frontend and backend, change;
- persistence and API both change;
- many files or work packages are involved;
- deterministic code and model behaviour both participate;
- many implementation tasks are needed.

### Split rules

- Split into vertical product slices. Never split into horizontal layers (database PRD, API PRD, frontend PRD, tests PRD).
- The split PRDs together must deliver the milestone's complete demonstrable increment. Do not change the milestone's outcome to make splitting easier; that is a roadmap change and follows the amendment rule.
- Names: `docs/prds/M6a-<slug>.md`, `docs/prds/M6b-<slug>.md`, each with its own `.plan.md`.
- Each split PRD's **Roadmap source** section states: which split-test criterion applies, what this PRD owns, what each sibling owns, the dependencies between siblings, and how together they satisfy the milestone.

## Deriving a PRD

1. Read the milestone in `docs/ROADMAP.md` in full, including its Out of scope and any "Open PRD inputs" entries for that milestone (Decisions & deviations).
2. Read only the design sections the milestone touches (`AGENTS.md` §5).
3. Copy `TEMPLATE.md` to `docs/prds/<milestone>-<slug>.md` and fill it in.
4. Resolve each "Open PRD input" for the milestone in the PRD, or list it under Open issues. Product-behaviour and architectural questions go to the user (`AGENTS.md` §49a); never decide them silently in the PRD.
5. Run the review gate (`AGENTS.md` §49a). Set Status to `Approved` only when the gate passes.
6. Only then write the PRD plan.

## Requirement quality

Requirements state observable behaviour within settled contracts.

| | Example |
|---|---|
| Too vague | "Support card search." |
| Too implementation-specific | "Add a FastAPI handler in `apps/api/routes/cards.py` that calls function X." |
| Right | "The user can search current local card data using the filter set defined by the card-search contract and receive structured results carrying source provenance." |

Each FR/NFR must be necessary, unambiguous, verifiable, and traceable to the milestone or a design-document section. Use "The system shall…" or "When X, the system shall…". NFRs cover only what this milestone needs (bounded execution, degradation, provenance, security, observability, persistence, determinism, recoverability, or latency where a document requires it). Don't add generic enterprise requirements the repository doesn't call for.

## Acceptance-criteria quality

- Every FR maps to at least one AC; the traceability table shows this.
- ACs cover the success path, important boundaries, required failure and degradation behaviour, and the milestone's demonstrable increment.
- ACs state what is observably true (Given / When / Then semantics; Gherkin syntax is optional).
- ACs never just prove that code exists.
  - Right: "With Commander Spellbook unavailable and a matching fixture present, the user receives the fixture-backed combo result marked with its source."
  - Wrong: "The fallback adapter is implemented."

## Verification versus evaluation

- **Deterministic verification** for anything with a right answer: schemas, persistence, lookups, routing constraints, state transitions, limits, citation resolution, exact rule text (`AGENTS.md` §23, §27).
- **Evaluation** only for probabilistic or qualitative behaviour, using the suites and gates in `contracts.md` → Evaluation contracts. Never use an LLM grader where a deterministic test decides correctness.

## Reviewability

A PRD is ready for the review gate only if a reviewer can determine:

- whether it faithfully represents the roadmap milestone;
- whether it contradicts any authoritative design document;
- whether the requirements are complete enough;
- whether the acceptance criteria genuinely prove the behaviour;
- whether scope has expanded beyond the milestone;
- whether PRD-plan material has leaked into it.

## Status

PRD status (in the PRD itself): `Proposed` → `Reviewed` → `Approved` → `In Progress` → `Complete`.

Milestone status lives only in `docs/ROADMAP.md` (`Not Started`, `In Progress`, `Complete`, `Blocked`). A milestone is `Complete` only when every PRD for it is `Complete`.

## Traceability chain

The repository must answer "why does this code exist?" without conversation history (`AGENTS.md` §53):

```text
design decision (vision/architecture/data-model/contracts section)
  → ROADMAP milestone
  → PRD requirement (FR/NFR)
  → acceptance criterion (AC)
  → test or eval
  → PRD-plan work package / commit
```

PRD plans reference the FR/AC IDs they implement; commit messages reference the PRD plan step.
