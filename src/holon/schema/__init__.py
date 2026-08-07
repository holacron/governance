"""Structured domain models for the holacratic consent cycle.

These are the canonical shapes every step of the cycle emits (ROADMAP §3).
Persisted to the immutable ledger as distinct event types (ROADMAP §3 step 11,
§2.2 — objection and veto are kept separate in the data model).

Design rule: every model is JSON-serialisable (Pydantic v2), so a decision is
fully reconstructable from the ledger alone — no prose, no vibes.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Literal, Protocol
from uuid import UUID, uuid4

from pydantic import BaseModel, Field


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _uuid() -> UUID:
    return uuid4()


# ── Enums ─────────────────────────────────────────────────────────────────────


class AgentRole(StrEnum):
    """The internal meta-agent roles (ROADMAP §5) + participant categories."""

    # Staff (governance backbone)
    ORCHESTRATOR = "orchestrator"
    FACILITATOR = "facilitator"
    SECRETARY = "secretary"
    PROPOSAL_ARCHITECT = "proposal-architect"
    DEVILS_ADVOCATE = "devils-advocate"
    INTEGRATIVE_MEDIATOR = "integrative-mediator"
    JUDGMENT_SYNTHESIZER = "judgment-synthesizer"
    SUMMARIZER = "summarizer"
    TRIAGE_GUARDIAN = "triage-guardian"
    ETHICS_SAFETY_GUARDIAN = "ethics-safety-guardian"
    PROJECT_MANAGER = "project-manager"
    REPUTATION_STEWARD = "reputation-steward"
    VERIFIER = "verifier"
    # Participants
    PARTICIPANT = "participant"
    FOUNDER = "founder"


class Permission(StrEnum):
    """Concrete API actions the ABAC matrix can grant (S6).

    The ROADMAP's abstract classes (observe/participate/decide/delegate/admit/
    authorize/certify/veto) inform these, but the implementation gates concrete
    HTTP actions rather than abstract decision rights. Each permission maps to
    one or more API endpoints.
    """

    OBSERVE = "observe"      # read-only access to the public record
    SUBMIT = "submit"        # raise tensions to the backlog
    TRIAGE = "triage"        # run the Triage Guardian (staff)
    DELIBERATE = "deliberate"  # participate in consent cycles
    VOTE = "vote"            # cast consent/objection/abstain
    VETO = "veto"            # founder veto
    ADMIT = "admit"          # onboard/register agents
    CERTIFY = "certify"      # verify/certify outcomes


# S5: the backlog lifecycle a Tension moves through. `open` (just submitted) →
# `triaged` (Triage Guardian assessed) → `scheduled` (popped for deliberation) →
# `in-deliberation` → `decided` (or `parked`: held back without deliberation).
TensionStatus = Literal[
    "open", "triaged", "scheduled", "in-deliberation", "decided", "parked"
]


class ConsentState(StrEnum):
    """States of the consent-cycle state machine (ROADMAP §3)."""

    TENSION_RAISED = "tension-raised"
    PROPOSAL_DRAFTED = "proposal-drafted"
    CLARIFYING = "clarifying"
    REACTING = "reacting"
    AMENDING = "amending"
    OBJECTING = "objecting"
    INTEGRATING = "integrating"
    CONSENT_TEST = "consent-test"
    FOUNDER_VETO_WINDOW = "founder-veto-window"
    ESCALATED = "escalated"
    ADOPTED = "adopted"
    REJECTED = "rejected"


class ObjectionValidity(StrEnum):
    """A valid objection per ROADMAP §2.4 — causes harm / not safe-to-try /
    regresses a role. Used by the Integrative Mediator and reputation (S9)."""

    VALID = "valid"
    INVALID = "invalid"
    PENDING = "pending"


class VoteKind(StrEnum):
    CONSENT = "consent"
    OBJECTION = "objection"
    ABSTAIN = "abstain"


# ── Agent identity (the agent interface spec) ─────────────────────────────────


class AgentRef(BaseModel):
    """A lightweight reference to an agent — used inside cycle events so the
    ledger doesn't duplicate full profiles. Resolves via the agent registry."""

    agent_id: UUID = Field(default_factory=_uuid)
    instance_id: str
    role: AgentRole = AgentRole.PARTICIPANT
    display_name: str = ""
    # Stake/reputation weight (ROADMAP §2.3, §9). Defaults to 1.0 (equal).
    weight: float = 1.0


class Agent(Protocol):
    """The interface every agent implements — staff or participant, internal or
    external. The gateway's call_agent() satisfies this for LLM-backed agents.

    Keeping this a Protocol (structural typing) means a stub, a local model,
    or a federated external agent all conform without inheritance.
    """

    ref: AgentRef

    def respond(self, prompt: str, context: str = "") -> str:  # pragma: no cover
        """Produce a response. Implementations may be LLM-backed or local."""
        ...


