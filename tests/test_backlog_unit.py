"""S5 tension-backlog CRUD tests (DB-gated; throwaway DB per test).

Covers the store layer: raise_tension, list_backlog, triage_tension,
next_tension (priority queue), mark_in_deliberation, mark_decided.

Uses a throwaway Postgres database (created + dropped per test) so the dev DB
is never modified — same pattern as tests/test_migrations.py. Marked `live`;
skipped unless DATABASE_URL is set.
"""

from __future__ import annotations

import os
import uuid
from urllib.parse import urlparse, urlunparse

import pytest
from dotenv import load_dotenv
from sqlalchemy import create_engine, inspect, text

from holon.store import (
    apply_migrations,
    get_tension,
    list_backlog,
    make_engine,
    mark_decided,
    mark_in_deliberation,
    next_tension,
    raise_tension,
    register_agent,
    triage_tension,
)
from holon.store import DecisionRow, ProposalRow
from sqlmodel import Session as SMSession

load_dotenv()

_HAS_DB = bool(os.getenv("DATABASE_URL"))


def _maintenance_url(db_url: str) -> str:
    parts = urlparse(db_url)
    return urlunparse(parts._replace(path="/postgres"))


def _throwaway_url(db_url: str, dbname: str) -> str:
    parts = urlparse(db_url)
    return urlunparse(parts._replace(path=f"/{dbname}"))


def _autocommit_engine(database_url: str):
    url = database_url.replace("postgresql://", "postgresql+psycopg://", 1)
    return create_engine(url, isolation_level="AUTOCOMMIT")


@pytest.fixture
def db_session():
    """A fresh throwaway DB with migrations applied, yielding a session.

    Cleans up the throwaway DB after the test (even on failure).
    """
    db_url = os.environ["DATABASE_URL"]
    dbname = f"holon_bklog_{uuid.uuid4().hex[:8]}"
    throwaway = _throwaway_url(db_url, dbname)

    maint = _autocommit_engine(_maintenance_url(db_url))
    with maint.connect() as conn:
        conn.execute(text(f'CREATE DATABASE "{dbname}"'))
    maint.dispose()

    apply_migrations(throwaway)
    eng = make_engine(throwaway)
    try:
        with SMSession(eng) as s:
            yield s
    finally:
        eng.dispose()
        maint = _autocommit_engine(_maintenance_url(db_url))
        with maint.connect() as conn:
            conn.execute(
                text(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                    "WHERE datname = :d AND pid <> pg_backend_pid()"
                ),
                {"d": dbname},
            )
            conn.execute(text(f'DROP DATABASE IF EXISTS "{dbname}"'))
        maint.dispose()


def _founder_agent_id(s: SMSession, instance_id: str = "kimberim") -> uuid.UUID:
    """Register a minimal agent to satisfy the raised_by FK."""
    row = register_agent(s, instance_id=instance_id, display_name="Founder")
    s.flush()
    return row.agent_id


# ── raise + list ─────────────────────────────────────────────────────────────


@pytest.mark.live
@pytest.mark.skipif(not _HAS_DB, reason="needs DATABASE_URL")
def test_raise_tension_creates_open_backlog_entry(db_session):
    s = db_session
    agent_id = _founder_agent_id(s)
    row = raise_tension(
        s, instance_id="kimberim", raised_by_agent_id=agent_id,
        title="Grid export vs compute", description="How to split 1GW?",
    )
    s.commit()
    assert row.status == "open"
    assert row.priority == 50
    assert row.id is not None

    backlog = list_backlog(s, instance_id="kimberim")
    assert len(backlog) == 1
    assert backlog[0].title == "Grid export vs compute"


@pytest.mark.live
@pytest.mark.skipif(not _HAS_DB, reason="needs DATABASE_URL")
def test_list_backlog_filters_by_status(db_session):
    s = db_session
    agent_id = _founder_agent_id(s)
    raise_tension(s, instance_id="kimberim", raised_by_agent_id=agent_id, title="a", description="d")
    raise_tension(s, instance_id="kimberim", raised_by_agent_id=agent_id, title="b", description="d")
    s.flush()

    # Initially both open.
    assert len(list_backlog(s, instance_id="kimberim", status="open")) == 2
    assert len(list_backlog(s, instance_id="kimberim", status="decided")) == 0


# ── priority queue: next_tension ─────────────────────────────────────────────


