"""Holon engage API routes (S4+S5): registration, tension intake, deliberation.

REST:
  POST /instances/{id}/agents        — register an agent ("Welcome an Agent")
  GET  /instances/{id}/agents        — list registered agents
  POST /instances/{id}/tensions      — submit a tension to the backlog (S5)
  GET  /instances/{id}/tensions      — list the backlog (S5)
  GET  /instances/{id}/tensions/{tid} — single tension detail (S5)
  POST /instances/{id}/deliberations — start a cycle (next backlog tension or first_decision)
  GET  /instances/{id}               — instance summary (branding, first_decision)
SSE:
  GET  /deliberations/{run_id}/events — live deliberation event stream
"""

from __future__ import annotations

import asyncio
import json
from uuid import UUID, uuid4

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlmodel import Session as SMSession
from sse_starlette.sse import EventSourceResponse

from holon.agents import TriageGuardian
from holon.api.feed import CLOSE
from holon.api.live import run_deliberation_live
from holon.config import (
    load_instance_config,
    load_runtime_config,
    resolve_cell,
)
from holon.schema import Permission
from holon.store import (
    agent_permissions,
    close_epoch,
    current_epoch,
    get_agent,
    get_epoch,
    get_tension,
    list_agents,
    list_backlog,
    list_epochs,
    make_engine,
    next_tension,
    open_epoch,
    raise_tension,
    register_agent,
    start_epoch,
    triage_tension,
)
from holon.utils import extract_json

router = APIRouter()


# ── Request/response models ──────────────────────────────────────────────────


class AgentRegistration(BaseModel):
    """The 'Welcome an Agent' form payload.

    S6: optional stakeholder_type + functional_domain place the agent in the
    ABAC matrix cell, resolving its permissions + weight at registration.
    """

    display_name: str = Field(..., min_length=1)
    owner: str = ""
    capability: str = ""  # stakeholder perspective / capability
    model: str = ""
    endpoint: str = ""
    api_key: str = Field("", alias="api_key")  # the registered key (opaque to MVP)
    # S6 ABAC taxonomy cell (optional; omitted = pre-S6 participant default).
    stakeholder_type: str | None = None
    functional_domain: str | None = None
    # S7 federation transport: "provider" (platform-proxy) | "endpoint" (self-hosted).
    # Omitted = auto-detect from model/endpoint fields.
    adapter: str | None = None


class AgentOut(BaseModel):
    agent_id: UUID
    display_name: str
    owner: str
    capability: str
    model: str


class TensionSubmission(BaseModel):
    """The tension-intake form payload (S5)."""

    title: str = Field(..., min_length=1)
    description: str = Field(..., min_length=1)
    raised_by: UUID | None = None  # agent_id; defaults to the instance founder
    priority: int = 50


# ── Instance ──────────────────────────────────────────────────────────────────


@router.get("/instances/{instance_id}")
async def instance_summary(instance_id: str) -> JSONResponse:
    ic = load_instance_config(instance_id)
    return JSONResponse({
        "instance_id": ic.instance_id,
        "display_name": ic.display_name,
        "tagline": ic.tagline,
        "first_decision": (
            {
                "id": ic.first_decision.id,
                "title": ic.first_decision.title,
                "summary": ic.first_decision.summary,
            }
            if ic.first_decision
            else None
        ),
        "branding": ic.branding.model_dump(),
        "domain_circles": ic.domain_circles,
    })


@router.get("/instances/{instance_id}/taxonomy")
async def taxonomy(instance_id: str) -> JSONResponse:
    """The instance's stakeholder-type × functional-domain taxonomy + the ABAC
    matrix (S6). Public transparency — the permission structure is visible to
    every participant, consistent with holacracy's fully-public-record rule.
    """
    ic = load_instance_config(instance_id)
    return JSONResponse({
        "instance_id": instance_id,
        "stakeholder_types": ic.taxonomy.stakeholder_types,
        "functional_domains": ic.taxonomy.functional_domains,
        "abac": {
            "weights": ic.abac.weights,
            "permissions": ic.abac.permissions,
            "overrides": ic.abac.overrides,
        },
    })


