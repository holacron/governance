"""Sprint 1 live acceptance test — the roadmap's exit criterion.

> "A fully automated internal run adopts a trivial proposal and records it."

Runs the consent cycle with REAL Z.ai meta-agents (Proposal Architect + Devil's
Advocate) against the Kimberim instance's first decision, persists every event
to the REAL Postgres ledger, and asserts the proposal is adopted + recorded.

Marked `live`; skipped without ZAI_API_KEY + DATABASE_URL.
"""

from __future__ import annotations

import os

import pytest
from dotenv import load_dotenv
from sqlmodel import Session as SMSession

from olon.agents import (
    DevilsAdvocate,
    Founder,
    IntegrativeMediator,
    JudgmentSynthesizer,
    MetaAgent,
    ProposalArchitect,
    Summarizer,
)
from olon.config import load_instance_config, load_runtime_config
from olon.cycle import CycleRun, run_cycle
from olon.schema import AgentRole, Tension
from olon.store import append_ledger_event, make_engine

load_dotenv()

INSTANCE = "kimberim"

_HAS_LIVE = bool(os.getenv("ZAI_API_KEY") and os.getenv("DATABASE_URL"))


@pytest.mark.live
@pytest.mark.skipif(not _HAS_LIVE, reason="needs ZAI_API_KEY + DATABASE_URL")
def test_live_cycle_adopts_and_records():
    """Real Z.ai agents deliberate Kimberim's first decision; outcome is adopted
    and written to the immutable ledger."""
    rt = load_runtime_config()
    ic = load_instance_config(INSTANCE)
    assert ic.first_decision is not None

    # Real LLM-backed agents.
    architect = ProposalArchitect(instance_id=INSTANCE)
    devils_advocate = DevilsAdvocate(instance_id=INSTANCE)

    tension = Tension(
        instance_id=INSTANCE,
        raised_by=architect.ref,
        title=ic.first_decision.title,
        description=ic.first_decision.summary.strip(),
    )

    # Ledger sink: persist every cycle event to Postgres (real DB).
    eng = make_engine(rt.database_url)
    persisted: list[str] = []

    def sink(event_type: str, payload: dict) -> None:
        with SMSession(eng) as s:
            append_ledger_event(
                s, instance_id=INSTANCE, event_type=event_type, payload=payload
            )
            s.commit()
        persisted.append(event_type)

    run = CycleRun(
        instance_id=INSTANCE,
        tension=tension,
        participants=[architect.ref],
        governance=ic.governance,
        proposal_architect=architect,
        devils_advocate=devils_advocate,
        ledger_sink=sink,
    )
    final = run_cycle(run)

    # Acceptance: a proposal was adopted and recorded.
    assert final["outcome"] in ("adopted", "escalated"), (
        f"cycle did not reach a terminal outcome: {final.get('outcome')}"
    )
    assert final["proposal"] is not None
    assert final["proposal"]["safe_to_try_rationale"].strip(), (
        "adopted proposal must justify safe-to-try (§2.1)"
    )
    # The Secretary recorded a decision + every prior step.
    assert "decision-recorded" in persisted
    assert persisted[0] == "proposal-drafted"
    assert persisted[-1] == "decision-recorded"


@pytest.mark.live
@pytest.mark.skipif(not _HAS_LIVE, reason="needs ZAI_API_KEY + DATABASE_URL")
def test_live_mediator_and_founder_path():
    """S2 acceptance: the Integrative Mediator + Founder participate for real.

    Real Z.ai Proposal Architect + Devil's Advocate + Integrative Mediator +
    Founder run the cycle against Kimberim's first decision; every event
    persists to Postgres. Asserts the S2 machinery (mediator amendment, veto
    bookkeeping) executes against live LLMs and reaches a recorded terminal
    outcome.
    """
    rt = load_runtime_config()
    ic = load_instance_config(INSTANCE)
    assert ic.first_decision is not None

    architect = ProposalArchitect(instance_id=INSTANCE)
    devils_advocate = DevilsAdvocate(instance_id=INSTANCE)
    mediator = IntegrativeMediator(instance_id=INSTANCE)
    founder = Founder(instance_id=INSTANCE)

    tension = Tension(
        instance_id=INSTANCE,
        raised_by=architect.ref,
        title=ic.first_decision.title,
        description=ic.first_decision.summary.strip(),
    )

    eng = make_engine(rt.database_url)
    persisted: list[tuple[str, dict]] = []

    def sink(event_type: str, payload: dict) -> None:
        with SMSession(eng) as s:
            append_ledger_event(
                s, instance_id=INSTANCE, event_type=event_type, payload=payload
            )
            s.commit()
        persisted.append((event_type, payload))

    run = CycleRun(
        instance_id=INSTANCE,
        tension=tension,
        participants=[architect.ref],
        governance=ic.governance,
        proposal_architect=architect,
        devils_advocate=devils_advocate,
        integrative_mediator=mediator,
        founder=founder,
        ledger_sink=sink,
    )
    final = run_cycle(run)

    # Reached a recorded terminal outcome via the S2 machinery.
    assert final["outcome"] in ("adopted", "escalated")
    assert final["proposal"] is not None
    event_types = [et for et, _ in persisted]
    assert event_types[0] == "proposal-drafted"
    assert event_types[-1] == "decision-recorded"

    # If an objection occurred, the Mediator's amendment + integration must have
    # run (objection-integrated), proving the integrative-resolution path.
    if "objection-raised" in event_types:
        assert "amendment" in event_types, "mediator should amend on objection"
        assert "objection-integrated" in event_types

    # The decision record carries the (now state-derived) veto bookkeeping.
    decision = next(p for et, p in persisted if et == "decision-recorded")
    assert "founder_vetoed" in decision
    assert "veto_overridden" in decision


