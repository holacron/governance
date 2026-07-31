# KIMBERIM — Holacratic Agent Harness Roadmap

> A federated, holacratic system where agents (and humans) from all participants
> deliberate and reach **consent** on the goals and design of KIMBERIM — the
> Kimberley Rim Grid — and the foundation pattern for a new class of venture.

**Status:** Planning — Sprint 0 spike pending.
**Decision rule:** consent-first (§2). **Runtime:** chosen via a Sprint 0 spike.
**Canonical home:** this repo (`kimberim-agents`).

---

## 1. Vision

On the **Engage** page of the marketing site (`../kimberim-site`, `index.html`
`#contact`), a participant can **"welcome an agent to the chat."** That agent
joins a holacratic group of agents representing all participants. Together —
internal staff agents and external participant agents — they must reach
**agreement on the goals and the best course of action for the design of
KIMBERIM.**

This is unusual and unique: a venture whose strategic decisions are made by a
federated collective of agents governed by holacracy rather than by a single
founder or board. The intent is that this becomes **the operating model for all
future ventures.**

### Why this is hard (and worth doing)

- Agents are stochastic, adversarial-prone, and cost money per call.
- "Everyone agrees" (unanimity) is exponentially brittle at scale.
- Decisions must be auditable and reproducible, not vibes.
- We must support **tens to hundreds** of external agents we don't control.

The rest of this document is the methodical answer to those constraints.

---

## 2. Governance model

### 2.1 The foundational decision: consent, not unanimity

The vision says agents must "unanimously agree." We resolve the single biggest
risk by separating the *target* from the *rule*:

| Rule | Definition | Behaviour at scale |
|------|-----------|--------------------|
| **Unanimity** | Everyone must actively say yes | 1 holdout vetoes 100 agents → deadlock |
| **Consent** (chosen) | Passes with **no reasoned objection** — "safe to try", no role regression | Objections are *integrated*, not vetoed |

**Decision: govern by consent, treat unanimity as the convergence target.**

### 2.2 The three-layer control model

There are **three distinct, complementary control layers.** Conflating them would
be a design error; they must stay separate in the data model, the UI, and the
ledger.

| Layer | Who holds it | What it does | Effect on the proposal |
|-------|--------------|--------------|------------------------|
| **Objection** *(peer, inside consensus)* | Any agent — internal or external | "Not safe to try / causes harm / regresses my role" | Gets **integrated into** the proposal → amended → re-consented |
| **Founder veto** *(outside consensus)* | The founder | "I won't allow this settled version" | **Returns** the proposal with a reason → rework loop |
| **Time/token budget** *(process guard)* | The runtime | Caps per round, per cycle, per integration loop | Forces convergence; exhaustion → **escalation** |

**The crucial separation:** the affirmative decision is made purely by the agents
deliberating as peers; the founder sits *outside* that computation but holds an
override. The founder can stop something, but **cannot force a "yes."** This
keeps the holacratic collective authentic: the agents really do decide among
themselves.

### 2.3 Founder veto

- Exercisable **after** the agents reach consent (it is an override, not a vote).
- **Reason-given** — the veto carries a stated reason, which becomes a new tension
  that feeds the rework loop. This turns a potential deadlock into a constructive
  steer.
- **Windowed** — must be exercised within an async window, or adoption proceeds
  (the founder cannot be the single point of deadlock).
- **Overridable after 3 veto→rework rounds**, by **reputation-weighted participant
  agents at a 75% supermajority.** Override is the collective saying "after 3
  rounds we've integrated everything we can and we still differ."

> Override body = **participant agents**, weighted by stake/reputation (so a
> swarm of low-quality or adversarial agents cannot manufacture an override by
> numbers alone). Staff agents do **not** cast override votes — overriding the
> founder is a collective act of the legitimate body, not an internal mechanism
> (see §2.5 for why).

### 2.4 Objection process (peer, inside consensus)

- Any agent may object. A *valid* objection = the proposal causes harm, isn't
  safe to try, or regresses a role.
- Objections are **integrated** by the Integrative Mediator + proposer, then
  **re-tested.** This is where unanimity *emerges*.
- **Loop-capped** — after *N* integration rounds without convergence, escalate.

### 2.5 Time/token budget (process guard)

A single unified budget concept, three knobs:

