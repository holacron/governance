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


# ── S5: on_decision callback closes the backlog loop ────────────────────────


def test_on_decision_fired_with_tension_id():
    """record() fires the on_decision callback once with the finalized decision
    payload, including the source tension_id so the live path can link the
    Decision back to its Tension (closing the dedup loop)."""
    captured: list[dict] = []

    def on_decision(payload: dict) -> None:
        captured.append(payload)

    tension = Tension(
        instance_id=INSTANCE, raised_by=_arch(_PROP).ref, title="t", description="d",
    )
    run = CycleRun(
        instance_id=INSTANCE, tension=tension, participants=[tension.raised_by],
        governance=GovernanceConfig(),
        proposal_architect=_arch(_PROP), devils_advocate=_da('{"objection": false}'),
        ledger_sink=lambda _et, _p: None,
        on_decision=on_decision,
    )
    final = run_cycle(run)
    assert final["outcome"] == "adopted"

    # The callback fired exactly once with the decision payload.
    assert len(captured) == 1
    payload = captured[0]
    assert payload["outcome"] == "adopted"
    assert payload["tension_id"] == str(tension.id)
    assert payload["proposal_id"] is not None
    assert payload["weighted_consent"] >= 1.0


def test_on_decision_none_is_safe():
    """When on_decision is None (the default), record() must not crash — this
    is the unit-test / DB-free path."""
    run, _events = _run(_arch(_PROP), _da('{"objection": false}'))
    # on_decision defaults to None; cycle must complete normally.
    final = run_cycle(run)
    assert final["outcome"] == "adopted"


# ── S7.5: concurrent round execution with timeout → abstain ─────────────────


class _SlowAgent:
    """An agent that sleeps longer than the timeout, simulating a hung/slow
    external agent. Conforms to the Agent Protocol (has .ref + .respond)."""

    def __init__(self, delay: float, *, display_name: str = "slow"):
        from holon.schema import AgentRef
        self.ref = AgentRef(instance_id=INSTANCE, display_name=display_name, weight=1.0)
        self._delay = delay

    def respond(self, prompt: str, context: str = "", **kwargs) -> str:
        import time
        time.sleep(self._delay)
        return '{"position": "consent"}'


class _ErrorAgent:
    """An agent that raises, simulating a provider/endpoint failure."""

    def __init__(self, *, display_name: str = "error"):
        from holon.schema import AgentRef
        self.ref = AgentRef(instance_id=INSTANCE, display_name=display_name, weight=1.0)

    def respond(self, prompt: str, context: str = "", **kwargs) -> str:
        raise RuntimeError("provider is down")


def test_slow_agent_defaults_to_abstain_within_timeout():
    """An agent that exceeds agent_timeout_s defaults to abstain; the cycle
    doesn't stall waiting for it."""
    arch = _arch(_PROP)
    da = _da('{"objection": false}')
    slow = _SlowAgent(delay=5.0, display_name="slow-external")
    tension = Tension(instance_id=INSTANCE, raised_by=arch.ref, title="t", description="d")
    run = CycleRun(
        instance_id=INSTANCE, tension=tension, participants=[arch.ref],
        governance=GovernanceConfig(),
        proposal_architect=arch, devils_advocate=da,
        participant_agents=[slow], agent_timeout_s=0.5,
        ledger_sink=lambda et, _p: None,
    )
    final = run_cycle(run)
    # The slow agent abstained; the DA consented → adopted.
    assert final["outcome"] == "adopted"
    positions = final["positions"]
    slow_pos = next(p for p in positions if p["display_name"] == "slow-external")
    assert slow_pos["position"] == "abstain"


def test_error_agent_defaults_to_abstain():
    """An agent that raises (provider error) defaults to abstain, not crash."""
    arch = _arch(_PROP)
    da = _da('{"objection": false}')
    err = _ErrorAgent(display_name="broken")
    tension = Tension(instance_id=INSTANCE, raised_by=arch.ref, title="t", description="d")
    run = CycleRun(
        instance_id=INSTANCE, tension=tension, participants=[arch.ref],
        governance=GovernanceConfig(),
        proposal_architect=arch, devils_advocate=da,
        participant_agents=[err], agent_timeout_s=5.0,
        ledger_sink=lambda et, _p: None,
    )
    final = run_cycle(run)
    assert final["outcome"] == "adopted"
    positions = final["positions"]
    err_pos = next(p for p in positions if p["display_name"] == "broken")
    assert err_pos["position"] == "abstain"


def test_fast_agent_real_position_preserved():
    """A fast agent (responds within timeout) records its real position, not
    abstain — the timeout only catches the slow ones."""
    arch = _arch(_PROP)
    da = _da('{"objection": false}')
    fast = StubAgent('{"position": "objection", "criterion": "not-safe-to-try", "reason": "x"}',
                     display_name="fast-objector")
    tension = Tension(instance_id=INSTANCE, raised_by=arch.ref, title="t", description="d")
    run = CycleRun(
        instance_id=INSTANCE, tension=tension, participants=[arch.ref],
        governance=GovernanceConfig(),
        proposal_architect=arch, devils_advocate=da,
        participant_agents=[fast], agent_timeout_s=5.0,
        ledger_sink=lambda et, _p: None,
    )
    final = run_cycle(run)
    positions = final["positions"]
    fast_pos = next(p for p in positions if p["display_name"] == "fast-objector")
    assert fast_pos["position"] == "objection"