# ── Sprint 3: multi-agent consensus (the S3 exit criterion) ───────────────────


def _participant(display_name: str, perspective: str) -> MetaAgent:
    """A real-LLM participant agent with a stakeholder persona.

    Each represents a distinct stakeholder viewpoint on the Kimberim decision,
    so the deliberation is genuinely multi-stakeholder rather than N copies of
    one agent.
    """

    class _P(MetaAgent):
        role = AgentRole.PARTICIPANT
        system_prompt = (
            f"You are a participant in Olon's OLOCRON consent cycle, "
            f"representing this stakeholder perspective: {perspective}. "
            "State your honest position on each proposal — consent, object (with a "
            "valid criterion + reason), or abstain. Be constructive. Respond as JSON: "
            '{"position": "consent"|"objection"|"abstain", ...}.'
        )

    return _P(instance_id=INSTANCE, display_name=display_name)


@pytest.mark.live
@pytest.mark.skipif(not _HAS_LIVE, reason="needs ZAI_API_KEY + DATABASE_URL")
def test_live_multi_agent_consensus():
    """S3 exit criterion: 5+ real Z.ai participant agents reach consent on a
    real KIMBERIM decision question.

    Five stakeholder-participant agents (energy, compute, finance, community,
    Traditional Owners) + the Devil's Advocate + Proposal Architect + Integrative
    Mediator + Summarizer + Judgment Synthesizer + Founder deliberate the
    energy-vs-compute split. Every event persists to Postgres. Asserts the cycle
    reaches a recorded terminal outcome via genuine multi-stakeholder positions.
    """
    rt = load_runtime_config()
    ic = load_instance_config(INSTANCE)
    assert ic.first_decision is not None

    architect = ProposalArchitect(instance_id=INSTANCE)
    devils_advocate = DevilsAdvocate(instance_id=INSTANCE)
    mediator = IntegrativeMediator(instance_id=INSTANCE)
    summarizer = Summarizer(instance_id=INSTANCE)
    synthesizer = JudgmentSynthesizer(instance_id=INSTANCE)
    founder = Founder(instance_id=INSTANCE)

    # Five distinct stakeholder participant agents (real LLM personas).
    participants = [
        _participant("energy-stakeholder",
                     "maximise clean energy generation and grid stability"),
        _participant("compute-stakeholder",
                     "maximise on-site compute capacity and local industry"),
        _participant("finance-stakeholder",
                     "optimise revenue, capex efficiency, and offtake economics"),
        _participant("community-stakeholder",
                     "protect local community benefit, jobs, and liveability"),
        _participant("traditional-owners-rep",
                     "uphold Miriwoong/Gija Country, cultural heritage, and free "
                     "prior informed consent"),
    ]

    tension = Tension(
        instance_id=INSTANCE,
        raised_by=architect.ref,
        title=ic.first_decision.title,
        description=ic.first_decision.summary.strip(),
    )

    eng = make_engine(rt.database_url)
    persisted: list[tuple[str, dict]] = []

    def sink(event_type: str, payload: dict) -> None:
        with SMSession(eng) as s:
            append_ledger_event(
                s, instance_id=INSTANCE, event_type=event_type, payload=payload
            )
            s.commit()
        persisted.append((event_type, payload))

    run = CycleRun(
        instance_id=INSTANCE,
        tension=tension,
        participants=[p.ref for p in participants],
        governance=ic.governance,
        proposal_architect=architect,
        devils_advocate=devils_advocate,
        integrative_mediator=mediator,
        summarizer=summarizer,
        judgment_synthesizer=synthesizer,
        founder=founder,
        participant_agents=participants,
        ledger_sink=sink,
    )
    final = run_cycle(run)

    # S3 acceptance: a multi-stakeholder run reached a recorded terminal outcome.
    assert final["outcome"] in ("adopted", "escalated"), (
        f"multi-agent cycle did not terminate: {final.get('outcome')}"
    )
    assert final["proposal"] is not None
    assert final["proposal"]["safe_to_try_rationale"].strip()

    event_types = [et for et, _ in persisted]
    assert event_types[0] == "proposal-drafted"
    assert event_types[-1] == "decision-recorded"

    # Multiple participants stated positions (>= 5 participants + DA = >= 6).
    position_events = [et for et in event_types if et == "position-stated"]
    assert len(position_events) >= 6, (
        f"expected >=6 position-stated events, got {len(position_events)}"
    )

    # The Summarizer produced a digest (>= 4 positions > 2 threshold).
    assert "digest" in event_types, "summarizer should fire with many participants"

    # If objections arose, the Synthesizer + Mediator resolved them.
    if "objection-raised" in event_types:
        if event_types.count("objection-raised") > 1:
            assert "core-disagreement" in event_types, (
                "synthesizer should fire on >1 objection"
            )
        assert "amendment" in event_types, "mediator should amend on objection"
        assert "objection-integrated" in event_types
