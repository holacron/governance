"""S4 API unit tests (deterministic; FastAPI TestClient; no live LLM).

Covers: registration persists + lists; instance summary; starting a
deliberation returns a run_id; the SSE endpoint yields an event stream for a
stub-driven feed and closes on the terminal event.

The deliberation here uses a STUB cycle (we inject events directly into the
broker) to avoid LLM cost; the live end-to-end test (test_api_live.py) exercises
the real engine.
"""

from __future__ import annotations

import os
import time
from uuid import uuid4

import pytest
from dotenv import load_dotenv
from fastapi.testclient import TestClient

from olon.api.feed import FeedBroker
from olon.api.server import create_app

load_dotenv()

_HAS_DB = bool(os.getenv("DATABASE_URL"))


@pytest.fixture
def client():
    return TestClient(create_app())


# ── Instance summary ──────────────────────────────────────────────────────────


def test_instance_summary(client):
    r = client.get("/instances/kimberim")
    assert r.status_code == 200
    body = r.json()
    assert body["instance_id"] == "kimberim"
    assert body["first_decision"] is not None
    assert "compute" in body["first_decision"]["title"].lower()


# ── Taxonomy & ABAC transparency (S6) ────────────────────────────────────────


def test_taxonomy_endpoint_returns_matrix(client):
    """GET /instances/{id}/taxonomy returns the stakeholder types, functional
    domains, and the full ABAC matrix (weights + permissions + overrides)."""
    r = client.get("/instances/kimberim/taxonomy")
    assert r.status_code == 200
    body = r.json()
    assert "founder" in body["stakeholder_types"]
    assert "traditional-owners" in body["stakeholder_types"]
    assert "cultural-heritage" in body["functional_domains"]
    abac = body["abac"]
    assert abac["weights"]["founder"] == 2.0
    assert abac["weights"]["traditional-owners"] == 2.0
    assert "veto" in abac["permissions"]["founder"]
    assert "veto" not in abac["permissions"]["traditional-owners"]


# ── Agent registration ("Welcome an Agent") ──────────────────────────────────


@pytest.mark.skipif(not _HAS_DB, reason="needs DATABASE_URL")
def test_register_and_list_agent(client):
    r = client.post("/instances/kimberim/agents", json={
        "display_name": "Grid Stability Agent",
        "owner": "TestCo",
        "capability": "maximise grid stability",
        "model": "GLM-5-Turbo",
        "endpoint": "https://api.z.ai",
        "api_key": "dummy-key",
    })
    assert r.status_code == 201
    body = r.json()
    assert body["status"] == "registered"
    assert body["eligible"] is True

    r2 = client.get("/instances/kimberim/agents")
    assert r2.status_code == 200
    names = [a["display_name"] for a in r2.json()["agents"]]
    assert "Grid Stability Agent" in names


@pytest.mark.skipif(not _HAS_DB, reason="needs DATABASE_URL")
def test_register_agent_with_taxonomy_resolves_cell(client):
    """S6: registering with stakeholder_type resolves the ABAC cell — the
    response carries permissions + weight from the instance matrix."""
    r = client.post("/instances/kimberim/agents", json={
        "display_name": "Miriwoong Delegate",
        "capability": "Cultural heritage & Country",
        "stakeholder_type": "traditional-owners",
        "functional_domain": "cultural-heritage",
    })
    assert r.status_code == 201
    body = r.json()
    assert body["stakeholder_type"] == "traditional-owners"
    # Traditional Owners get weight 2.0 in the seeded KIMBERIM matrix.
    assert body["weight"] == 2.0
    perms = set(body["permissions"])
    assert "submit" in perms and "vote" in perms
    assert "veto" not in perms  # TOs vote but do not veto