# ── Agent registration ("Welcome an Agent") ──────────────────────────────────


@router.post("/instances/{instance_id}/agents")
async def register(instance_id: str, body: AgentRegistration) -> JSONResponse:
    rt = load_runtime_config()
    eng = make_engine(rt.database_url)
    # S6: resolve the ABAC cell when a stakeholder_type is given.
    stakeholder_type = body.stakeholder_type or None
    functional_domain = body.functional_domain or None
    perms: set[str] | None = None
    weight = 1.0
    if stakeholder_type is not None:
        ic = load_instance_config(instance_id)
        perms, weight = resolve_cell(ic.abac, stakeholder_type, functional_domain)
    with SMSession(eng) as s:
        row = register_agent(
            s, instance_id=instance_id,
            display_name=body.display_name, owner=body.owner,
            capability=body.capability, model=body.model, endpoint=body.endpoint,
            api_key_enc=body.api_key or "",  # S7: used by the adapter (provider proxy)
            stakeholder_type=stakeholder_type,
            functional_domain=functional_domain,
            permissions=perms,
            weight=weight,
            adapter=body.adapter,
        )
        s.commit()
        return JSONResponse(
            {"agent_id": str(row.agent_id), "display_name": row.display_name,
             "status": "registered", "eligible": True,
             "stakeholder_type": stakeholder_type,
             "permissions": sorted(perms) if perms else None,
             "weight": weight,
             "adapter": body.adapter},
            status_code=201,
        )


@router.get("/instances/{instance_id}/agents")
async def agents(instance_id: str) -> JSONResponse:
    rt = load_runtime_config()
    eng = make_engine(rt.database_url)
    with SMSession(eng) as s:
        rows = list_agents(s, instance_id=instance_id)
    return JSONResponse({"agents": [
        AgentOut(agent_id=r.agent_id, display_name=r.display_name, owner=r.owner,
                 capability=r.capability, model=r.model).model_dump(mode="json")
        for r in rows
    ]})


@router.get("/instances/{instance_id}/agents/{agent_id}")
async def agent_detail(instance_id: str, agent_id: UUID) -> JSONResponse:
    """Single agent detail including its ABAC cell (S6 transparency).

    Returns the resolved permission set + weight so the permission structure is
    fully visible (the holacracy principle that authority is public). 404 if
    the agent doesn't exist or belongs to another instance.
    """
    rt = load_runtime_config()
    eng = make_engine(rt.database_url)
    with SMSession(eng) as s:
        row = get_agent(s, agent_id=agent_id)
        if row is None or row.instance_id != instance_id:
            return JSONResponse({"error": "agent not found"}, status_code=404)
        return JSONResponse({
            "agent_id": str(row.agent_id),
            "instance_id": row.instance_id,
            "display_name": row.display_name,
            "role": row.role,
            "owner": row.owner,
            "capability": row.capability,
            "model": row.model,
            "stakeholder_type": row.stakeholder_type,
            "functional_domain": row.functional_domain,
            "permissions": sorted(agent_permissions(row)),
            "weight": row.weight,
        })


# ── Tension intake & backlog (S5) ────────────────────────────────────────────


def _ensure_founder_agent(s: SMSession, instance_id: str):
    """Get or create the founder's agent_registry row for this instance.

    A Tension.raised_by is a FK to agent_registry, so a submitter needs an
    agent_id. When none is given, we attribute the tension to the instance
    founder (the holacracy default: anyone can raise, the founder sponsors).
    """
    ic = load_instance_config(instance_id)
    founder_name = ic.founder.name if ic.founder else "Founder"
    # Reuse an existing founder agent if one exists for this instance.
    existing = [a for a in list_agents(s, instance_id=instance_id)
                if a.display_name == founder_name and a.role == "founder"]
    if existing:
        return existing[0]
    # S6: resolve the founder ABAC cell so the founder row carries its
    # permissions + weight from the instance matrix.
    perms, weight = resolve_cell(ic.abac, "founder", None)
    row = register_agent(
        s, instance_id=instance_id, display_name=founder_name,
        role="founder", capability="Instance founder",
        stakeholder_type="founder", permissions=perms, weight=weight,
    )
    s.flush()
    return row


