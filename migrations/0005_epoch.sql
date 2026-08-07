-- Holon migration 0005 — epoch engine (Sprint 7)
-- An epoch is the configurable heartbeat of the collective: one governance
-- cycle per epoch (ROADMAP glossary). This table tracks the epoch lifecycle
-- (pending → running → completed|skipped) and links each epoch to the tension
-- it deliberated + the SSE run it spawned, closing the cadence loop.
-- Idempotent (CREATE TABLE IF NOT EXISTS / CREATE INDEX IF NOT EXISTS).

CREATE TABLE IF NOT EXISTS epoch (
    id            UUID PRIMARY KEY,
    instance_id   VARCHAR NOT NULL,
    seq           INTEGER NOT NULL,
    status        VARCHAR NOT NULL DEFAULT 'pending',
    tension_id    UUID REFERENCES tension (id),
    run_id        UUID,
    opened_at     TIMESTAMP,
    closed_at     TIMESTAMP,
    UNIQUE (instance_id, seq)
);

CREATE INDEX IF NOT EXISTS ix_epoch_instance_id ON epoch (instance_id);
CREATE INDEX IF NOT EXISTS ix_epoch_status ON epoch (status);
