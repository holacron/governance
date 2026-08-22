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

from olon.store import apply_migrations, make_engine

load_dotenv()

_HAS_DB = bool(os.getenv("DATABASE_URL"))

# The 9 tables the authoritative migrations must create (8 from 0001 + epoch from 0005).
_EXPECTED_TABLES = {
    "instance", "agent_registry", "tension", "proposal",
    "vote", "decision", "ledger_event", "runner_state", "epoch",
}
# The 5 S4 registration columns 0002 must add to agent_registry.
_EXPECTED_AGENT_REGISTRY_COLUMNS = {
    "owner", "capability", "model", "endpoint", "api_key_enc",
}
# The 6 S5 backlog columns 0003 must add to tension.
_EXPECTED_TENSION_COLUMNS = {
    "status", "priority", "triage", "triaged_by", "triaged_at", "decision_id",
}
# The 3 S6 ABAC taxonomy columns 0004 must add to agent_registry.
_EXPECTED_ABAC_COLUMNS = {
    "stakeholder_type", "functional_domain", "permissions",
}
# The S7 adapter transport hint 0006 must add to agent_registry.
_EXPECTED_ADAPTER_COLUMNS = {"adapter"}


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
    dbname = f"olon_migtest_{uuid.uuid4().hex[:8]}"
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
            # The 6 S5 backlog columns exist on tension.
            tension_cols = {c["name"] for c in insp.get_columns("tension")}
            assert _EXPECTED_TENSION_COLUMNS <= tension_cols, (
                f"missing tension columns: {_EXPECTED_TENSION_COLUMNS - tension_cols}"
            )
            # The 3 S6 ABAC taxonomy columns exist on agent_registry.
            assert _EXPECTED_ABAC_COLUMNS <= cols, (
                f"missing agent_registry ABAC columns: "
                f"{_EXPECTED_ABAC_COLUMNS - cols}"
            )
            # The S7 adapter column exists on agent_registry.
            assert _EXPECTED_ADAPTER_COLUMNS <= cols, (
                f"missing agent_registry adapter columns: "
                f"{_EXPECTED_ADAPTER_COLUMNS - cols}"
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
    dbname = f"olon_migtest_{uuid.uuid4().hex[:8]}"
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
