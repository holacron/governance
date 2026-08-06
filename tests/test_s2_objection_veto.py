"""Sprint 2 deterministic tests — objection handling, integrative resolution,
and the founder veto window (ROADMAP §8, S2).

Drives the new S2 paths with StubAgents. All S1 tests in test_cycle_unit.py
must remain green too (back-compat).
"""

from __future__ import annotations

import pytest

from holon.agents import StubAgent
from holon.config import GovernanceConfig
from holon.cycle import CycleRun, run_cycle
from holon.schema import AgentRole, Tension

INSTANCE = "kimberim"

_PROP = (
    '{"title":"Cap compute at 30%","context":"1GW campus",'
    '"change":"max 30% to compute","expected_impact":"protects revenue",'
    '"safe_to_try_rationale":"reversible quarterly; no role regressed"}'
)
_AMENDED = (
    '{"title":"Cap compute at 30% w/ review","context":"1GW campus",'
    '"change":"max 30% to compute, quarterly review","expected_impact":"protects revenue",'
    '"safe_to_try_rationale":"reversible quarterly; explicit review gate; no role regressed"}'
)


def _arch(json_str: str | None = None) -> StubAgent:
    return StubAgent(json_str or _PROP, role=AgentRole.PROPOSAL_ARCHITECT, display_name="arch")


def _mediator(json_str: str | None = None) -> StubAgent:
    return StubAgent(
        json_str or _AMENDED, role=AgentRole.INTEGRATIVE_MEDIATOR, display_name="mediator"
    )


def _da(json_str: str | None = None) -> StubAgent:
    return StubAgent(
        json_str or '{"objection": false}',
        role=AgentRole.DEVILS_ADVOCATE, display_name="da",
    )


def _founder(json_str: str | None = None) -> StubAgent:
    return StubAgent(json_str or '{"veto": false}', role=AgentRole.FOUNDER, display_name="founder")


def _run(arch, da, *, mediator=None, founder=None, gov=None):
    tension = Tension(instance_id=INSTANCE, raised_by=arch.ref, title="t", description="d")
    events: list[tuple[str, dict]] = []
    run = CycleRun(
        instance_id=INSTANCE, tension=tension, participants=[arch.ref],
        governance=gov or GovernanceConfig(),
        proposal_architect=arch, devils_advocate=da,
        integrative_mediator=mediator, founder=founder,
        ledger_sink=lambda et, p: events.append((et, p)),
    )
    return run, events


# ── S2.3: Mediator + Objection.integrated ────────────────────────────────────


def test_mediator_amends_and_objection_marked_integrated():
    """The Integrative Mediator amends the proposal; objection-integrated events
    carry integrated=true; re-test consents -> adopted."""
    arch = _arch()
    mediator = _mediator()
    # DA objects once, then consents on the amended proposal.
    da_q = ['{"criterion":"not-safe-to-try","reason":"no review gate"}', '{"objection": false}']
    da = StubAgent(lambda _p, _c: da_q.pop(0), role=AgentRole.DEVILS_ADVOCATE)

    run, events = _run(arch, da, mediator=mediator)
    final = run_cycle(run)

    assert final["outcome"] == "adopted"
    # The Mediator (not the architect) produced the amended proposal.
    assert "review" in final["proposal"]["title"].lower()
    # objection-integrated events carry integrated=true (the schema field, now set).
    integrated_events = [p for et, p in events if et == "objection-integrated"]
    assert integrated_events, "expected at least one objection-integrated event"
    assert all(e.get("integrated") is True for e in integrated_events)


# ── S2.4: founder veto ───────────────────────────────────────────────────────


def test_founder_veto_triggers_rework():
    """A founder veto with reason emits founder-veto, increments veto_rounds,
    routes back to draft (rework), and the cycle eventually adopts."""
    arch = _arch()
    da = _da()
    fq = ['{"veto": true, "reason": "misaligned with grid-first intent"}', '{"veto": false}']
    founder = StubAgent(lambda _p, _c: fq.pop(0), role=AgentRole.FOUNDER)

    run, events = _run(arch, da, founder=founder)
    final = run_cycle(run)

    assert final["outcome"] == "adopted"
    assert final["veto_rounds"] == 1
    assert final["veto_overridden"] is False
    event_types = [et for et, _ in events]
    assert "founder-veto" in event_types
    # The veto caused a re-draft (two proposal-drafted events).
    assert event_types.count("proposal-drafted") == 2
    # The veto carried a reason in its payload.
    veto_payload = next(p for et, p in events if et == "founder-veto")
    assert "grid-first" in veto_payload["reason"]


