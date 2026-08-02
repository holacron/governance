"""Holon agents — staff meta-agents + the stub used for deterministic tests."""

from holon.agents.base import MetaAgent, StubAgent
from holon.agents.roles import (
    DevilsAdvocate,
    Facilitator,
    Founder,
    IntegrativeMediator,
    JudgmentSynthesizer,
    Orchestrator,
    ProposalArchitect,
    Secretary,
    Summarizer,
)

__all__ = [
    "DevilsAdvocate",
    "Facilitator",
    "Founder",
    "IntegrativeMediator",
    "JudgmentSynthesizer",
    "MetaAgent",
    "Orchestrator",
    "ProposalArchitect",
    "Secretary",
    "StubAgent",
    "Summarizer",
]
