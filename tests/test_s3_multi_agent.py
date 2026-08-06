"""Sprint 3 deterministic tests — multi-agent positions & synthesis (ROADMAP §8, S3).

Drives the multi-participant paths with StubAgents. All S0/S1/S2 tests remain
green (back-compat: empty participant_agents -> DA-only S2 behaviour).
"""

from __future__ import annotations

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


def _arch() -> StubAgent:
    return StubAgent(_PROP, role=AgentRole.PROPOSAL_ARCHITECT, display_name="arch")


def _participant(position: str, *, name: str, weight: float = 1.0) -> StubAgent:
    return StubAgent(
        f'{{"position": "{position}"}}',
        role=AgentRole.PARTICIPANT, display_name=name, weight=weight,
    )


def _da(json_str: str = '{"position": "consent"}') -> StubAgent:
    """DA stub. Pass a full JSON string (defaults to a consent position)."""
    return StubAgent(json_str, role=AgentRole.DEVILS_ADVOCATE, display_name="da")


def _run(arch, da, *, participants=None, mediator=None, summarizer=None,
         synthesizer=None, founder=None, gov=None):
    tension = Tension(instance_id=INSTANCE, raised_by=arch.ref, title="t", description="d")
    events: list[tuple[str, dict]] = []
    run = CycleRun(
        instance_id=INSTANCE, tension=tension, participants=[arch.ref],
        governance=gov or GovernanceConfig(),
        proposal_architect=arch, devils_advocate=da,
        participant_agents=participants or [],
        integrative_mediator=mediator, summarizer=summarizer,
        judgment_synthesizer=synthesizer, founder=founder,
        ledger_sink=lambda et, p: events.append((et, p)),
    )
    return run, events


# ── S3.3: multiple participants take positions ───────────────────────────────


def test_multiple_participants_take_positions():
    """4 participants + DA all state positions; a position-stated event per
    agent; outcome adopted."""
    run, events = _run(
        _arch(), _da(),
        participants=[
            _participant("consent", name="p1"),
            _participant("consent", name="p2"),
            _participant("abstain", name="p3"),
            _participant("consent", name="p4"),
        ],
    )
    final = run_cycle(run)
    assert final["outcome"] == "adopted"
    # 4 participants + the DA = 5 positions recorded.
    assert len(final["positions"]) == 5
    position_events = [et for et, _ in events if et == "position-stated"]
    assert len(position_events) == 5


# ── H3 regression: decision-recorded must carry a weighted tally ────────────


def test_decision_recorded_carries_weighted_tally():
    """H3 regression: the decision-recorded event's weighted_consent must be the
    reputation-weighted sum, not a head count.

    The pre-H3 record() used len([...]) — a count — for a field named
    weighted_consent. consent_test already computed the real weighted sum for
    its own consent-reached event; record() discarded it.

    With weights 2.0 / 1.0 / 0.5 all consenting (+ DA at 1.0), the correct
    weighted_consent is 4.5 — a head count would give 4.0.
    """
    run, events = _run(
        _arch(), _da(),
        participants=[
            _participant("consent", name="heavy", weight=2.0),
            _participant("consent", name="mid", weight=1.0),
            _participant("consent", name="light", weight=0.5),
        ],
    )
    final = run_cycle(run)
    assert final["outcome"] == "adopted"

    decision_ev = next(p for et, p in events if et == "decision-recorded")
    # 2.0 + 1.0 + 0.5 (participants) + 1.0 (DA) = 4.5 weighted consent.
    # A head-count bug would produce 4.0.
    assert decision_ev["weighted_consent"] == 4.5, (
        f"weighted_consent should be the weighted sum (4.5), not a head count; "
        f"got {decision_ev['weighted_consent']}"
    )


def test_decision_recorded_weighted_tally_matches_consent_reached():
    """H3: the decision-recorded tally must agree with consent-test's
    consent-reached tally for the same round (consistency invariant)."""
    run, events = _run(
        _arch(), _da(),
        participants=[
            _participant("consent", name="a", weight=2.0),
            _participant("consent", name="b", weight=1.5),
        ],
    )
    final = run_cycle(run)
    assert final["outcome"] == "adopted"

    consent_ev = next(p for et, p in events if et == "consent-reached")
    decision_ev = next(p for et, p in events if et == "decision-recorded")
    assert decision_ev["weighted_consent"] == consent_ev["weighted_consent"], (
        f"record() and consent_test disagree on weighted_consent: "
        f"{decision_ev['weighted_consent']} vs {consent_ev['weighted_consent']}"
    )


# ── S3.4: weighted tally in the decision ─────────────────────────────────────


