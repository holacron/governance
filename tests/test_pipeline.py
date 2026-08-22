"""Sprint 0 exit-criterion test (ROADMAP §8, S0):

> "One internal agent emits a valid proposal object; pipeline wired + tested."

Two layers:
  - Unit tests (run in CI, no credentials needed): the pipeline composes
    correctly — a Proposal is well-formed and persists to the ledger shape.
  - A live test (marked, needs ZAI_API_KEY + DATABASE_URL): an LLM-backed
    Proposal Architect actually emits a valid Proposal end-to-end.

The live test is the real acceptance; the unit test is the always-green floor.
"""

from __future__ import annotations

import json
import os
import re

import pytest
from dotenv import load_dotenv
from sqlmodel import Session as SMSession

from olon.config import load_instance_config, load_runtime_config
from olon.gateway import LLMGateway
from olon.schema import AgentRef, AgentRole, Proposal, Tension
from olon.store import append_ledger_event, make_engine

# Load .env at import time so the live-test skip check can see credentials.
load_dotenv()

INSTANCE = "kimberim"


# ── Unit: pipeline composition (always green floor) ───────────────────────────


def _stub_architect() -> AgentRef:
    return AgentRef(
        instance_id=INSTANCE,
        role=AgentRole.PROPOSAL_ARCHITECT,
        display_name="Proposal Architect (stub)",
    )


def test_proposal_is_well_formed():
    """A Proposal built from a Tension validates against the canonical schema."""
    arch = _stub_architect()
    tension = Tension(
        instance_id=INSTANCE,
        raised_by=arch,
        title="Compute may crowd out grid revenue",
        description="seed tension",
    )
    proposal = Proposal(
        instance_id=INSTANCE,
        tension_id=tension.id,
        drafted_by=arch,
        title="Cap on-site compute at 30% of generation",
        context="1 GW campus; grid-export vs compute trade-off",
        change="Allocate a maximum of 30% of generation to on-site compute",
        expected_impact="Protects grid-export revenue; defers compute scale-up",
        safe_to_try_rationale="Reversible quarterly decision; regresses no role",
    )
    # Round-trips through JSON (the ledger storage format).
    as_json = proposal.model_dump_json()
    restored = Proposal.model_validate_json(as_json)
    assert restored.id == proposal.id
    assert restored.tension_id == tension.id
    # Safe-to-try rationale is non-empty — the §2.1 consent precondition.
    assert restored.safe_to_try_rationale.strip()


def test_instance_config_has_first_decision():
    """The Kimberim instance pins the MVP test case (energy-vs-compute split)."""
    ic = load_instance_config(INSTANCE)
    assert ic.first_decision is not None
    assert "compute" in ic.first_decision.title.lower()


# ── Live: LLM-backed architect emits a valid Proposal (the acceptance gate) ───

_LIVE_MARK = pytest.mark.live
_HAS_LIVE = bool(os.getenv("ZAI_API_KEY") and os.getenv("DATABASE_URL"))


@_LIVE_MARK
@pytest.mark.skipif(not _HAS_LIVE, reason="needs ZAI_API_KEY + DATABASE_URL")
def test_live_architect_emits_valid_proposal():
    """The roadmap's literal exit criterion: one internal agent (the Proposal
    Architect, LLM-backed via the gateway) emits a valid Proposal, persisted
    to the immutable ledger.
    """
    rt = load_runtime_config()
    gw = LLMGateway(rt)
    ic = load_instance_config(INSTANCE)
    assert ic.first_decision is not None

    arch = AgentRef(
        instance_id=INSTANCE, role=AgentRole.PROPOSAL_ARCHITECT,
        display_name="Proposal Architect",
    )
    tension = Tension(
        instance_id=INSTANCE,
        raised_by=arch,
        title=ic.first_decision.title,
        description=ic.first_decision.summary.strip(),
    )

    # Ask the LLM architect to draft a proposal as JSON.
    seed = "; ".join(ic.first_decision.seed_tensions)
    prompt = (
        "Draft ONE proposal as a JSON object with keys: "
        'title, context, change, expected_impact, safe_to_try_rationale. '
        f"Topic: {ic.first_decision.title}. Tensions: {seed}. "
        "safe_to_try_rationale must explain why it is reversible/regresses no role. "
        "Respond with ONLY the JSON object."
    )
    resp = gw.call_agent(AgentRole.PROPOSAL_ARCHITECT, prompt, max_tokens=600, temperature=0.4)

    # Extract the JSON object from the response (tolerant of code fences).
    m = re.search(r"\{.*\}", resp.text, re.DOTALL)
    assert m, f"no JSON object in response: {resp.text[:200]}"
    payload = json.loads(m.group(0))

    proposal = Proposal(
        instance_id=INSTANCE,
        tension_id=tension.id,
        drafted_by=arch,
        title=payload["title"],
        context=payload.get("context", ""),
        change=payload.get("change", ""),
        expected_impact=payload.get("expected_impact", ""),
        safe_to_try_rationale=payload.get("safe_to_try_rationale", ""),
    )
    assert proposal.safe_to_try_rationale.strip(), "proposal must justify safe-to-try"

    # Persist the proposal + the cycle events to the immutable ledger.
    eng = make_engine(rt.database_url)
    with SMSession(eng) as s:
        append_ledger_event(
            s, instance_id=INSTANCE, event_type="tension-raised",
            payload=tension.model_dump(mode="json"),
        )
        append_ledger_event(
            s, instance_id=INSTANCE, event_type="proposal-drafted",
            payload=proposal.model_dump(mode="json"),
        )
        s.commit()

    # Acceptance: a valid proposal object was emitted and ledgered.
    assert proposal.id is not None
    assert resp.cost_usd >= 0