def _tension_out(r) -> dict:
    """Serialize a TensionRow for the API (with triage parsed if present)."""
    import json as _json
    triage = None
    if r.triage:
        try:
            triage = _json.loads(r.triage)
        except (ValueError, TypeError):
            triage = None
    return {
        "tension_id": str(r.id),
        "instance_id": r.instance_id,
        "title": r.title,
        "description": r.description,
        "status": r.status,
        "priority": r.priority,
        "raised_by": str(r.raised_by),
        "triage": triage,
        "decision_id": str(r.decision_id) if r.decision_id else None,
        "created_at": r.created_at.isoformat() if r.created_at else None,
    }


@router.post("/instances/{instance_id}/tensions")
async def submit_tension(instance_id: str, body: TensionSubmission) -> JSONResponse:
    """Submit a tension to the instance backlog. Returns 201 with the new id.

    Anyone can raise (holacracy: 'any participant or internal role files a
    structured tension'). If raised_by is omitted, the tension is attributed to
    the instance founder.

    S6 ABAC: if raised_by is given, that agent must hold the 'submit'
    permission (resolved from its taxonomy cell). The founder fallback always
    passes (founders carry submit by default). NULL-permission agents (pre-S6)
    get the participant default {submit, deliberate, vote} → pass.
    """
    rt = load_runtime_config()
    eng = make_engine(rt.database_url)
    with SMSession(eng) as s:
        if body.raised_by is not None:
            raised_by = body.raised_by
            # S6 ABAC submission gate.
            agent = get_agent(s, agent_id=raised_by)
            if agent is None or agent.instance_id != instance_id:
                return JSONResponse(
                    {"error": "raised_by agent not registered on this instance"},
                    status_code=404,
                )
            if Permission.SUBMIT not in agent_permissions(agent):
                return JSONResponse(
                    {"error": f"agent lacks '{Permission.SUBMIT.value}' permission"},
                    status_code=403,
                )
        else:
            founder = _ensure_founder_agent(s, instance_id)
            raised_by = founder.agent_id
        row = raise_tension(
            s, instance_id=instance_id, raised_by_agent_id=raised_by,
            title=body.title, description=body.description, priority=body.priority,
        )
        s.commit()
        return JSONResponse(
            {"tension_id": str(row.id), "status": row.status, "priority": row.priority},
            status_code=201,
        )


@router.get("/instances/{instance_id}/tensions")
async def list_tensions(instance_id: str, status: str | None = None) -> JSONResponse:
    """List the instance backlog, optionally filtered by status."""
    rt = load_runtime_config()
    eng = make_engine(rt.database_url)
    with SMSession(eng) as s:
        rows = list_backlog(s, instance_id=instance_id, status=status)
    return JSONResponse({"tensions": [_tension_out(r) for r in rows]})


@router.get("/instances/{instance_id}/tensions/{tension_id}")
async def get_tension_detail(instance_id: str, tension_id: UUID) -> JSONResponse:
    """Single tension detail including triage assessment + linked decision."""
    rt = load_runtime_config()
    eng = make_engine(rt.database_url)
    with SMSession(eng) as s:
        row = get_tension(s, tension_id=tension_id)
        if row is None or row.instance_id != instance_id:
            return JSONResponse({"error": "tension not found"}, status_code=404)
        return JSONResponse(_tension_out(row))


