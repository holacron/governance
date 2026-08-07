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
    # S4 registration fields (defaults preserve back-compat with S0-S3 inserts).
    owner: str = ""
    capability: str = ""  # the stakeholder perspective / capability description
    model: str = ""  # provider model id (captured for S7 federation)
    endpoint: str = ""  # provider base URL (captured for S7 federation)
    api_key_enc: str = ""  # registered key (opaque; NOT used in MVP execution)
    # S6 ABAC taxonomy cell (stakeholder-type × functional-domain). NULLable so
    # every pre-S6 registration still loads; resolved permissions cached here.
    stakeholder_type: str | None = None
    functional_domain: str | None = None
    permissions: str | None = None  # JSON array of Permission values
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


class EpochRow(SQLModel, table=True):
    """An epoch — the configurable heartbeat of the collective (S7, ROADMAP
    glossary). One governance cycle per epoch. Tracks the lifecycle
    pending → running → completed|skipped and links the epoch to the tension
    it deliberated + the SSE run it spawned."""

    __tablename__ = "epoch"

    id: UUID = Field(default_factory=_uuid_pk, primary_key=True)
    instance_id: str = Field(index=True)
    seq: int
    status: str = Field(default="pending", index=True)  # pending|running|completed|skipped
    tension_id: UUID | None = Field(default=None, foreign_key="tension.id")
    run_id: UUID | None = None
    opened_at: datetime | None = None
    closed_at: datetime | None = None


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
    # S5 backlog columns.
    status: str = Field(default="open", index=True)  # open|triaged|scheduled|in-deliberation|decided|parked
    priority: int = Field(default=50, index=True)  # lower = higher priority (1..100)
    triage: str | None = None  # JSON: {on_domain, materiality, duplicate_of, notes}
    triaged_by: UUID | None = Field(default=None, foreign_key="agent_registry.agent_id")
    triaged_at: datetime | None = None
    decision_id: UUID | None = Field(default=None, foreign_key="decision.id")


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
    VoteRow, DecisionRow, LedgerEventRow, RunnerStateRow, EpochRow,
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
    """Apply the authoritative SQL migrations, then reconcile with the ORM model.

    The .sql files in migrations/ are the source of truth (idempotent: IF NOT
    EXISTS / ADD COLUMN IF NOT EXISTS), run in filename order. This handles BOTH
    fresh setup and upgrading an existing DB (e.g. an S0-era DB missing the S4
    registration columns). A `create_all` backstop catches any drift between the
    files and the SQLModel definitions. A proper migration tool (alembic) is a
    later-sprint refinement.
    """
    eng = make_engine(database_url)
    sql_files = sorted(MIGRATIONS_DIR.glob("*.sql"))
    with eng.begin() as conn:
        for path in sql_files:
            sql = path.read_text(encoding="utf-8")
            # psycopg3 executescript-equivalent: run the whole script.
            conn.exec_driver_sql(sql)
            log.info("applied migration %s", path.name)
    # Backstop: ensure anything in the ORM model not in a .sql file is created.
    SQLModel.metadata.create_all(eng)
    log.info("schema applied (%d migration files + create_all backstop)", len(sql_files))


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


# ── Agent registration helpers (S4) ───────────────────────────────────────────


def register_agent(
    session: SMSession,
    *,
    instance_id: str,
    display_name: str,
    owner: str = "",
    capability: str = "",
    role: str = "participant",
    weight: float = 1.0,
    model: str = "",
    endpoint: str = "",
    api_key_enc: str = "",
    stakeholder_type: str | None = None,
    functional_domain: str | None = None,
    permissions: set[str] | None = None,
) -> AgentRegistryRow:
    """Register an external/participant agent (ROADMAP §6 'Welcome an Agent').

    The registered agent becomes eligible for the next cycle. model/endpoint/
    api_key_enc are captured for S7 federation; for the MVP the agent executes
    via the platform's own gateway.

    S6 ABAC: when stakeholder_type is given, the resolved permissions set is
    JSON-encoded into the permissions column. The caller resolves the cell from
    the instance's ABACMatrix (via holon.config.resolve_cell) and passes both
    the taxonomy cell and the resolved permissions — store stays YAML-free.
    weight is the caller's call too (defaults 1.0 preserves back-compat).
    """
    row = AgentRegistryRow(
        instance_id=instance_id,
        role=role,
        display_name=display_name,
        weight=weight,
        owner=owner,
        capability=capability,
        model=model,
        endpoint=endpoint,
        api_key_enc=api_key_enc,
        stakeholder_type=stakeholder_type,
        functional_domain=functional_domain,
        permissions=json.dumps(sorted(permissions)) if permissions else None,
    )
    session.add(row)
    session.flush()
    return row


