"""Olon agents — staff meta-agents + the stub used for deterministic tests."""

from olon.agents.base import MetaAgent, StubAgent
from olon.agents.roles import (
    DevilsAdvocate,
    Facilitator,
    Founder,
    IntegrativeMediator,
    JudgmentSynthesizer,
    Orchestrator,
    ProposalArchitect,
    Secretary,
    Summarizer,
    TriageGuardian,
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
    "TriageGuardian",
]