def test_veto_round_cap_proceeds_anyway():
    """A founder that vetoes past veto_round_cap is overridden (stubbed override)
    and the proposal proceeds to adopted."""
    arch = _arch()
    da = _da()
    founder = StubAgent('{"veto": true, "reason": "never acceptable"}', role=AgentRole.FOUNDER)

    run, events = _run(
        arch, da, founder=founder, gov=GovernanceConfig(veto_round_cap=2),
    )
    final = run_cycle(run)

    assert final["outcome"] == "adopted"
    assert final["veto_overridden"] is True
    assert final["veto_rounds"] == 2
    event_types = [et for et, _ in events]
    assert "founder-veto" in event_types
    assert "veto-override" in event_types


# ── H4 regression: veto without a reason still vetoes (surfaced, not dropped) ──


def test_veto_without_reason_still_vetoes():
    """H4 regression: a founder returning {"veto": true} with no reason must
    STILL count as a veto (the founder's stated intent), with the missing reason
    surfaced as "(no reason provided)" and a reason_missing=True flag.

    Pre-H4 the AND of (veto) and (reason) silently treated this as no-veto,
    overriding the founder's intent invisibly. Governance requires a reason to
    be GIVEN; the correct response to a missing one is to surface it in the
    ledger, not to silently drop the veto.
    """
    arch = _arch()
    da = _da()
    # Founder vetoes once WITHOUT a reason, then doesn't veto on rework.
    fq = ['{"veto": true}', '{"veto": false}']
    founder = StubAgent(lambda _p, _c: fq.pop(0), role=AgentRole.FOUNDER)

    run, events = _run(arch, da, founder=founder)
    final = run_cycle(run)

    # The veto was honoured — a rework happened.
    assert final["outcome"] == "adopted"
    assert final["veto_rounds"] == 1
    event_types = [et for et, _ in events]
    assert "founder-veto" in event_types
    # Two drafts confirm the veto forced a rework (not silently dropped).
    assert event_types.count("proposal-drafted") == 2

    veto_payload = next(p for et, p in events if et == "founder-veto")
    # The missing reason is surfaced, not swallowed.
    assert veto_payload["reason"] == "(no reason provided)"
    assert veto_payload["reason_missing"] is True


def test_veto_with_empty_reason_still_vetoes():
    """H4: an explicit empty-string reason behaves the same as a missing one."""
    arch = _arch()
    da = _da()
    fq = ['{"veto": true, "reason": "   "}', '{"veto": false}']
    founder = StubAgent(lambda _p, _c: fq.pop(0), role=AgentRole.FOUNDER)

    run, events = _run(arch, da, founder=founder)
    final = run_cycle(run)

    assert final["outcome"] == "adopted"
    assert final["veto_rounds"] == 1
    veto_payload = next(p for et, p in events if et == "founder-veto")
    assert veto_payload["reason"] == "(no reason provided)"
    assert veto_payload["reason_missing"] is True


# ── S2.5: consent_test objection_id wiring ───────────────────────────────────


def test_objection_integrated_links_objection_id():
    """Objection linkage is sound end-to-end: each objection carries an id, and
    objection-integrated events reference that id (ROADMAP §2.4, §2.2 — objection
    and integration are tracked as distinct, linkable ledger events).

    Note on the consent_test objection-vote branch: in the S2 single-objector
    graph, an objection routes object->integrate (not through consent_test), so
    the objection-vote branch of consent_test is currently unreachable; the
    authoritative objection linkage is objection-raised -> objection-integrated.
    S3 (multiple objectors + weighted tally) re-routes consent_test and makes
    the objection vote live.
    """
    arch = _arch()
    mediator = _mediator()
    da_q = ['{"criterion":"not-safe-to-try","reason":"risky"}', '{"objection": false}']
    da = StubAgent(lambda _p, _c: da_q.pop(0), role=AgentRole.DEVILS_ADVOCATE)

    run, events = _run(arch, da, mediator=mediator, gov=GovernanceConfig(integration_loop_cap=3))
    final = run_cycle(run)

    raised = [p for et, p in events if et == "objection-raised"]
    integrated = [p for et, p in events if et == "objection-integrated"]
    assert raised, "expected an objection-raised event"
    assert integrated, "expected an objection-integrated event"
    # Every raised objection has an id.
    raised_ids = {ob["id"] for ob in raised}
    assert all(rid is not None for rid in raised_ids)
    # Each integrated event references a raised objection's id.
    for ev in integrated:
        assert ev["objection_id"] in raised_ids, (
            "objection-integrated must reference a raised objection id"
        )
        assert ev.get("integrated") is True
    assert final["outcome"] == "adopted"


