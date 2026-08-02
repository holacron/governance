"""The consent-cycle node functions + transition guards (ADR 0001).

Each node is a pure function: (CycleState, CycleRun) -> CycleState delta. They
call the relevant meta-agent, parse its JSON into a schema model, and (via the
Secretary) emit a LedgerEvent. Nodes never mutate shared state directly — they
return the keys to merge, keeping the graph reproducible.

Guard functions return string keys that must match named conditional edges in
graph.py. Those keys live here as module constants to avoid typos (the risk
called out in the S1 plan).
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any
from uuid import uuid4

from holon.cycle.state import CycleRun, CycleState
from holon.schema import (
    AgentRef,
    AgentRole,
    Objection,
    ObjectionValidity,
    Proposal,
    Vote,
    VoteKind,
)

log = logging.getLogger(__name__)

# ── Guard return-value constants (named edges in graph.py) ────────────────────
# Centralised so a typo is a NameError, not a silent misroute.
G_TO_DRAFT = "to_draft"                       # TENSION_RAISED -> PROPOSAL_DRAFTED
G_OBJECTIONS = "has_objections"               # OBJECTING -> INTEGRATING
G_NO_OBJECTIONS = "no_objections"             # OBJECTING -> CONSENT_TEST
G_LOOP_CAP = "loop_cap"                       # INTEGRATING -> ESCALATED
G_RETEST = "retest"                           # INTEGRATING -> OBJECTING
G_CONSENT = "consent"                         # CONSENT_TEST -> FOUNDER_VETO_WINDOW
G_NO_CONSENT = "no_consent"                   # CONSENT_TEST -> INTEGRATING
G_VETO = "veto"                               # VETO_WINDOW -> PROPOSAL_DRAFTED
G_NO_VETO = "no_veto"                         # VETO_WINDOW -> ADOPTED


# ── Helpers ───────────────────────────────────────────────────────────────────


def _emit(run: CycleRun, event_type: str, payload: dict) -> None:
    """Send a structured event to the ledger sink (if any). Non-fatal if absent."""
    if run.ledger_sink is not None:
        try:
            run.ledger_sink(event_type, payload)
        except Exception as e:  # noqa: BLE001
            log.warning("ledger sink failed (non-fatal): %s", e)


def _extract_json(text: str) -> dict[str, Any]:
    """Tolerantly extract the first {...} object from an LLM response."""
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        return {}
    return json.loads(m.group(0))


def _architect_ref(run: CycleRun) -> AgentRef:
    """The AgentRef to attribute proposals to (the architect's ref)."""
    a = run.proposal_architect
    ref = getattr(a, "ref", None)
    if ref is not None:
        return ref
    return AgentRef(instance_id=run.instance_id, role=AgentRole.PROPOSAL_ARCHITECT)


# ── Nodes ─────────────────────────────────────────────────────────────────────


def draft(state: CycleState, run: CycleRun) -> CycleState:
    """PROPOSAL_DRAFTED — Proposal Architect turns the tension into a proposal."""
    tension_payload = state["tension"]
    architect = run.proposal_architect
    if architect is None:
        raise RuntimeError("CycleRun has no proposal_architect")

    prompt = (
        "Draft ONE proposal as a JSON object with keys: "
        "title, context, change, expected_impact, safe_to_try_rationale.\n"
        f"Tension title: {tension_payload.get('title')}\n"
        f"Tension description: {tension_payload.get('description')}"
    )
    text = architect.respond(prompt, max_tokens=600, temperature=0.4)
    payload = _extract_json(text)
    proposal = Proposal(
        instance_id=run.instance_id,
        tension_id=tension_payload["id"],
        drafted_by=_architect_ref(run),
        title=payload.get("title", "(untitled proposal)"),
        context=payload.get("context", ""),
        change=payload.get("change", ""),
        expected_impact=payload.get("expected_impact", ""),
        safe_to_try_rationale=payload.get("safe_to_try_rationale", ""),
    )
    pd = proposal.model_dump(mode="json")
    _emit(run, "proposal-drafted", pd)
    return {
        "state": "proposal-drafted",
        "proposal": pd,
        # Reset per-round collections when a new proposal is drafted.
        "objections": [],
        "votes": [],
    }


def object_round(state: CycleState, run: CycleRun) -> CycleState:
    """OBJECTING — the Devil's Advocate (mandatory) raises any valid objection.

    S1 uses only the Devil's Advocate as the objector (mandatory per ADR). S3
    adds participant agents. Returns the objections list for this round.
    """
    da = run.devils_advocate
    if da is None:
        raise RuntimeError("CycleRun has no devils_advocate")

    proposal_payload = state["proposal"]
    prompt = (
        "Object to this proposal ONLY if it causes harm, is not safe to try, "
        "or regresses a role. If you object, respond as JSON with keys: "
        "criterion (one of causes-harm|not-safe-to-try|regresses-role), reason. "
        "If you have NO valid objection, respond exactly: {\"objection\": false}.\n\n"
        f"Proposal: {json.dumps(proposal_payload)}"
    )
    text = da.respond(prompt, max_tokens=400, temperature=0.3)
    payload = _extract_json(text)

    objections: list[dict] = []
    if payload.get("objection", True) is not False and "criterion" in payload:
        ob = Objection(
            instance_id=run.instance_id,
            proposal_id=proposal_payload["id"],
            raised_by=getattr(da, "ref", AgentRef(
                instance_id=run.instance_id, role=AgentRole.DEVILS_ADVOCATE)),
            reason=payload.get("reason", ""),
            criterion=payload["criterion"],
            validity=ObjectionValidity.VALID,
        )
        od = ob.model_dump(mode="json")
        _emit(run, "objection-raised", od)
        objections.append(od)
    return {"state": "objecting", "objections": objections}


def integrate(state: CycleState, run: CycleRun) -> CycleState:
    """INTEGRATING — amend the proposal to address objections (ADR open-q #1:
    a withdrawn/reduced objection is handled here). Increments the loop counter.
    """
    rounds = state.get("integration_rounds", 0) + 1
    cap = run.governance.integration_loop_cap
    if rounds > cap:
        # Loop cap hit — escalate (ADR).
        _emit(run, "escalation", {"reason": "integration_loop_cap_exceeded", "rounds": rounds})
        return {"state": "escalated", "integration_rounds": rounds, "outcome": "escalated"}

    # Ask the architect to amend the proposal in light of the objections.
    architect = run.proposal_architect
    proposal_payload = state["proposal"]
    objections = state.get("objections", [])
    if architect is not None and objections:
        prompt = (
            "Amend this proposal to address the objection(s). Respond as the SAME "
            "JSON shape (title, context, change, expected_impact, safe_to_try_rationale).\n"
            f"Current proposal: {json.dumps(proposal_payload)}\n"
            f"Objections: {json.dumps(objections)}"
        )
        text = architect.respond(prompt, max_tokens=600, temperature=0.4)
        payload = _extract_json(text)
        if payload:
            merged = {**proposal_payload, **payload}
            merged["id"] = str(uuid4())  # amended proposal = new artefact
            _emit(run, "amendment", merged)
            for ob in objections:
                _emit(run, "objection-integrated", {"objection_id": ob["id"]})
            return {
                "state": "integrating",
                "proposal": merged,
                "integration_rounds": rounds,
            }
    return {"integration_rounds": rounds}


def consent_test(state: CycleState, run: CycleRun) -> CycleState:
    """CONSENT_TEST — weighted tally: consent if no valid objection remains.

    S1: the Devil's Advocate is the sole objector; 'no objections this round' =
    consent. S3 generalises to weighted participant votes. Abstain counts as
    neither (governance.abstain_counts_as == 'neither', ADR open-q #2).
    """
    objections = state.get("objections", [])
    votes: list[dict] = []
    if not objections:
        v = Vote(
            instance_id=run.instance_id,
            proposal_id=state["proposal"]["id"],
            cast_by=getattr(run.devils_advocate, "ref", AgentRef(
                instance_id=run.instance_id, role=AgentRole.DEVILS_ADVOCATE)),
            kind=VoteKind.CONSENT,
        )
        votes.append(v.model_dump(mode="json"))
        _emit(run, "consent-reached", {"proposal_id": state["proposal"]["id"]})
        return {"state": "consent-test", "votes": votes}
    # Re-collect objections: send back through integration.
    v = Vote(
        instance_id=run.instance_id,
        proposal_id=state["proposal"]["id"],
        cast_by=getattr(run.devils_advocate, "ref", AgentRef(
            instance_id=run.instance_id, role=AgentRole.DEVILS_ADVOCATE)),
        kind=VoteKind.OBJECTION,
    )
    votes.append(v.model_dump(mode="json"))
    _emit(run, "vote-cast", v.model_dump(mode="json"))
    return {"state": "consent-test", "votes": votes}


def veto_window(state: CycleState, run: CycleRun) -> CycleState:
    """FOUNDER_VETO_WINDOW — in S1 the founder is a stub that does not veto.

    The veto_round counter and override path exist but are not exercised (needs
    reputation weighting, S10). This node advances to ADOPTED by default.
    """
    # Hook for a future founder agent: if it vetoes with a reason, increment
    # veto_rounds and return to PROPOSAL_DRAFTED. For S1, no veto.
    return {"state": "founder-veto-window"}


def record(state: CycleState, run: CycleRun) -> CycleState:
    """Terminal — Secretary records the Decision + a decision-recorded event.

    outcome is already set by the path that reached here (adopted/escalated).
    """
    outcome = state.get("outcome") or "adopted"
    proposal_id = state["proposal"]["id"] if state.get("proposal") else None
    decision_payload = {
        "instance_id": run.instance_id,
        "proposal_id": proposal_id,
        "outcome": outcome,
        "weighted_consent": float(len([v for v in state.get("votes", [])
                                       if v.get("kind") == "consent"])),
        "weighted_objection": float(len([v for v in state.get("votes", [])
                                         if v.get("kind") == "objection"])),
        "founder_vetoed": False,
        "veto_overridden": False,
    }
    _emit(run, "decision-recorded", decision_payload)
    final_state = "adopted" if outcome == "adopted" else ("escalated" if outcome == "escalated"
                                                          else "rejected")
    return {"state": final_state, "outcome": outcome}


# ── Guards (conditional-edge routers) ─────────────────────────────────────────


def route_after_objecting(state: CycleState) -> str:
    objections = state.get("objections", [])
    return G_OBJECTIONS if objections else G_NO_OBJECTIONS


def route_after_integrating(state: CycleState) -> str:
    if state.get("outcome") == "escalated":
        return G_LOOP_CAP
    return G_RETEST


def route_after_consent_test(state: CycleState) -> str:
    votes = state.get("votes", [])
    consented = any(v.get("kind") == "consent" for v in votes)
    return G_CONSENT if consented else G_NO_CONSENT


def route_after_veto(state: CycleState, run: CycleRun) -> str:
    # S1: founder never vetoes. Hook for S10's override logic.
    return G_NO_VETO


__all__ = [
    "G_CONSENT",
    "G_LOOP_CAP",
    "G_NO_CONSENT",
    "G_NO_OBJECTIONS",
    "G_NO_VETO",
    "G_OBJECTIONS",
    "G_RETEST",
    "G_TO_DRAFT",
    "G_VETO",
    "consent_test",
    "draft",
    "integrate",
    "object_round",
    "record",
    "route_after_consent_test",
    "route_after_integrating",
    "route_after_objecting",
    "route_after_veto",
    "veto_window",
]
