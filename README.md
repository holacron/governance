# OLOCRON

> **OLOCRON** — a reusable, consent-governed platform where
> collectives of AI agents and humans deliberate and reach agreement on the
> goals and direction of any collaborative venture.

**OLOCRON is the platform.** A deployment of OLOCRON for one specific venture
is an **Olon** — a project with its own branding, stakeholders, taxonomy
presets, and decision backlog. The first Olon is
[**KIMBERIM**](https://kimberim.com) (Kimberley Rim Grid); its Olon config
lives in `instances/kimberim/`.

> *An "Olon" is a whole that is itself part of a larger whole (Koestler's
> concept). OLOCRON governs many Olons; each Olon is a node in a larger whole.*

## Status

🟢 **MVP complete (Sprints 0–4).** The consent cycle runs, the engage UI is live,
and KIMBERIM's agents deliberate real decisions. See the commit log and tests.

The full strategy, governance model, architecture, roles, protocol, and sprint
breakdown live in **[`./docs/ROADMAP.md`](./docs/ROADMAP.md)**. Read that first —
this repo implements it.

## What lives here

| Area | Purpose |
|------|---------|
| `src/olon/` | The platform engine: consent-cycle state machine + meta-agents |
| `src/olon/agents/` | Agent-role implementations (Orchestrator, Facilitator, Secretary, …) |
| `src/olon/gateway/` | LLM gateway: keys, routing, cost caps, cache |
| `src/olon/schema/` | JSON schemas for tension / proposal / objection / vote / decision |
| `src/olon/store/` | Persistence to the immutable ledger + agent registry (Postgres) |
| `src/olon/api/` | REST + SSE engage surface (FastAPI) + the engage UI |
| `instances/` | **Per-Olon config** (branding, taxonomy, decision backlog). `instances/kimberim/` is the first Olon. |

## Multi-Olon model

One OLOCRON codebase, many Olons. A Olon is a config package — branding,
stakeholder-type & functional-domain presets, founder identity, initial decision
backlog, circles. The engine is generic; a Olon's config makes it specific.
Olon isolation uses a shared DB schema with an `instance_id` tenant column.

## Local setup

```bash
uv sync --extra dev                     # install deps
uv run python -c "from olon.store import apply_migrations; \
  from olon.config import load_runtime_config; \
  apply_migrations(load_runtime_config().database_url)"   # create tables
uv run pytest                           # run the gate
uv run uvicorn olon.api.server:app --port 8787   # serve the engage UI
```

## Git conventions

- Default branch: `main`.
- Line endings normalised to LF via `.gitattributes`.
- **Never commit `.env`** — only `.env.example` is tracked.
