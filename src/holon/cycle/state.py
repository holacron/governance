"""The cycle's carried state + the participants/agents it runs with.

`CycleState` is the TypedDict that flows through the LangGraph StateGraph. It
holds the working set of the consent cycle: the tension, the current proposal,
the objections/votes collected so far, the loop counters (the §2.5 guards), and
the current ConsentState.

A `CycleRun` bundles the state with the agents that act on it + the governance
params + an optional ledger sink. Keeping agents on the run object (not in the
graph state) means the graph stays pure data-in/data-out — the nodes read the
run from a closure and return state deltas.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TypedDict

from holon.config import GovernanceConfig
from holon.schema import AgentRef, ConsentState, Tension

# Type of the ledger sink the Secretary uses. Mirrors store.append_ledger_event
# but lets unit tests inject an in-memory capture instead of a DB session.
LedgerSink = Callable[[str, dict], None]

# S5: called once by record() with the finalized decision payload (incl. the
# tension_id + proposal_id) so the live path can write a DecisionRow and
# mark the source tension decided — closing the backlog loop. Optional; when
# None, the decision lives only in the ledger (unit-test / DB-free path).
OnDecision = Callable[[dict], None]


class CycleState(TypedDict, total=False):
    """The pure-data state flowing through the LangGraph StateGraph.

    `total=False` so each node returns only the keys it changes (LangGraph
    merges them into the running state).
    """

    instance_id: str
    state: str  # a ConsentState value
    tension: dict  # Tension.model_dump(mode="json")
    proposal: dict | None  # Proposal.model_dump(mode="json") or None
    objections: list[dict]  # Objection dicts raised this round
    votes: list[dict]  # Vote dicts from the consent test
    integration_rounds: int
    veto_rounds: int
    # S2: veto bookkeeping (set by veto_window, read by record).
    founder_vetoed: bool
    veto_overridden: bool
    veto_reason: str
    # S3: per-participant positions collected in object_round
    # ({"agent_id","position":"consent"|"objection"|"abstain", ...}).
    positions: list[dict]
    # S3: the Summarizer's compressed digest of the round's positions (if any).
    digest: dict | None
    # S3: the Synthesizer's identified core disagreement (if >1 objection).
    core_disagreement: str
    # Terminal outcome ('adopted' | 'rejected' | 'escalated') once reached.
    outcome: str


@dataclass
class CycleRun:
    """A single run of the consent cycle for one tension.

    Bundles: the seed state, the agents that act, governance params, and an
    optional ledger sink. The graph nodes read this via a closure.
    """

    instance_id: str
    tension: Tension
    participants: list[AgentRef]  # who may object/vote (stub in S1)
    governance: GovernanceConfig = field(default_factory=GovernanceConfig)
    # Agents are injected so tests pass StubAgents; live runs pass MetaAgents.
    # They're typed loosely (Agent Protocol) to avoid importing concrete classes
    # here and creating a cycle in the import graph.
    proposal_architect: object | None = None
    facilitator: object | None = None
    devils_advocate: object | None = None
    secretary: object | None = None
    orchestrator: object | None = None
    # S2: the Mediator owns amendment (distinct from the architect); the founder
    # makes the veto exercisable. Both optional for back-compat with S1.
    integrative_mediator: object | None = None
    founder: object | None = None
    # S3: participant agents that take positions per round (each conforming to
    # the Agent Protocol, with .ref + .respond). The DA stays mandatory and is
    # consulted separately. When empty -> S2 back-compat (DA only).
    participant_agents: list = field(default_factory=list)
    # S3: synthesis agents (optional; activate when there are many participants).
    summarizer: object | None = None
    judgment_synthesizer: object | None = None
    # Ledger sink: signature (event_type, payload_dict) -> None. When None,
    # events are only captured in state (no DB write) — used by unit tests.
    ledger_sink: LedgerSink | None = None
    # S5: optional callback fired by record() with the finalized decision payload
    # so the live path can persist a DecisionRow + mark the tension decided.
    on_decision: OnDecision | None = None
    # Initial state deltas accumulate here for the graph's first invocation.
    seed: CycleState = field(default_factory=dict)  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if not self.seed:
            self.seed = {
                "instance_id": self.instance_id,
                "state": ConsentState.TENSION_RAISED.value,
                "tension": self.tension.model_dump(mode="json"),
                "proposal": None,
                "objections": [],
                "votes": [],
                "integration_rounds": 0,
                "veto_rounds": 0,
                "outcome": "",
                # S2: veto bookkeeping carried in state (set by veto_window,
                # read by record). Defaults preserve S1's no-veto behavior.
                "founder_vetoed": False,
                "veto_overridden": False,
                # S3: multi-agent positions + synthesis (set by object_round /
                # integrate when participant_agents are present).
                "positions": [],
                "digest": None,
                "core_disagreement": "",
            }


def empty_state(instance_id: str, tension: Tension) -> CycleState:
    """Build the initial CycleState for a tension."""
    return CycleRun(instance_id=instance_id, tension=tension).seed


__all__ = ["CycleRun", "CycleState", "LedgerSink", "OnDecision", "empty_state"]
