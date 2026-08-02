"""Persistence layer — Postgres-backed immutable ledger + agent registry.

Sprint 0 delivers the schema and connection. The store uses psycopg3 directly
for the migration/connection plumbing and SQLModel table definitions for the
canonical tables. All tables carry `instance_id` (tenant isolation decision:
shared schema + tenant column; ROADMAP §7, §12 open question).

The ledger (ledger_event) is append-only: rows are inserted, never updated or
deleted, so any decision is fully reconstructable (ROADMAP §3 step 11, §9).
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from sqlmodel import Field, SQLModel, create_engine, select
from sqlmodel import Session as SMSession

from holon.config import REPO_ROOT

log = logging.getLogger(__name__)

# ── SQLModel tables ───────────────────────────────────────────────────────────


def _uuid_pk() -> UUID:
    return uuid4()


def _now() -> datetime:
    return datetime.now(UTC)


class InstanceRow(SQLModel, table=True):
    """Registered instances (ROADMAP §7). One row per instance_id."""

    __tablename__ = "instance"

    instance_id: str = Field(primary_key=True)
    display_name: str
    created_at: datetime = Field(default_factory=_now)


class AgentRegistryRow(SQLModel, table=True):
    """Agent registry (ROADMAP §6) — staff + participant agents."""

    __tablename__ = "agent_registry"

    agent_id: UUID = Field(default_factory=_uuid_pk, primary_key=True)
    instance_id: str = Field(index=True)
    role: str
    display_name: str = ""
    # Stake/reputation weight (ROADMAP §2.3, §9).
    weight: float = 1.0
    created_at: datetime = Field(default_factory=_now)


class LedgerEventRow(SQLModel, table=True):
    """The immutable ledger (ROADMAP §3 step 11). Append-only by convention.

    sequence is monotonic per-instance, making the ledger replayable. The store
    helper append_ledger_event() computes it transactionally.
    """

    __tablename__ = "ledger_event"

    id: UUID = Field(default_factory=_uuid_pk, primary_key=True)
    instance_id: str = Field(index=True)
    sequence: int
    event_type: str = Field(index=True)
    payload: str  # JSON-encoded cycle model (Tension/Proposal/Vote/...)
    created_at: datetime = Field(default_factory=_now)


class RunnerStateRow(SQLModel, table=True):
    """Checkpoint state for the autonomous runner (ROADMAP §11).

    One row per (instance, run). Lets a stopped run resume and lets a human
    inspect/intervene — the L2 consent gate.
    """

    __tablename__ = "runner_state"

    run_id: UUID = Field(default_factory=_uuid_pk, primary_key=True)
    instance_id: str = Field(index=True)
    status: str = Field(default="pending", index=True)  # pending|running|stopped|done
    current_task: str = ""
    spent_usd: float = 0.0
    iterations: int = 0
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)


# Lightweight convenience tables mirroring the schema models — store the full
# structured cycle objects in the ledger; these are query-friendly projections.
class TensionRow(SQLModel, table=True):
    __tablename__ = "tension"
    id: UUID = Field(default_factory=_uuid_pk, primary_key=True)
    instance_id: str = Field(index=True)
    raised_by: UUID = Field(foreign_key="agent_registry.agent_id")
    title: str
    description: str
    created_at: datetime = Field(default_factory=_now)


class ProposalRow(SQLModel, table=True):
    __tablename__ = "proposal"
    id: UUID = Field(default_factory=_uuid_pk, primary_key=True)
    instance_id: str = Field(index=True)
    tension_id: UUID = Field(foreign_key="tension.id")
    drafted_by: UUID = Field(foreign_key="agent_registry.agent_id")
    title: str
    context: str = ""
    change: str = ""
    expected_impact: str = ""
    safe_to_try_rationale: str = ""
    state: str = "drafted"
    created_at: datetime = Field(default_factory=_now)


class VoteRow(SQLModel, table=True):
    __tablename__ = "vote"
    id: UUID = Field(default_factory=_uuid_pk, primary_key=True)
    instance_id: str = Field(index=True)
    proposal_id: UUID = Field(foreign_key="proposal.id", index=True)
    cast_by: UUID = Field(foreign_key="agent_registry.agent_id")
    kind: str = Field(index=True)  # consent|objection|abstain
    created_at: datetime = Field(default_factory=_now)


class DecisionRow(SQLModel, table=True):
    __tablename__ = "decision"
    id: UUID = Field(default_factory=_uuid_pk, primary_key=True)
    instance_id: str = Field(index=True)
    proposal_id: UUID = Field(foreign_key="proposal.id", index=True)
    outcome: str = Field(index=True)  # adopted|rejected|escalated
    weighted_consent: float = 0.0
    weighted_objection: float = 0.0
    founder_vetoed: bool = False
    veto_overridden: bool = False
    created_at: datetime = Field(default_factory=_now)


ALL_TABLES = [
    InstanceRow, AgentRegistryRow, TensionRow, ProposalRow,
    VoteRow, DecisionRow, LedgerEventRow, RunnerStateRow,
]

MIGRATIONS_DIR = REPO_ROOT / "migrations"


# ── Engine ────────────────────────────────────────────────────────────────────


def _engine_url(database_url: str) -> str:
    """psycopg3 driver: ensure the URL uses postgresql+psycopg scheme."""
    if database_url.startswith("postgresql://"):
        return database_url.replace("postgresql://", "postgresql+psycopg://", 1)
    return database_url


def make_engine(database_url: str, echo: bool = False):
    url = _engine_url(database_url)
    return create_engine(url, echo=echo, pool_pre_ping=True)


# ── Migration ─────────────────────────────────────────────────────────────────


def apply_migrations(database_url: str) -> None:
    """Create all tables (idempotent). Sprint 0 uses SQLModel.metadata.create_all;
    a proper migration tool (alembic) is a later-sprint refinement.
    """
    eng = make_engine(database_url)
    SQLModel.metadata.create_all(eng)
    log.info("schema applied (all tables ensured)")


def record_migration_files() -> None:
    """Write a human-readable migration note alongside the code-generated schema."""
    note = MIGRATIONS_DIR / "0001_initial.sql"
    if note.exists():
        return
    note.parent.mkdir(parents=True, exist_ok=True)
    # Reflect the intent; the actual DDL is emitted by SQLModel at apply time.
    note.write_text(
        "-- Holon initial schema (Sprint 0)\n"
        "-- Tables: instance, agent_registry, tension, proposal, vote,\n"
        "--         decision, ledger_event, runner_state.\n"
        "-- Applied via SQLModel.metadata.create_all() (idempotent).\n"
        "-- All tables carry instance_id (tenant isolation).\n"
        "-- ledger_event is append-only (immutable).\n",
        encoding="utf-8",
    )


# ── Ledger helper ─────────────────────────────────────────────────────────────


def append_ledger_event(
    session: SMSession,
    *,
    instance_id: str,
    event_type: str,
    payload: dict[str, Any],
) -> LedgerEventRow:
    """Append an immutable ledger event, computing the per-instance sequence
    transactionally so the ledger is strictly monotonic and replayable.
    """
    # Highest existing sequence for this instance.
    stmt = select(LedgerEventRow).where(LedgerEventRow.instance_id == instance_id)
    existing = session.exec(stmt).all()
    next_seq = (max(e.sequence for e in existing) + 1) if existing else 1
    row = LedgerEventRow(
        instance_id=instance_id,
        sequence=next_seq,
        event_type=event_type,
        payload=json.dumps(payload, default=str),
    )
    session.add(row)
    session.flush()
    return row


__all__ = [
    "ALL_TABLES",
    "AgentRegistryRow",
    "DecisionRow",
    "InstanceRow",
    "LedgerEventRow",
    "ProposalRow",
    "RunnerStateRow",
    "TensionRow",
    "VoteRow",
    "append_ledger_event",
    "apply_migrations",
    "make_engine",
    "record_migration_files",
]
