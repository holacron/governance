"""The LLM gateway — THE core primitive of the harness.

`call_agent()` is the single function the whole platform (and the bootstrap
runner) builds on. It routes to Z.ai (Anthropic-compatible), tracks cost against
the per-run cap (ROADMAP §2.5), caches identical prompts, and satisfies the
`Agent` Protocol so an LLM-backed role is interchangeable with a stub.

Sprint 5/7 extend this to multi-provider routing + federation; today it targets
the one verified provider (Z.ai / GLM).
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from anthropic import Anthropic

from holon.config import RuntimeConfig

if TYPE_CHECKING:
    from holon.schema import AgentRole

log = logging.getLogger(__name__)

# Rough per-1M-token USD pricing for cost-cap accounting (ROADMAP §2.5).
# Z.ai / GLM list pricing is volatile; these are conservative estimates used
# ONLY to enforce HARNESS_COST_CAP_USD — not for billing. Tunable via env later.
_MODEL_PRICING_USD_PER_MTOK: dict[str, tuple[float, float]] = {
    # (input, output) per 1M tokens
    "glm-5-turbo": (0.50, 1.50),
    "glm-5.2": (1.00, 3.00),
    # Fallback for unknown models: assume the cheapest known tier.
}
_FALLBACK_PRICING = (1.00, 3.00)
# Z.ai reports usage in tokens; the anthropic SDK exposes input/output token counts.


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
    """

    config: RuntimeConfig
    _client: Anthropic | None = field(default=None, init=False)
    _cache: dict[str, _CacheEntry] = field(default_factory=dict, init=False)
    spent_usd: float = field(default=0.0, init=False)

    def __post_init__(self) -> None:
        if not self.config.has_provider:
            raise RuntimeError(
                "No LLM provider configured. Set ZAI_API_KEY (or OPENAI/ANTHROPIC) in .env."
            )
        # Z.ai is Anthropic-compatible; point the SDK at its base URL.
        self._client = Anthropic(
            api_key=self.config.zai_api_key,
            base_url=self.config.zai_base_url,
        )

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
        chosen = (model or self._default_model()).strip()
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
        est_cost = self._estimate_cost(chosen, len(prompt), max_tokens)
        if self.spent_usd + est_cost > self.config.harness_cost_cap_usd:
            raise CostCapExceeded(
                f"Call would push run to ~${self.spent_usd + est_cost:.4f} "
                f"(cap ${self.config.harness_cost_cap_usd:.2f}). Aborting per §2.5."
            )

        resp = self._client.messages.create(
            model=chosen,
            max_tokens=max_tokens,
            temperature=temperature,
            system=full_system,
            messages=[{"role": "user", "content": prompt}],
        )
        text = self._extract_text(resp)
        in_tok = getattr(resp.usage, "input_tokens", 0)
        out_tok = getattr(resp.usage, "output_tokens", 0)
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

    # ── helpers ───────────────────────────────────────────────────────────────

    def _default_model(self) -> str:
        if self.config.harness_models:
            return self.config.harness_models[-1]  # cheapest tier = last in list
        return "glm-5-turbo"

    @staticmethod
    def _role_preamble(role: str) -> str:
        return (
            f"You are the '{role}' role in HOLACRON — a holacratic, consent-governed "
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
        from holon.config import load_runtime_config

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
