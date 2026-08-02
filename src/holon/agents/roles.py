"""The 5 MVP meta-agents (ROADMAP §5, roles 1-5).

Each is a thin `MetaAgent` subclass with a focused system prompt that encodes
its holacratic responsibility. The consent cycle (cycle/nodes.py) calls these
at the right steps.

Design note: prompts ask for JSON where structured output is needed, because
the gateway's default preamble already tells agents to 'respond as JSON when
asked'. The node functions parse and validate that JSON into schema models.
"""

from __future__ import annotations

from holon.agents.base import MetaAgent
from holon.schema import AgentRole


class Orchestrator(MetaAgent):
    """Role 1 — runs the meeting cycle; calls agents in order per round.

    In S1 the graph itself is the orchestrator; this agent is used for any
    meta-level reasoning about cycle progress (e.g. 'is this tension ready to
    become a proposal?').
    """

    role = AgentRole.ORCHESTRATOR
    system_prompt = (
        "You are the Orchestrator in Holon's holacratic consent cycle. "
        "You sequence the rounds and decide when the group is ready to advance. "
        "Be terse and procedural. When asked, respond as JSON."
    )


class Facilitator(MetaAgent):
    """Role 2 — enforces governance; validates proposal format; rules on
    whether an objection is valid (causes-harm / not-safe-to-try / regresses-role).
    """

    role = AgentRole.FACILITATOR
    system_prompt = (
        "You are the Facilitator in Holon's holacratic consent cycle. You enforce "
        "the governance rules. A proposal is valid if it has a clear change and a "
        "safe-to-try rationale. An objection is VALID only if the proposal causes "
        "harm, is not safe to try, or regresses a role. Be impartial. Respond as JSON."
    )


class Secretary(MetaAgent):
    """Role 3 — tallies votes and writes the immutable ledger / final Decision.

    The Secretary is the SINGLE writer of ledger events and decisions, giving a
    clean audit trail (ROADMAP §3 step 11). Its LLM call is only for composing
    human-readable summaries; the structured writes are deterministic.
    """

    role = AgentRole.SECRETARY
    system_prompt = (
        "You are the Secretary in Holon's holacratic consent cycle. You record "
        "decisions and tally votes precisely. You never editorialise. "
        "Respond as JSON with exact fields requested."
    )


class ProposalArchitect(MetaAgent):
    """Role 4 — drafts proposals from tensions into the standard format:
    context / change / expected-impact / safe-to-try rationale.
    """

    role = AgentRole.PROPOSAL_ARCHITECT
    system_prompt = (
        "You are the Proposal Architect in Holon's holacratic consent cycle. You "
        "convert tensions into proposals. A proposal has: title, context, change, "
        "expected_impact, and a safe_to_try_rationale explaining why it is reversible "
        "and regresses no role. Respond ONLY as a JSON object with those keys."
    )


class DevilsAdvocate(MetaAgent):
    """Role 5 — mandatory red-team. Hunts failure modes + objections for every
    proposal. ADR §Consequences: mandatory on every decision to keep the
    escalation path honest.
    """

    role = AgentRole.DEVILS_ADVOCATE
    system_prompt = (
        "You are the Devil's Advocate in Holon's holacratic consent cycle. Your job "
        "is to find the strongest objections and failure modes to any proposal — even "
        "ones you think will pass. Raise a valid objection only if the proposal causes "
        "harm, is not safe to try, or regresses a role. If you find none, say so plainly. "
        "Respond as JSON."
    )


class IntegrativeMediator(MetaAgent):
    """Role 6 — resolves objections by amending proposals until 'safe to try'
    (ROADMAP §2.4). Distinct from the Proposal Architect: the Architect drafts,
    the Mediator amends in light of objections while preserving safe-to-try.
    """

    role = AgentRole.INTEGRATIVE_MEDIATOR
    system_prompt = (
        "You are the Integrative Mediator in Holon's holacratic consent cycle. Given a "
        "proposal and one or more objections, amend the proposal to address each "
        "objection while keeping it safe to try (reversible, regresses no role). Preserve "
        "the proposal's intent; change only what the objections demand. Respond ONLY as a "
        "JSON object with keys: title, context, change, expected_impact, "
        "safe_to_try_rationale."
    )


class Founder(MetaAgent):
    """The instance's founder/principal — holds the veto (ROADMAP §2.3).

    Asked during the founder veto window whether to veto a consented proposal.
    A veto must carry a reason (which feeds the rework loop). S2's override is
    stubbed (proceed-after-cap); the reputation-weighted 75% override is S10.
    """

    role = AgentRole.FOUNDER
    system_prompt = (
        "You are the Founder in Holon's holacratic consent cycle. After the agents reach "
        "consent, you may veto a proposal — but only with a stated reason, which becomes "
        "a steer for rework. Default to proceeding unless the proposal genuinely conflicts "
        "with the venture's core intent. Respond as JSON with keys: veto (bool), reason."
    )


__all__ = [
    "DevilsAdvocate",
    "Facilitator",
    "Founder",
    "IntegrativeMediator",
    "Orchestrator",
    "ProposalArchitect",
    "Secretary",
]
