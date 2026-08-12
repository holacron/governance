#!/bin/sh
# HOLACRON API container entrypoint.
#
# 1. Wait for Postgres to accept connections.
# 2. Apply database migrations (idempotent — safe on fresh or existing DB).
# 3. Start uvicorn (binds 0.0.0.0 so Caddy can reach it).
set -e

echo "[entrypoint] HOLACRON engage API starting..."

# ── 1. Wait for Postgres ──────────────────────────────────────────────────────
# The compose stack has a healthcheck on the postgres service, and `depends_on:
# condition: service_healthy` ensures we only start once it's ready. This loop
# is a belt-and-suspenders guard for non-compose environments.
if [ -n "$DATABASE_URL" ]; then
    echo "[entrypoint] waiting for database..."
    # Extract host:port from DATABASE_URL for a quick reachability check.
    DB_HOST=$(printf '%s\n' "$DATABASE_URL" | sed -n 's|.*@\([^:]*\):.*|\1|p')
    DB_PORT=$(printf '%s\n' "$DATABASE_URL" | sed -n 's|.*:\([0-9]*\)/.*|\1|p')
    DB_PORT="${DB_PORT:-5432}"
    for i in $(seq 1 30); do
        if curl -sf "http://${DB_HOST}:${DB_PORT}" >/dev/null 2>&1 \
           || python3 -c "import socket; socket.create_connection(('${DB_HOST}', ${DB_PORT}), timeout=2)" 2>/dev/null; then
            echo "[entrypoint] database reachable at ${DB_HOST}:${DB_PORT}"
            break
        fi
        echo "[entrypoint]   waiting for ${DB_HOST}:${DB_PORT}... ($i/30)"
        sleep 2
    done
fi

# ── 2. Apply migrations ───────────────────────────────────────────────────────
echo "[entrypoint] applying migrations..."
uv run python -c "\
from holon.config import load_runtime_config
from holon.store import apply_migrations
cfg = load_runtime_config()
apply_migrations(cfg.database_url)
print('[entrypoint] migrations applied OK')
"

# ── 3. Start uvicorn ──────────────────────────────────────────────────────────
echo "[entrypoint] starting uvicorn on 0.0.0.0:8787..."
exec uv run uvicorn holon.api.server:app --host 0.0.0.0 --port 8787
