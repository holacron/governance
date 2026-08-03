-- Holon migration 0002 — agent registration (Sprint 4)
-- Adds the 'Welcome an Agent' fields to agent_registry. Idempotent.
-- These were added to the SQLModel in S4.1; this upgrades an existing S0-era DB.

ALTER TABLE agent_registry ADD COLUMN IF NOT EXISTS owner        VARCHAR NOT NULL DEFAULT '';
ALTER TABLE agent_registry ADD COLUMN IF NOT EXISTS capability  VARCHAR NOT NULL DEFAULT '';
ALTER TABLE agent_registry ADD COLUMN IF NOT EXISTS model       VARCHAR NOT NULL DEFAULT '';
ALTER TABLE agent_registry ADD COLUMN IF NOT EXISTS endpoint    VARCHAR NOT NULL DEFAULT '';
ALTER TABLE agent_registry ADD COLUMN IF NOT EXISTS api_key_enc VARCHAR NOT NULL DEFAULT '';
