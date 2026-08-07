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
from holon.utils import extract_json as _extract_json

log = logging.getLogger(__name__)

# ── Guard return-value constants (named edges in graph.py) ────────────────────
# Centralised so a typo is a NameError, not a silent misroute.
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
        # H2: also reset the integration loop budget. A veto-reworked proposal is
        # a NEW artefact and must get its full integration budget; LangGraph
        # merges state, so without this the reworked proposal inherits the prior
        # one's depleted counter (could already be at the cap → instant escalate).
        "integration_rounds": 0,
    }


def object_round(state: CycleState, run: CycleRun) -> CycleState:
    """OBJECTING — every participant + the mandatory Devil's Advocate states a
    position on the proposal (ROADMAP §2.4, S3 row).

    Each agent returns {"position": "consent"|"objection"|"abstain", ...}. An
    objection becomes a structured Objection (causes-harm / not-safe-to-try /
    regresses-role). The DA is always consulted (ADR: mandatory red-team).

    If a Summarizer is wired AND >2 agents responded, it compresses the round's
    positions into a digest (the scalability workhorse). When participant_agents
    is empty → S2 back-compat (DA only).
    """
    da = run.devils_advocate
    if da is None:
        raise RuntimeError("CycleRun has no devils_advocate")

    proposal_payload = state["proposal"]
    proposal_json = json.dumps(proposal_payload)

    objections: list[dict] = []
    positions: list[dict] = []

    # ── Position prompt shared by participants and the DA ────────────────────
    position_prompt = (
        "State your position on this proposal. Respond as JSON: "
        '{"position": "consent" | "objection" | "abstain"}. If you object, ALSO '
        'include "criterion" (one of causes-harm|not-safe-to-try|regresses-role) '
        'and "reason". Only object if the proposal causes harm, is not safe to '
        f'try, or regresses a role. Proposal: {proposal_json}'
    )

    def _record(agent, *, mandatory: bool = False) -> None:
        """Ask one agent for its position; record position + any objection.

        Tolerates BOTH response formats for back-compat with S1/S2 stubs:
          - new (S3): {"position": "consent"|"objection"|"abstain", ...}
          - old (S1/S2): {"objection": false} -> consent; {"criterion":...} -> objection
        """
        ref = getattr(agent, "ref", None) or AgentRef(instance_id=run.instance_id)
        text = agent.respond(position_prompt, max_tokens=400, temperature=0.3)
        payload = _extract_json(text)

        # Resolve the position from whichever format the agent used.
        if "position" in payload:
            pos = payload.get("position", "abstain").strip().lower()
        elif payload.get("objection", True) is False:
            pos = "consent"  # old format: explicit no-objection == consent
        elif "criterion" in payload:
            pos = "objection"  # old format: a criterion means an objection
        else:
            pos = "abstain"
        if pos not in ("consent", "objection", "abstain"):
            pos = "abstain"

        positions.append({"agent_id": str(ref.agent_id), "position": pos,
                          "display_name": ref.display_name, "weight": ref.weight})
        _emit(run, "position-stated", {"agent_id": str(ref.agent_id), "position": pos})
        if pos == "objection":
            criterion = payload.get("criterion", "not-safe-to-try")
            if criterion not in ("causes-harm", "not-safe-to-try", "regresses-role"):
                criterion = "not-safe-to-try"
            ob = Objection(
                instance_id=run.instance_id,
                proposal_id=proposal_payload["id"],
                raised_by=ref,
                reason=payload.get("reason", ""),
                criterion=criterion,
                validity=ObjectionValidity.VALID,
            )
            od = ob.model_dump(mode="json")
            _emit(run, "objection-raised", od)
            objections.append(od)

    # S3: every participant agent states a position.
    for agent in getattr(run, "participant_agents", []) or []:
        _record(agent)
    # The Devil's Advocate is always consulted (mandatory red-team).
    _record(da, mandatory=True)

    # S3: optionally compress when there are many positions.
    digest: dict | None = None
    summarizer = getattr(run, "summarizer", None)
    if summarizer is not None and len(positions) > 2:
        digest_text = summarizer.respond(
            f"Compress these positions into a digest. Positions: {json.dumps(positions)}",
            max_tokens=300, temperature=0.2,
        )
        digest = _extract_json(digest_text)
        if digest:
            _emit(run, "digest", digest)

    return {"state": "objecting", "objections": objections,
            "positions": positions, "digest": digest}


