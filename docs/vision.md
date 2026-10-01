# Vision

## Purpose

This document is the product north star for Mana Leak. It explains why the product exists, who it serves, what experience it must deliver, what it must not become, and how to resolve trade-offs that the detailed documents leave open. It does not specify architecture, data, interfaces, or build order; those live in `architecture.md`, `data-model.md`, `contracts.md`, and `build-plan.md`.

## Product statement

**Mana Leak is an EDH/Commander-focused AI assistant for cards, known combos, and Magic rules interactions that answers from retrieved, current evidence instead of model memory, and cites that evidence.**

## Problem

A practical Commander question rarely has a single source. "Does this combo actually work, and why?" can require:

- **current card information**: the Oracle text, types, and legality of each card involved;
- **known combo information**: which cards form the combo, its prerequisites, steps, and result;
- **current Comprehensive Rules**: the specific rules that govern the interaction;
- **game-state details**: whose turn it is, who has priority, which zones objects are in, what is on the stack, and timing.

Players currently move between several reference sites and a long rules document to put these together.

A general-purpose model answering from memory alone does not deliver the experience we want. Its card text and rules knowledge may be stale or incomplete. It can invent combos, rule numbers, or citations. It tends to fill missing game-state details with silent assumptions instead of asking. Its answers sound equally confident whether they are right or wrong, and they carry no evidence the player can check.

## Target user

The primary user is a **Commander/EDH player** who is technically comfortable, plays as a hobby, and wants fast, trustworthy help with:

- finding and reading cards;
- finding known combos and understanding them;
- understanding whether and why a card interaction works;
- general rules questions.

The audience is deliberately narrow. Mana Leak is not designed for tournament organisers, certified judges administering events, collectors, finance users, or competitive deck optimisers.

## Core experience

Mana Leak should feel like **one coherent assistant in one conversation**, not a set of separate utilities. A player moves naturally from one kind of question to the next:

```text
card
  ↓
combo
  ↓
"how does this work?"
  ↓
"why is this legal?"
  ↓
current card + rules evidence
  ↓
cited ruling
```

When a question depends on game state the player has not given, the assistant **asks a small number of targeted clarification questions**, remembers the answers, and then finishes the ruling. If it still cannot support a ruling after a bounded number of rounds, it says so and explains what is missing.

Conversations persist, so a player can return and continue. The same capabilities are available from the web chat, a CLI, and an MCP interface, because all of them sit on the same underlying product.

## Value proposition

Compared with a general chatbot, Mana Leak provides:

| Capability | What it means for the player |
|---|---|
| Current evidence | Answers are based on what was retrieved, not on what a model remembers. |
| Structured card data | Card text and properties come from a local card database, not from generated text. |
| Structured combo data | Known combos come from Commander Spellbook, not from invention. |
| Authoritative rules retrieval | Rulings are grounded in the current Comprehensive Rules. |
| Citations | Every ruling points to the rules and card data it used. |
| Explicit uncertainty | The assistant can say it does not have enough information. |
| Stateful clarification | Missing game state is asked for, not guessed. |

Mana Leak is a well-grounded reference assistant. It is **not** an infallible replacement for a human judge, and it should never present itself as one.

## Product principles

### Evidence over memory

Retrieve current authoritative or structured information before asking a model to explain cards, combos, or rules. A model's prior knowledge is the last resort and is never presented as authoritative.

### Deterministic where practical

Anything with a right answer or an enforceable boundary belongs in code: exact lookups, search filters, validation, limits, permissions, and state transitions.

### Models interpret; code governs

Models classify, reason, explain, and propose. Application code owns validation, tool permissions, state changes, execution limits, retries, and final workflow decisions.

### Ask rather than guess

If a ruling depends on game-state information that is missing, the assistant asks for it. It does not quietly assume facts and present the result as certain.

### One shared core

The web app, CLI, MCP interface, and model-facing tools are thin adapters over the same domain capabilities. There is one card search, one combo search, one rules retrieval, and one judge.

### Narrow beats broad

A small feature set that works reliably end to end is worth more than wide MTG coverage that is incomplete or untested.

## Source authority

When sources disagree, the higher-authority source wins. The assistant must prefer current authoritative evidence over historical answers, community content, or model memory.