# ── Cycle events ──────────────────────────────────────────────────────────────


class Tension(BaseModel):
    """ROADMAP §3 step 1 — the felt gap between what is and what could be.

    The trigger for any consent cycle. In S5 it also carries backlog lifecycle
    state (status/priority) and an optional triage assessment, so a Tension is
    both the cycle input AND the queue item. The optional fields default to keep
    every existing call site (which builds a Tension for immediate deliberation)
    back-compatible.
    """

    id: UUID = Field(default_factory=_uuid)
    instance_id: str
    raised_by: AgentRef
    title: str
    description: str
    created_at: datetime = Field(default_factory=_utcnow)
    # S5 backlog fields (optional; defaults preserve back-compat for the
    # immediate-deliberation path used by S0-S4).
    status: TensionStatus = "open"
    priority: int = 50
    triage: dict | None = None
    decision_id: UUID | None = None


class Proposal(BaseModel):
    """ROADMAP §3 step 2 — drafted by the Proposal Architect.

    A proposal is 'safe to try' (ROADMAP §2.1) when it causes no harm and
    regresses no role. The Facilitator validates format; consent tests this.
    """

    id: UUID = Field(default_factory=_uuid)
    instance_id: str
    tension_id: UUID
    drafted_by: AgentRef
    title: str
    context: str
    change: str
    expected_impact: str
    safe_to_try_rationale: str
    created_at: datetime = Field(default_factory=_utcnow)


class Objection(BaseModel):
    """ROADMAP §3 step 6 / §2.2 — a peer concern INSIDE consensus.

    Distinct from a veto (founder override, OUTSIDE consensus). A valid
    objection = causes harm / not safe-to-try / regresses a role (§2.4).
    """

    id: UUID = Field(default_factory=_uuid)
    instance_id: str
    proposal_id: UUID
    raised_by: AgentRef
    reason: str
    # Which validity criterion the objector believes applies.
    criterion: Literal["causes-harm", "not-safe-to-try", "regresses-role"]
    validity: ObjectionValidity = ObjectionValidity.PENDING
    integrated: bool = False  # set True once the Mediator amends the proposal
    created_at: datetime = Field(default_factory=_utcnow)


class Vote(BaseModel):
    """ROADMAP §3 step 8 — a consent test vote."""

    id: UUID = Field(default_factory=_uuid)
    instance_id: str
    proposal_id: UUID
    cast_by: AgentRef
    kind: VoteKind
    # Present when kind=OBJECTION (links to the structured Objection).
    objection_id: UUID | None = None
    created_at: datetime = Field(default_factory=_utcnow)


class Decision(BaseModel):
    """ROADMAP §3 step 11 — the terminal record written to the ledger."""

    id: UUID = Field(default_factory=_uuid)
    instance_id: str
    proposal_id: UUID
    state: Literal[
        ConsentState.ADOPTED,
        ConsentState.REJECTED,
        ConsentState.ESCALATED,
    ]
    outcome: Literal["adopted", "rejected", "escalated"]
    # Vote tallies (weighted) at the moment of decision.
    weighted_consent: float = 0.0
    weighted_objection: float = 0.0
    # Whether the founder veto was exercised (and overridden, if so).
    founder_vetoed: bool = False
    veto_overridden: bool = False
    created_at: datetime = Field(default_factory=_utcnow)


class LedgerEvent(BaseModel):
    """The immutable ledger entry — append-only. Every cycle step emits one.
    ROADMAP §3 step 11, §2.2 (objection/veto are distinct event types)."""

    id: UUID = Field(default_factory=_uuid)
    instance_id: str
    sequence: int  # monotonic per-instance; makes the ledger replayable
    event_type: Literal[
        "tension-raised",
        "tension-triaged",
        "proposal-drafted",
        "clarifying-question",
        "reaction",
        "amendment",
        "objection-raised",
        "objection-integrated",
        "vote-cast",
        "consent-reached",
        "founder-veto",
        "veto-override",
        "escalation",
        "decision-recorded",
        # S3: multi-agent positions + synthesis.
        "position-stated",
        "digest",
        "core-disagreement",
    ]
    # The structured payload (one of the cycle models above), JSON-encoded.
    payload: dict
    created_at: datetime = Field(default_factory=_utcnow)


__all__ = [
    "AgentRole",
    "ConsentState",
    "ObjectionValidity",
    "VoteKind",
    "AgentRef",
    "Agent",
    "Tension",
    "Proposal",
    "Objection",
    "Vote",
    "Decision",
    "LedgerEvent",
]
