"""Holon agents — staff meta-agents + the stub used for deterministic tests."""

from holon.agents.base import MetaAgent, StubAgent
from holon.agents.roles import (
    DevilsAdvocate,
    Facilitator,
    Orchestrator,
    ProposalArchitect,
    Secretary,
)

__all__ = [
    "DevilsAdvocate",
    "Facilitator",
    "MetaAgent",
    "Orchestrator",
    "ProposalArchitect",
    "Secretary",
    "StubAgent",
]
