"""Uniform agent adapter — the federation layer (S7, ROADMAP §6).

A registered participant agent can run via one of two transports, both of which
conform to the `Agent` Protocol so the cycle never knows or cares which it's
talking to:

  - ProviderAdapter (platform-proxy): the platform holds the agent's provider
    key and calls OpenAI/Anthropic/Z.ai on its behalf. Activates the dormant
    model/endpoint/api_key_enc registry columns. Lower-friction on-ramp.
  - EndpointAdapter (self-hosted): the platform POSTs a prompt to the agent's
    HTTPS endpoint and waits. Genuine federation — the agent runs its own
    intelligence (a different model, RAG over private data, human-in-the-loop).

`make_adapter(row, ...)` is the factory: given an AgentRegistryRow, it picks
the transport from the row's fields (or an explicit `adapter` marker).
"""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING

from holon.gateway import LLMGateway, get_gateway
from holon.schema import AgentRef, AgentRole

if TYPE_CHECKING:
    from holon.store import AgentRegistryRow

log = logging.getLogger(__name__)

# Known LLM provider base-URL host fragments. An `endpoint` whose host matches
# one of these is treated as a provider base URL (platform-proxy), not a
# self-hosted agent endpoint.
_PROVIDER_HOST_FRAGMENTS = (
    "api.z.ai", "api.openai.com", "api.anthropic.com",
)

# S7.4 defaults: a generous per-agent cost cap (fraction of the platform cap)
# and rate limit so a single runaway external agent can't burn the budget or
# flood a provider. Override per-agent via make_adapter kwargs when needed.
_DEFAULT_PER_AGENT_CAP_FRACTION = 1.0  # share the platform cap by default
_DEFAULT_RATE_MAX_CALLS = 60
_DEFAULT_RATE_WINDOW_S = 60.0


class RateLimitExceeded(RuntimeError):
    """Raised when an agent exceeds its per-window call budget (S7.4)."""


class RateLimiter:
    """A tiny token-bucket rate limiter (S7.4, ROADMAP §6 'per-agent rate limits').

    max_calls per window_s; refill is continuous (not fixed-window). Thread-safe
    enough for the cycle's ThreadPoolExecutor fan-out (a lock guards the bucket).
    """

    def __init__(self, max_calls: int = _DEFAULT_RATE_MAX_CALLS,
                 window_s: float = _DEFAULT_RATE_WINDOW_S) -> None:
        self.max_calls = max_calls
        self.window_s = window_s
        self._tokens = float(max_calls)
        self._last = time.monotonic()
        import threading
        self._lock = threading.Lock()

    def acquire(self) -> None:
        with self._lock:
            now = time.monotonic()
            elapsed = now - self._last
            # Refill proportional to elapsed time.
            self._tokens = min(
                self.max_calls,
                self._tokens + elapsed * (self.max_calls / self.window_s),
            )
            self._last = now
            if self._tokens < 1.0:
                raise RateLimitExceeded(
                    f"agent rate limit exceeded: {self.max_calls} calls / "
                    f"{self.window_s}s"
                )
            self._tokens -= 1.0


class AgentAdapter:
    """Base for all participant adapters — conforms to the `Agent` Protocol.

    Holds an AgentRef + a system_prompt + an LLMGateway. `respond()` calls the
    gateway. Subclasses (ProviderAdapter, EndpointAdapter) supply the gateway.
    """

    role = AgentRole.PARTICIPANT

    def __init__(
        self, *, ref: AgentRef, system_prompt: str, gateway: LLMGateway | None = None,
        rate_limiter: RateLimiter | None = None,
    ) -> None:
        self.ref = ref
        self.system_prompt = system_prompt
        self.gateway = gateway or get_gateway()
        self.rate_limiter = rate_limiter

    def respond(self, prompt: str, context: str = "", **kwargs) -> str:
        if self.rate_limiter is not None:
            self.rate_limiter.acquire()
        full_prompt = f"{context}\n\n{prompt}" if context else prompt
        resp = self.gateway.call_agent(
            role=self.role, prompt=full_prompt,
            system=self.system_prompt, **kwargs,
        )
        return resp.text