def integrate(state: CycleState, run: CycleRun) -> CycleState:
    """INTEGRATING — the Integrative Mediator amends the proposal to address
    objections (ROADMAP §2.4), then objections are re-tested.

    Falls back to the Proposal Architect if no Mediator is wired (S1 back-compat).
    Increments the loop counter; at the cap, escalates (ADR §2.5). Each addressed
    objection is marked `integrated: true` (the schema field, previously unset).
    Withdraw (ADR open-q #1) is modelled by the objector raising no objection on
    re-test — handled naturally by object_round on the next pass.
    """
    rounds = state.get("integration_rounds", 0) + 1
    cap = run.governance.integration_loop_cap
    if rounds > cap:
        # Loop cap hit — escalate (ADR).
        _emit(run, "escalation", {"reason": "integration_loop_cap_exceeded", "rounds": rounds})
        return {"state": "escalated", "integration_rounds": rounds, "outcome": "escalated"}

    # Prefer the Mediator (S2); fall back to the architect (S1 back-compat).
    amender = run.integrative_mediator or run.proposal_architect
    proposal_payload = state["proposal"]
    objections = state.get("objections", [])
    if amender is not None and objections:
        # S3: when >1 objection, the Judgment Synthesizer pinpoints the core
        # disagreement so the Mediator fixes the root cause, not every surface
        # objection.
        core_disagreement = ""
        synthesizer = getattr(run, "judgment_synthesizer", None)
        if synthesizer is not None and len(objections) > 1:
            syn_text = synthesizer.respond(
                "Identify the single core disagreement underlying these objections. "
                f"Objections: {json.dumps(objections)}",
                max_tokens=300, temperature=0.2,
            )
            syn_payload = _extract_json(syn_text)
            core_disagreement = syn_payload.get("core_disagreement", "") if syn_payload else ""
            if core_disagreement:
                _emit(run, "core-disagreement", {"core_disagreement": core_disagreement})

        prompt = (
            "Amend this proposal to address the objection(s) while keeping it safe to "
            "try (reversible, regresses no role). Respond as the SAME JSON shape "
            "(title, context, change, expected_impact, safe_to_try_rationale).\n"
            f"Current proposal: {json.dumps(proposal_payload)}\n"
            f"Objections: {json.dumps(objections)}"
        )
        # H8: wire the round's Summarizer digest into the Mediator's context.
        # The digest was computed in object_round but never surfaced to the
        # amender — so the Mediator amended blind to the round's consensus shape
        # and stated concerns. Now it sees them, so it can resolve objections in
        # light of what the whole circle actually said.
        digest = state.get("digest")
        if digest:
            prompt += f"\nRound digest (Summarizer compression of all positions): {json.dumps(digest)}"
        if core_disagreement:
            prompt += (
                f"\nCore disagreement to resolve (per the Judgment Synthesizer): "
                f"{core_disagreement}"
            )
        text = amender.respond(prompt, max_tokens=600, temperature=0.4)
        payload = _extract_json(text)
        if payload:
            merged = {**proposal_payload, **payload}
            merged["id"] = str(uuid4())  # amended proposal = new artefact
            _emit(run, "amendment", merged)
            # Mark each objection integrated (schema field now set) + emit events.
            for ob in objections:
                _emit(run, "objection-integrated", {
                    "objection_id": ob["id"], "integrated": True,
                })
            return {
                "state": "integrating",
                "proposal": merged,
                "integration_rounds": rounds,
                "core_disagreement": core_disagreement,
            }
    return {"integration_rounds": rounds}