def list_agents(session: SMSession, *, instance_id: str) -> list[AgentRegistryRow]:
    """List registered agents for an instance."""
    stmt = select(AgentRegistryRow).where(AgentRegistryRow.instance_id == instance_id)
    return list(session.exec(stmt).all())


def agent_permissions(row: AgentRegistryRow | None) -> set[str]:
    """Resolve an agent's permission set from its cached permissions column.

    NULL permissions (every pre-S6 registration, and any agent registered
    without a stakeholder_type) → the participant default {submit, deliberate,
    vote}. This preserves the pre-ABAC 'anyone can act as a participant'
    behaviour for all existing rows and tests.
    """
    if row is None:
        return {"submit", "deliberate", "vote"}
    if not row.permissions:
        return {"submit", "deliberate", "vote"}
    try:
        perms = json.loads(row.permissions)
        return set(perms) if isinstance(perms, list) else {"submit", "deliberate", "vote"}
    except (json.JSONDecodeError, TypeError):
        return {"submit", "deliberate", "vote"}


def get_agent(session: SMSession, *, agent_id: UUID) -> AgentRegistryRow | None:
    """Fetch a single registered agent by id (None if absent)."""
    return session.get(AgentRegistryRow, agent_id)


# ── Tension backlog CRUD (S5) ────────────────────────────────────────────────


def raise_tension(
    session: SMSession,
    *,
    instance_id: str,
    raised_by_agent_id: UUID,
    title: str,
    description: str,
    priority: int = 50,
) -> TensionRow:
    """Create a new tension in the backlog (status='open').

    Also appends a `tension-raised` ledger event — activating the event_type
    that has been in the enum since S0 but never emitted. The caller commits.
    """
    row = TensionRow(
        instance_id=instance_id,
        raised_by=raised_by_agent_id,
        title=title,
        description=description,
        status="open",
        priority=priority,
    )
    session.add(row)
    session.flush()
    append_ledger_event(
        session,
        instance_id=instance_id,
        event_type="tension-raised",
        payload={
            "tension_id": str(row.id),
            "title": title,
            "description": description,
            "raised_by": str(raised_by_agent_id),
            "priority": priority,
        },
    )
    return row


def get_tension(session: SMSession, *, tension_id: UUID) -> TensionRow | None:
    """Fetch a single tension by id (any status)."""
    stmt = select(TensionRow).where(TensionRow.id == tension_id)
    return session.exec(stmt).first()


def list_backlog(
    session: SMSession, *, instance_id: str, status: str | None = None,
) -> list[TensionRow]:
    """List tensions for an instance, optionally filtered by status.

    Ordered by priority ascending (lower number = higher priority), then
    created_at (oldest first) for a stable, fair queue.
    """
    stmt = select(TensionRow).where(TensionRow.instance_id == instance_id)
    if status is not None:
        stmt = stmt.where(TensionRow.status == status)
    stmt = stmt.order_by(TensionRow.priority.asc(), TensionRow.created_at.asc())
    return list(session.exec(stmt).all())


def triage_tension(
    session: SMSession,
    *,
    tension_id: UUID,
    triaged_by_agent_id: UUID,
    triage: dict[str, Any],
) -> TensionRow | None:
    """Record a Triage Guardian's assessment and advance status to 'triaged'.

    `triage` is the structured assessment: {on_domain, materiality,
    duplicate_of, notes}. Also appends a `tension-triaged` ledger event so the
    assessment is part of the fully-public record.
    """
    row = get_tension(session, tension_id=tension_id)
    if row is None:
        return None
    row.triage = json.dumps(triage, default=str)
    row.triaged_by = triaged_by_agent_id
    row.triaged_at = datetime.now(UTC)
    row.status = "triaged"
    session.add(row)
    session.flush()
    append_ledger_event(
        session,
        instance_id=row.instance_id,
        event_type="tension-triaged",
        payload={"tension_id": str(tension_id), "triage": triage},
    )
    return row


def next_tension(session: SMSession, *, instance_id: str) -> TensionRow | None:
    """Pop the highest-priority triaged tension for deliberation.

    Prefers 'triaged' tensions; falls back to 'open' (untriaged) if none are
    triaged yet, so the system degrades gracefully when triage is skipped.
    Marks the chosen tension 'scheduled'. Returns None if the backlog is empty.
    """
    for preferred_status in ("triaged", "open"):
        stmt = (
            select(TensionRow)
            .where(TensionRow.instance_id == instance_id)
            .where(TensionRow.status == preferred_status)
            .order_by(TensionRow.priority.asc(), TensionRow.created_at.asc())
        )
        row = session.exec(stmt).first()
        if row is not None:
            row.status = "scheduled"
            session.add(row)
            session.flush()
            return row
    return None


