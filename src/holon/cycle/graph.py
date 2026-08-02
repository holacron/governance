"""The consent-cycle LangGraph StateGraph (ADR 0001).

Builds the FSM: one node per ConsentState, transitions via conditional edges
whose guard functions live in nodes.py (returning the G_* constants). Each node
is wrapped to close over its CycleRun, so the graph is pure data-in/data-out.

`build_consent_graph(run)` returns a compiled graph; `run_cycle(run)` is the
convenience entrypoint that invokes it from the seed state and returns the final
CycleState.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from langgraph.graph import END, START, StateGraph

from holon.cycle import nodes as N
from holon.cycle.state import CycleState

if TYPE_CHECKING:
    from langgraph.graph.state import CompiledStateGraph

    from holon.cycle.state import CycleRun


def build_consent_graph(run: CycleRun) -> CompiledStateGraph:
    """Compile the consent-cycle StateGraph closed over `run`.

    Nodes are thin wrappers that call the node fn with (state, run) and return
    the delta. Guards are the route_* functions, partially closed over `run`
    where they need it.
    """
    g: StateGraph = StateGraph(CycleState)

    # ── nodes (closed over run) ───────────────────────────────────────────────
    g.add_node("draft", lambda s: N.draft(s, run))
    g.add_node("object", lambda s: N.object_round(s, run))
    g.add_node("integrate", lambda s: N.integrate(s, run))
    g.add_node("consent_test", lambda s: N.consent_test(s, run))
    g.add_node("veto_window", lambda s: N.veto_window(s, run))
    g.add_node("record", lambda s: N.record(s, run))

    # ── edges ─────────────────────────────────────────────────────────────────
    # Entry: TENSION_RAISED -> draft the proposal.
    g.add_edge(START, "draft")
    # draft -> object (the questioning rounds CLARIFYING/REACTING/AMENDING are
    # collapsed into the architect's drafting for S1's single-decision MVP).
    g.add_edge("draft", "object")

    # object -> {integrate | consent_test} based on whether objections exist.
    g.add_conditional_edges(
        "object",
        N.route_after_objecting,
        {N.G_OBJECTIONS: "integrate", N.G_NO_OBJECTIONS: "consent_test"},
    )

    # integrate -> {escalate(record) | re-object} based on loop cap / outcome.
    g.add_conditional_edges(
        "integrate",
        N.route_after_integrating,
        {N.G_LOOP_CAP: "record", N.G_RETEST: "object"},
    )

    # consent_test -> {veto_window | integrate} based on whether consent reached.
    g.add_conditional_edges(
        "consent_test",
        N.route_after_consent_test,
        {N.G_CONSENT: "veto_window", N.G_NO_CONSENT: "integrate"},
    )

    # veto_window -> record (S1: founder never vetoes).
    g.add_edge("veto_window", "record")
    # record is terminal.
    g.add_edge("record", END)

    return g.compile()


def run_cycle(run: CycleRun) -> CycleState:
    """Build + invoke the graph from the run's seed state. Returns final state."""
    graph = build_consent_graph(run)
    final: dict[str, Any] = graph.invoke(run.seed)
    return final  # type: ignore[return-value]


__all__ = ["build_consent_graph", "run_cycle"]
