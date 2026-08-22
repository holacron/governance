"""Gateway unit tests (deterministic; no live LLM).

Covers the two gateway correctness gaps surfaced in the audit:

  * H5 — the LLM transport retries transient provider failures (429/5xx/overload/
    conn/timeout) with exponential backoff, and non-retryable errors propagate.
  * H7 — the cost cap is enforced BEFORE any API call (the primary safety rail)
    and spent_usd accrues correctly.

All tests mock the Anthropic client (``gateway._client``) so they run offline
and deterministically, with no token spend.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from anthropic import (
    AuthenticationError,
    BadRequestError,
    InternalServerError,
    RateLimitError,
)

from olon.config import RuntimeConfig
from olon.gateway import CostCapExceeded, LLMGateway

# tenacity's waits would make this test sleep 2s + 4s between retries. Patch the
# retry's sleep so the retry path executes instantly but still retries 3x.
import olon.gateway as gw_mod


@pytest.fixture(autouse=True)
def _no_retry_sleep(monkeypatch):
    """Make tenacity's between-retry backoff a no-op so retry tests run fast.

    tenacity's default nap calls time.sleep under the hood; patching time.sleep
    (in this module's namespace) zeroes the exp-backoff waits (2s, 4s, 8s)
    while still exercising the full retry sequence.
    """
    import time

    monkeypatch.setattr(time, "sleep", lambda _secs: None)


def _make_gateway(*, cap_usd: float = 5.0) -> LLMGateway:
    """A gateway with a dummy key (so construction passes has_provider) and a
    MOCK client. No real network is touched."""
    rt = RuntimeConfig(
        ZAI_API_KEY="dummy-key-for-tests",
        HARNESS_COST_CAP_USD=cap_usd,
        HARNESS_MODELS=["glm-5-turbo"],
    )
    g = LLMGateway(rt)
    g._client = MagicMock()
    return g


def _resp(text: str = "ok", in_tok: int = 10, out_tok: int = 5):
    """A minimal stand-in for the anthropic Message response object.

    Content blocks carry type='text' so _extract_text's filter picks them up.
    """
    return SimpleNamespace(
        content=[SimpleNamespace(type="text", text=text)],
        usage=SimpleNamespace(input_tokens=in_tok, output_tokens=out_tok),
    )


def _rate_limit_error() -> RateLimitError:
    # The anthropic SDK's status errors need a response + body to construct.
    import httpx

    req = httpx.Request("POST", "https://api.z.ai/v1/messages")
    resp = httpx.Response(status_code=429, request=req)
    return RateLimitError("rate limited", response=resp, body=None)


def _internal_error() -> InternalServerError:
    import httpx

    req = httpx.Request("POST", "https://api.z.ai/v1/messages")
    resp = httpx.Response(status_code=500, request=req)
    return InternalServerError("boom", response=resp, body=None)


def _auth_error() -> AuthenticationError:
    import httpx

    req = httpx.Request("POST", "https://api.z.ai/v1/messages")
    resp = httpx.Response(status_code=401, request=req)
    return AuthenticationError("bad key", response=resp, body=None)


def _bad_request_error() -> BadRequestError:
    import httpx

    req = httpx.Request("POST", "https://api.z.ai/v1/messages")
    resp = httpx.Response(status_code=400, request=req)
    return BadRequestError("malformed", response=resp, body=None)


# ── H5: retry + timeout ─────────────────────────────────────────────────────


def test_retry_then_succeed_on_transient_429():
    """H5: a transient 429 (rate limit) is retried; the call succeeds once the
    provider recovers. Three attempts max — two failures then success is within
    budget."""
    g = _make_gateway()
    g._client.messages.create.side_effect = [
        _rate_limit_error(),
        _rate_limit_error(),
        _resp("recovered"),
    ]
    out = g.call_agent("test-role", "do the thing", max_tokens=100, temperature=0.0)
    assert out.text == "recovered"
    assert g._client.messages.create.call_count == 3
    # Each attempt must carry the timeout kwarg (H5 belt-and-braces).
    for call in g._client.messages.create.call_args_list:
        assert call.kwargs.get("timeout") == gw_mod._LLM_TIMEOUT_S


def test_retry_then_succeed_on_transient_500():
    """H5: a transient 5xx (internal server error) is also retried."""
    g = _make_gateway()
    g._client.messages.create.side_effect = [_internal_error(), _resp("ok")]
    out = g.call_agent("test-role", "x", max_tokens=100, temperature=0.0)
    assert out.text == "ok"
    assert g._client.messages.create.call_count == 2


def test_non_retryable_error_propagates_immediately():
    """H5: an authentication error is NOT retried — it propagates after a single
    attempt. We don't burn retry budget on errors that won't fix themselves."""
    g = _make_gateway()
    g._client.messages.create.side_effect = [_auth_error()]
    with pytest.raises(AuthenticationError):
        g.call_agent("test-role", "x", max_tokens=100, temperature=0.0)
    assert g._client.messages.create.call_count == 1


def test_non_retryable_bad_request_propagates():
    """H5: a 400 (malformed request) is not retried either."""
    g = _make_gateway()
    g._client.messages.create.side_effect = [_bad_request_error()]
    with pytest.raises(BadRequestError):
        g.call_agent("test-role", "x", max_tokens=100, temperature=0.0)
    assert g._client.messages.create.call_count == 1


def test_retry_exhausted_reraises():
    """H5: if all 3 attempts fail with a retryable error, the last error is
    reraised (reraise=True — no RetryError wrapping)."""
    g = _make_gateway()
    g._client.messages.create.side_effect = [
        _rate_limit_error(),
        _rate_limit_error(),
        _rate_limit_error(),
    ]
    with pytest.raises(RateLimitError):
        g.call_agent("test-role", "x", max_tokens=100, temperature=0.0)
    assert g._client.messages.create.call_count == 3


# ── H7: cost cap enforcement + accrual ──────────────────────────────────────


def test_cost_cap_blocks_call_before_api():
    """H7: with a $0 cap, call_agent raises CostCapExceeded WITHOUT ever hitting
    the API. This is the primary safety rail; it must be enforceable to zero."""
    g = _make_gateway(cap_usd=0.0)
    with pytest.raises(CostCapExceeded):
        g.call_agent("test-role", "x", max_tokens=100, temperature=0.0)
    g._client.messages.create.assert_not_called()
    assert g.spent_usd == 0.0


def test_cost_cap_aborts_as_run_approaches_budget():
    """H7: the cap is enforced PER CALL against running spend. The first call
    succeeds (under budget); the second (which would push over) is blocked
    before the API — so a runaway loop aborts before burning the budget."""
    # cap=$0.002. First call actual spend (~$0.00125) stays under; the second
    # call's worst-case estimate (~$0.0015) would push total to ~$0.0028 > cap.
    g = _make_gateway(cap_usd=0.002)
    g._client.messages.create.return_value = _resp("ok", in_tok=1000, out_tok=500)
    # First call: under cap — proceeds. use_cache=False so the second identical
    # call is treated as a distinct spend (cache would otherwise short-circuit).
    g.call_agent("test-role", "x", max_tokens=1000, temperature=0.0, use_cache=False)
    assert g.spent_usd > 0.0
    assert g._client.messages.create.call_count == 1
    # Second call: would push over the cap — blocked before the API.
    with pytest.raises(CostCapExceeded):
        g.call_agent("test-role", "y", max_tokens=1000, temperature=0.0, use_cache=False)
    assert g._client.messages.create.call_count == 1  # no new API call


def test_spent_usd_accrues_from_usage():
    """H7: spent_usd accrues from each response's usage, exactly as the pricing
    model predicts (deterministic, no live LLM)."""
    g = _make_gateway(cap_usd=5.0)
    g._client.messages.create.return_value = _resp("ok", in_tok=100, out_tok=50)
    g.call_agent("test-role", "x", max_tokens=200, temperature=0.0, use_cache=False)
    # glm-5-turbo pricing: input $0.50/MTok, output $1.50/MTok.
    # 100 in + 50 out -> 100*0.50/1e6 + 50*1.50/1e6 = 0.00005 + 0.000075
    expected = 100 * 0.50 / 1e6 + 50 * 1.50 / 1e6
    assert g.spent_usd == pytest.approx(expected)
