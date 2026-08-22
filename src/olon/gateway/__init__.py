"""The LLM gateway — THE core primitive of the harness.

`call_agent()` is the single function the whole platform (and the bootstrap
runner) builds on. It routes to a provider (Z.ai/Anthropic-compatible or
OpenAI-compatible), tracks cost against the per-run cap (ROADMAP §2.5), caches
identical prompts, and satisfies the `Agent` Protocol so an LLM-backed role is
interchangeable with a stub.

S7 federation: the gateway is now multi-provider. A registered agent with its
own model/endpoint/key gets a per-agent gateway via `from_provider()`, so
external-model agents participate in a cycle on their own provider. The
platform default gateway (Z.ai) is unchanged.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from anthropic import (
    APIConnectionError,
    Anthropic,
    APITimeoutError,
    InternalServerError,
    OverloadedError,
    RateLimitError,
)
from openai import OpenAI as _OpenAIClient
from openai import (
    APIConnectionError as _OAConnectionError,
    APITimeoutError as _OATimeoutError,
    InternalServerError as _OAInternalServerError,
    RateLimitError as _OARateLimitError,
)
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from olon.config import RuntimeConfig

if TYPE_CHECKING:
    from olon.schema import AgentRole

log = logging.getLogger(__name__)

# Rough per-1M-token USD pricing for cost-cap accounting (ROADMAP §2.5).
# Provider list pricing is volatile; these are conservative estimates used
# ONLY to enforce HARNESS_COST_CAP_USD — not for billing. Tunable via env later.
_MODEL_PRICING_USD_PER_MTOK: dict[str, tuple[float, float]] = {
    # (input, output) per 1M tokens
    "glm-5-turbo": (0.50, 1.50),
    "glm-5.2": (1.00, 3.00),
    "gpt-4o": (2.50, 10.00),
    "gpt-4o-mini": (0.15, 0.60),
    "gpt-4.1": (2.00, 8.00),
    "gpt-4.1-mini": (0.40, 1.60),
    "claude-3-5-sonnet": (3.00, 15.00),
    "claude-3-5-haiku": (0.80, 4.00),
    # Fallback for unknown models: assume the cheapest known tier.
}
_FALLBACK_PRICING = (1.00, 3.00)
# Z.ai reports usage in tokens; the anthropic SDK exposes input/output token counts.

# H5: resilience — retry transient provider failures (429 rate-limit, 5xx /
# overload, connection + timeout) with exponential backoff. Non-retryable
# errors (auth, validation, bad request) propagate immediately. tenacity is a
# transitive dep via LangGraph, so this adds no new dependency.
#   3 attempts, waits 2s -> 4s -> 8s (capped). A single transient blip no longer
# aborts an entire deliberation.
_RETRYABLE = (
    RateLimitError,           # 429
    OverloadedError,          # provider overload (5xx-family)
    InternalServerError,      # 5xx
    APIConnectionError,       # network/DNS
    APITimeoutError,          # request timed out
)
_RETRYABLE_OPENAI = (
    _OARateLimitError,
    _OAInternalServerError,
    _OAConnectionError,
    _OATimeoutError,
)
_LLM_RETRY = retry(
    retry=retry_if_exception_type(_RETRYABLE),
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=8),
    reraise=True,
)
_LLM_RETRY_OPENAI = retry(
    retry=retry_if_exception_type(_RETRYABLE_OPENAI),
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=8),
    reraise=True,
)
# Per-request timeout (seconds). Belt-and-braces alongside APITimeoutError retry.
_LLM_TIMEOUT_S = 60


class CostCapExceeded(RuntimeError):
    """Raised when a run would exceed HARNESS_COST_CAP_USD (ROADMAP §2.5)."""


@dataclass
class _CacheEntry:
    text: str
    model: str


@dataclass
class AgentResponse:
    """The structured result of an agent call — satisfies introspection needs."""

    text: str
    model: str
    input_tokens: int
    output_tokens: int
    cost_usd: float
    cached: bool = False


@dataclass
class LLMGateway:
    """Stateful gateway: holds the client, a prompt cache, and running cost.

    A fresh gateway = a fresh run. The running cost is checked against the cap
    on every call so a runaway loop aborts before burning the budget (§2.5).

    S7 federation: `provider` selects the HTTP shape ("anthropic" | "openai");
    both Z.ai and Anthropic speak the Anthropic protocol, OpenAI speaks its own.
    A per-agent override of `model`/`api_key`/`base_url` routes a registered
    agent to its own provider via `from_provider()`.
    """

    config: RuntimeConfig
    provider: str = "anthropic"
    # Optional per-gateway overrides (set by from_provider; None = use config).
    _override_api_key: str | None = field(default=None, init=False)
    _override_base_url: str | None = field(default=None, init=False)
    _override_model: str | None = field(default=None, init=False)
    _override_cap_usd: float | None = field(default=None, init=False)
    _client: object | None = field(default=None, init=False)
    _cache: dict[str, _CacheEntry] = field(default_factory=dict, init=False)
    spent_usd: float = field(default=0.0, init=False)

    def __post_init__(self) -> None:
        provider = self.provider.lower()
        if provider not in ("anthropic", "openai"):
            raise ValueError(f"unknown provider: {self.provider!r}")
        self.provider = provider
        api_key = self._override_api_key or self._platform_api_key()
        if not api_key:
            raise RuntimeError(
                "No LLM provider API key configured. Set ZAI_API_KEY / "
                "OPENAI_API_KEY / ANTHROPIC_API_KEY, or pass api_key to from_provider()."
            )
        base_url = self._override_base_url or self._platform_base_url()
        if provider == "openai":
            self._client = _OpenAIClient(api_key=api_key, base_url=base_url)
        else:
            self._client = Anthropic(api_key=api_key, base_url=base_url)

    # ── per-agent factory (S7 federation) ─────────────────────────────────────

    @classmethod
    def from_provider(
        cls,
        provider: str,
        *,
        api_key: str,
        base_url: str | None = None,
        model: str | None = None,
        cap_usd: float | None = None,
        config: RuntimeConfig | None = None,
    ) -> LLMGateway:
        """Build a per-agent gateway pointed at an arbitrary provider+key.

        Reuses the platform's cost-cap/retry/cache machinery but routes to the
        agent's own provider client. This is the platform-proxy transport: the
        platform holds the agent's key and calls the provider on its behalf.
        """
        from olon.config import load_runtime_config
        cfg = config or load_runtime_config()
        # Build without __init__ so we can set overrides before __post_init__
        # builds the client (the platform config may have no keys of its own).
        gw = object.__new__(cls)
        gw.config = cfg
        gw.provider = provider
        gw._override_api_key = api_key
        gw._override_base_url = base_url
        gw._override_model = model
        gw._override_cap_usd = cap_usd
        gw._client = None
        gw._cache = {}
        gw.spent_usd = 0.0
        gw.__post_init__()
        return gw

    def _platform_api_key(self) -> str:
        if self.provider == "openai":
            return self.config.openai_api_key
        # anthropic: prefer Z.ai key, fall back to native Anthropic key.
        return self.config.zai_api_key or self.config.anthropic_api_key

    def _platform_base_url(self) -> str:
        if self.provider == "openai":
            return self.config.openai_base_url
        return self.config.zai_base_url

    # ── public API ────────────────────────────────────────────────────────────

    def call_agent(
        self,
        role: AgentRole | str,
        prompt: str,
        *,
        system: str = "",
        model: str | None = None,
        max_tokens: int = 1024,
        use_cache: bool = True,
        temperature: float = 0.3,
    ) -> AgentResponse:
        """Call the LLM as a given role, with cost-cap enforcement + caching.

        Args:
            role: the meta-agent role or a free-form participant label. Used in
                  the system preamble and logs.
            prompt: the user-turn content.
            system: optional system prompt (prepended to a role-based preamble).
            model: model id; defaults to the first/cheapest in HARNESS_MODELS.
            max_tokens: output cap.
            use_cache: if True, identical (model, prompt) returns cached text
                       with zero token cost.
            temperature: sampling temperature.

        Raises:
            CostCapExceeded: if this call would push the run over the cap.
        """
        role_str = role.value if hasattr(role, "value") else str(role)
        chosen = (model or self._override_model or self._default_model()).strip()
        full_system = system or self._role_preamble(role_str)

        cache_key = self._cache_key(chosen, full_system, prompt)
        if use_cache and cache_key in self._cache:
            entry = self._cache[cache_key]
            log.debug("gateway cache hit for role=%s model=%s", role_str, chosen)
            return AgentResponse(
                text=entry.text, model=entry.model,
                input_tokens=0, output_tokens=0, cost_usd=0.0, cached=True,
            )

        # Pre-flight cost check: estimate worst-case (full max_tokens) output.
        cap = self._override_cap_usd if self._override_cap_usd is not None else (
            self.config.harness_cost_cap_usd
        )
        est_cost = self._estimate_cost(chosen, len(prompt), max_tokens)
        if self.spent_usd + est_cost > cap:
            raise CostCapExceeded(
                f"Call would push run to ~${self.spent_usd + est_cost:.4f} "
                f"(cap ${cap:.2f}). Aborting per §2.5."
            )

        resp = self._call_llm(
            chosen, full_system, prompt, max_tokens, temperature
        )
        text, in_tok, out_tok = self._extract_response(resp)
        cost = self._cost(chosen, in_tok, out_tok)
        self.spent_usd += cost

        if use_cache:
            self._cache[cache_key] = _CacheEntry(text=text, model=chosen)

        log.info(
            "call_agent role=%s model=%s in=%d out=%d cost=$%.5f spent=$%.4f",
            role_str, chosen, in_tok, out_tok, cost, self.spent_usd,
        )
        return AgentResponse(
            text=text, model=chosen,
            input_tokens=in_tok, output_tokens=out_tok, cost_usd=cost, cached=False,
        )

    # ── LLM transport (H5: retry + timeout; S7: multi-provider) ───────────────

    def _call_llm(
        self, model: str, system: str, prompt: str,
        max_tokens: int, temperature: float,
    ):
        """Single retry-wrapped, timeout-bound call. Branches on provider:
        Anthropic (Z.ai) via messages.create, OpenAI via chat.completions.create.
        Retries transient failures with exp backoff (3 attempts, 2–8s).
        """
        if self.provider == "openai":
            return self._call_openai(model, system, prompt, max_tokens, temperature)
        return self._call_anthropic(model, system, prompt, max_tokens, temperature)

    def _call_anthropic(
        self, model: str, system: str, prompt: str,
        max_tokens: int, temperature: float,
    ):
        @_LLM_RETRY
        def _create():
            return self._client.messages.create(
                model=model,
                max_tokens=max_tokens,
                temperature=temperature,
                system=system,
                messages=[{"role": "user", "content": prompt}],
                timeout=_LLM_TIMEOUT_S,
            )
        return _create()

    def _call_openai(
        self, model: str, system: str, prompt: str,
        max_tokens: int, temperature: float,
    ):
        @_LLM_RETRY_OPENAI
        def _create():
            return self._client.chat.completions.create(
                model=model,
                max_tokens=max_tokens,
                temperature=temperature,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": prompt},
                ],
                timeout=_LLM_TIMEOUT_S,
            )
        return _create()

    @staticmethod
    def _extract_response(resp) -> tuple[str, int, int]:
        """Extract (text, input_tokens, output_tokens) from either provider's
        response shape. Anthropic: resp.content[].text + resp.usage.{in,out}.
        OpenAI: resp.choices[0].message.content + resp.usage.{prompt,completion}."""
        # OpenAI shape.
        choices = getattr(resp, "choices", None)
        if choices:
            text = (getattr(choices[0].message, "content", "") or "").strip()
            usage = getattr(resp, "usage", None)
            in_tok = getattr(usage, "prompt_tokens", 0) if usage else 0
            out_tok = getattr(usage, "completion_tokens", 0) if usage else 0
            return text, in_tok, out_tok
        # Anthropic shape.
        text = LLMGateway._extract_text(resp)
        in_tok = getattr(getattr(resp, "usage", None), "input_tokens", 0)
        out_tok = getattr(getattr(resp, "usage", None), "output_tokens", 0)
        return text, in_tok, out_tok

    # ── helpers ───────────────────────────────────────────────────────────────

    def _default_model(self) -> str:
        if self.config.harness_models:
            return self.config.harness_models[-1]  # cheapest tier = last in list
        return "glm-5-turbo"

    @staticmethod
    def _role_preamble(role: str) -> str:
        return (
            f"You are the '{role}' role in OLOCRON — a consent-governed, consent-governed "
            "collective of agents. Be precise and structured. Respond as JSON when "
            "asked. Do not roleplay other agents."
        )

    @staticmethod
    def _cache_key(model: str, system: str, prompt: str) -> str:
        h = hashlib.sha256()
        h.update(model.encode())
        h.update(b"\x1f")
        h.update(system.encode())
        h.update(b"\x1f")
        h.update(prompt.encode())
        return h.hexdigest()

    @staticmethod
    def _pricing(model: str) -> tuple[float, float]:
        return _MODEL_PRICING_USD_PER_MTOK.get(model.lower(), _FALLBACK_PRICING)

    def _estimate_cost(self, model: str, input_chars: int, max_out_tokens: int) -> float:
        in_p, out_p = self._pricing(model)
        in_tok = input_chars / 4  # rough char->token
        return (in_tok / 1_000_000) * in_p + (max_out_tokens / 1_000_000) * out_p

    def _cost(self, model: str, in_tok: int, out_tok: int) -> float:
        in_p, out_p = self._pricing(model)
        return (in_tok / 1_000_000) * in_p + (out_tok / 1_000_000) * out_p

    @staticmethod
    def _extract_text(resp) -> str:
        parts = getattr(resp, "content", []) or []
        texts = [getattr(b, "text", "") for b in parts if getattr(b, "type", "") == "text"]
        return "".join(texts).strip()


# ── Module-level convenience (for the runner/tests) ───────────────────────────

_default_gateway: LLMGateway | None = None


def get_gateway(config: RuntimeConfig | None = None) -> LLMGateway:
    """Return a process-wide default gateway, lazily built from .env."""
    global _default_gateway
    if _default_gateway is None or config is not None:
        from olon.config import load_runtime_config

        _default_gateway = LLMGateway(config or load_runtime_config())
    return _default_gateway


def call_agent(role, prompt: str, **kwargs) -> AgentResponse:
    """Module-level shortcut: get_gateway().call_agent(...)."""
    return get_gateway().call_agent(role, prompt, **kwargs)


__all__ = [
    "AgentResponse",
    "CostCapExceeded",
    "LLMGateway",
    "call_agent",
    "get_gateway",
]