@pytest.mark.skipif(not _HAS_DB, reason="needs DATABASE_URL")
def test_agent_detail_returns_resolved_cell(client):
    """GET /instances/{id}/agents/{agent_id} returns the agent's ABAC cell —
    stakeholder_type, functional_domain, resolved permissions, weight."""
    reg = client.post("/instances/kimberim/agents", json={
        "display_name": "Finance Lead",
        "capability": "capital structure",
        "stakeholder_type": "investor",
        "functional_domain": "finance",
    })
    assert reg.status_code == 201
    agent_id = reg.json()["agent_id"]

    r = client.get(f"/instances/kimberim/agents/{agent_id}")
    assert r.status_code == 200
    body = r.json()
    assert body["agent_id"] == agent_id
    assert body["stakeholder_type"] == "investor"
    assert body["functional_domain"] == "finance"
    # investor not in the seeded matrix → participant default perms + weight 1.0
    assert set(body["permissions"]) == {"submit", "deliberate", "vote"}
    assert body["weight"] == 1.0


@pytest.mark.skipif(not _HAS_DB, reason="needs DATABASE_URL")
def test_agent_detail_unknown_returns_404(client):
    """An unregistered agent_id → 404."""
    r = client.get(f"/instances/kimberim/agents/{uuid4()}")
    assert r.status_code == 404


@pytest.mark.skipif(not _HAS_DB, reason="needs DATABASE_URL")
def test_submit_tension_rejected_for_observe_only_agent(client):
    """S6 ABAC submission gate: an observe-only agent (regulator) cannot
    submit a tension → 403."""
    reg = client.post("/instances/kimberim/agents", json={
        "display_name": "ERA Regulator",
        "capability": "regulatory oversight",
        "stakeholder_type": "regulator",
    })
    assert reg.status_code == 201
    agent_id = reg.json()["agent_id"]
    # Regulator perms are observe + submit in the seeded matrix — so to test
    # the 403 path we register an observe-ONLY agent via a stakeholder type
    # whose matrix has no submit. Override is not wired in YAML yet, so we
    # craft a minimal agent with empty permissions directly via the store.
    # Simpler: use a stakeholder type NOT in the matrix → gets the participant
    # default {submit, deliberate, vote}, which PASSES. The 403 path needs an
    # agent with permissions lacking submit. We build that via the store layer
    # to isolate the gate logic (the gate is what we're testing here).
    from sqlmodel import Session as SMSession
    from olon.config import load_runtime_config
    from olon.store import make_engine, register_agent
    rt = load_runtime_config()
    eng = make_engine(rt.database_url)
    with SMSession(eng) as s:
        row = register_agent(
            s, instance_id="kimberim", display_name="Observe-Only Bot",
            permissions={"observe"},  # no submit
        )
        s.commit()
        observe_only_id = row.agent_id

    sub = client.post("/instances/kimberim/tensions", json={
        "title": "should be blocked",
        "description": "this agent cannot submit",
        "raised_by": str(observe_only_id),
    })
    assert sub.status_code == 403
    assert "submit" in sub.json()["error"]


@pytest.mark.skipif(not _HAS_DB, reason="needs DATABASE_URL")
def test_submit_tension_allowed_for_submit_agent(client):
    """S6 ABAC submission gate: an agent WITH submit passes → 201."""
    from sqlmodel import Session as SMSession
    from olon.config import load_runtime_config
    from olon.store import make_engine, register_agent
    rt = load_runtime_config()
    eng = make_engine(rt.database_url)
    with SMSession(eng) as s:
        row = register_agent(
            s, instance_id="kimberim", display_name="Submitter Bot",
            permissions={"submit", "deliberate"},  # has submit
        )
        s.commit()
        submitter_id = row.agent_id

    sub = client.post("/instances/kimberim/tensions", json={
        "title": "allowed tension",
        "description": "this agent can submit",
        "raised_by": str(submitter_id),
    })
    assert sub.status_code == 201
    assert sub.json()["status"] == "open"