# ── S2 exit criterion: seeded controversy converges or escalates ─────────────


@pytest.mark.parametrize("da_json,loop_cap,expected", [
    # Converges: DA objects once, mediator amends, re-test consents.
    ('{"criterion":"not-safe-to-try","reason":"x"}', 3, "adopted"),
    # Escalates: DA never concedes; loop cap hit.
    ('{"criterion":"not-safe-to-try","reason":"never safe"}', 1, "escalated"),
])
def test_seeded_controversy_converges_or_escalates(da_json, loop_cap, expected):
    """S2 exit criterion: a seeded-controversial proposal converges to consent
    OR escalates — never deadlocks."""
    arch = _arch()
    mediator = _mediator()
    # DA: objects on first round; on re-test either consents (converge case) or
    # keeps objecting (escalate case). For the converge case the mediator's
    # amendment must satisfy it — modelled by DA consenting after one object.
    if expected == "adopted":
        da_q = [da_json, '{"objection": false}']
        da = StubAgent(lambda _p, _c: da_q.pop(0), role=AgentRole.DEVILS_ADVOCATE)
    else:
        da = StubAgent(da_json, role=AgentRole.DEVILS_ADVOCATE)

    run, _events = _run(
        arch, da, mediator=mediator, gov=GovernanceConfig(integration_loop_cap=loop_cap),
    )
    final = run_cycle(run)
    assert final["outcome"] == expected


# ── H2 regression: veto rework must reset integration_rounds ────────────────


def test_veto_rework_resets_integration_rounds():
    """H2 regression: when a veto forces a re-draft, the new proposal must start
    with integration_rounds=0, not inherit the prior proposal's depleted counter.

    Reproduces the original bug: the prior proposal runs through N integration
    rounds; then a veto reworks it; LangGraph merges state so the reworked
    proposal would inherit rounds=N. With a tight integration_loop_cap the
    reworked proposal could instantly hit the cap and escalate — even though the
    founder's objection was about the OLD proposal, not the reworked one.

    Scenario: objection → 1 integration round → consent → founder veto → rework
    → objection → integration round → consent → adopted. The reworked proposal
    must be allowed its full budget; here it reaches adopted rather than
    escalating on the carried-over counter.
    """
    arch = _arch()
    mediator = _mediator()
    # DA objects on the first proposal, consents on the amended version.
    da_q = [
        '{"criterion":"not-safe-to-try","reason":"r1"}',   # object on proposal v1
        '{"objection": false}',                              # consent on v1-amended
        '{"criterion":"not-safe-to-try","reason":"r2"}',   # object on reworked v2
        '{"objection": false}',                              # consent on v2-amended
    ]
    da = StubAgent(lambda _p, _c: da_q.pop(0), role=AgentRole.DEVILS_ADVOCATE)
    # Founder: consents on v1-amended AFTER the cycle consents, then on v2.
    fq = ['{"veto": true, "reason": "rework it"}', '{"veto": false}']
    founder = StubAgent(lambda _p, _c: fq.pop(0), role=AgentRole.FOUNDER)

    # cap = 1 so the bug bites: v1's amendment sets integration_rounds=1;
    # if v2 inherits it, v2's first integrate computes 1+1=2 > 1 → spurious
    # escalation. With the fix, v2 resets to 0 and reaches adopted.
    run, events = _run(
        arch, da, mediator=mediator, founder=founder,
        gov=GovernanceConfig(integration_loop_cap=1),
    )
    final = run_cycle(run)

    # The reworked proposal (v2) must reach adopted, not escalate.
    assert final["outcome"] == "adopted", (
        f"reworked proposal inherited stale integration_rounds and misrouted; "
        f"got {final['outcome']}"
    )
    assert final["veto_rounds"] == 1
    # Two proposal drafts: v1 (then vetoed) and v2 (rework).
    assert sum(1 for et, _ in events if et == "proposal-drafted") == 2
