# kimberim-agents

> The holacratic agent harness for **KIMBERIM** — a federated, consent-governed
> collective of agents (internal staff + external participants) that deliberate
> and reach agreement on the goals and design of the Kimberley Rim Grid.

This repo holds the **orchestration runtime** (the harness), separate from the
marketing/docs site ([`../kimberim-site`](../kimberim-site)).

## Status

🟢 **Sprint 0 — Foundations & spike** (not yet started).

The full strategy, governance model, architecture, roles, protocol, and sprint
breakdown live in **[`./docs/ROADMAP.md`](./docs/ROADMAP.md)**. Read that first —
this repo implements it.

## What lives here (target)

| Area | Purpose |
|------|---------|
| `runtime/` (or `src/`) | The consent-cycle state machine + internal meta-agents |
| `agents/` | Agent-role implementations (Orchestrator, Facilitator, Secretary, …) |
| `adapter/` | Uniform agent adapter — abstracts external providers (OpenAI/Anthropic/local) |
| `gateway/` | LLM gateway: keys, routing, cost caps, cache |
| `schema/` | JSON schemas for tension / proposal / objection / vote / decision |
| `store/` | Persistence to the immutable ledger + agent registry (Postgres) |
| `api/` | REST + SSE/WebSocket surface consumed by the Engage page |

## Sprint 0 open decisions

1. **Runtime** — Python + LangGraph vs Node.js + TypeScript (settled via spike).
2. **Agent identity model** — self-hosted endpoints vs proxied provider keys.
3. **Cost model** — who pays for external agents' LLM calls.
4. **First real decision** — which KIMBERIM design question is the MVP test case.

## Local setup

_TBD in Sprint 0._ Provisional:

```bash
# Copy the secrets template and fill in your provider key(s)
cp .env.example .env
# edit .env

# (Python path)            |  (Node path)
# python -m venv .venv     |  npm install
# .venv/Scripts/activate   |
# pip install -e .[dev]    |
# pytest                   |  npm test
```

## Git conventions

- Default branch: `main`.
- Line endings normalised to LF via `.gitattributes`.
- **Never commit `.env`** — only `.env.example` is tracked.
