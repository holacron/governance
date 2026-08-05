"""The HOLACRON engage API server (S4) — FastAPI + SSE on HARNESS_PORT.

Serves the instance engage UI + REST (register/act) + SSE (live deliberation
feed). Built ON TOP of the S0-S3 engine; the engine is unchanged.

Run locally:
    uv run uvicorn holon.api.server:app --reload --port 8787
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from holon.api.feed import FeedBroker
from holon.api.routes import router

STATIC_DIR = Path(__file__).resolve().parent / "static"


def create_app() -> FastAPI:
    """Build the FastAPI app. The FeedBroker is a process-wide singleton
    (in-memory; fine for a single-node local MVP)."""
    app = FastAPI(title="HOLACRON Engage", version="0.0.1")
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
