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
from holon.config import load_instance_config, load_runtime_config
from holon.store import (
    get_tension,
    list_agents,
    list_backlog,
    make_engine,
    raise_tension,
    register_agent,
    triage_tension,
)
from holon.utils import extract_json

router = APIRouter()


# ── Request/response models ──────────────────────────────────────────────────


class AgentRegistration(BaseModel):
    """The 'Welcome an Agent' form payload."""

    display_name: str = Field(..., min_length=1)
    owner: str = ""
    capability: str = ""  # stakeholder perspective / capability
    model: str = ""
    endpoint: str = ""
    api_key: str = Field("", alias="api_key")  # the registered key (opaque to MVP)


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


# ── Agent registration ("Welcome an Agent") ──────────────────────────────────


@router.post("/instances/{instance_id}/agents")
async def register(instance_id: str, body: AgentRegistration) -> JSONResponse:
    rt = load_runtime_config()
    eng = make_engine(rt.database_url)
    with SMSession(eng) as s:
        row = register_agent(
            s, instance_id=instance_id,
            display_name=body.display_name, owner=body.owner,
            capability=body.capability, model=body.model, endpoint=body.endpoint,
            api_key_enc=body.api_key or "",  # opaque; not used in MVP execution
        )
        s.commit()
        return JSONResponse(
            {"agent_id": str(row.agent_id), "display_name": row.display_name,
             "status": "registered", "eligible": True},
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
    row = register_agent(
        s, instance_id=instance_id, display_name=founder_name,
        role="founder", capability="Instance founder",
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
    the instance founder. ABAC submission gates come in the next sprint.
    """
    rt = load_runtime_config()
    eng = make_engine(rt.database_url)
    with SMSession(eng) as s:
        if body.raised_by is not None:
            raised_by = body.raised_by
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

        guardian = TriageGuardian(instance_id=instance_id)
        prompt = (
            "Assess this new tension for the backlog. Respond ONLY as JSON.\n"
            f"Tension title: {row.title}\n"
            f"Tension description: {row.description}\n"
            f"Existing tensions to check for duplicates: {json.dumps(dedup_context)}\n"
            f"Instance taxonomy (for on-domain check): {json.dumps(taxonomy)}\n"
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
async def start_deliberation(instance_id: str, request: Request) -> JSONResponse:
    """Start a consent cycle on the instance's first_decision. Returns a run_id
    whose event stream is available at GET /deliberations/{run_id}/events."""
    broker = request.app.state.broker
    run_id = uuid4()
    # Open the feed BEFORE the thread starts so events aren't missed.
    broker.open(run_id, asyncio.get_running_loop())
    run_deliberation_live(instance_id=instance_id, run_id=run_id, broker=broker)
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


__all__ = ["router"]