@router.post("/instances/{instance_id}/tensions/{tension_id}/triage")
async def triage(instance_id: str, tension_id: UUID) -> JSONResponse:
    """Run the Triage Guardian on a backlog tension and record its assessment.

    Feeds the Guardian: the tension itself, a compact digest of existing open/
    decided tensions (for dedup), and the instance taxonomy (for on-domain).
    The assessment is written via triage_tension (status → 'triaged', a
    tension-triaged ledger event is appended). Returns the assessment.

    This is a SOFT gate: the assessment flags duplicates/off-domain/noise but
    never blocks a tension from the backlog — the founder can still deliberate
    a flagged tension. The flags live in the fully-public record.
    """
    rt = load_runtime_config()
    eng = make_engine(rt.database_url)
    ic = load_instance_config(instance_id)
    with SMSession(eng) as s:
        row = get_tension(s, tension_id=tension_id)
        if row is None or row.instance_id != instance_id:
            return JSONResponse({"error": "tension not found"}, status_code=404)

        # Dedup context: existing open/decided tensions the Guardian can match
        # against. Compact (id+title+status) so it fits the context window.
        candidates = list_backlog(s, instance_id=instance_id)
        dedup_context = [
            {"id": str(c.id), "title": c.title, "status": c.status}
            for c in candidates if c.id != tension_id
        ]

        # Taxonomy context for on-domain assessment.
        taxonomy = ic.taxonomy.model_dump() if ic.taxonomy else {}
        # S6: include the ABAC matrix so the Guardian can weigh materiality
        # against who raised it (a founder-weight tension reads differently
        # from an observe-only regulator's). Cell-level overrides included.
        abac = ic.abac.model_dump() if ic.abac else {}

        guardian = TriageGuardian(instance_id=instance_id)
        prompt = (
            "Assess this new tension for the backlog. Respond ONLY as JSON.\n"
            f"Tension title: {row.title}\n"
            f"Tension description: {row.description}\n"
            f"Existing tensions to check for duplicates: {json.dumps(dedup_context)}\n"
            f"Instance taxonomy (for on-domain check): {json.dumps(taxonomy)}\n"
            f"ABAC matrix (stakeholder authority, for materiality): {json.dumps(abac)}\n"
            "If this duplicates an existing tension, set duplicate_of to that "
            "tension's id (string). Otherwise null."
        )
        text = guardian.respond(prompt, max_tokens=400, temperature=0.2)
        assessment = extract_json(text)

        triaged = triage_tension(
            s, tension_id=tension_id,
            triaged_by_agent_id=guardian.ref.agent_id,
            triage=assessment,
        )
        s.commit()
        return JSONResponse({
            "tension_id": str(tension_id),
            "status": triaged.status,
            "assessment": assessment,
        })


# ── Deliberation ──────────────────────────────────────────────────────────────


@router.post("/instances/{instance_id}/deliberations")
async def start_deliberation(
    instance_id: str, request: Request, tension_id: UUID | None = None,
) -> JSONResponse:
    """Start a consent cycle. Returns a run_id whose event stream is at
    GET /deliberations/{run_id}/events.

    Tension source (S5): if ?tension_id=<uuid> is given, deliberate that
    specific backlog tension; otherwise the cycle pops the next backlog tension
    (or falls back to first_decision if the backlog is empty).
    """
    broker = request.app.state.broker
    run_id = uuid4()
    # Open the feed BEFORE the thread starts so events aren't missed.
    broker.open(run_id, asyncio.get_running_loop())
    run_deliberation_live(
        instance_id=instance_id, run_id=run_id, broker=broker, tension_id=tension_id,
    )
    return JSONResponse({"run_id": str(run_id), "events_url": f"/deliberations/{run_id}/events"},
                        status_code=202)


@router.get("/deliberations/{run_id}/events")
async def events(run_id: UUID, request: Request) -> EventSourceResponse:
    """SSE stream of the deliberation's events, live. Closes after the terminal
    decision-recorded event."""
    broker = request.app.state.broker
    # Subscribe to the EXISTING feed opened by POST — never overwrite it, or we
    # would lose any events pushed between POST and GET.
    queue = broker.subscribe(run_id, asyncio.get_running_loop())

    async def event_generator():
        try:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    data = await asyncio.wait_for(queue.get(), timeout=15.0)
                except TimeoutError:
                    yield {"event": "ping", "data": "{}"}
                    continue
                if data == CLOSE:
                    yield {"event": "close", "data": "{}"}
                    break
                yield {"event": data["event_type"], "data": json.dumps(data["payload"])}
        finally:
            broker.drop(run_id)

    return EventSourceResponse(event_generator())


