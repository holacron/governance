"""Small shared utilities.

Currently houses the tolerant LLM-JSON extractor, used by both the consent
cycle (cycle/nodes.py) and the triage flow (api/routes.py). Kept here so the
two layers don't reach into each other's private helpers.
"""

from __future__ import annotations

import json
import re
from typing import Any


def extract_json(text: str) -> dict[str, Any]:
    """Tolerantly extract the first valid {...} object from an LLM response.

    Live LLMs occasionally wrap JSON in markdown fences, emit prose with stray
    braces, or return truncated output. We try every brace-delimited span from
    the outside in; the first that parses wins. If none parse, return {} (which
    callers treat as a neutral/empty payload) rather than crashing the caller.
    """
    for m in re.finditer(r"\{.*\}", text, re.DOTALL):
        candidate = m.group(0)
        # Strip markdown code fences if present.
        candidate = re.sub(r"```+\w*\n?", "", candidate).strip()
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            continue
    return {}


__all__ = ["extract_json"]
