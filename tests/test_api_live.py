"""S4 live acceptance test — the MVP exit criterion.

> "A human welcomes an agent on the KIMBERIM instance and watches it deliberate live."

Registers an agent via the API, starts a deliberation on Kimberim's first
decision, consumes the SSE event stream, and asserts the full cycle arrives
live and a decision is recorded. Real Z.ai engine + Postgres.

Marked `live`; skipped without ZAI_API_KEY + DATABASE_URL.
"""

from __future__ import annotations

import json
import os
import time

import pytest
from dotenv import load_dotenv
from fastapi.testclient import TestClient

from holon.api.server import create_app

load_dotenv()

INSTANCE = "kimberim"
_HAS_LIVE = bool(os.getenv("ZAI_API_KEY") and os.getenv("DATABASE_URL"))


@pytest.mark.live
@pytest.mark.skipif(not _HAS_LIVE, reason="needs ZAI_API_KEY + DATABASE_URL")
def test_live_welcome_and_watch_deliberation():
    """The S4 exit criterion, end-to-end via the HTTP API + SSE."""
    client = TestClient(create_app())

    # 1. Welcome an agent.
    reg = client.post(f"/instances/{INSTANCE}/agents", json={
        "display_name": "Live Energy Agent",
        "owner": "S4 Test",
        "capability": "grid stability and export revenue",
        "model": "GLM-5-Turbo",
        "endpoint": "https://api.z.ai",
        "api_key": "unused-in-mvp",
    })
    assert reg.status_code == 201
    assert reg.json()["status"] == "registered"

    # 2. Start a deliberation.
    start = client.post(f"/instances/{INSTANCE}/deliberations")
    assert start.status_code == 202
    run_id = start.json()["run_id"]
    events_url = start.json()["events_url"]
    assert events_url == f"/deliberations/{run_id}/events"

    # 3. Consume the SSE stream live, collecting events until close.
    received_types: list[str] = []
    last_payload: dict = {}
    deadline = time.time() + 180  # generous; a real cycle takes ~30-60s
    with client.stream("GET", events_url) as resp:
        assert resp.status_code == 200
        buf_type = None
        for line in resp.iter_lines():
            if time.time() > deadline:
                break
            if line.startswith("event:"):
                buf_type = line.split(":", 1)[1].strip()
            elif line.startswith("data:") and buf_type:
                if buf_type == "close":
                    break
                if buf_type == "ping":
                    buf_type = None
                    continue
                received_types.append(buf_type)
                try:
                    last_payload = json.loads(line.split(":", 1)[1].strip())
                except (json.JSONDecodeError, IndexError):
                    pass
                if buf_type == "decision-recorded":
                    pass  # close follows
                buf_type = None

    # 4. Assert the live cycle ran and terminated with a recorded decision.
    assert "proposal-drafted" in received_types, "expected the cycle to start"
    assert received_types[-1] == "decision-recorded", (
        f"expected decision-recorded last, got {received_types[-3:]}"
    )
    assert last_payload.get("outcome") in ("adopted", "escalated"), (
        f"expected a terminal outcome, got {last_payload.get('outcome')}"
    )


@pytest.mark.live
@pytest.mark.skipif(not _HAS_LIVE, reason="needs ZAI_API_KEY + DATABASE_URL")
def test_live_submit_triage_deliberate_decide():
    """The S5 exit criterion, end-to-end via the HTTP API:

    submit a tension → (optionally) triage it → start a deliberation on it →
    consume the SSE stream → assert the cycle records a decision AND the source
    tension is marked 'decided' in the backlog (the loop is closed).
    """
    client = TestClient(create_app())

    # 1. Submit a tension to the backlog.
    sub = client.post(f"/instances/{INSTANCE}/tensions", json={
        "title": "S5 e2e: water usage for compute cooling",
        "description": (
            "On-site compute at the 1GW campus raises cooling-water demand in "
            "an arid region. Should we cap compute density or require air-cooling?"
        ),
    })
    assert sub.status_code == 201
    tension_id = sub.json()["tension_id"]

    # 2. Start a deliberation targeting that specific tension.
    start = client.post(f"/instances/{INSTANCE}/deliberations?tension_id={tension_id}")
    assert start.status_code == 202
    run_id = start.json()["run_id"]
    events_url = start.json()["events_url"]

    # 3. Consume the SSE stream until close (the terminal decision-recorded).
    received_types: list[str] = []
    last_payload: dict = {}
    deadline = time.time() + 240  # a real cycle with a richer tension can take a while
    with client.stream("GET", events_url) as resp:
        assert resp.status_code == 200
        buf_type = None
        for line in resp.iter_lines():
            if time.time() > deadline:
                break
            if line.startswith("event:"):
                buf_type = line.split(":", 1)[1].strip()
            elif line.startswith("data:") and buf_type:
                if buf_type == "close":
                    break
                if buf_type == "ping":
                    buf_type = None
                    continue
                received_types.append(buf_type)
                try:
                    last_payload = json.loads(line.split(":", 1)[1].strip())
                except (json.JSONDecodeError, IndexError):
                    pass
                buf_type = None

    # 4. The cycle ran to a recorded decision.
    assert "proposal-drafted" in received_types, "expected the cycle to start"
    assert received_types[-1] == "decision-recorded", (
        f"expected decision-recorded last, got {received_types[-3:]}"
    )
    assert last_payload.get("outcome") in ("adopted", "escalated")

    # 5. S5 close-the-loop: the source tension is marked 'decided' + linked.
    det = client.get(f"/instances/{INSTANCE}/tensions/{tension_id}")
    assert det.status_code == 200
    detail = det.json()
    assert detail["status"] == "decided", (
        f"tension should be decided after the cycle; got {detail['status']}"
    )
    assert detail["decision_id"] is not None, "tension should link to its decision"
