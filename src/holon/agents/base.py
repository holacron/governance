"""Base classes for HOLACRON agents.

Two flavours:
  - `MetaAgent`: an LLM-backed staff role. Wraps `call_agent()` with a
    role-specific system prompt. Used by the 5 MVP meta-agents (roles.py).
  - `StubAgent`: deterministic, no LLM. Conforms to the `Agent` Protocol so
    unit tests can drive the cycle without cost or network. Its responses are
    scripted via a callable or a fixed string.

Both satisfy the `Agent` Protocol (structural typing — no inheritance needed).
"""

from __future__ import annotations

from collections.abc import Callable

from holon.gateway import LLMGateway, get_gateway
from holon.schema import AgentRef, AgentRole


class MetaAgent:
    """LLM-backed base for staff meta-agents.

    Subclasses set `role` and a `system_prompt`; `respond()` calls the gateway.
    """

    role: AgentRole = AgentRole.PARTICIPANT
    system_prompt: str = ""

    def __init__(
        self,
        ref: AgentRef | None = None,
        *,
        instance_id: str = "holon",
        display_name: str | None = None,
        gateway: LLMGateway | None = None,
    ) -> None:
        self.gateway = gateway or get_gateway()
        if ref is None:
            ref = AgentRef(
                instance_id=instance_id,
                role=self.role,
                display_name=display_name or self.role.value,
            )
        # Ensure the ref's role matches the class (defensive).
        self.ref = ref.model_copy(update={"role": self.role}) if ref.role != self.role else ref

    def respond(self, prompt: str, context: str = "", **kwargs) -> str:
        """Call the LLM as this role, returning the response text.

        `context` is prepended to the prompt if given (a common pattern: pass
        the current proposal/tension as context).
        """
        full_prompt = f"{context}\n\n{prompt}" if context else prompt
        resp = self.gateway.call_agent(
            role=self.role,
            prompt=full_prompt,
            system=self.system_prompt,
            **kwargs,
        )
        return resp.text


class StubAgent:
    """Deterministic agent for tests — no LLM, no cost, no network.

    Conforms to `Agent`. `responder` is either a string (always returned) or a
    callable(prompt, context) -> str for scripted, prompt-aware responses.
    """

    def __init__(
        self,
        responder: str | Callable[[str, str], str],
        *,
        ref: AgentRef | None = None,
        instance_id: str = "holon",
        role: AgentRole = AgentRole.PARTICIPANT,
        display_name: str = "stub",
        weight: float = 1.0,
    ) -> None:
        self._responder = responder
        self.ref = ref or AgentRef(
            instance_id=instance_id,
            role=role,
            display_name=display_name,
            weight=weight,
        )

    def respond(self, prompt: str, context: str = "", **kwargs) -> str:
        if isinstance(self._responder, str):
            return self._responder
        return self._responder(prompt, context)


__all__ = ["MetaAgent", "StubAgent"]
