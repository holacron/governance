"""EndpointAdapter — the self-hosted federation transport (S7.2).

Genuine federation: the platform POSTs a prompt to the agent's HTTPS endpoint
and waits for a JSON response. The agent runs its own intelligence (a different
model, RAG over private data, human-in-the-loop, culturally-sensitive data that
shouldn't transit a vendor). The platform never sees the agent's provider key.

Wire protocol (JSON over HTTPS):
    POST <endpoint>
    {"prompt": "...", "system": "...", "context": "...", "max_tokens": 1024}
    → 200 {"text": "..."}  (or {"response": "..."})

A non-200 or malformed response raises (caught by the cycle's fan-out → the
agent defaults to abstain). The timeout bounds how long the platform waits.
"""

from __future__ import annotations

import logging

import httpx

from holon.agents.adapter import AgentAdapter
from holon.schema import AgentRef

log = logging.getLogger(__name__)

# Default per-call timeout for a self-hosted agent response (seconds). The
# cycle's fan-out imposes its own shorter window; this is the HTTP-level bound.
_ENDPOINT_TIMEOUT_S = 30.0


class EndpointAdapter(AgentAdapter):
    """Self-hosted transport: POSTs prompts to the agent's HTTPS endpoint."""

    def __init__(
        self, *, ref: AgentRef, system_prompt: str, endpoint: str,
        timeout_s: float = _ENDPOINT_TIMEOUT_S, client: httpx.Client | None = None,
    ) -> None:
        super().__init__(ref=ref, system_prompt=system_prompt)
        self.endpoint = endpoint
        self.timeout_s = timeout_s
        # A client may be injected for testing (httpx MockTransport).
        self._client = client or httpx.Client(timeout=timeout_s)

    def respond(self, prompt: str, context: str = "", **kwargs) -> str:
        """POST the prompt to the endpoint, return the agent's text response.

        kwargs forwarded: max_tokens, temperature (the endpoint may honour them).
        Raises httpx.HTTPStatusError on a non-200, or ValueError on a malformed
        body — the cycle catches these and defaults the agent to abstain.
        """
        body = {
            "prompt": prompt,
            "system": self.system_prompt,
            "context": context,
        }
        if "max_tokens" in kwargs:
            body["max_tokens"] = kwargs["max_tokens"]
        if "temperature" in kwargs:
            body["temperature"] = kwargs["temperature"]

        resp = self._client.post(self.endpoint, json=body)
        resp.raise_for_status()
        data = resp.json()
        # Accept either {"text": "..."} or {"response": "..."}.
        text = data.get("text") or data.get("response") or ""
        if not isinstance(text, str):
            raise ValueError(f"endpoint returned non-string text: {type(text)}")
        return text


__all__ = ["EndpointAdapter"]
