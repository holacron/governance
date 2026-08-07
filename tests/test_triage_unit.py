"""S5 triage-flow tests (StubAgent-driven; throwaway DB).

Tests the triage SOFT-GATE logic: the Triage Guardian assesses a tension
(dedup, on-domain, materiality) and the assessment is recorded without ever
blocking the tension. Uses a StubAgent for the Guardian (no live LLM) but the
real store + a real throwaway Postgres DB.
"""

from __future__ import annotations

import json
import os
import uuid
from urllib.parse import urlparse, urlunparse

import pytest
from dotenv import load_dotenv
from sqlalchemy import create_engine, text
from sqlmodel import Session as SMSession

from holon.agents import StubAgent
from holon.schema import AgentRole
from holon.store import (
    apply_migrations,
    get_tension,
    list_backlog,
    make_engine,
    raise_tension,
    register_agent,
    triage_tension,
)
from holon.utils import extract_json

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
    db_url = os.environ["DATABASE_URL"]
    dbname = f"holon_triage_{uuid.uuid4().hex[:8]}"
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


def _founder(s: SMSession, instance_id: str = "kimberim") -> uuid.UUID:
    row = register_agent(s, instance_id=instance_id, display_name="Founder", role="founder")
    s.flush()
    return row.agent_id


# ── Triage Guardian assessment shape ────────────────────────────────────────


@pytest.mark.live
@pytest.mark.skipif(not _HAS_DB, reason="needs DATABASE_URL")
def test_stub_triage_guardian_returns_structured_assessment():
    """The Triage Guardian's output parses into the expected assessment shape:
    on_domain, materiality, duplicate_of, notes, suggested_priority."""
    guardian = StubAgent(
        json.dumps({
            "on_domain": True,
            "materiality": "high",
            "duplicate_of": None,
            "notes": "Relevant to the compute circle.",
            "suggested_priority": 20,
        }),
        role=AgentRole.TRIAGE_GUARDIAN, display_name="tg",
    )
    assessment = extract_json(guardian.respond("assess this"))
    assert assessment["on_domain"] is True
    assert assessment["materiality"] == "high"
    assert assessment["suggested_priority"] == 20


# ── Dedup: a flagged duplicate is still accepted (soft gate) ────────────────


@pytest.mark.live
@pytest.mark.skipif(not _HAS_DB, reason="needs DATABASE_URL")
def test_triage_flags_duplicate_but_does_not_block(db_session):
    """The core soft-gate property: a tension flagged as a duplicate is STILL
    in the backlog and can still be deliberated. The flag is advisory, public,
    and never a rejection."""
    s = db_session
    founder = _founder(s)
    # An existing tension in the backlog.
    original = raise_tension(
        s, instance_id="kimberim", raised_by_agent_id=founder,
        title="Grid export revenue crowding out compute", description="d",
    )
    s.flush()
    # A new tension that duplicates it.
    dupe = raise_tension(
        s, instance_id="kimberim", raised_by_agent_id=founder,
        title="Export revenue vs compute value-add conflict", description="d2",
    )
    s.flush()

    # The Guardian flags it as a duplicate of the original.
    assessment = {
        "on_domain": True,
        "materiality": "medium",
        "duplicate_of": str(original.id),
        "notes": "Substantially the same tension as an existing one.",
        "suggested_priority": 70,
    }
    triaged = triage_tension(
        s, tension_id=dupe.id, triaged_by_agent_id=founder, triage=assessment,
    )
    s.commit()

    # SOFT GATE: the duplicate is still in the backlog (status triaged, not
    # rejected/parked). It can still be deliberated.
    assert triaged.status == "triaged"
    assert get_tension(s, tension_id=dupe.id) is not None

    # The flag is recorded + visible in the public record.
    stored = json.loads(triaged.triage)
    assert stored["duplicate_of"] == str(original.id)

    # Both tensions are still listable.
    backlog = list_backlog(s, instance_id="kimberim")
    assert len(backlog) == 2


# ── Off-domain / noise flagged but not blocked ──────────────────────────────


@pytest.mark.live
@pytest.mark.skipif(not _HAS_DB, reason="needs DATABASE_URL")
def test_triage_flags_off_domain_noise_but_keeps_it(db_session):
    """An off-domain, noise-rated tension is flagged low-priority but never
    removed from the backlog — the founder can still pick it up."""
    s = db_session
    founder = _founder(s)
    t = raise_tension(
        s, instance_id="kimberim", raised_by_agent_id=founder,
        title="What colour should the logo be?", description="purely cosmetic.",
    )
    s.flush()

    assessment = {
        "on_domain": False,
        "materiality": "noise",
        "duplicate_of": None,
        "notes": "Cosmetic; not a governance tension.",
        "suggested_priority": 99,
    }
    triaged = triage_tension(
        s, tension_id=t.id, triaged_by_agent_id=founder, triage=assessment,
    )
    s.commit()

    # Still in the backlog, just low priority + flagged.
    assert triaged.status == "triaged"
    assert triaged.priority == 50  # original priority unchanged by triage
    stored = json.loads(triaged.triage)
    assert stored["materiality"] == "noise"
    assert stored["suggested_priority"] == 99


# ── extract_json robustness (the Guardian's output path) ────────────────────


def test_extract_json_handles_markdown_fenced_llm_output():
    """The Guardian (like all LLM agents) may wrap JSON in markdown fences.
    extract_json must still recover the assessment."""
    fenced = '```json\n{"on_domain": true, "materiality": "high"}\n```'
    result = extract_json(fenced)
    assert result["on_domain"] is True


def test_extract_json_returns_empty_on_garbage():
    """A totally malformed Guardian response yields {} not a crash — the triage
    is recorded with an empty assessment rather than aborting."""
    assert extract_json("the tension is fine I guess") == {}
