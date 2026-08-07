"""Runs a deliberation live, in a background thread, bridging events to SSE.

`run_deliberation_live()` builds a CycleRun whose ledger_sink persists to the
Postgres ledger AND pushes each event to the FeedBroker (for the SSE stream),
then calls run_cycle in a thread. On completion it closes the feed.

The MVP wires the platform's staff agents + any registered participant agents
(each an LLM-backed participant on the platform's Z.ai gateway). The registered
model/endpoint/key are captured but NOT yet used (S7 federation).
"""

from __future__ import annotations

import logging
import threading
from typing import Any
from uuid import UUID

from sqlmodel import Session as SMSession

from holon.agents import (
    DevilsAdvocate,
    Founder,
    IntegrativeMediator,
    JudgmentSynthesizer,
    MetaAgent,
    ProposalArchitect,
    Summarizer,
)
from holon.api.feed import FeedBroker
from holon.config import InstanceConfig, RuntimeConfig, load_instance_config, load_runtime_config
from holon.cycle import CycleRun, run_cycle
from holon.schema import AgentRole, AgentRef, Tension
from holon.store import (
    DecisionRow,
    ProposalRow,
    append_ledger_event,
    get_tension,
    list_agents,
    make_engine,
    mark_decided,
    mark_in_deliberation,
    next_tension,
)

log = logging.getLogger(__name__)


def _participant_agent(display_name: str, capability: str, instance_id: str) -> MetaAgent:
    """Build an LLM-backed participant agent from a registered agent's profile.

    For the MVP the participant runs on the platform's own Z.ai gateway; the
    registered model/endpoint/key are captured in the registry for S7.
    """

    class _Registered(MetaAgent):
        role = AgentRole.PARTICIPANT
        system_prompt = (
            f"You are a participant in HOLACRON's holacratic consent cycle, "
            f"representing this stakeholder perspective: {capability or 'a general '
            'stakeholder interest'}. State your honest position on each proposal. "
            "Be constructive. Respond as JSON: "
            '{"position": "consent"|"objection"|"abstain", ...}.'
        )

    return _Registered(instance_id=instance_id, display_name=display_name)


