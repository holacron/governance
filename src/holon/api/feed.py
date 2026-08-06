"""The live-feed broker — bridges the synchronous cycle thread to SSE.

The cycle runs in a background thread (it's a blocking call). Each step emits a
structured event through `ledger_sink`. This broker gives each deliberation run
an asyncio.Queue; the cycle thread pushes events onto it (thread-safe via
loop.call_soon_threadsafe), and the SSE endpoint drains it from the async loop.

This is the engineering insight that makes a live feed possible without
rewriting the cycle or gateway: we reuse the existing ledger_sink callback.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

log = logging.getLogger(__name__)

# Sentinel placed on the queue to signal the SSE stream should close.
CLOSE = "__close__"


@dataclass
class _RunFeed:
    queue: asyncio.Queue
    loop: asyncio.AbstractEventLoop


@dataclass
class FeedBroker:
    """Process-wide registry of per-run live feeds (in-memory; single-node MVP).

    Two-phase access prevents the SSE handler from clobbering the queue created
    by the POST handler:

      * POST ``/deliberations``  →  ``open()``   (creates the queue ONCE, before
        the cycle thread starts, so no event is missed)
      * GET  ``/deliberations/{run_id}/events``  →  ``subscribe()``  (returns the
        *existing* queue; never overwrites it. Subscribing late still delivers
        any events already buffered on it.)
    """

    _feeds: dict[UUID, _RunFeed] = field(default_factory=dict)

    def open(self, run_id: UUID, loop: asyncio.AbstractEventLoop) -> asyncio.Queue:
        """Create the feed for a run. Called from the async loop (POST handler)
        BEFORE the cycle thread starts, so the queue exists when events arrive.

        Idempotent: if a feed is already open for ``run_id`` it is returned as-is
        rather than replaced — protecting any buffered events.
        """
        existing = self._feeds.get(run_id)
        if existing is not None:
            return existing.queue
        q: asyncio.Queue = asyncio.Queue()
        self._feeds[run_id] = _RunFeed(queue=q, loop=loop)
        return q

    def subscribe(self, run_id: UUID, loop: asyncio.AbstractEventLoop) -> asyncio.Queue:
        """Get the existing queue for a run (GET/SSE handler).

        Returns the queue created by ``open()`` — preserving every event pushed
        between POST and GET. If no feed exists (e.g. the server restarted and
        the cycle is mid-flight in a thread that outlived the broker), create one
        so the stream still works; events emitted before this point are simply
        not delivered (the ledger remains the durable record).
        """
        existing = self._feeds.get(run_id)
        if existing is not None:
            return existing.queue
        q: asyncio.Queue = asyncio.Queue()
        self._feeds[run_id] = _RunFeed(queue=q, loop=loop)
        return q

    def is_open(self, run_id: UUID) -> bool:
        """Whether a feed has been opened for a run (used by tests/diagnostics)."""
        return run_id in self._feeds

    def push(self, run_id: UUID, event_type: str, payload: dict[str, Any]) -> None:
        """Push an event for a run. Called from the cycle THREAD — must be
        thread-safe, so we hop to the loop via call_soon_threadsafe."""
        feed = self._feeds.get(run_id)
        if feed is None:
            return  # no SSE subscriber yet; event still persisted to the ledger
        data = {"event_type": event_type, "payload": payload}
        feed.loop.call_soon_threadsafe(feed.queue.put_nowait, data)

    def close(self, run_id: UUID) -> None:
        """Signal the SSE stream to close (called from the cycle thread on
        decision-recorded / completion)."""
        feed = self._feeds.get(run_id)
        if feed is None:
            return
        feed.loop.call_soon_threadsafe(feed.queue.put_nowait, CLOSE)

    def drop(self, run_id: UUID) -> None:
        """Remove a run's feed (housekeeping after the SSE stream ends)."""
        self._feeds.pop(run_id, None)


__all__ = ["CLOSE", "FeedBroker"]
