-- Holon migration 0003 — tension backlog & triage (Sprint 5)
-- Turns the tension table from a never-written projection into the intake
-- backlog: status lifecycle, priority queue position, triage assessment, and a
-- link back to the resulting decision (closes the dedup loop).
-- Idempotent (ADD COLUMN IF NOT EXISTS / CREATE INDEX IF NOT EXISTS).

ALTER TABLE tension ADD COLUMN IF NOT EXISTS status        VARCHAR NOT NULL DEFAULT 'open';
ALTER TABLE tension ADD COLUMN IF NOT EXISTS priority      INTEGER NOT NULL DEFAULT 50;
ALTER TABLE tension ADD COLUMN IF NOT EXISTS triage        TEXT;
ALTER TABLE tension ADD COLUMN IF NOT EXISTS triaged_by    UUID REFERENCES agent_registry (agent_id);
ALTER TABLE tension ADD COLUMN IF NOT EXISTS triaged_at    TIMESTAMP;
ALTER TABLE tension ADD COLUMN IF NOT EXISTS decision_id   UUID REFERENCES decision (id);

CREATE INDEX IF NOT EXISTS ix_tension_status ON tension (status);
CREATE INDEX IF NOT EXISTS ix_tension_priority ON tension (priority);
