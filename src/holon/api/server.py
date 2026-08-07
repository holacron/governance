"""The HOLACRON engage API server (S4) — FastAPI + SSE on HARNESS_PORT.

Serves the instance engage UI + REST (register/act) + SSE (live deliberation
feed). Built ON TOP of the S0-S3 engine; the engine is unchanged.

S7: when any instance has non-manual cadence, a lifespan starts the in-process
epoch scheduler. Manual cadence (the default) = no scheduler task, so the app
behaves identically to S4-S6 when no instance opts into scheduling.

Run locally:
    uv run uvicorn holon.api.server:app --reload --port 8787
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from holon.api.feed import FeedBroker
from holon.api.routes import router
from holon.api.scheduler import epoch_scheduler

STATIC_DIR = Path(__file__).resolve().parent / "static"


def _any_scheduled_instance() -> bool:
    """True if any instance config opts into non-manual cadence."""
    from holon.config import INSTANCES_DIR, load_instance_config
    if not INSTANCES_DIR.exists():
        return False
    for path in INSTANCES_DIR.glob("*/instance.yaml"):
        try:
            ic = load_instance_config(path.parent.name)
        except Exception:  # noqa: BLE001
            continue
        if ic.cadence.preset != "manual":
            return True
    return False


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Start the epoch scheduler on startup (if any instance is non-manual),
    cancel it on shutdown."""
    task = None
    if _any_scheduled_instance():
        task = asyncio.create_task(epoch_scheduler(app))
    try:
        yield
    finally:
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass


def create_app() -> FastAPI:
    """Build the FastAPI app. The FeedBroker is a process-wide singleton
    (in-memory; fine for a single-node local MVP)."""
    app = FastAPI(title="HOLACRON Engage", version="0.0.1", lifespan=lifespan)
    app.state.broker = FeedBroker()

    app.include_router(router)

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    # Serve the engage UI at the root.
    @app.get("/")
    async def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")

    if STATIC_DIR.exists():
        app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    return app


# uvicorn entrypoint
app = create_app()


__all__ = ["app", "create_app"]
