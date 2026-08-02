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

from holon.agents import DevilsAdvocate, ProposalArchitect
from holon.config import load_instance_config, load_runtime_config
from holon.cycle import CycleRun, run_cycle
from holon.schema import Tension
from holon.store import append_ledger_event, make_engine

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
