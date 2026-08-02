"""Deterministic consent-cycle tests (no LLM, no DB).

Drives every transition with StubAgents so the FSM mechanics are proven without
cost or network. The live acceptance test (test_cycle_live.py) exercises the
real LLM + Postgres path.

These are the per-transition unit tests the S1 plan requires.
"""

from __future__ import annotations

from holon.agents import StubAgent
from holon.config import GovernanceConfig
from holon.cycle import CycleRun, run_cycle
from holon.schema import AgentRole, Tension

INSTANCE = "kimberim"


def _arch(json_str: str) -> StubAgent:
    return StubAgent(json_str, role=AgentRole.PROPOSAL_ARCHITECT, display_name="stub-architect")


def _da(json_str: str) -> StubAgent:
    return StubAgent(json_str, role=AgentRole.DEVILS_ADVOCATE, display_name="stub-da")


def _run(arch: StubAgent, da: StubAgent, *, gov: GovernanceConfig | None = None):
    tension = Tension(
        instance_id=INSTANCE, raised_by=arch.ref, title="t", description="d"
    )
    events: list[str] = []
    run = CycleRun(
        instance_id=INSTANCE,
        tension=tension,
        participants=[arch.ref],
        governance=gov or GovernanceConfig(),
        proposal_architect=arch,
        devils_advocate=da,
        ledger_sink=lambda et, _p: events.append(et),
    )
    return run, events


_PROP = (
    '{"title":"Cap compute at 30%","context":"1GW campus",'
    '"change":"max 30% to compute","expected_impact":"protects revenue",'
    '"safe_to_try_rationale":"reversible quarterly; no role regressed"}'
)


def test_tension_to_proposal_to_adoption():
    """Happy path: no objection -> consent -> adopted (ROADMAP S1 exit criterion,
    deterministic version)."""
    run, events = _run(_arch(_PROP), _da('{"objection": false}'))
    final = run_cycle(run)
    assert final["outcome"] == "adopted"
    assert final["state"] == "adopted"
    # The proposal was carried through to the decision.
    assert final["proposal"]["title"] == "Cap compute at 30%"
    # Ledger sequence (S3 enriched): drafted -> position -> vote -> consent -> decision.
    assert events[0] == "proposal-drafted"
    assert "consent-reached" in events
    assert events[-1] == "decision-recorded"


def test_objection_then_integration_then_adoption():
    """OBJECTING -> INTEGRATING -> OBJECTING (re-test) -> CONSENT_TEST -> adopted."""
    # NOTE: the response lists are defined ONCE outside the lambda so .pop(0)
    # advances across calls (a literal inside the lambda would reset each call).
    arch_q = [
        _PROP,
        '{"title":"v2-amended","context":"1GW","change":"fixed",'
        '"expected_impact":"safe","safe_to_try_rationale":"safer"}',
    ]
    da_q = [
        '{"criterion":"not-safe-to-try","reason":"risky"}',
        '{"objection": false}',
    ]
    arch = StubAgent(lambda _p, _c: arch_q.pop(0), role=AgentRole.PROPOSAL_ARCHITECT)
    da = StubAgent(lambda _p, _c: da_q.pop(0), role=AgentRole.DEVILS_ADVOCATE)
    run, events = _run(arch, da)
    final = run_cycle(run)
    assert final["outcome"] == "adopted"
    assert final["integration_rounds"] >= 1
    # Objection was raised, integrated (amended), then consent reached.
    assert "objection-raised" in events
    assert "objection-integrated" in events
    assert "amendment" in events
    assert events[-1] == "decision-recorded"


def test_loop_cap_escalation():
    """Persistent objection past integration_loop_cap -> escalated (ADR §2.5)."""
    run, events = _run(
        _arch(_PROP),
        _da('{"criterion":"not-safe-to-try","reason":"never safe"}'),
        gov=GovernanceConfig(integration_loop_cap=2),
    )
    final = run_cycle(run)
    assert final["outcome"] == "escalated"
    assert final["state"] == "escalated"
    assert "escalation" in events
    assert events[-1] == "decision-recorded"


def test_decision_carries_weighted_tally():
    """The recorded decision carries a weighted consent/objection tally."""
    run, _events = _run(_arch(_PROP), _da('{"objection": false}'))
    final = run_cycle(run)
    # One consent vote, zero objection votes.
    assert final["votes"][0]["kind"] == "consent"


def test_ledger_events_are_consistent_with_path():
    """Every cycle path emits decision-recorded as its terminal event and
    proposal-drafted as its first — the invariant the Secretary guarantees."""
    for da_json, expected_outcome in [
        ('{"objection": false}', "adopted"),
        ('{"criterion":"not-safe-to-try","reason":"x"}', "escalated"),
    ]:
        run, events = _run(_arch(_PROP), _da(da_json), gov=GovernanceConfig(integration_loop_cap=1))
        final = run_cycle(run)
        assert final["outcome"] == expected_outcome
        assert events[0] == "proposal-drafted"
        assert events[-1] == "decision-recorded"


def test_governance_defaults_match_adr():
    """GovernanceConfig defaults match ADR 0001's proposed values."""
    g = GovernanceConfig()
    assert g.integration_loop_cap == 3
    assert g.veto_window_h == 24.0
    assert g.veto_round_cap == 3
    assert g.override_threshold == 0.75
    assert g.abstain_counts_as == "neither"
