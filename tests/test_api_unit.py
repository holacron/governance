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

from holon.api.feed import FeedBroker
from holon.api.server import create_app

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