@pytest.mark.live
@pytest.mark.skipif(not _HAS_DB, reason="needs DATABASE_URL")
def test_next_tension_picks_highest_priority(db_session):
    s = db_session
    agent_id = _founder_agent_id(s)
    # Three tensions with priorities 50 (default), 10 (urgent), 90 (low).
    t_low = raise_tension(s, instance_id="kimberim", raised_by_agent_id=agent_id,
                          title="low", description="d", priority=90)
    t_urgent = raise_tension(s, instance_id="kimberim", raised_by_agent_id=agent_id,
                             title="urgent", description="d", priority=10)
    t_mid = raise_tension(s, instance_id="kimberim", raised_by_agent_id=agent_id,
                          title="mid", description="d", priority=50)
    s.flush()

    popped = next_tension(s, instance_id="kimberim")
    assert popped is not None
    assert popped.id == t_urgent.id, "lowest priority number = highest priority"
    assert popped.status == "scheduled"

    # Next pop should be the mid one (priority 50).
    popped2 = next_tension(s, instance_id="kimberim")
    assert popped2.id == t_mid.id
    s.commit()


@pytest.mark.live
@pytest.mark.skipif(not _HAS_DB, reason="needs DATABASE_URL")
def test_next_tension_returns_none_when_backlog_empty(db_session):
    s = db_session
    assert next_tension(s, instance_id="kimberim") is None


@pytest.mark.live
@pytest.mark.skipif(not _HAS_DB, reason="needs DATABASE_URL")
def test_next_tension_prefers_triaged_over_open(db_session):
    """A triaged tension is preferred over an open one, even if the open one
    has a better (lower) priority number — triage is the signal of readiness."""
    s = db_session
    agent_id = _founder_agent_id(s)
    t_open = raise_tension(s, instance_id="kimberim", raised_by_agent_id=agent_id,
                           title="open-but-urgent", description="d", priority=5)
    t_open_id = t_open.id
    t_triaged = raise_tension(s, instance_id="kimberim", raised_by_agent_id=agent_id,
                              title="triaged", description="d", priority=50)
    s.flush()
    triage_tension(s, tension_id=t_triaged.id, triaged_by_agent_id=agent_id,
                   triage={"on_domain": True, "materiality": "high", "duplicate_of": None})
    s.flush()

    popped = next_tension(s, instance_id="kimberim")
    assert popped.id == t_triaged.id, "triaged should be preferred over open"
    s.commit()


# ── triage ───────────────────────────────────────────────────────────────────


@pytest.mark.live
@pytest.mark.skipif(not _HAS_DB, reason="needs DATABASE_URL")
def test_triage_advances_status_and_records_assessment(db_session):
    s = db_session
    agent_id = _founder_agent_id(s)
    t = raise_tension(s, instance_id="kimberim", raised_by_agent_id=agent_id,
                      title="water usage", description="compute raises water demand")
    s.flush()

    assessment = {
        "on_domain": True,
        "materiality": "high",
        "duplicate_of": None,
        "notes": "First time raised; relevant to compute circle.",
    }
    triaged = triage_tension(s, tension_id=t.id, triaged_by_agent_id=agent_id, triage=assessment)
    s.commit()

    assert triaged.status == "triaged"
    assert triaged.triaged_by == agent_id
    assert triaged.triaged_at is not None
    # The triage JSON round-trips.
    import json
    assert json.loads(triaged.triage)["materiality"] == "high"


# ── close the loop: mark_decided ─────────────────────────────────────────────


@pytest.mark.live
@pytest.mark.skipif(not _HAS_DB, reason="needs DATABASE_URL")
def test_mark_decided_links_tension_to_decision(db_session):
    s = db_session
    agent_id = _founder_agent_id(s)
    t = raise_tension(s, instance_id="kimberim", raised_by_agent_id=agent_id,
                      title="decide me", description="d")
    s.flush()
    mark_in_deliberation(s, tension_id=t.id)
    s.flush()

    # decision_id is a real FK — insert a minimal proposal + decision row so the
    # reference is valid (mirrors what record() will do in 5.7).
    prop = ProposalRow(
        instance_id="kimberim", tension_id=t.id, drafted_by=agent_id,
        title="p", context="", change="", expected_impact="", safe_to_try_rationale="",
    )
    s.add(prop)
    s.flush()
    decision = DecisionRow(
        instance_id="kimberim", proposal_id=prop.id, outcome="adopted",
        weighted_consent=2.0, weighted_objection=0.0,
    )
    s.add(decision)
    s.flush()

    closed = mark_decided(s, tension_id=t.id, decision_id=decision.id)
    s.commit()

    assert closed.status == "decided"
    assert closed.decision_id == decision.id

    # get_tension reflects the final state.
    refetched = get_tension(s, tension_id=t.id)
    assert refetched.status == "decided"
    assert refetched.decision_id == decision.id