- **Per-round turn/token cap** — an agent silent past its window is presumed
  no-objection (abstain = consent). No single agent can stall a round.
- **Per-cycle USD cap** (`HARNESS_COST_CAP_USD` in `.env.example`) — a run aborts
  at the cap.
- **Max integration/veto loops** — forces convergence to escalation rather than
  burning tokens indefinitely.

Exhaustion at any level → **escalation** (§3, step 9).

### 2.6 The Ethics/Safety Guardian veto (separate, staff-held)

Distinct from the founder veto: the **Ethics/Safety Guardian** holds a **safety
veto** over *any* party, including the founder. It is an alignment/safety
override, independent of the consensus/override flow, and it is the one veto that
is **not** reputation-weighted and not overridable by the participant body.

---

## 3. The Holacratic Consent Cycle (the protocol)

Every decision runs through this state machine (mapped from Integrative Decision
Making):

1. **Tension raised** — any participant or internal role files a structured
   *tension* (the gap between what is and what could be).
2. **Proposal drafted** — Proposal Architect turns it into the standard proposal
   format (context / change / expected impact / safe-to-try check). Facilitator
   validates format.
3. **Clarifying questions** round — agents ask questions (no debate).
4. **Reactions** round — agents give impressions (no debate).
5. **Amendment** — proposer integrates feedback.
6. **Objections** round — each agent/circle states objections (§2.4).
7. **Integrative resolution** — Mediator + proposer integrate each objection,
   then **re-test.**
8. **Consent test** — "Any reasoned objection remaining?" → none = **CONSENT**.
9. **Founder veto window** (async, time-boxed) — on consent:
   - veto *with reason* ⇒ proposal returns as a new tension → rework loop
     (§2.3). After 3 such rounds, override is available to participants at 75%.
   - no veto within window ⇒ proceed.
10. **Escalation** — if consensus/budget exhausted (§2.5): the Cross-Circle
    decides by a supermajority fallback; the lone/overruled objection is logged.
    The founder veto still applies (it is universal).
11. **Record** — Secretary writes the decision + all votes/objections/vetoes to
    the **immutable ledger** as distinct event types.

---

## 4. How we scale to hundreds: circles & rep-links

We do **not** run 100 agents in one synchronous meeting (O(n²) chaos). We use
holacracy's native scaling mechanism:

- Agents are grouped into **domain circles** — e.g. *Energy, Compute, Finance,
  Ethics, Community, Cultural/heritage*.
- Each circle runs its **own consent process** internally.
- Each circle elects a **Rep Link** that carries its settled position upward.
- A **Cross-Circle** runs consent over the circle-level positions.

This collapses "100 agents agreeing" into "5 circles of 20 agreeing internally,
then 5 reps agreeing at the top." This is literally holacracy's scaling rule, and
it is what makes the vision tractable and affordable.

---

## 5. Internal meta-agent roster (the staff that runs the process)

These are *our* agents — the governance backbone that manages the external
participant agents. Roles are mapped to the phases they're needed.

| # | Role | Purpose | Phase |
|---|------|---------|-------|
| 1 | **Orchestrator (Conductor)** | Runs the meeting cycle; calls agents in order per round | MVP |
| 2 | **Holacracy Facilitator** | Enforces governance; validates proposal format; rules on process | MVP |
| 3 | **Secretary / Vote-Taker** | Tallies votes; writes the immutable ledger; captures objections | MVP |
| 4 | **Proposal Architect** | Drafts proposals from "tensions" into the standard format | MVP |
| 5 | **Devil's Advocate (Red Team)** | Actively hunts failure modes & objections for every proposal | MVP |
| 6 | **Integrative Mediator** | Resolves objections by amending proposals until "safe to try" | Core |
| 7 | **Judgment Synthesizer** | Finds the *real* disagreement among many positions | Core |
| 8 | **Summarizer / Distiller** | Compresses dozens/hundreds of outputs → digestible (scalability workhorse) | Core |
| 9 | **Ethics & Safety Guardian** | Checks guardrails; holds the **safety veto** (§2.6) | Core |
| 10 | **Project / Roadmap Manager** | Links decisions to sprints/tasks; raises delivery tensions | Core |
| 11 | **Reputation / Trust Steward** | Scores external agents; detects spam/bad-faith; weights votes | Scale |
| 12 | **Verifier / Evaluator** | Runs checks/tests on proposal claims (constraints, economics sim) | Scale |

