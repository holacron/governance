"""Holon engage API routes (S4): registration, deliberation, SSE live feed.

REST:
  POST /instances/{id}/agents        — register an agent ("Welcome an Agent")
  GET  /instances/{id}/agents        — list registered agents
  POST /instances/{id}/deliberations — start a cycle on first_decision
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

from holon.api.feed import CLOSE
from holon.api.live import run_deliberation_live
from holon.config import load_instance_config, load_runtime_config
from holon.store import list_agents, make_engine, register_agent

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
    queue = broker.open(run_id, asyncio.get_running_loop())

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
