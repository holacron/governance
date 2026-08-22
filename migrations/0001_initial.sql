-- Olon initial schema (Sprint 0)
-- Authoritative DDL. Idempotent (IF NOT EXISTS). All tables carry instance_id
-- (tenant isolation); ledger_event is append-only (immutable).
-- Run by store.apply_migrations() in filename order.

CREATE TABLE IF NOT EXISTS instance (
    instance_id  VARCHAR PRIMARY KEY,
    display_name VARCHAR NOT NULL,
    created_at   TIMESTAMP NOT NULL DEFAULT (now() AT TIME ZONE 'UTC')
);

CREATE TABLE IF NOT EXISTS agent_registry (
    agent_id     UUID PRIMARY KEY,
    instance_id  VARCHAR NOT NULL,
    role         VARCHAR NOT NULL,
    display_name VARCHAR NOT NULL DEFAULT '',
    weight       DOUBLE PRECISION NOT NULL DEFAULT 1.0,
    created_at   TIMESTAMP NOT NULL DEFAULT (now() AT TIME ZONE 'UTC')
);
CREATE INDEX IF NOT EXISTS ix_agent_registry_instance_id ON agent_registry (instance_id);

CREATE TABLE IF NOT EXISTS tension (
    id           UUID PRIMARY KEY,
    instance_id  VARCHAR NOT NULL,
    raised_by    UUID NOT NULL REFERENCES agent_registry (agent_id),
    title        VARCHAR NOT NULL,
    description  VARCHAR NOT NULL,
    created_at   TIMESTAMP NOT NULL DEFAULT (now() AT TIME ZONE 'UTC')
);
CREATE INDEX IF NOT EXISTS ix_tension_instance_id ON tension (instance_id);

CREATE TABLE IF NOT EXISTS proposal (
    id                    UUID PRIMARY KEY,
    instance_id           VARCHAR NOT NULL,
    tension_id            UUID NOT NULL REFERENCES tension (id),
    drafted_by            UUID NOT NULL REFERENCES agent_registry (agent_id),
    title                 VARCHAR NOT NULL,
    context               VARCHAR NOT NULL DEFAULT '',
    change                VARCHAR NOT NULL DEFAULT '',
    expected_impact       VARCHAR NOT NULL DEFAULT '',
    safe_to_try_rationale VARCHAR NOT NULL DEFAULT '',
    state                 VARCHAR NOT NULL DEFAULT 'drafted',
    created_at            TIMESTAMP NOT NULL DEFAULT (now() AT TIME ZONE 'UTC')
);
CREATE INDEX IF NOT EXISTS ix_proposal_instance_id ON proposal (instance_id);

CREATE TABLE IF NOT EXISTS vote (
    id           UUID PRIMARY KEY,
    instance_id  VARCHAR NOT NULL,
    proposal_id  UUID NOT NULL REFERENCES proposal (id),
    cast_by      UUID NOT NULL REFERENCES agent_registry (agent_id),
    kind         VARCHAR NOT NULL,
    created_at   TIMESTAMP NOT NULL DEFAULT (now() AT TIME ZONE 'UTC')
);
CREATE INDEX IF NOT EXISTS ix_vote_instance_id ON vote (instance_id);
CREATE INDEX IF NOT EXISTS ix_vote_proposal_id ON vote (proposal_id);
CREATE INDEX IF NOT EXISTS ix_vote_kind ON vote (kind);

CREATE TABLE IF NOT EXISTS decision (
    id                  UUID PRIMARY KEY,
    instance_id         VARCHAR NOT NULL,
    proposal_id         UUID NOT NULL REFERENCES proposal (id),
    outcome             VARCHAR NOT NULL,
    weighted_consent    DOUBLE PRECISION NOT NULL DEFAULT 0.0,
    weighted_objection  DOUBLE PRECISION NOT NULL DEFAULT 0.0,
    founder_vetoed      BOOLEAN NOT NULL DEFAULT FALSE,
    veto_overridden     BOOLEAN NOT NULL DEFAULT FALSE,
    created_at          TIMESTAMP NOT NULL DEFAULT (now() AT TIME ZONE 'UTC')
);
CREATE INDEX IF NOT EXISTS ix_decision_instance_id ON decision (instance_id);
CREATE INDEX IF NOT EXISTS ix_decision_proposal_id ON decision (proposal_id);
CREATE INDEX IF NOT EXISTS ix_decision_outcome ON decision (outcome);

CREATE TABLE IF NOT EXISTS ledger_event (
    id           UUID PRIMARY KEY,
    instance_id  VARCHAR NOT NULL,
    sequence     INTEGER NOT NULL,
    event_type   VARCHAR NOT NULL,
    payload      TEXT NOT NULL,
    created_at   TIMESTAMP NOT NULL DEFAULT (now() AT TIME ZONE 'UTC')
);
CREATE INDEX IF NOT EXISTS ix_ledger_event_instance_id ON ledger_event (instance_id);
CREATE INDEX IF NOT EXISTS ix_ledger_event_event_type ON ledger_event (event_type);

CREATE TABLE IF NOT EXISTS runner_state (
    run_id       UUID PRIMARY KEY,
    instance_id  VARCHAR NOT NULL,
    status       VARCHAR NOT NULL DEFAULT 'pending',
    current_task VARCHAR NOT NULL DEFAULT '',
    spent_usd    DOUBLE PRECISION NOT NULL DEFAULT 0.0,
    iterations   INTEGER NOT NULL DEFAULT 0,
    created_at   TIMESTAMP NOT NULL DEFAULT (now() AT TIME ZONE 'UTC'),
    updated_at   TIMESTAMP NOT NULL DEFAULT (now() AT TIME ZONE 'UTC')
);
CREATE INDEX IF NOT EXISTS ix_runner_state_instance_id ON runner_state (instance_id);
CREATE INDEX IF NOT EXISTS ix_runner_state_status ON runner_state (status);
