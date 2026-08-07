"""S7 epoch CRUD tests (DB-gated; throwaway DB per test).

Covers the epoch store layer: open_epoch, start_epoch, close_epoch,
list_epochs, get_epoch, current_epoch. Uses a throwaway Postgres database
(created + dropped per test) so the dev DB is never modified — same pattern
as tests/test_backlog_unit.py. Marked `live`; skipped unless DATABASE_URL set.
"""

from __future__ import annotations

import os
import uuid
from urllib.parse import urlparse, urlunparse

import pytest
from dotenv import load_dotenv
from sqlalchemy import create_engine, text
from sqlmodel import Session as SMSession

from holon.store import (
    apply_migrations,
    close_epoch,
    current_epoch,
    get_epoch,
    list_epochs,
    make_engine,
    open_epoch,
    raise_tension,
    register_agent,
    start_epoch,
)

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
    dbname = f"holon_epoch_{uuid.uuid4().hex[:8]}"
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


# ── open / start / close lifecycle ────────────────────────────────────────────


@pytest.mark.live
@pytest.mark.skipif(not _HAS_DB, reason="needs DATABASE_URL")
def test_open_epoch_starts_at_seq_1(db_session):
    s = db_session
    row = open_epoch(s, instance_id="kimberim")
    s.commit()
    assert row.seq == 1
    assert row.status == "pending"
    assert row.opened_at is not None
    assert row.closed_at is None


@pytest.mark.live
@pytest.mark.skipif(not _HAS_DB, reason="needs DATABASE_URL")
def test_open_epoch_seq_is_monotonic(db_session):
    s = db_session
    e1 = open_epoch(s, instance_id="kimberim")
    e2 = open_epoch(s, instance_id="kimberim")
    e3 = open_epoch(s, instance_id="kimberim")
    s.commit()
    assert (e1.seq, e2.seq, e3.seq) == (1, 2, 3)


@pytest.mark.live
@pytest.mark.skipif(not _HAS_DB, reason="needs DATABASE_URL")
def test_open_epoch_links_tension(db_session):
    s = db_session
    agent = register_agent(s, instance_id="kimberim", display_name="Founder")
    s.flush()
    t = raise_tension(
        s, instance_id="kimberim", raised_by_agent_id=agent.agent_id,
        title="water", description="d",
    )
    s.flush()
    row = open_epoch(s, instance_id="kimberim", tension_id=t.id)
    s.commit()
    assert row.tension_id == t.id


@pytest.mark.live
@pytest.mark.skipif(not _HAS_DB, reason="needs DATABASE_URL")
def test_start_then_close_lifecycle(db_session):
    s = db_session
    e = open_epoch(s, instance_id="kimberim")
    s.flush()
    run_id = uuid.uuid4()
    started = start_epoch(s, epoch_id=e.id, run_id=run_id)
    s.flush()
    assert started.status == "running"
    assert started.run_id == run_id

    closed = close_epoch(s, epoch_id=e.id)
    s.commit()
    assert closed.status == "completed"
    assert closed.closed_at is not None

    refetched = get_epoch(s, epoch_id=e.id)
    assert refetched.status == "completed"


@pytest.mark.live
@pytest.mark.skipif(not _HAS_DB, reason="needs DATABASE_URL")
def test_close_epoch_skipped_emits_skipped_status(db_session):
    s = db_session
    e = open_epoch(s, instance_id="kimberim")
    s.flush()
    closed = close_epoch(s, epoch_id=e.id, status="skipped")
    s.commit()
    assert closed.status == "skipped"


# ── list / get / current ──────────────────────────────────────────────────────


@pytest.mark.live
@pytest.mark.skipif(not _HAS_DB, reason="needs DATABASE_URL")
def test_list_epochs_newest_first(db_session):
    s = db_session
    open_epoch(s, instance_id="kimberim")
    open_epoch(s, instance_id="kimberim")
    open_epoch(s, instance_id="kimberim")
    s.flush()
    epochs = list_epochs(s, instance_id="kimberim")
    assert [e.seq for e in epochs] == [3, 2, 1]


@pytest.mark.live
@pytest.mark.skipif(not _HAS_DB, reason="needs DATABASE_URL")
def test_list_epochs_filters_by_status(db_session):
    s = db_session
    e1 = open_epoch(s, instance_id="kimberim")
    e2 = open_epoch(s, instance_id="kimberim")
    s.flush()
    start_epoch(s, epoch_id=e2.id, run_id=uuid.uuid4())
    s.flush()
    pending = list_epochs(s, instance_id="kimberim", status="pending")
    running = list_epochs(s, instance_id="kimberim", status="running")
    assert len(pending) == 1 and pending[0].id == e1.id
    assert len(running) == 1 and running[0].id == e2.id


@pytest.mark.live
@pytest.mark.skipif(not _HAS_DB, reason="needs DATABASE_URL")
def test_current_epoch_returns_running(db_session):
    s = db_session
    open_epoch(s, instance_id="kimberim")  # pending
    e2 = open_epoch(s, instance_id="kimberim")
    s.flush()
    start_epoch(s, epoch_id=e2.id, run_id=uuid.uuid4())
    s.flush()
    cur = current_epoch(s, instance_id="kimberim")
    assert cur is not None
    assert cur.id == e2.id
    assert cur.status == "running"


@pytest.mark.live
@pytest.mark.skipif(not _HAS_DB, reason="needs DATABASE_URL")
def test_current_epoch_none_when_not_running(db_session):
    s = db_session
    open_epoch(s, instance_id="kimberim")  # pending only
    s.flush()
    assert current_epoch(s, instance_id="kimberim") is None


@pytest.mark.live
@pytest.mark.skipif(not _HAS_DB, reason="needs DATABASE_URL")
def test_get_epoch_unknown_returns_none(db_session):
    s = db_session
    assert get_epoch(s, epoch_id=uuid.uuid4()) is None
