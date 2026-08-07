-- Holon migration 0004 — ABAC taxonomy columns on agent_registry (Sprint 6)
-- Adds the stakeholder-type × functional-domain cell + resolved permissions so
-- the ABAC matrix (resolve_cell) is resolved once at registration and stored on
-- the row. NULLable + defaulted to preserve every pre-S6 registration.
-- Idempotent (ADD COLUMN IF NOT EXISTS), following the 0002/0003 pattern.

ALTER TABLE agent_registry ADD COLUMN IF NOT EXISTS stakeholder_type    VARCHAR;
ALTER TABLE agent_registry ADD COLUMN IF NOT EXISTS functional_domain   VARCHAR;
ALTER TABLE agent_registry ADD COLUMN IF NOT EXISTS permissions         TEXT;

CREATE INDEX IF NOT EXISTS ix_agent_registry_stakeholder_type ON agent_registry (stakeholder_type);