@pytest.mark.skipif(not _HAS_DB, reason="needs DATABASE_URL")
def test_submit_tension_unknown_agent_returns_404(client):
    """S6: a raised_by pointing at an unregistered agent → 404 (not 403)."""
    sub = client.post("/instances/kimberim/tensions", json={
        "title": "x", "description": "y",
        "raised_by": str(uuid4()),
    })
    assert sub.status_code == 404


@pytest.mark.skipif(not _HAS_DB, reason="needs DATABASE_URL")
def test_submit_tension_null_permissions_back_compat(client):
    """S6 back-compat: an agent registered with no taxonomy (NULL permissions)
    gets the participant default → submission passes (201)."""
    from sqlmodel import Session as SMSession
    from olon.config import load_runtime_config
    from olon.store import make_engine, register_agent
    rt = load_runtime_config()
    eng = make_engine(rt.database_url)
    with SMSession(eng) as s:
        row = register_agent(
            s, instance_id="kimberim", display_name="Legacy No-Taxonomy Agent",
        )
        s.commit()
        legacy_id = row.agent_id

    sub = client.post("/instances/kimberim/tensions", json={
        "title": "legacy submit",
        "description": "pre-S6 agent submitting",
        "raised_by": str(legacy_id),
    })
    assert sub.status_code == 201


# ── Tension intake & backlog (S5) ────────────────────────────────────────────


@pytest.mark.skipif(not _HAS_DB, reason="needs DATABASE_URL")
def test_submit_and_list_tension(client):
    """POST a tension → it appears in the backlog list."""
    r = client.post("/instances/kimberim/tensions", json={
        "title": "Compute heat load on water supply",
        "description": "On-site compute raises cooling water demand in an arid region.",
    })
    assert r.status_code == 201
    body = r.json()
    assert body["status"] == "open"
    assert body["priority"] == 50
    tension_id = body["tension_id"]

    # It appears in the full list.
    r2 = client.get("/instances/kimberim/tensions")
    assert r2.status_code == 200
    titles = [t["title"] for t in r2.json()["tensions"]]
    assert "Compute heat load on water supply" in titles

    # Detail endpoint returns it with the right fields.
    r3 = client.get(f"/instances/kimberim/tensions/{tension_id}")
    assert r3.status_code == 200
    detail = r3.json()
    assert detail["status"] == "open"
    assert detail["triage"] is None  # not yet triaged


@pytest.mark.skipif(not _HAS_DB, reason="needs DATABASE_URL")
def test_submit_tension_with_explicit_priority(client):
    """A submitted tension honours the priority field."""
    r = client.post("/instances/kimberim/tensions", json={
        "title": "Urgent grid-stability review",
        "description": "Needs attention now.",
        "priority": 10,
    })
    assert r.status_code == 201
    assert r.json()["priority"] == 10


@pytest.mark.skipif(not _HAS_DB, reason="needs DATABASE_URL")
def test_get_tension_unknown_returns_404(client):
    """A non-existent tension_id returns 404, not a 500."""
    from uuid import uuid4
    r = client.get(f"/instances/kimberim/tensions/{uuid4()}")
    assert r.status_code == 404


@pytest.mark.skipif(not _HAS_DB, reason="needs DATABASE_URL")
def test_list_tensions_filtered_by_status(client):
    """The status query param filters the backlog."""
    created = client.post("/instances/kimberim/tensions", json={
        "title": "open one", "description": "d",
    })
    assert created.status_code == 201
    created_id = created.json()["tension_id"]
    r = client.get("/instances/kimberim/tensions?status=open")
    assert r.status_code == 200
    open_ids = {t["tension_id"] for t in r.json()["tensions"]}
    assert all(t["status"] == "open" for t in r.json()["tensions"])
    assert created_id in open_ids, "the tension we just posted should be in 'open'"
    # The 'decided' filter must return only decided tensions AND must NOT include
    # the 'open' one we just created (proves the filter excludes other statuses).
    # We don't assert the bucket is empty — a shared dev DB accumulates state from
    # live smoke runs; the point is that filtering is correct, not that it's bare.
    r2 = client.get("/instances/kimberim/tensions?status=decided")
    decided = r2.json()["tensions"]
    assert all(t["status"] == "decided" for t in decided)
    assert created_id not in {t["tension_id"] for t in decided}


