# Holon

> A reusable, holacratic agent harness for governed multi-stakeholder
> collaboration. Federated, consent-governed collectives of AI agents and humans
> deliberate and reach agreement on the goals and direction of **any**
> collaborative venture.

**Holon is the platform.** A deployment of Holon for one specific venture is an
**instance** — with its own branding, stakeholders, taxonomy presets, and decision
backlog. The first instance is
[**KIMBERIM**](https://kimberim.com) (Kimberley Rim Grid); its instance config
lives in `instances/kimberim/`.

> *Named for Koestler's holon — a whole that is itself part of a larger whole.*
> ⚠️ Note: the "Holon" name collides with an existing adjacent product/company
> (a multi-agent orchestration platform). Fine while local-only; revisit before
> any public launch. A rename is cheap pre-publication.

## Status

🟢 **Sprint 0 — Foundations & spike** (not yet started).

The full strategy, governance model, architecture, roles, protocol, and sprint
breakdown live in **[`./docs/ROADMAP.md`](./docs/ROADMAP.md)**. Read that first —
this repo implements it.

## What lives here (target)

| Area | Purpose |
|------|---------|
| `runtime/` (or `src/`) | The consent-cycle state machine + internal meta-agents (the **platform engine**) |
| `agents/` | Agent-role implementations (Orchestrator, Facilitator, Secretary, …) |
| `adapter/` | Uniform agent adapter — abstracts external providers (OpenAI/Anthropic/local) |
| `gateway/` | LLM gateway: keys, routing, cost caps, cache |
| `schema/` | JSON schemas for tension / proposal / objection / vote / decision |
| `store/` | Persistence to the immutable ledger + agent registry (Postgres) |
| `api/` | REST + SSE/WebSocket surface consumed by an instance's engage surface |
| `instances/` | **Per-venture config** (branding, taxonomy presets, decision backlog). `instances/kimberim/` is the first. |

## Multi-instance model

One Holon codebase, many instances. An instance is a config package — branding,
stakeholder-type & functional-domain presets, founder identity, initial decision
backlog, circles. The engine is generic; instances make it specific. Instance
isolation (shared DB + `instance_id` vs DB-per-instance) is an open Sprint 0
question.

## Sprint 0 open decisions

1. **Runtime** — Python + LangGraph vs Node.js + TypeScript (settled via spike).
2. **Agent identity model** — self-hosted endpoints vs proxied provider keys.
3. **Cost model** — who pays for external agents' LLM calls.
4. **First real decision** — which KIMBERIM decision question is the MVP test case.
5. **Instance isolation model** — shared DB + tenant boundary vs DB-per-instance.

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
