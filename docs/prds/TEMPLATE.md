# PRD — <Milestone ID>: <Capability>

<!-- Authoring rules: docs/prds/README.md. Delete these comments when done. -->

## Status

Proposed <!-- Proposed | Reviewed | Approved | In Progress | Complete -->

## Roadmap source

- Milestone: <M# — name> (`docs/ROADMAP.md`)
- Roadmap outcome: <quoted or close paraphrase>
- Depends on: <earlier milestones>
- Demonstrable increment: <from ROADMAP>
- Open PRD inputs resolved here: <IDs from ROADMAP → Decisions & deviations, or "none">
<!-- Split PRDs only: split-test criterion applied; what this PRD owns; what each sibling owns; dependencies between siblings; how together they satisfy the milestone. -->

## 1. Problem / context

<Why this capability exists and what this milestone solves. Reference design-document sections instead of copying them. No redesign.>

## 2. Outcome

<The observable capability that exists when this PRD is complete. Consistent with the roadmap outcome.>

## 3. Scope

### In scope

- <behaviour/capability>

### Out of scope

- <roadmap exclusions, plus narrower PRD boundaries>

## 4. Functional requirements

- **FR-1** — The system shall …
- **FR-2** — When …, the system shall …

## 5. Non-functional requirements

- **NFR-1** — <bounded execution / degradation / provenance / security / observability / persistence / determinism, only as relevant>

## 6. Interfaces and contracts affected

| Surface | Reference | Change |
|---|---|---|
| <model, endpoint, event, table, UI behaviour, CLI command, tool> | <`contracts.md` → section / `data-model.md` → table / `architecture.md` → section> | <uses / implements / first introduced> |

<If required behaviour conflicts with a settled contract, record it under Open issues; do not redefine the contract here.>

## 7. Acceptance criteria

- **AC-1** — Given …, when …, then …
- **AC-2** — <boundary condition>
- **AC-3** — <failure / degradation behaviour>
- **AC-n** — The milestone's demonstrable increment: <end-to-end statement through the browser chat>

## 8. Test and evaluation requirements

### Deterministic verification

- <what must be verified with ordinary assertions, by AC>

### Evaluation

- <suite/gate from `contracts.md` → Evaluation contracts, or "None for this milestone">

## 9. Constraints

- <inherited constraints relevant to this PRD, with source>

## 10. Risks and fallbacks

| Risk | Impact | Design-supported fallback |
|---|---|---|
| <risk> | <what breaks> | <fallback and source, or "none — flagged"> |

## 11. Dependencies

- Milestones: <…>
- Existing capabilities: <…>
- External: <services, data sources, credentials>

## 12. Traceability

| Requirement | Source | Acceptance criteria | Verification |
|---|---|---|---|
| FR-1 | <ROADMAP M# / contracts section> | AC-1 | deterministic test |
| NFR-1 | <…> | AC-3 | <…> |

## 13. Open issues / design gaps

<Ideally empty before approval. Contradictions or missing decisions in design documents go here and follow the amendment rule in ROADMAP → Decisions & deviations. Product or architecture questions go to the user.>

## 14. Completion condition

This PRD is complete only when:

- every acceptance criterion holds;
- the required verification and evaluations pass;
- the milestone's demonstrable increment works end to end;
- the required failure and degradation paths work;
- no BLOCKER or MAJOR review finding remains open.

Implementation existing is not sufficient.