@pytest.mark.skipif(not _HAS_DB, reason="needs DATABASE_URL")
def test_deliberation_targets_specific_tension(client):
    """POST .../deliberations?tension_id=<uuid> starts a cycle on that specific
    backlog tension (not the first_decision fallback). The tension transitions
    to 'in-deliberation' promptly, before the (slow) cycle completes."""
    import time
    from uuid import uuid4

    # Submit a tension to target.
    sub = client.post("/instances/kimberim/tensions", json={
        "title": "Target tension for deliberation", "description": "deliberate me",
    })
    tension_id = sub.json()["tension_id"]

    # Start a deliberation targeting it. The worker runs in a background thread.
    r = client.post(f"/instances/kimberim/deliberations?tension_id={tension_id}")
    assert r.status_code == 202
    assert r.json()["run_id"]

    # The worker marks the tension 'in-deliberation' early (before the LLM
    # cycle). Poll briefly for the transition.
    deadline = time.time() + 10
    status = "open"
    while time.time() < deadline:
        det = client.get(f"/instances/kimberim/tensions/{tension_id}")
        status = det.json()["status"]
        if status in ("in-deliberation", "scheduled", "decided"):
            break
        time.sleep(0.3)

    assert status in ("in-deliberation", "scheduled", "decided"), (
        f"targeted tension should have left 'open'; got {status}"
    )


# ── SSE live feed (stub-driven; no LLM) ──────────────────────────────────────


def test_sse_feed_yields_events_and_closes(client):
    """The SSE stream yields pushed events in order and closes on the terminal
    event. We drive the broker directly (no cycle/LLM) to test the plumbing."""
    run_id = uuid4()
    broker: FeedBroker = client.app.state.broker

    # We must open the feed from within the running loop the SSE handler uses.
    # The TestClient runs the app's loop, so we push events after a short delay
    # to let the SSE connection subscribe.
    import threading

    def _push_after_delay():
        time.sleep(0.3)
        broker.push(run_id, "proposal-drafted", {"title": "test proposal"})
        broker.push(run_id, "consent-reached", {"weighted_consent": 2.0})
        broker.close(run_id)

    threading.Thread(target=_push_after_delay, daemon=True).start()

    events_received: list[str] = []
    with client.stream("GET", f"/deliberations/{run_id}/events") as resp:
        assert resp.status_code == 200
        for line in resp.iter_lines():
            if line.startswith("event:"):
                events_received.append(line.split(":", 1)[1].strip())
            if line.startswith("event: close"):
                break

    assert "proposal-drafted" in events_received
    assert "consent-reached" in events_received
    assert events_received[-1] == "close"


# ── H1 regression: GET must not clobber the POST-created queue ──────────────


def test_feed_broker_subscribe_does_not_overwrite_open():
    """H1 regression: ``subscribe()`` must return the SAME queue created by
    ``open()``, so events pushed between POST and GET are never lost.

    Reproduces the original bug shape: open() (POST) → push() → open() again
    (the old GET path) would replace the queue and drop the buffered event.
    """
    import asyncio
    from uuid import uuid4

    broker = FeedBroker()
    run_id = uuid4()
    loop = asyncio.new_event_loop()

    try:
        # POST handler opens the feed BEFORE the cycle thread starts.
        q_open = broker.open(run_id, loop)
        # The cycle thread pushes events onto it (call_soon_threadsafe is fine
        # because loop is running-bound; but here we exercise the queue directly).
        q_open.put_nowait({"event_type": "proposal-drafted", "payload": {"i": 1}})
        q_open.put_nowait({"event_type": "consent-reached", "payload": {"i": 2}})

        assert broker.is_open(run_id)

        # GET handler subscribes — must see the SAME queue, not a fresh one.
        q_sub = broker.subscribe(run_id, loop)
        assert q_sub is q_open, "subscribe() must not overwrite the open() queue"

        # No events lost: both buffered events are still readable.
        first = loop.run_until_complete(q_sub.get())
        second = loop.run_until_complete(q_sub.get())
        assert first["payload"]["i"] == 1
        assert second["payload"]["i"] == 2
    finally:
        loop.close()


