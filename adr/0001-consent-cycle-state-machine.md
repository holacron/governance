# ADR 0001 — The Holacratic Consent Cycle as a State Machine

- **Status:** Accepted (Sprint 0)
- **Date:** 2026-08-02
- **Implements:** ROADMAP §3 (protocol), §2 (governance); implemented as code in Sprint 1
- **Supersedes:** none

## Context

Holon's central artefact is a decision-making protocol — the holacratic consent
cycle. Every decision the platform makes (and, via the bootstrap runner in §11,
every decision about *building* the platform) flows through it. We need a precise,
machine-checkable definition of its states and transitions before Sprint 1
implements it as running code.

The governance rules it must encode (from ROADMAP §2):

- **Consent, not unanimity** (§2.1): a proposal passes with no reasoned objection.
- **Three distinct control layers** (§2.2): peer *objection* (inside consensus),
  *founder veto* (outside consensus), *time/token budget* (process guard). These
  must not be conflated.
- **Founder veto** (§2.3): reason-given, windowed, overridable after 3 veto→rework
  rounds by reputation-weighted participants at 75%.
- **Objection process** (§2.4): valid = causes-harm / not-safe-to-try /
  regresses-role; loop-capped; integrated, then re-tested.
- **Budget** (§2.5): per-round turn cap, per-cycle USD cap, max loops; exhaustion
  ⇒ escalation.
- **Ethics/Safety veto** (§2.6): separate, staff-held, not overridable.

## Decision

Model the cycle as an **explicit finite-state machine** (FSM), implemented in
Sprint 1 on **LangGraph** (LangGraph is purpose-built for stateful agent graphs;
the consent cycle is exactly such a graph). The canonical states are the
`ConsentState` StrEnum in `src/holon/schema/__init__.py`.

### States

```
TENSION_RAISED
  └─> PROPOSAL_DRAFTED            (Proposal Architect drafts; Facilitator validates)
        ├─> CLARIFYING            (questions only, no debate)
        │     └─> REACTING        (impressions only, no debate)
        │           └─> AMENDING  (proposer integrates feedback)
        │                 └─> OBJECTING  (each agent states objections)
        │                       ├─[no objection]─> CONSENT_TEST
        │                       └─[objection(s)]─> INTEGRATING
        │                                             └─[amended]─> OBJECTING (re-test)
        │                                             └─[loop cap hit]─> ESCALATED
        ├─> CONSENT_TEST          (§2.1: no reasoned objection remaining?)
        │     ├─[consent]─> FOUNDER_VETO_WINDOW
        │     └─[no consent]─> INTEGRATING
        └─> FOUNDER_VETO_WINDOW   (§2.3, async/time-boxed)
              ├─[no veto in window]─> ADOPTED
              ├─[veto w/ reason]─> PROPOSAL_DRAFTED (rework; count toward 3)
              └─[veto after 3 rounds + 75% override]─> ADOPTED (veto_overridden=true)

ESCALATED  ──>  (Cross-Circle supermajority fallback, §3 step 10)  ──>  ADOPTED | REJECTED

Terminal: ADOPTED | REJECTED      (Secretary records Decision + LedgerEvents, §3 step 11)
```

### Transitions and guards

| Transition | Trigger | Guard / note |
|---|---|---|
| any → OBJECTING re-test | objection integrated | loop counter +1; at cap → ESCALATED |
| OBJECTING → CONSENT_TEST | no valid objection | weighted tally: consent weight ≥ (total − objection weight) |
| CONSENT_TEST → FOUNDER_VETO_WINDOW | consent reached | only when state == CONSENT (not abstain-defaulted silently) |
| FOUNDER_VETO_WINDOW → ADOPTED | window elapsed, no veto | veto window length is a §2.5 parameter |
| FOUNDER_VETO_WINDOW → PROPOSAL_DRAFTED | founder veto w/ reason | veto_round counter +1 |
| veto_round ≥ 3 + 75% override | participant supermajority | weighted by reputation (S9/S10); founder veto not reputation-weighted |
| budget exhausted (§2.5) | any state | → ESCALATED |
| Ethics/Safety veto (§2.6) | any state | → REJECTED (cannot be overridden by participants) |

### Parameters left to Sprint 0→1 (§12 open questions)

- `N` integration-loop cap (concrete number) — default proposed: **3**.
- Veto window length (async) — default proposed: **24h** (timezone-fair, §S6).
- 3 veto→rework rounds — **confirmed**.
- Override threshold — **75% weighted supermajority, confirmed**.

These become config (`instance.yaml` runtime section or `.env`) so they're
tunable per instance without code changes.

## Consequences

- **Positive:** every decision is a replayable path through an FSM; the
  immutable ledger captures each transition as a distinct `LedgerEvent`, so any
  decision is reconstructable (§3 step 11, §9 audit). LangGraph gives us
  checkpointing, retries, and graph-level observability for free.
- **Positive:** the three control layers (objection / veto / budget) map to
  distinct transition guards, so they can never be conflated — matching the
  hard rule in §2.2 that they stay separate in data, UI, and ledger.
- **Negative:** a strict FSM is less flexible than free-form agent chat; some
  emergent holacratic behaviours may need adding states later. Mitigation: the
  schema's `event_type` enum and `LedgerEvent` are open to extension; new states
  are additive.
- **Risk:** the budget→escalation path means a contentious proposal can be
  adopted over a lone objection. This is intentional (prevents deadlock, §9) and
  logged — but the Verifier (§5 role 12) and Devil's Advocate (role 5) are
  mandatory on every decision to keep this honest.

## Open questions for Sprint 1 implementation

1. Is a "withdraw" action (objector withdraws) a first-class transition, or
   handled within INTEGRATING? — lean: within INTEGRATING (it just reduces the
   objection set before re-test).
2. How are abstain-defaults (silent past window, §2.5) counted in the weighted
   tally? — lean: abstain = not-an-objection, so contributes neither to consent
   nor objection weight.
