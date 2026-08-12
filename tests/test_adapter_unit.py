"""S7 adapter unit tests — the federation factory + transport selection.

Pure factory/selection tests with mocked gateways (no network, no LLM). Verifies
make_adapter picks the right transport (provider/endpoint/platform) from the
registry fields, and that ProviderAdapter builds a per-agent gateway from the
captured model/endpoint/api_key columns.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from holon.agents.adapter import (
    AgentAdapter,
    ProviderAdapter,
    _auto_detect_kind,
    _detect_provider,
    _is_provider_endpoint,
    make_adapter,
)
from holon.gateway import LLMGateway
from holon.schema import AgentRef, AgentRole


def _row(**kw):
    """A minimal AgentRegistryRow stand-in for factory tests."""
    defaults = dict(
        agent_id="00000000-0000-0000-0000-000000000001",
        instance_id="kimberim", role="participant",
        display_name="Test Agent", owner="", capability="grid stability",
        weight=1.0, model="", endpoint="", api_key_enc="",
        stakeholder_type=None, functional_domain=None, permissions=None,
        adapter=None,
    )
    defaults.update(kw)
    return SimpleNamespace(**defaults)


# ── provider detection helpers ───────────────────────────────────────────────


def test_detect_provider_openai_models():
    assert _detect_provider("gpt-4o") == "openai"
    assert _detect_provider("gpt-4o-mini") == "openai"
    assert _detect_provider("o3-mini") == "openai"


def test_detect_provider_anthropic_for_glm_and_claude():
    assert _detect_provider("glm-5-turbo") == "anthropic"
    assert _detect_provider("claude-3-5-sonnet") == "anthropic"
    assert _detect_provider("") == "anthropic"  # unknown → anthropic protocol


def test_is_provider_endpoint_recognises_known_hosts():
    assert _is_provider_endpoint("https://api.openai.com/v1")
    assert _is_provider_endpoint("https://api.z.ai/api/anthropic")
    assert _is_provider_endpoint("https://api.anthropic.com")
    assert not _is_provider_endpoint("https://agents.example.com/infer")


# ── auto-detect kind ─────────────────────────────────────────────────────────


def test_auto_detect_provider_when_model_and_key():
    row = _row(model="gpt-4o", api_key_enc="sk-test")
    assert _auto_detect_kind(row) == "provider"


def test_auto_detect_endpoint_when_non_provider_url_and_no_model():
    row = _row(endpoint="https://agents.example.com/infer")
    assert _auto_detect_kind(row) == "endpoint"


def test_auto_detect_platform_when_bare():
    row = _row()
    assert _auto_detect_kind(row) == "platform"


# ── make_adapter factory ─────────────────────────────────────────────────────


def test_make_adapter_platform_fallback_for_bare_agent():
    """A bare agent (no model/endpoint/key) → plain AgentAdapter on the
    platform gateway (back-compat with pre-S7 registrations)."""
    row = _row()
    adapter = make_adapter(row, instance_id="kimberim")
    assert isinstance(adapter, AgentAdapter)
    assert not isinstance(adapter, ProviderAdapter)
    assert adapter.ref.display_name == "Test Agent"
    assert adapter.ref.weight == 1.0
    # The system prompt carries the capability.
    assert "grid stability" in adapter.system_prompt


def test_make_adapter_provider_for_model_and_key():
    """An agent with model + api_key → ProviderAdapter with a per-agent gateway
    routed to the detected provider."""
    row = _row(model="gpt-4o", api_key_enc="sk-test")
    adapter = make_adapter(row, instance_id="kimberim", weight=2.0)
    assert isinstance(adapter, ProviderAdapter)
    assert adapter.gateway.provider == "openai"
    assert adapter.gateway._override_model == "gpt-4o"
    assert adapter.ref.weight == 2.0


def test_make_adapter_provider_anthropic_for_claude():
    row = _row(model="claude-3-5-sonnet", api_key_enc="sk-ant-test")
    adapter = make_adapter(row, instance_id="kimberim")
    assert isinstance(adapter, ProviderAdapter)
    assert adapter.gateway.provider == "anthropic"


def test_make_adapter_respects_explicit_endpoint_marker():
    """An explicit adapter='endpoint' marker forces the self-hosted transport
    even if a model is present."""
    row = _row(
        adapter="endpoint", endpoint="https://agents.example.com/infer",
        model="ignored",
    )
    adapter = make_adapter(row, instance_id="kimberim")
    # EndpointAdapter is imported lazily; check by class name to avoid the
    # circular import at module load.
    assert type(adapter).__name__ == "EndpointAdapter"
    assert adapter.endpoint == "https://agents.example.com/infer"


def test_make_adapter_respects_explicit_provider_marker():
    """An explicit adapter='provider' marker forces platform-proxy even when
    auto-detect would have picked endpoint."""
    row = _row(
        adapter="provider", model="gpt-4o", api_key_enc="sk-test",
        endpoint="https://agents.example.com/infer",
    )
    adapter = make_adapter(row, instance_id="kimberim")
    assert isinstance(adapter, ProviderAdapter)


# ── ProviderAdapter calls its per-agent gateway ──────────────────────────────


def test_provider_adapter_respond_uses_its_own_gateway():
    """A ProviderAdapter's respond() routes through its per-agent gateway, not
    the platform singleton."""
    mock_gw = MagicMock(spec=LLMGateway)
    mock_gw.call_agent.return_value = SimpleNamespace(
        text='{"position": "consent"}', model="gpt-4o",
        input_tokens=10, output_tokens=5, cost_usd=0.001, cached=False,
    )
    ref = AgentRef(instance_id="kimberim", role=AgentRole.PARTICIPANT,
                   display_name="OA Agent", weight=1.0)
    adapter = ProviderAdapter(ref=ref, system_prompt="you are a voter", gateway=mock_gw)
    text = adapter.respond("vote on this", max_tokens=100)
    assert text == '{"position": "consent"}'
    mock_gw.call_agent.assert_called_once()
    # The call used the adapter's gateway, with the adapter's system prompt.
    _, kwargs = mock_gw.call_agent.call_args
    assert kwargs["system"] == "you are a voter"
    assert kwargs["max_tokens"] == 100


# ── EndpointAdapter (self-hosted transport, S7.2) ────────────────────────────


def _mock_client(handler) -> "httpx.Client":
    """An httpx.Client backed by a MockTransport (no network)."""
    import httpx
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_endpoint_adapter_posts_and_parses_text():
    """EndpointAdapter POSTs the prompt + returns the JSON {"text": ...} body."""
    import httpx

    received: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        import json as _json
        received.update(_json.loads(request.read()))
        return httpx.Response(200, json={"text": '{"position": "objection"}'})

    ref = AgentRef(instance_id="kimberim", display_name="Self-Hosted", weight=1.0)
    from holon.agents.endpoint import EndpointAdapter
    adapter = EndpointAdapter(
        ref=ref, system_prompt="you are a voter",
        endpoint="https://agents.example.com/infer", client=_mock_client(handler),
    )
    text = adapter.respond("vote on this", max_tokens=200)
    assert text == '{"position": "objection"}'
    # The POST carried the prompt + system + max_tokens.
    assert received["prompt"] == "vote on this"
    assert received["system"] == "you are a voter"
    assert received["max_tokens"] == 200


def test_endpoint_adapter_accepts_response_key_alias():
    """The endpoint may return {"response": "..."} instead of {"text": ...}."""
    import httpx

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"response": '{"position": "consent"}'})

    ref = AgentRef(instance_id="kimberim", display_name="Self-Hosted")
    from holon.agents.endpoint import EndpointAdapter
    adapter = EndpointAdapter(
        ref=ref, system_prompt="x",
        endpoint="https://agents.example.com/infer", client=_mock_client(handler),
    )
    assert adapter.respond("p") == '{"position": "consent"}'


def test_endpoint_adapter_raises_on_non_200():
    """A 500 from the endpoint raises (the cycle catches this → abstain)."""
    import httpx
    import pytest

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": "agent down"})

    ref = AgentRef(instance_id="kimberim", display_name="Self-Hosted")
    from holon.agents.endpoint import EndpointAdapter
    adapter = EndpointAdapter(
        ref=ref, system_prompt="x",
        endpoint="https://agents.example.com/infer", client=_mock_client(handler),
    )
    with pytest.raises(httpx.HTTPStatusError):
        adapter.respond("p")


# ── Per-agent cost guard + rate limiter (S7.4) ───────────────────────────────


def test_rate_limiter_blocks_call_within_window():
    """A RateLimiter at 1 call/60s allows the first call, blocks the second."""
    from holon.agents.adapter import RateLimiter, RateLimitExceeded
    rl = RateLimiter(max_calls=1, window_s=60.0)
    rl.acquire()  # first call OK
    with pytest.raises(RateLimitExceeded):
        rl.acquire()  # second call within the window → blocked


def test_rate_limiter_allows_after_refill():
    """After enough time elapses, the bucket refills and a call succeeds."""
    from holon.agents.adapter import RateLimiter
    rl = RateLimiter(max_calls=1, window_s=0.05)  # 50ms window
    rl.acquire()
    import time
    time.sleep(0.06)  # past the window → 1 token refilled
    rl.acquire()  # should not raise


def test_provider_adapter_enforces_rate_limit():
    """A ProviderAdapter with a tight rate limiter blocks the 2nd call."""
    from holon.agents.adapter import ProviderAdapter, RateLimiter, RateLimitExceeded
    mock_gw = MagicMock(spec=LLMGateway)
    mock_gw.call_agent.return_value = SimpleNamespace(
        text="ok", model="gpt-4o", input_tokens=1, output_tokens=1,
        cost_usd=0.0, cached=False,
    )
    ref = AgentRef(instance_id="kimberim", display_name="RL Agent")
    adapter = ProviderAdapter(
        ref=ref, system_prompt="x", gateway=mock_gw,
        rate_limiter=RateLimiter(max_calls=1, window_s=60.0),
    )
    assert adapter.respond("p") == "ok"
    with pytest.raises(RateLimitExceeded):
        adapter.respond("p")  # 2nd call within window → blocked, gateway NOT called again
    assert mock_gw.call_agent.call_count == 1


def test_make_adapter_provider_gets_per_agent_cap():
    """A provider-kind agent gets a per-agent cost cap on its gateway (a
    fraction of the platform cap), so it can't burn the whole budget."""
    row = _row(model="gpt-4o", api_key_enc="sk-test")
    adapter = make_adapter(row, instance_id="kimberim")
    assert isinstance(adapter, ProviderAdapter)
    # The per-agent cap is set (not None).
    assert adapter.gateway._override_cap_usd is not None
    assert adapter.gateway._override_cap_usd > 0
