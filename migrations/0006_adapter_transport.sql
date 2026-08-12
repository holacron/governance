-- Holon migration 0006 — adapter transport hint on agent_registry (Sprint 7)
-- Records whether a registered agent runs via platform-proxy ("provider") or
-- self-hosted ("endpoint"), so the federation factory (make_adapter) doesn't
-- have to rely solely on field heuristics. NULLable: auto-detect when absent.
-- Idempotent (ADD COLUMN IF NOT EXISTS), following the 0002/0003/0004 pattern.

ALTER TABLE agent_registry ADD COLUMN IF NOT EXISTS adapter VARCHAR;
