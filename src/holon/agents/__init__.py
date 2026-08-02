"""Holon agents — staff meta-agents + the stub used for deterministic tests."""

from holon.agents.base import MetaAgent, StubAgent
from holon.agents.roles import (
    DevilsAdvocate,
    Facilitator,
    Founder,
    IntegrativeMediator,
    Orchestrator,
    ProposalArchitect,
    Secretary,
)

__all__ = [
    "DevilsAdvocate",
    "Facilitator",
    "Founder",
    "IntegrativeMediator",
    "MetaAgent",
    "Orchestrator",
    "ProposalArchitect",
    "Secretary",
    "StubAgent",
]