def consent_test(state: CycleState, run: CycleRun) -> CycleState:
    """CONSENT_TEST — record a formal weighted Vote per participant.

    In holacracy, objections are resolved in the object↔integrate loop BEFORE
    consent is tested, so consent_test is reached only with no objections this
    round — everyone has either consented or abstained. This node records a
    Vote (with AgentRef.weight) for each agent who stated a position, computes
    the weighted tally, and emits consent-reached.

    Abstain counts as neither consent nor objection by default
    (governance.abstain_counts_as == 'neither', ADR open-q #2). If set to
    'consent', abstainers inflate the consent weight.

    S2 back-compat: when no participant positions exist, the DA casts the
    single consent vote (the original S1 behaviour).
    """
    objections = state.get("objections", [])
    proposal_id = state["proposal"]["id"]
    da_ref = getattr(run.devils_advocate, "ref", AgentRef(
        instance_id=run.instance_id, role=AgentRole.DEVILS_ADVOCATE))
    positions = state.get("positions", [])

    # Defensive: if somehow reached WITH objections (shouldn't happen via the
    # current routing), re-route to integration by casting an objection vote.
    #
    # DEAD in S4: in the current graph, objections route object -> integrate and
    # never reach consent_test, so this branch is unreachable today. It is kept
    # (not deleted) because S5 (multi-objector weighted tally / re-routed
    # consent_test) may revive it. If you find yourself here, the routing has
    # changed — treat the presence of objections as a real signal, not a bug.
    if objections:
        v = Vote(
            instance_id=run.instance_id, proposal_id=proposal_id, cast_by=da_ref,
            kind=VoteKind.OBJECTION, objection_id=objections[0]["id"],
        )
        _emit(run, "vote-cast", v.model_dump(mode="json"))
        return {"state": "consent-test", "votes": [v.model_dump(mode="json")]}

    votes: list[dict] = []
    if positions:
        # S3: a Vote per participant, weighted, kind from their stated position.
        # Build a lookup of agent_id -> position so each vote matches the round.
        for pos in positions:
            ref = AgentRef(
                agent_id=pos["agent_id"], instance_id=run.instance_id,
                role=AgentRole.PARTICIPANT,
                display_name=pos.get("display_name", ""),
                weight=pos.get("weight", 1.0),
            )
            kind = {"consent": VoteKind.CONSENT,
                    "abstain": VoteKind.ABSTAIN}.get(pos["position"], VoteKind.ABSTAIN)
            v = Vote(
                instance_id=run.instance_id, proposal_id=proposal_id,
                cast_by=ref, kind=kind,
            )
            vd = v.model_dump(mode="json")
            votes.append(vd)
            _emit(run, "vote-cast", vd)
    else:
        # S2 back-compat: DA casts the single consent vote.
        v = Vote(
            instance_id=run.instance_id, proposal_id=proposal_id,
            cast_by=da_ref, kind=VoteKind.CONSENT,
        )
        votes.append(v.model_dump(mode="json"))

    # Weighted tally (consulting abstain_counts_as).
    abstain_as = run.governance.abstain_counts_as
    weighted_consent = 0.0
    for vd in votes:
        w = vd["cast_by"]["weight"]
        if vd["kind"] == "consent":
            weighted_consent += w
        elif vd["kind"] == "abstain" and abstain_as == "consent":
            weighted_consent += w

    _emit(run, "consent-reached", {
        "proposal_id": proposal_id,
        "weighted_consent": weighted_consent,
        "votes": len(votes),
    })
    return {"state": "consent-test", "votes": votes}