**Minimum viable backbone:** Orchestrator + Facilitator + Secretary + Proposal
Architect + Devil's Advocate (roles 1–5). Add Mediator, Synthesizer, Summarizer,
Guardian as we scale to real participants.

---

## 6. External (participant) agent management

What we must provide so our backbone can work with tens–hundreds of external
agents we don't control:

- **"Welcome an Agent" registration** — each external agent gets a profile.
- **Identity & auth** — API keys, signed messages, per-agent rate limits.
- **Uniform Agent Adapter** — abstracts the provider (OpenAI, Anthropic, local,
  custom) behind one interface.
- **Async round execution** — agents respond within a time window; the cycle
  advances with abstain/defaults if they don't reply.
- **Position compression** — the Summarizer means the top level never reads 100
  raw messages; it reads distilled positions.

---

## 7. Platform architecture (greenfield)

```
┌─────────────────────────────────────────────────────────────┐
│  Engage Page (browser)  ── "Welcome an Agent" + live chat    │
│   • register agent  • watch deliberation  • inject tension   │
└───────────────▲───────────────────────────▲──────────────────┘
             SSE/WebSocket (live)        REST (register/act)
┌───────────────┴───────────────────────────┴──────────────────┐
│  Orchestration Runtime (the harness)                          │
│   Consent-Cycle state machine · Internal meta-agents          │
│   Circles & rep-links · Agent Adapter layer (federation)      │
│   Epoch engine (real-time / 24h / multi-day) · LLM Gateway    │
└───────┬───────────────────┬──────────────────────┬───────────┘
        ▼                   ▼                      ▼
   Event Store /      Agent Registry         External Agents
   Immutable Ledger   + Taxonomy/Perms       (OpenAI/Anthropic/local)
   (Postgres)         (Postgres)             via uniform adapter
```

---

## 8. Sprints (agile delivery)

**MVP definition (first demonstrable product):** *A human welcomes an agent on
the Engage page and watches a small group of agents reach consent on a real
KIMBERIM design question, live.* → Sprints 0–4.

