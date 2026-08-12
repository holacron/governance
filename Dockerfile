# HOLACRON engage API — production image.
#
# Runs from source (not a wheel) because the app resolves instances/,
# migrations/, and docs/ relative to the repo root via __file__ parents.
# Layout inside the container: /app/src/holon/...  →  REPO_ROOT = /app.
#
# Build:  docker compose build     (or:  docker build -t holacron-api .)

FROM python:3.12-slim AS base

# System deps: curl for healthchecks, build-essential not needed (psycopg binary wheels).
RUN apt-get update && apt-get install -y --no-install-recommends \
        curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Install uv (fast Python package manager) from the official slim image.
COPY --from=ghcr.io/astral-sh/uv:0.5.11 /uv /usr/local/bin/uv

WORKDIR /app

# Copy dependency manifests first for layer caching.
COPY pyproject.toml uv.lock ./

# Copy the application source + runtime data directories.
COPY src/ ./src/
COPY migrations/ ./migrations/
COPY instances/ ./instances/
COPY docs/ ./docs/
COPY deploy/entrypoint.sh ./deploy/entrypoint.sh
RUN chmod +x deploy/entrypoint.sh

# Install dependencies from the lockfile (no dev extras, frozen).
# uv creates /app/.venv automatically.
RUN uv sync --frozen --no-dev

EXPOSE 8787

# Healthcheck: the /health endpoint returns {"status":"ok"}.
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD curl -sf http://localhost:8787/health || exit 1

CMD ["./deploy/entrypoint.sh"]