def test_feed_broker_open_is_idempotent():
    """A second ``open()`` for the same run must not replace the queue either
    (defensive: even if a caller accidentally re-opens, events survive)."""
    import asyncio
    from uuid import uuid4

    broker = FeedBroker()
    run_id = uuid4()
    loop = asyncio.new_event_loop()

    try:
        q1 = broker.open(run_id, loop)
        q1.put_nowait({"event_type": "ping", "payload": {}})
        q2 = broker.open(run_id, loop)  # accidental re-open
        assert q1 is q2, "open() must be idempotent"
        # Event still present.
        assert loop.run_until_complete(q2.get())["event_type"] == "ping"
    finally:
        loop.close()


# ── Epoch & cadence (S7) ─────────────────────────────────────────────────────


def test_cadence_endpoint_returns_manual_default(client):
    """GET /instances/{id}/cadence returns the cadence config. KIMBERIM defaults
    to 'manual' (no scheduler) — zero behaviour change."""
    r = client.get("/instances/kimberim/cadence")
    assert r.status_code == 200
    body = r.json()
    assert body["preset"] == "manual"
    assert body["interval_seconds"] == 0


@pytest.mark.skipif(not _HAS_DB, reason="needs DATABASE_URL")
def test_epochs_list_returns_list(client):
    """GET /instances/{id}/epochs returns the epoch list (newest first). The
    shared dev DB accumulates epochs from prior runs, so we assert shape, not
    emptiness."""
    r = client.get("/instances/kimberim/epochs")
    assert r.status_code == 200
    epochs = r.json()["epochs"]
    assert isinstance(epochs, list)
    # Newest-first ordering (seq descending).
    if len(epochs) > 1:
        seqs = [e["seq"] for e in epochs]
        assert seqs == sorted(seqs, reverse=True)


@pytest.mark.skipif(not _HAS_DB, reason="needs DATABASE_URL")
def test_post_epoch_opens_and_starts_deliberation(client):
    """POST /instances/{id}/epochs opens an epoch + starts a deliberation. The
    epoch appears in GET .../epochs and the returned run_id is linkable.

    If a prior epoch is still 'running' in the shared dev DB (overlap guard),
    we accept the 409 and verify the list reflects the running epoch instead."""
    r = client.post("/instances/kimberim/epochs")
    if r.status_code == 409:
        # Overlap guard fired — a prior epoch is running. Verify it's visible.
        r2 = client.get("/instances/kimberim/epochs?status=running")
        assert r2.status_code == 200
        assert len(r2.json()["epochs"]) >= 1
        return
    assert r.status_code == 202
    body = r.json()
    assert body["status"] == "running"
    epoch_id = body["epoch_id"]
    run_id = body["run_id"]
    assert body["events_url"] == f"/deliberations/{run_id}/events"
    assert body["seq"] >= 1

    # The epoch is visible in the list.
    r2 = client.get("/instances/kimberim/epochs")
    ids = [e["epoch_id"] for e in r2.json()["epochs"]]
    assert epoch_id in ids

    # Epoch detail is fetchable.
    r3 = client.get(f"/instances/kimberim/epochs/{epoch_id}")
    assert r3.status_code == 200
    assert r3.json()["epoch_id"] == epoch_id


@pytest.mark.skipif(not _HAS_DB, reason="needs DATABASE_URL")
def test_epoch_detail_unknown_returns_404(client):
    r = client.get(f"/instances/kimberim/epochs/{uuid4()}")
    assert r.status_code == 404