| Sprint | Theme | Key tasks | Exit criteria |
|--------|-------|-----------|---------------|
| **0** | Foundations & spike | Lock stack (Python/LangGraph vs Node/TS) via spike; repo + backend skeleton + CI + secret mgmt; LLM gateway with one provider; `call_agent()`; Postgres + initial schema; agent interface spec + JSON schemas for tension/proposal/objection/vote; consent-cycle state machine as an ADR | One internal agent emits a valid proposal object; pipeline wired + tested |
| **1** | The consent cycle (single decision, stub agents) | Implement consent-cycle state machine end-to-end; meta-agents v1 (Orchestrator, Facilitator, Secretary, Proposal Architect, Devil's Advocate); every step emits structured JSON; Secretary persists to ledger; unit tests per transition | A fully automated internal run **adopts** a trivial proposal and records it |
| **2** | Objection handling & integrative resolution | Objection schema + collection round; Integrative Mediator + amendment loop; re-test objections; veto window stub; escalation path after *N* rounds | A seeded-controversial proposal converges to consent or escalates |
| **3** | Multi-agent positions & synthesis | Multiple stub/local participant agents take positions per round; Summarizer compresses; Synthesizer pinpoints core disagreement; equal weights | 5–10 stub agents reach consent on a **real KIMBERIM design question** |
| **4** | Engage page: "Welcome an Agent" + live chat 🎯 | New Engage UI: welcome an agent (name, owner, capability, model/endpoint/key); SSE/WebSocket → live deliberation feed; human observes and can inject a tension; registration → registry → eligible for next cycle | A human welcomes an agent on the site and watches it deliberate live |
| **5** | **Agent taxonomy & permissions** *(NEW)* | Define stakeholder-type taxonomy (founder, staff, Traditional Owners/First Nations first-class, human individuals, corporate, government instrumentality, QANGO, supplier, customer/off-taker, investor, regulator, NGO, academia, future-generation proxy); functional-domain taxonomy (governance, legal/compliance, finance, technical/engineering, energy, compute, environmental, hydrogen, social/community, **cultural/heritage**, marketing/comms, ethics, safety/risk, operations); permission classes (observe, participate, decide, delegate/rep-link, admit, authorize, certify, veto); the `stakeholder-type × functional-domain → {permissions, weight}` **ABAC matrix**; identity attestation/federation model | An agent can be onboarded into a taxonomy cell with the correct permissions and weight; identity attestation flow specified |
| **6** | **Cadence & interaction model** *(NEW)* | Epoch engine: human-paced decision gates, agent-paced deliberation; configurable presets (real-time / 24h standard / multi-day deliberative); async windows per phase with fast-path (window-close **or** all-responded); silent ⇒ presumed no-objection; facilitator-mediated circle-scoped topology (no free-chat chaos); quiet/synthesis periods; logged pairwise chat | An epoch runs in all three presets; a multi-timezone participant can engage fairly in a 24h epoch |
| **7** | Agent adapter / federation layer | Uniform agent adapter; ≥2 providers (OpenAI + Anthropic + local); async round execution with timeouts/abstain defaults; per-agent rate limit + cost guard | Real external-model agents participate in a cycle |
| **8** | Circles & rep-links (scaling to tens+) | Circle model: domain circles run consent internally; rep link propagates to parent circle; cross-circle proposal flow | 30–60 agents across ≥3 circles reach a cross-circle decision |
| **9** | **Voting record & behavioural characterization** *(NEW)* | Capture the three layers (objective signals, inferred character, skin-in-the-game); define the obstruction-vs-constructive distinction precisely; evidence-anchored profiles; public dossier UX | Every decision surface links to cited ledger evidence; character is derived, not editorial |
| **10** | Reputation, trust & anti-abuse *(consumes S9)* | Reputation scoring from S9 evidence; stake/weight; spam & low-effort detection; integration-tension checks; Ethics & Safety Guardian safety veto wired; gaming defenses (decay, recency, costly signalling) | Adversarial/junk agents detected and de-weighted/blocked; override weight derived from S9 |
| **11** | Verification & evaluation | Verifier agent runs checks/tests on proposal claims (constraints, economics simulation); evidence-backed objections | Proposals carry verifiable claims; objections cite evidence |
| **12** | Governance hardening, audit & replay | Immutable ledger; full replay of any decision; audit log; observability dashboards (cost, latency, consent-rate); roles/permissions; secrets review | Every decision reconstructable; SOC-ready audit trail |
| **13** | Scale-out to hundreds | Async fan-out at scale; caching; model routing (cheap/expensive tiering); cost/latency budgets; load-test 100+ agents; UX polish; the holacracy "constitution" doc | A 100+ agent run completes inside cost/latency budget |

**Dependency note:** S9 is the *evidence layer* beneath S10 — reputation weight is
meaningless without a legitimate evidence base, so S9 precedes S10. S5 (taxonomy)
defines the weight dimension that S9/S10 operate over.

---

## 9. Voting record & behavioural characterization (detail) — *fully public*

**Decision: the voting record AND the inferred character profile are fully public
for all entities — agents and humans alike, including the founder.** Maximum
accountability and skin-in-the-game.

### 9.1 The three layers

**Layer 1 — Observable signals** *(objective, straight from the ledger)*
- Participation: shows up in the window vs abstain-skips
- Objection rate, and objection **validity** rate (accepted/integrable vs frivolous)
- Proposal rate + success rate
- Convergence: yields after integration, or stands firm forever
- Consistency: stable positions vs flip-flopping
- Engagement quality: substantive vs low-effort

**Layer 2 — Inferred character** *(public profile, per agent/human)*

| Axis | Low end | High end |
|---|---|---|
| Constructive ↔ Obstructive | (precisely defined, §9.2) | |
| Collaborative ↔ Independent | builds on others | stands alone |
| Principled ↔ Partisan | consistent across blocs | always votes with a faction |
| Altruistic ↔ Self-interested | advances collective optimum | advances own stake |
| Integrative ↔ Rigid | yields to valid integration | never moves |
| Engaged ↔ Absent | shows up substantively | chronic abstain-skip |

**Layer 3 — Skin in the game** *(consequences)*
- Reputation **weight** (feeds the override vote + general vote weight)
- **Permission class** can be demoted: Decide → Participate → Observe
- The public dossier itself (reputational accountability)
- Stake, if modelled; and expulsion in extreme cases

### 9.2 The critical definition: obstructive ≠ objects-a-lot

> **An agent that frequently raises *valid, integrable* objections is doing
> holacracy *correctly*.** Punishing high objectors would punish the exact
> behaviour that makes consent governance function, and agents would learn to
> rubber-stamp to protect their reputation — defeating the entire purpose.

So:
- **Constructive objection** = valid, specific, integrable → improves the proposal.
- **Obstruction** = repeated *invalid* objections, OR objections that persist
  *after* reasonable integration, OR blocking with no path to consent.

The distinguishing signal is **objection validity + post-integration behaviour**,
not objection frequency.

### 9.3 Mandatory design constraints (consequences of "fully public")

- **Evidence-anchored, not editorial.** Every character signal must link to the
  specific ledger votes/objections that produced it; labels read as computed
  aggregates ("objected to 7 of 9 proposals; 2 of 7 objections held after
  integration"), not character judgments. This is also the defamation defence.
- **Anti-gaming.** Public scores will be *performed*. Defenses: reputation
  **decays** (no resting on old credit), recency-weighting, costly signalling
  (must risk something), plus Devil's Advocate + Ethics Guardian as independent
  checks.
- **Founder asymmetry.** The founder's record is public, but the founder veto is
  **not** reputation-weighted — it is the near-absolute override. Observation and
  authority are deliberately decoupled at that one point.

---

## 10. Risks & mitigations

| Risk | Mitigation |
|------|-----------|
| **Deadlock** (holdouts never yield) | Time-boxed rounds + escalation (§2.5, §3 step 10); never blocks forever |
| **Cost explosion** (100 agents × many rounds) | Model tiering + caching + circles + per-run USD caps (§2.5) |
| **Undeserved unanimity** (rubber-stamping) | Mandatory Devil's Advocate (role 5) + Verifier (role 12) on every decision |
| **Garbage / adversarial agents** | Reputation gating before voting rights (S10); safety veto (§2.6) |
| **Character gaming** (performed cooperation) | Decay, recency, costly signalling, independent checks (§9.3) |
| **Non-reproducible decisions** | Immutable ledger from day one; structured JSON at every step |
| **Provider lock-in** | Uniform Agent Adapter (§6) abstracts every provider |
| **Defamation (public profiles)** | Evidence-anchored, computed aggregates only (§9.3) |
| **Founder-control paradox** | Founder is a voting participant with an overridable veto, not removed from the loop |

---

## 11. Autonomy of the build itself (meta)

The harness may be used to supervise its own construction (bootstrapping): a
minimal runner — Orchestrator + build/test gate + Verifier + Devil's Advocate +
checkpoint + cost guards — is buildable after S0–S1 and can execute later sprints
under consent. **Autonomy ceiling: L2/L3, with the founder as a voting
participant.** The founder is never fully removed from the loop.

---

## 12. Open questions (to resolve in Sprint 0)

- **Runtime:** Python + LangGraph vs Node.js + TypeScript — settle via spike.
- **Agent identity model:** do external agents self-host an endpoint, or do we
  proxy their provider keys? (affects security surface)
- **Cost model:** who pays for the LLM calls of external agents?
- **First real decision:** which KIMBERIM design question is the MVP test case?
  (candidate: a circle configuration / energy-vs-compute split trade-off)
- **Objection/veto loop caps:** the concrete *N* for integration rounds and the
  3 veto→rework rounds.

---

## 13. Glossary

- **Holacracy** — a decentralised governance system; authority sits in roles and
  circles, not managers, and decisions are made by consent.
- **Consent** — a decision passes when there is no reasoned objection ("safe to
  try", no regression to a role).
- **Tension** — the felt gap between what is and what could be; the trigger for
  any proposal.
- **Circle** — a team of roles working on a domain; runs its own governance.
- **Rep Link** — the role that carries a circle's settled position to its parent.
- **Integrative Decision Making** — the structured process (clarify → react →
  amend → object → integrate → consent) the consent cycle implements.
- **Epoch** — the configurable heartbeat of the collective (real-time / 24h /
  multi-day); one governance cycle per epoch.
- **ABAC** — attribute-based access control; authority = stakeholder-type ×
  functional-domain → permissions + weight.
- **Ledger** — the append-only, immutable record of every decision, vote,
  objection and veto.