# ── Epoch & cadence (S7) ─────────────────────────────────────────────────────


def _epoch_out(r) -> dict:
    """Serialize an EpochRow for the API."""
    return {
        "epoch_id": str(r.id),
        "instance_id": r.instance_id,
        "seq": r.seq,
        "status": r.status,
        "tension_id": str(r.tension_id) if r.tension_id else None,
        "run_id": str(r.run_id) if r.run_id else None,
        "opened_at": r.opened_at.isoformat() if r.opened_at else None,
        "closed_at": r.closed_at.isoformat() if r.closed_at else None,
    }


@router.get("/instances/{instance_id}/cadence")
async def get_cadence(instance_id: str) -> JSONResponse:
    """The instance's epoch cadence config (S7)."""
    ic = load_instance_config(instance_id)
    return JSONResponse({"instance_id": instance_id, **ic.cadence.model_dump()})


@router.get("/instances/{instance_id}/epochs")
async def epochs(instance_id: str, status: str | None = None) -> JSONResponse:
    """List the instance's epochs, newest first. Optional status filter."""
    rt = load_runtime_config()
    eng = make_engine(rt.database_url)
    with SMSession(eng) as s:
        rows = list_epochs(s, instance_id=instance_id, status=status)
    return JSONResponse({"epochs": [_epoch_out(r) for r in rows]})


@router.get("/instances/{instance_id}/epochs/{epoch_id}")
async def epoch_detail(instance_id: str, epoch_id: UUID) -> JSONResponse:
    """Single epoch detail. 404 if not found or belongs to another instance."""
    rt = load_runtime_config()
    eng = make_engine(rt.database_url)
    with SMSession(eng) as s:
        row = get_epoch(s, epoch_id=epoch_id)
        if row is None or row.instance_id != instance_id:
            return JSONResponse({"error": "epoch not found"}, status_code=404)
        return JSONResponse(_epoch_out(row))


@router.post("/instances/{instance_id}/epochs")
async def start_epoch_cycle(instance_id: str, request: Request) -> JSONResponse:
    """Open an epoch and start a deliberation on it (S7 epoch-aware trigger).

    This is the bridge between manual S5 deliberations and scheduled S7 epochs.
    It opens an epoch, resolves the next backlog tension (seeding from
    seed_tensions if the backlog is empty — same S5.8 logic), fires
    run_deliberation_live, and links the run to the epoch. The epoch closes
    when the cycle's decision is recorded (wired via the live worker).
    Returns {epoch_id, seq, run_id, events_url, tension_id}.
    """
    rt = load_runtime_config()
    eng = make_engine(rt.database_url)
    ic = load_instance_config(instance_id)

    # Open the epoch + resolve the tension to deliberate in one session.
    with SMSession(eng) as s:
        # Overlap guard: refuse if an epoch is already running for this instance.
        if current_epoch(s, instance_id=instance_id) is not None:
            return JSONResponse(
                {"error": "an epoch is already running for this instance"},
                status_code=409,
            )
        epoch = open_epoch(s, instance_id=instance_id)
        s.commit()
        epoch_id = epoch.id
        epoch_seq = epoch.seq

    # Fire the deliberation (the worker resolves the tension + seeds if empty).
    broker = request.app.state.broker
    run_id = uuid4()
    broker.open(run_id, asyncio.get_running_loop())
    run_deliberation_live(
        instance_id=instance_id, run_id=run_id, broker=broker,
        config=rt, instance=ic, epoch_id=epoch_id,
    )
    return JSONResponse(
        {
            "epoch_id": str(epoch_id),
            "seq": epoch_seq,
            "run_id": str(run_id),
            "events_url": f"/deliberations/{run_id}/events",
            "status": "running",
        },
        status_code=202,
    )


__all__ = ["router"]