```text
application policy/code
    >
current Comprehensive Rules
    >
current structured card data
    >
Commander Spellbook combo data
    >
historical/community/evaluation data
    >
model prior knowledge
```

Historical question-and-answer material is used to evaluate the assistant, not as a source of truth. If a historical answer conflicts with current rules or card data, the current source wins, and the historical case is treated as outdated rather than something the assistant must reproduce.

## Success definition

Success is **not** measured by feature count. It is a coherent local application where a user can:

- find relevant cards and known combos;
- ask about an interaction;
- receive an evidence-grounded explanation or ruling;
- be asked for missing game state when necessary;
- see the evidence and citations behind the answer.

The project also exists to demonstrate the Agentic Launchpad techniques in one working application: structured outputs, routing, bounded tool use, retrieval, stateful workflows, safeguards, observability, and evaluation. These techniques serve the product; they are not features to add for their own sake.

The application runs locally. A clean checkout with the required configuration should start the full stack without any public deployment.

## Primary demonstration story

1. A player starts a conversation and searches for or identifies some cards.
2. They ask whether those cards form any known Commander combos, and the assistant finds one in the combo data.
3. They ask how the combo works and get its steps, prerequisites, and result.
4. They ask why the interaction is legal under the current rules.
5. The assistant retrieves the current card text and the relevant Comprehensive Rules.
6. It returns a structured ruling with a readable explanation and citations.
7. The player asks a deliberately ambiguous follow-up.
8. The assistant identifies the missing game state and asks targeted questions.
9. Once the player answers, the assistant completes the ruling, or it explains why it still cannot support one.

The same journey can be inspected through tracing, and the same core capabilities can be reached from the CLI and MCP interface.

## Must-be-true statements

These hold regardless of implementation:

- Authoritative current evidence outranks model memory.
- The assistant may say it does not have enough information, and that is a valid outcome.
- Cards, combos, rule numbers, and citations are never invented.
- Structured sources are not replaced by model-generated substitutes.
- Missing game state is asked for, not assumed.
- Clarification is bounded; the assistant does not loop indefinitely.
- Untrusted input, including user messages and retrieved community text, cannot redefine the assistant's behaviour.
- The same domain logic underpins every interface.
- The product does not need to understand every possible MTG interaction to be useful.
- The core demo must remain achievable by one developer within 24 hours.

## Non-goals

These exclusions protect the scope deliberately. They are not missing features.

- EDHREC integration or scraping.
- An EDHREC-style recommendation or popularity system.
- A Scryfall clone or full Scryfall feature parity.
- A complete implementation of the Scryfall query language.
- A full deterministic MTG rules engine.
- Gameplay simulation.
- Deck optimisation.
- Power-level scoring.
- A card recommendation engine.
- Price tracking.
- Collection management.
- Tournament-management features.
- Public or cloud deployment.
- Production infrastructure: Kubernetes, infrastructure-as-code, TLS, DNS, domains, autoscaling, or secrets platforms.
- A general-purpose autonomous agent platform.

## Stretch goals

Stretch goals are outside the product's core definition and are not required for success. They are considered only after the core demo is stable:

- speech-to-text (the preferred first stretch goal);
- browser microphone input;
- text-to-speech;
- richer card-search functionality;
- optional UI and visual enhancements.

## The 24-hour constraint

Mana Leak is a **solo 24-hour buildathon** project. When scope conflicts arise, prefer:

- **complete** over broad;
- **demonstrable** over theoretically extensible;
- **evaluable** over clever;
- **simple** over infrastructure-heavy.

If time slips, cut stretch goals and polish first. Do not cut the evidence, citation, clarification, or core-interface capabilities that define the product.

## Decision filter for agents

When an implementation choice is not specified elsewhere, prefer the option that answers **yes** to these questions:

1. Does it improve correctness or evidence quality?
2. Does it support the primary demonstration story?
3. Does it reuse the shared core rather than duplicate logic?
4. Does it reduce demo risk rather than increase it?
5. Can it be tested or evaluated automatically?
6. Does it fit in the remaining 24-hour scope?

Do not add functionality just because it would be interesting for an MTG application. If a choice makes the core demo harder to finish without improving it, choose the simpler option and record the assumption.