class ProviderAdapter(AgentAdapter):
    """Platform-proxy transport: the agent runs on its own registered provider.

    Built from a registered agent's model/endpoint/api_key. The platform holds
    the key and calls the provider (OpenAI/Anthropic/Z.ai) on the agent's
    behalf via a per-agent LLMGateway (its own client + its own cost cap).
    """

    def __init__(
        self, *, ref: AgentRef, system_prompt: str, gateway: LLMGateway,
        rate_limiter: RateLimiter | None = None,
    ) -> None:
        super().__init__(ref=ref, system_prompt=system_prompt, gateway=gateway,
                         rate_limiter=rate_limiter)


def _detect_provider(model: str) -> str:
    """Infer the provider ("openai" | "anthropic") from a model id."""
    m = (model or "").lower()
    if m.startswith(("gpt-", "o1", "o3", "o4")):
        return "openai"
    # Everything else (glm-*, claude-*, or unknown) uses the Anthropic protocol
    # (Z.ai is Anthropic-compatible; native Anthropic speaks it natively).
    return "anthropic"


def _is_provider_endpoint(endpoint: str) -> bool:
    """True if an endpoint URL looks like a known LLM provider base URL."""
    ep = (endpoint or "").lower()
    return any(frag in ep for frag in _PROVIDER_HOST_FRAGMENTS)


def make_adapter(
    row: AgentRegistryRow,
    *,
    instance_id: str,
    weight: float = 1.0,
) -> AgentAdapter:
    """Build the right adapter for a registered agent (the federation factory).

    Transport selection:
      - explicit `adapter` field on the row ("endpoint" → EndpointAdapter)
      - elif model + api_key present → ProviderAdapter (platform-proxy)
      - else → platform-gateway fallback (current behaviour; back-compat)

    A bare agent (no model/endpoint/key, registered pre-S7) gets a plain
    AgentAdapter on the platform gateway — identical to the old _Registered.
    """
    from holon.agents.endpoint import EndpointAdapter  # lazy (S7.2)

    ref = AgentRef(
        instance_id=instance_id, role=AgentRole.PARTICIPANT,
        display_name=row.display_name or "participant", weight=weight,
    )
    capability = row.capability or "a general stakeholder interest"
    system_prompt = (
        "You are a participant in HOLACRON's holacratic consent cycle, "
        f"representing this stakeholder perspective: {capability}. "
        "State your honest position on each proposal. Be constructive. "
        'Respond as JSON: {"position": "consent"|"objection"|"abstain", ...}.'
    )

    adapter_kind = getattr(row, "adapter", None) or _auto_detect_kind(row)

    if adapter_kind == "endpoint" and row.endpoint:
        return EndpointAdapter(
            ref=ref, system_prompt=system_prompt, endpoint=row.endpoint,
        )

    if adapter_kind == "provider" and row.model and row.api_key_enc:
        provider = _detect_provider(row.model)
        base_url = row.endpoint if row.endpoint and _is_provider_endpoint(row.endpoint) else None
        # S7.4: per-agent cost cap = a fraction of the platform cap, so one
        # runaway external agent can't burn the whole budget.
        from holon.config import load_runtime_config
        platform_cap = load_runtime_config().harness_cost_cap_usd
        cap = platform_cap * _DEFAULT_PER_AGENT_CAP_FRACTION
        gw = LLMGateway.from_provider(
            provider, api_key=row.api_key_enc, base_url=base_url, model=row.model,
            cap_usd=cap,
        )
        return ProviderAdapter(
            ref=ref, system_prompt=system_prompt, gateway=gw,
            rate_limiter=RateLimiter(),
        )

    # Fallback: platform gateway (back-compat for pre-S7 / bare registrations).
    return AgentAdapter(ref=ref, system_prompt=system_prompt)


def _auto_detect_kind(row: AgentRegistryRow) -> str:
    """Auto-detect the transport from the registry fields when no explicit
    `adapter` marker is set."""
    # An endpoint that isn't a known provider base → self-hosted agent.
    if row.endpoint and not _is_provider_endpoint(row.endpoint) and not row.model:
        return "endpoint"
    if row.model and row.api_key_enc:
        return "provider"
    return "platform"


__all__ = [
    "AgentAdapter",
    "ProviderAdapter",
    "RateLimiter",
    "RateLimitExceeded",
    "make_adapter",
]