def mark_in_deliberation(session: SMSession, *, tension_id: UUID) -> TensionRow | None:
    """Mark a tension as actively under deliberation."""
    row = get_tension(session, tension_id=tension_id)
    if row is None:
        return None
    row.status = "in-deliberation"
    session.add(row)
    session.flush()
    return row


def mark_decided(
    session: SMSession, *, tension_id: UUID, decision_id: UUID,
) -> TensionRow | None:
    """Close the loop: link a tension to its resulting Decision and mark decided.

    This is what makes dedup a real query — a future triage can ask 'is there a
    decided tension like this?' and get a definitive answer via decision_id.
    """
    row = get_tension(session, tension_id=tension_id)
    if row is None:
        return None
    row.status = "decided"
    row.decision_id = decision_id
    session.add(row)
    session.flush()
    return row


# ── Epoch CRUD (S7) ──────────────────────────────────────────────────────────


def open_epoch(
    session: SMSession, *, instance_id: str, tension_id: UUID | None = None,
) -> EpochRow:
    """Open a new epoch for the instance. seq is monotonic per-instance
    (max+1 — same race note as append_ledger_event; SELECT FOR UPDATE deferred).
    Emits an epoch-opened ledger event. Returns the pending epoch row."""
    max_seq = session.exec(
        select(EpochRow.seq).where(EpochRow.instance_id == instance_id)
    ).all()
    next_seq = (max(max_seq) + 1) if max_seq else 1
    row = EpochRow(
        instance_id=instance_id, seq=next_seq, status="pending",
        tension_id=tension_id, opened_at=_now(),
    )
    session.add(row)
    session.flush()
    append_ledger_event(
        session, instance_id=instance_id, event_type="epoch-opened",
        payload={
            "epoch_id": str(row.id), "seq": next_seq,
            "tension_id": str(tension_id) if tension_id else None,
        },
    )
    return row


def start_epoch(
    session: SMSession, *, epoch_id: UUID, run_id: UUID,
) -> EpochRow | None:
    """Mark an epoch running, linking it to the SSE run it spawned."""
    row = session.get(EpochRow, epoch_id)
    if row is None:
        return None
    row.status = "running"
    row.run_id = run_id
    session.add(row)
    session.flush()
    return row


def close_epoch(
    session: SMSession, *, epoch_id: UUID, status: str = "completed",
) -> EpochRow | None:
    """Close an epoch (completed or skipped). Emits the matching ledger event.
    Returns the row, or None if the epoch doesn't exist."""
    row = session.get(EpochRow, epoch_id)
    if row is None:
        return None
    row.status = status
    row.closed_at = _now()
    session.add(row)
    session.flush()
    event_type = "epoch-skipped" if status == "skipped" else "epoch-closed"
    append_ledger_event(
        session, instance_id=row.instance_id, event_type=event_type,
        payload={"epoch_id": str(row.id), "seq": row.seq, "status": status},
    )
    return row


def list_epochs(
    session: SMSession, *, instance_id: str, status: str | None = None,
) -> list[EpochRow]:
    """List epochs for an instance, newest first (seq desc). Optional status filter."""
    stmt = select(EpochRow).where(EpochRow.instance_id == instance_id)
    if status is not None:
        stmt = stmt.where(EpochRow.status == status)
    stmt = stmt.order_by(EpochRow.seq.desc())
    return list(session.exec(stmt).all())


def get_epoch(session: SMSession, *, epoch_id: UUID) -> EpochRow | None:
    """Fetch a single epoch by id."""
    return session.get(EpochRow, epoch_id)


def current_epoch(
    session: SMSession, *, instance_id: str,
) -> EpochRow | None:
    """The latest running epoch for an instance (for overlap detection).
    None if no epoch is currently running."""
    stmt = (
        select(EpochRow)
        .where(EpochRow.instance_id == instance_id, EpochRow.status == "running")
        .order_by(EpochRow.seq.desc())
    )
    rows = list(session.exec(stmt).all())
    return rows[0] if rows else None


__all__ = [
    "ALL_TABLES",
    "AgentRegistryRow",
    "DecisionRow",
    "EpochRow",
    "InstanceRow",
    "LedgerEventRow",
    "ProposalRow",
    "RunnerStateRow",
    "TensionRow",
    "VoteRow",
    "agent_permissions",
    "append_ledger_event",
    "apply_migrations",
    "close_epoch",
    "current_epoch",
    "get_agent",
    "get_epoch",
    "get_tension",
    "list_agents",
    "list_backlog",
    "list_epochs",
    "make_engine",
    "mark_decided",
    "mark_in_deliberation",
    "next_tension",
    "open_epoch",
    "raise_tension",
    "register_agent",
    "start_epoch",
    "triage_tension",
]