def run_deliberation_live(
    *,
    instance_id: str,
    run_id: UUID,
    broker: FeedBroker,
    config: RuntimeConfig | None = None,
    instance: InstanceConfig | None = None,
    tension_id: UUID | None = None,
) -> threading.Thread:
    """Start a deliberation in a background thread. Returns the thread (started).

    Tension source (S5 generalization):
      1. If tension_id is given, deliberate that specific backlog tension.
      2. Else if the backlog is non-empty, pop the next triaged/open tension.
      3. Else fall back to the instance's first_decision (S0-S4 back-compat —
         preserves every existing live test, which has no backlog).

    Each cycle event is persisted to Postgres AND pushed to the broker for the
    SSE stream. On completion (or error) the feed is closed.
    """
    config = config or load_runtime_config()
    instance = instance or load_instance_config(instance_id)

    def _worker() -> None:
        eng = make_engine(config.database_url)

        def sink(event_type: str, payload: dict[str, Any]) -> None:
            # 1. Persist to the immutable ledger.
            try:
                with SMSession(eng) as s:
                    append_ledger_event(
                        s, instance_id=instance_id, event_type=event_type, payload=payload
                    )
                    s.commit()
            except Exception as e:  # noqa: BLE001
                log.warning("ledger persist failed (non-fatal): %s", e)
            # 2. Push to the SSE broker.
            try:
                broker.push(run_id, event_type, payload)
            except Exception as e:  # noqa: BLE001
                log.warning("broker push failed (non-fatal): %s", e)
            # 3. Close the feed on the terminal event.
            if event_type == "decision-recorded":
                broker.close(run_id)

        # Staff agents (the platform's backbone).
        architect = ProposalArchitect(instance_id=instance_id)
        devils_advocate = DevilsAdvocate(instance_id=instance_id)
        mediator = IntegrativeMediator(instance_id=instance_id)
        summarizer = Summarizer(instance_id=instance_id)
        synthesizer = JudgmentSynthesizer(instance_id=instance_id)
        founder = Founder(instance_id=instance_id)

        # Registered participant agents (from the registry).
        with SMSession(eng) as s:
            registered = list_agents(s, instance_id=instance_id)
        participants = [
            _participant_agent(a.display_name, a.capability, instance_id)
            for a in registered
            if a.display_name
        ]

        # ── Resolve the tension to deliberate (S5 generalization) ──────────
        # Priority: explicit tension_id > next backlog tension > first_decision
        # fallback (back-compat for S0-S4, which have no backlog).
        tension = None
        with SMSession(eng) as s:
            trow = None
            if tension_id is not None:
                trow = get_tension(s, tension_id=tension_id)
            else:
                trow = next_tension(s, instance_id=instance_id)
            if trow is not None:
                # Build the Tension model from the backlog row + mark it active.
                tension = Tension(
                    id=trow.id,
                    instance_id=trow.instance_id,
                    raised_by=AgentRef(
                        agent_id=trow.raised_by, instance_id=trow.instance_id,
                        role=AgentRole.PARTICIPANT,
                    ),
                    title=trow.title,
                    description=trow.description,
                    status=trow.status,
                    priority=trow.priority,
                )
                mark_in_deliberation(s, tension_id=trow.id)
                s.commit()

        if tension is None:
            # Fallback: the instance's seeded first_decision (S0-S4 back-compat).
            if instance.first_decision is None:
                log.error("no backlog tension and no first_decision for %s", instance_id)
                broker.close(run_id)
                return
            tension = Tension(
                instance_id=instance_id,
                raised_by=architect.ref,
                title=instance.first_decision.title,
                description=instance.first_decision.summary.strip(),
            )

        # S5: close the loop on decision — persist a DecisionRow (+ the
        # ProposalRow it references, since record() emits only ledger events)
        # and mark the source tension 'decided'. This is what makes dedup real
        # and the backlog self-cleaning.
        def on_decision(payload: dict) -> None:
            try:
                with SMSession(eng) as s:
                    # The proposal row must exist for the decision FK. The
                    # proposal lives in the ledger as a proposal-drafted event,
                    # but the projection table may not have it — upsert a row.
                    prop_id = payload.get("proposal_id")
                    if prop_id is not None:
                        existing = s.get(ProposalRow, prop_id)
                        if existing is None:
                            s.add(ProposalRow(
                                id=prop_id, instance_id=instance_id,
                                tension_id=payload.get("tension_id") or tension.id,
                                drafted_by=architect.ref.agent_id,
                                title=tension.title,
                            ))
                            s.flush()
                    decision = DecisionRow(
                        instance_id=instance_id,
                        proposal_id=prop_id,
                        outcome=payload.get("outcome", "adopted"),
                        weighted_consent=payload.get("weighted_consent", 0.0),
                        weighted_objection=payload.get("weighted_objection", 0.0),
                        founder_vetoed=payload.get("founder_vetoed", False),
                        veto_overridden=payload.get("veto_overridden", False),
                    )
                    s.add(decision)
                    s.flush()
                    # Link the tension to its decision (closes dedup).
                    tid = payload.get("tension_id") or tension.id
                    if tid is not None:
                        mark_decided(s, tension_id=tid, decision_id=decision.id)
                    s.commit()
            except Exception as e:  # noqa: BLE001
                log.warning("on_decision persistence failed (non-fatal): %s", e)

        run = CycleRun(
            instance_id=instance_id,
            tension=tension,
            participants=[p.ref for p in participants] or [architect.ref],
            governance=instance.governance,
            proposal_architect=architect,
            devils_advocate=devils_advocate,
            integrative_mediator=mediator,
            summarizer=summarizer,
            judgment_synthesizer=synthesizer,
            founder=founder,
            participant_agents=participants,
            ledger_sink=sink,
            on_decision=on_decision,
        )
        try:
            final = run_cycle(run)
            log.info("deliberation %s finished: %s", run_id, final.get("outcome"))
        except Exception as e:  # noqa: BLE001
            log.exception("deliberation %s failed: %s", run_id, e)
            broker.close(run_id)

    thread = threading.Thread(target=_worker, name=f"holon-deliberation-{run_id}", daemon=True)
    thread.start()
    return thread


__all__ = ["run_deliberation_live"]