def test_weighted_tally_in_decision():
    """Participants with varied weights: weighted_consent reflects the weighted
    sum, not just a head count."""
    run, events = _run(
        _arch(), _da(),
        participants=[
            _participant("consent", name="heavy", weight=3.0),
            _participant("consent", name="light", weight=1.0),
        ],
    )
    final = run_cycle(run)
    assert final["outcome"] == "adopted"
    consent_ev = next(p for et, p in events if et == "consent-reached")
    # heavy(3.0) + light(1.0) + DA(1.0) = 5.0 weighted consent.
    assert consent_ev["weighted_consent"] == 5.0


def test_abstain_counts_as_neither():
    """An abstainer (default abstain_counts_as='neither') inflates neither
    consent nor objection weight."""
    run, events = _run(
        _arch(), _da(),
        participants=[
            _participant("consent", name="c"),
            _participant("abstain", name="a"),
        ],
    )
    final = run_cycle(run)
    assert final["outcome"] == "adopted"
    consent_ev = next(p for et, p in events if et == "consent-reached")
    # Only the consenting participant (1.0) + DA (1.0) count; abstainer excluded.
    assert consent_ev["weighted_consent"] == 2.0


def test_abstain_counts_as_consent_when_configured():
    """With abstain_counts_as='consent', abstainers inflate the consent weight."""
    run, events = _run(
        _arch(), _da(),
        participants=[
            _participant("consent", name="c"),
            _participant("abstain", name="a"),
        ],
        gov=GovernanceConfig(abstain_counts_as="consent"),
    )
    final = run_cycle(run)
    assert final["outcome"] == "adopted"
    consent_ev = next(p for et, p in events if et == "consent-reached")
    # consent(1.0) + abstain-as-consent(1.0) + DA(1.0) = 3.0
    assert consent_ev["weighted_consent"] == 3.0


# ── S3.3/S3.5: summarizer + synthesizer ──────────────────────────────────────


def test_summarizer_compresses_positions():
    """With a Summarizer wired and >2 positions, a digest event is emitted."""
    summarizer = StubAgent(
        '{"consent_count": 3, "objection_count": 0, "abstain_count": 1, "themes": []}',
        role=AgentRole.SUMMARIZER, display_name="sum",
    )
    run, events = _run(
        _arch(), _da(),
        participants=[
            _participant("consent", name="p1"),
            _participant("consent", name="p2"),
            _participant("abstain", name="p3"),
        ],
        summarizer=summarizer,
    )
    final = run_cycle(run)
    assert final["outcome"] == "adopted"
    # 3 participants + DA = 4 positions (>2) -> Summarizer fires.
    assert any(et == "digest" for et, _ in events)
    assert final["digest"] is not None
    assert final["digest"]["consent_count"] == 3


def test_synthesizer_finds_core_disagreement():
    """With >1 objection and a Synthesizer wired, a core-disagreement event is
    emitted and feeds the Mediator."""
    arch = _arch()
    mediator = StubAgent(
        '{"title":"amended","context":"c","change":"ch2","expected_impact":"i",'
        '"safe_to_try_rationale":"s2"}',
        role=AgentRole.INTEGRATIVE_MEDIATOR, display_name="med",
    )
    synthesizer = StubAgent(
        '{"core_disagreement": "grid-revenue risk", "shared_by": []}',
        role=AgentRole.JUDGMENT_SYNTHESIZER, display_name="syn",
    )
    # Two objectors object once, then both consent after amendment.
    p1_q = ['{"position": "objection", "criterion": "not-safe-to-try", "reason": "revenue"}',
            '{"position": "consent"}']
    p2_q = ['{"position": "objection", "criterion": "regresses-role", "reason": "ops"}',
            '{"position": "consent"}']
    p1 = StubAgent(lambda _p, _c: p1_q.pop(0), role=AgentRole.PARTICIPANT, display_name="p1")
    p2 = StubAgent(lambda _p, _c: p2_q.pop(0), role=AgentRole.PARTICIPANT, display_name="p2")

    run, events = _run(
        arch, _da(), participants=[p1, p2],
        mediator=mediator, synthesizer=synthesizer,
    )
    final = run_cycle(run)
    assert final["outcome"] == "adopted"
    # The Synthesizer fired on the 2 objections.
    assert any(et == "core-disagreement" for et, _ in events)
    assert final["core_disagreement"] == "grid-revenue risk"


# ── S3 back-compat guard ──────────────────────────────────────────────────────


def test_backcompat_no_participant_agents():
    """Empty participant_agents -> S2 DA-only behaviour: the DA is the sole
    position/voter, no digest, outcome adopted on no-objection."""
    run, events = _run(_arch(), _da('{"objection": false}'))
    final = run_cycle(run)
    assert final["outcome"] == "adopted"
    # Only the DA stated a position (no participant agents).
    assert len(final["positions"]) == 1
    assert final["positions"][0]["display_name"] == "da"
    # No summarizer wired -> no digest.
    assert not any(et == "digest" for et, _ in events)
