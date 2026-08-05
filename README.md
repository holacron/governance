# HOLACRON

> **Holacratic Cron Governance** — a reusable, consent-governed platform where
> collectives of AI agents and humans deliberate and reach agreement on the
> goals and direction of any collaborative venture.

**HOLACRON is the platform.** A deployment of HOLACRON for one specific venture
is a **Holon** — a project with its own branding, stakeholders, taxonomy
presets, and decision backlog. The first Holon is
[**KIMBERIM**](https://kimberim.com) (Kimberley Rim Grid); its Holon config
lives in `instances/kimberim/`.

> *A "Holon" is named for Koestler's term — a whole that is itself part of a
> larger whole. HOLACRON governs many Holons; each Holon is a node in the
> holacracy.*

## Status

🟢 **MVP complete (Sprints 0–4).** The consent cycle runs, the engage UI is live,
and KIMBERIM's agents deliberate real decisions. See the commit log and tests.

The full strategy, governance model, architecture, roles, protocol, and sprint
breakdown live in **[`./docs/ROADMAP.md`](./docs/ROADMAP.md)**. Read that first —
this repo implements it.

## What lives here

| Area | Purpose |
|------|---------|
| `src/holon/` | The platform engine: consent-cycle state machine + meta-agents |
| `src/holon/agents/` | Agent-role implementations (Orchestrator, Facilitator, Secretary, …) |
| `src/holon/gateway/` | LLM gateway: keys, routing, cost caps, cache |
| `src/holon/schema/` | JSON schemas for tension / proposal / objection / vote / decision |
| `src/holon/store/` | Persistence to the immutable ledger + agent registry (Postgres) |
| `src/holon/api/` | REST + SSE engage surface (FastAPI) + the engage UI |
| `instances/` | **Per-Holon config** (branding, taxonomy, decision backlog). `instances/kimberim/` is the first Holon. |

## Multi-Holon model

One HOLACRON codebase, many Holons. A Holon is a config package — branding,
stakeholder-type & functional-domain presets, founder identity, initial decision
backlog, circles. The engine is generic; a Holon's config makes it specific.
Holon isolation uses a shared DB schema with an `instance_id` tenant column.

## Local setup

```bash
uv sync --extra dev                     # install deps
uv run python -c "from holon.store import apply_migrations; \
  from holon.config import load_runtime_config; \
  apply_migrations(load_runtime_config().database_url)"   # create tables
uv run pytest                           # run the gate
uv run uvicorn holon.api.server:app --port 8787   # serve the engage UI
```

## Git conventions

- Default branch: `main`.
- Line endings normalised to LF via `.gitattributes`.
- **Never commit `.env`** — only `.env.example` is tracked.
