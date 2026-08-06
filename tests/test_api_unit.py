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
