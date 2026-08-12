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
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from holon.api.feed import FeedBroker
from holon.api.routes import router
from holon.api.scheduler import epoch_scheduler

STATIC_DIR = Path(__file__).resolve().parent / "static"
# The repo-level docs/ directory (AGENT_PROTOCOL.md, PARTICIPIPANT_HANDBOOK.md,
# the generated PDF). Served at GET /docs/ so agents can fetch the protocol
# directly from the API surface, in addition to the kimberim.com copy.
DOCS_DIR = Path(__file__).resolve().parents[3] / "docs"

# Origins allowed to make cross-origin requests to the API. The kimberim.com
# Apply Here form POSTs cross-origin; localhost is for local dev.
_CORS_ORIGINS = [
    "https://kimberim.com",
    "https://www.kimberim.com",
    "http://localhost:8787",
    "http://127.0.0.1:8787",
    # Permissive for any localhost port during local development.
    "http://localhost:3000",
    "http://127.0.0.1:3000",
]


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

    # CORS — the kimberim.com Apply Here form POSTs cross-origin to the API.
    # allow_credentials=False (the MVP uses agent_id as handle, no cookies).
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_CORS_ORIGINS,
        allow_origin_regex=r"https?://(localhost|127\.0\.0\.1)(:\d+)?",  # dev
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["Content-Type", "Authorization"],
    )

    app.include_router(router)

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    # Serve the engage UI at the root.
    @app.get("/")
    async def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")

    # Protocol discoverability — where an agent finds the machine-readable
    # protocol + the human handbook. Linked from the kimberim.com engage surface.
    @app.get("/instances/{instance_id}/protocol")
    async def protocol(instance_id: str) -> JSONResponse:
        return JSONResponse({
            "instance_id": instance_id,
            "agent_protocol": "/docs/AGENT_PROTOCOL.md",
            "participant_handbook": "/docs/holacron-participant-handbook.pdf",
            "engage_surface": "https://kimberim.com",
        })

    if STATIC_DIR.exists():
        app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    # Serve the repo docs/ (AGENT_PROTOCOL.md, the handbook PDF) so they're
    # fetchable from the API surface: GET /docs/AGENT_PROTOCOL.md etc.
    if DOCS_DIR.exists():
        app.mount("/docs", StaticFiles(directory=DOCS_DIR), name="docs")
    return app


# uvicorn entrypoint
app = create_app()


__all__ = ["app", "create_app"]
