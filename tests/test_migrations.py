"""Migration regression test (H6).

`apply_migrations` had ZERO test coverage; a fresh-DB deploy was unverified
(the migration-drift bug originally slipped through exactly because of this).
This test would have caught it.

Runs against a THROWAWAY database (created + dropped per test) so the dev DB is
never touched. Needs DATABASE_URL (the server address/credentials); the test
connects to the postgres maintenance DB to issue CREATE/DATABASE DROP DATABASE.

Marked ``live``: skipped unless DATABASE_URL is set.
"""

from __future__ import annotations

import os
import uuid
from urllib.parse import urlparse, urlunparse

import pytest
from dotenv import load_dotenv
from sqlalchemy import create_engine, inspect, text

from holon.store import apply_migrations, make_engine

load_dotenv()

_HAS_DB = bool(os.getenv("DATABASE_URL"))

# The 8 tables the authoritative 0001 migration must create.
_EXPECTED_TABLES = {
    "instance", "agent_registry", "tension", "proposal",
    "vote", "decision", "ledger_event", "runner_state",
}
# The 5 S4 registration columns 0002 must add to agent_registry.
_EXPECTED_AGENT_REGISTRY_COLUMNS = {
    "owner", "capability", "model", "endpoint", "api_key_enc",
}


def _maintenance_url(db_url: str) -> str:
    """The DATABASE_URL rewired to point at the postgres maintenance DB, used to
    CREATE/DROP the throwaway test database."""
    parts = urlparse(db_url)
    return urlunparse(parts._replace(path="/postgres"))


def _throwaway_url(db_url: str, dbname: str) -> str:
    parts = urlparse(db_url)
    return urlunparse(parts._replace(path=f"/{dbname}"))


def _autocommit_engine(database_url: str):
    """An engine in AUTOCOMMIT mode (CREATE/DROP DATABASE can't run in a txn),
    using the psycopg3 driver the rest of the stack uses."""
    url = database_url.replace("postgresql://", "postgresql+psycopg://", 1)
    return create_engine(url, isolation_level="AUTOCOMMIT")


@pytest.mark.live
@pytest.mark.skipif(not _HAS_DB, reason="needs DATABASE_URL")
def test_apply_migrations_creates_full_schema_on_fresh_db():
    """A fresh database, after apply_migrations, has all 8 tables and the 5 S4
    registration columns on agent_registry. This is the test that would have
    caught the original migration-drift bug.

    Uses a throwaway database so the dev DB is never modified.
    """
    db_url = os.environ["DATABASE_URL"]
    dbname = f"holon_migtest_{uuid.uuid4().hex[:8]}"
    throwaway = _throwaway_url(db_url, dbname)

    # Create the throwaway DB via the maintenance connection (autocommit mode —
    # CREATE DATABASE cannot run inside a transaction).
    maint = _autocommit_engine(_maintenance_url(db_url))
    try:
        with maint.connect() as conn:
            conn.execute(text(f'CREATE DATABASE "{dbname}"'))
    finally:
        maint.dispose()

    try:
        # The act under test: a completely fresh DB, migrations applied.
        apply_migrations(throwaway)

        eng = make_engine(throwaway)
        try:
            insp = inspect(eng)
            tables = set(insp.get_table_names())
            # All 8 core tables present.
            assert _EXPECTED_TABLES <= tables, (
                f"missing tables: {_EXPECTED_TABLES - tables}"
            )
            # The 5 S4 registration columns exist on agent_registry.
            cols = {c["name"] for c in insp.get_columns("agent_registry")}
            assert _EXPECTED_AGENT_REGISTRY_COLUMNS <= cols, (
                f"missing agent_registry columns: "
                f"{_EXPECTED_AGENT_REGISTRY_COLUMNS - cols}"
            )
        finally:
            eng.dispose()
    finally:
        # Always clean up the throwaway DB, even on assertion failure.
        maint = _autocommit_engine(_maintenance_url(db_url))
        try:
            with maint.connect() as conn:
                # Terminate any leftover connection from the engine above first.
                conn.execute(
                    text(
                        "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                        "WHERE datname = :d AND pid <> pg_backend_pid()"
                    ),
                    {"d": dbname},
                )
                conn.execute(text(f'DROP DATABASE IF EXISTS "{dbname}"'))
        finally:
            maint.dispose()


@pytest.mark.live
@pytest.mark.skipif(not _HAS_DB, reason="needs DATABASE_URL")
def test_apply_migrations_is_idempotent():
    """Running apply_migrations twice is a no-op (idempotent: IF NOT EXISTS /
    ADD COLUMN IF NOT EXISTS). This is what lets it upgrade an existing DB."""
    db_url = os.environ["DATABASE_URL"]
    dbname = f"holon_migtest_{uuid.uuid4().hex[:8]}"
    throwaway = _throwaway_url(db_url, dbname)

    maint = _autocommit_engine(_maintenance_url(db_url))
    try:
        with maint.connect() as conn:
            conn.execute(text(f'CREATE DATABASE "{dbname}"'))
    finally:
        maint.dispose()

    try:
        apply_migrations(throwaway)
        # Second run must not error (idempotency is the whole point).
        apply_migrations(throwaway)

        eng = make_engine(throwaway)
        try:
            tables = set(inspect(eng).get_table_names())
            assert _EXPECTED_TABLES <= tables
        finally:
            eng.dispose()
    finally:
        maint = _autocommit_engine(_maintenance_url(db_url))
        try:
            with maint.connect() as conn:
                conn.execute(
                    text(
                        "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                        "WHERE datname = :d AND pid <> pg_backend_pid()"
                    ),
                    {"d": dbname},
                )
                conn.execute(text(f'DROP DATABASE IF EXISTS "{dbname}"'))
        finally:
            maint.dispose()