def veto_window(state: CycleState, run: CycleRun) -> CycleState:
    """FOUNDER_VETO_WINDOW — the founder may veto a consented proposal (§2.3).

    - No founder wired, or founder does not veto → proceed to ADOPTED.
    - Founder vetoes WITH a reason → emit `founder-veto`, increment veto_rounds.
      If veto_rounds < veto_round_cap → route back to draft (rework loop).
      If veto_rounds >= veto_round_cap → the stubbed override: proceed anyway,
      emitting `veto-override` with veto_overridden=True (the real reputation-
      weighted 75% override is S10).

    S2's window is synchronous/in-process; S6 owns real async windows.
    """
    founder = run.founder
    # No founder wired → S1 back-compat: no veto, proceed.
    if founder is None:
        return {"state": "founder-veto-window", "founder_vetoed": False, "veto_overridden": False}

    proposal_payload = state["proposal"]
    prompt = (
        "The agents have reached consent on this proposal. As the founder, decide "
        "whether to VETO it (send it back for rework) or let it proceed. Respond as "
        f'JSON: {{"veto": bool, "reason": str}}. Default to proceeding. Proposal: '
        f"{json.dumps(proposal_payload)}"
    )
    text = founder.respond(prompt, max_tokens=300, temperature=0.2)
    payload = _extract_json(text)
    wants_veto = bool(payload.get("veto", False))

    if not wants_veto:
        # No veto: explicitly clear veto flags. LangGraph merges state, so a
        # stale True from a prior round must be overwritten or route_after_veto
        # misroutes.
        return {
            "state": "founder-veto-window",
            "founder_vetoed": False,
            "veto_overridden": False,
        }

    # H4: the governance rule is reason-given, but the correct response to a
    # MISSING reason is to SURFACE it (visible in the ledger), not to silently
    # override the founder's stated intent. A veto without a reason still counts
    # as a veto — flagged so a missing reason is never silently swallowed.
    reason = payload.get("reason", "").strip()
    if not reason:
        reason = "(no reason provided)"
        missing_reason = True
    else:
        missing_reason = False

    # Veto with reason: emit + increment.
    veto_rounds = state.get("veto_rounds", 0) + 1
    _emit(run, "founder-veto", {
        "proposal_id": proposal_payload["id"],
        "reason": reason,
        "reason_missing": missing_reason,
        "veto_rounds": veto_rounds,
    })

    # Stubbed override: past the cap, the veto is overruled and we proceed.
    if veto_rounds >= run.governance.veto_round_cap:
        _emit(run, "veto-override", {
            "proposal_id": proposal_payload["id"],
            "veto_rounds": veto_rounds,
            "note": "stubbed override (proceed-after-cap); real 75% override is S10",
        })
        return {
            "state": "founder-veto-window",
            "veto_rounds": veto_rounds,
            "founder_vetoed": True,
            "veto_overridden": True,
        }

    # Within the cap: signal a rework (route_after_veto routes to draft).
    return {
        "state": "founder-veto-window",
        "veto_rounds": veto_rounds,
        "founder_vetoed": True,
        # A pending veto reason could seed the next tension; carried in state.
        # Uses the surfaced reason (flagged if the founder omitted one).
        "veto_reason": reason,
    }


def record(state: CycleState, run: CycleRun) -> CycleState:
    """Terminal — Secretary records the Decision + a decision-recorded event.

    outcome is already set by the path that reached here (adopted/escalated).
    Veto bookkeeping is read from state (set by veto_window) — no longer
    hard-coded.
    """
    outcome = state.get("outcome") or "adopted"
    proposal_id = state["proposal"]["id"] if state.get("proposal") else None
    # H3: sum AgentRef.weight per vote — NOT a head count. The fields are named
    # weighted_*; consent_test already computes a real weighted sum for its own
    # event. record() now matches that logic so the recorded Decision reflects
    # the actual reputation-weighted consent/objection weight of the round.
    # Consult abstain_counts_as so abstain-as-consent tallies consistently.
    abstain_as = run.governance.abstain_counts_as
    weighted_consent = 0.0
    weighted_objection = 0.0
    for v in state.get("votes", []):
        w = float(v.get("cast_by", {}).get("weight", 1.0))
        kind = v.get("kind")
        if kind == "consent":
            weighted_consent += w
        elif kind == "objection":
            weighted_objection += w
        elif kind == "abstain" and abstain_as == "consent":
            weighted_consent += w
    decision_payload = {
        "instance_id": run.instance_id,
        "tension_id": state.get("tension", {}).get("id"),
        "proposal_id": proposal_id,
        "outcome": outcome,
        "weighted_consent": weighted_consent,
        "weighted_objection": weighted_objection,
        "founder_vetoed": bool(state.get("founder_vetoed", False)),
        "veto_overridden": bool(state.get("veto_overridden", False)),
    }
    _emit(run, "decision-recorded", decision_payload)
    # S5: close the loop — let the live path persist a DecisionRow + mark the
    # source tension decided. Non-fatal (unit tests leave on_decision=None).
    if run.on_decision is not None:
        try:
            run.on_decision(decision_payload)
        except Exception as e:  # noqa: BLE001
            log.warning("on_decision callback failed (non-fatal): %s", e)
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


def route_after_veto(state: CycleState) -> str:
    """Route after the founder veto window (single-arg: LangGraph passes state).

    G_VETO   → back to draft (rework) when the founder vetoed within the cap.
    G_NO_VETO → to record when no veto, or past the cap (stubbed override).
    """
    if state.get("founder_vetoed") and not state.get("veto_overridden", False):
        return G_VETO
    return G_NO_VETO


__all__ = [
    "G_CONSENT",
    "G_LOOP_CAP",
    "G_NO_CONSENT",
    "G_NO_OBJECTIONS",
    "G_NO_VETO",
    "G_OBJECTIONS",
    "G_RETEST",
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
