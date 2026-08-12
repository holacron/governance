# Deploying the HOLACRON API to production

This guide takes you from a fresh Linux server to a live API at
`https://api.kimberim.com`, with automatic HTTPS and a managed Postgres — all
in one `docker compose up`.

```
                    ┌──────────┐
  internet ───443──▶│  Caddy   │── auto HTTPS (Let's Encrypt)
                    │ (reverse │── terminates TLS
                    │  proxy)  │
                    └────┬─────┘
                         │ 8787 (internal)
                    ┌────▼─────┐    ┌───────────┐
                    │   API    │───▶│ Postgres  │
                    │ (uvicorn)│    │ (vol:pg)  │
                    └──────────┘    └───────────┘
```

All three run as Docker containers on one server. Caddy is the only thing
exposed to the internet (ports 80 + 443).

---

## Prerequisites

- A **Linux server** (Ubuntu 22.04+ / Debian 12+ recommended) with:
  - **Root or sudo** access via SSH
  - **1 GB RAM minimum** (2 GB recommended — LLM deliberations are memory-bursty)
  - **10 GB disk** (Postgres + container images)
  - **Ports 80 and 443** open to the internet (for HTTPS)
- The server's **public IP address** (find it: `curl -s ifconfig.me`)
- Your **Z.ai API key** (the same one in the local `.env`)
- DNS access to **kimberim.com** (SiteGround panel, or wherever DNS lives)

---

## Step 1 — Install Docker on the server

SSH into the server and run the official install script:

```bash
ssh root@YOUR.SERVER.IP

# Install Docker + the compose plugin (official one-liner)
curl -fsSL https://get.docker.com | sh

# Verify
docker --version
docker compose version
```

That's it — Docker is installed and enabled on boot.

---

## Step 2 — Get the code onto the server

Clone the repo (or pull if you've done this before):

```bash
cd /opt
git clone https://github.com/holacron/governance.git holacron
cd holacron
```

> If the repo is private, you'll need a deploy key or personal access token.
> Alternatively, from your local machine:
> ```bash
> scp -r /c/Users/Administrator/ZCodeProject/holon root@YOUR.SERVER.IP:/opt/holacron
> ```

---

## Step 3 — Configure the environment

Create the `.env` file from the template and fill in two secrets:

```bash
cd /opt/holacron
cp deploy/.env.production.example .env
nano .env    # or vi, or your preferred editor
```

Set **two required values**:

| Variable | What to set |
|----------|-------------|
| `POSTGRES_PASSWORD` | Generate a strong password: `python3 -c "import secrets; print(secrets.token_urlsafe(24))"` |
| `ZAI_API_KEY` | Your Z.ai API key (same as the local `.env`) |

Save and exit. **Never commit this file** — it's in `.gitignore`.

---

## Step 4 — Point DNS at the server (do this BEFORE starting Caddy)

Caddy needs `api.kimberim.com` to resolve to your server's IP *before* it can
provision a Let's Encrypt certificate. If DNS isn't pointed yet, Caddy will
retry and eventually fail.

In your **DNS management panel** (SiteGround → Site Tools → DNS, or your
registrar), add an **A record**:

```
Type:  A
Name:  api.kimberim.com  (or just "api" if the zone is kimberim.com)
Value: YOUR.SERVER.PUBLIC.IP
TTL:   300 (5 min — keep it low until everything works)
```

Verify it has propagated before continuing:

```bash
# Run this on your local machine, not the server:
dig +short api.kimberim.com
# Should return YOUR.SERVER.PUBLIC.IP
```

DNS propagation can take 5–60 minutes. If `dig` doesn't show your IP yet, wait.

---

## Step 5 — Open the firewall (if applicable)

If the server has `ufw` or a cloud firewall (AWS security group, etc.), ensure
ports 80 and 443 are open:

```bash
# Ubuntu ufw example:
ufw allow 80/tcp
ufw allow 443/tcp
ufw allow 22/tcp   # SSH — don't lock yourself out!
ufw enable
```

For cloud providers (DigitalOcean, Hetzner Cloud, AWS), also check the
**security group / firewall rules** in the provider's console.

---

## Step 6 — Launch the stack

```bash
cd /opt/holacron
docker compose up -d --build
```

This will:
1. **Build** the API image (installs Python deps via `uv` — ~2 min first time)
2. **Start** Postgres, wait for it to be healthy
3. **Start** the API (runs migrations automatically, then uvicorn)
4. **Start** Caddy (provisions the Let's Encrypt cert, begins proxying)

Watch the logs to confirm everything came up:

```bash
docker compose logs -f api
```

You should see:
```
[entrypoint] HOLACRON engage API starting...
[entrypoint] database reachable at postgres:5432
[entrypoint] applying migrations...
[entrypoint] migrations applied OK
[entrypoint] starting uvicorn on 0.0.0.0:8787...
INFO:     Uvicorn running on http://0.0.0.0:8787
```

Press `Ctrl+C` to exit the log stream (the containers keep running).

Check Caddy got its certificate:

```bash
docker compose logs caddy | grep -i "certificate"
```

---

## Step 7 — Verify it's live

From your local machine:

```bash
# Health check
curl https://api.kimberim.com/health
# → {"status":"ok"}

# Instance summary
curl https://api.kimberim.com/instances/kimberim
# → {"instance_id":"kimberim","display_name":"KIMBERIM",...}

# Taxonomy (the Apply Here form fetches this)
curl https://api.kimberim.com/instances/kimberim/taxonomy | python -m json.tool

# Agent protocol (served by the API)
curl -s https://api.kimberim.com/docs/AGENT_PROTOCOL.md | head -5
```

If all of those return data, **the API is live** and the Apply Here form on
kimberim.com will now work end-to-end. No code changes needed — the form is
already pointed at `https://api.kimberim.com`.

---

## Day-to-day operations

### Update to a new version

```bash
cd /opt/holacron
git pull origin main
docker compose up -d --build
```

Migrations run automatically on every restart (they're idempotent).

### View logs

```bash
docker compose logs -f api       # API only, live
docker compose logs -f caddy     # Caddy (requests, TLS)
docker compose logs --tail 100 api   # last 100 lines
```

### Check container status

```bash
docker compose ps
```

### Restart a service

```bash
docker compose restart api
```

### Database backup

```bash
docker compose exec postgres pg_dump -U holon holon > backup_$(date +%F).sql
```

### Restore

```bash
cat backup_2026-01-15.sql | docker compose exec -T postgres psql -U holon holon
```

### Tear down (keeps data)

```bash
docker compose down
```

### Wipe everything (⚠️ deletes the database)

```bash
docker compose down -v
```

---

## Troubleshooting

### Caddy can't get a certificate

**Symptom:** `docker compose logs caddy` shows `obtain certificate` errors.

**Cause:** DNS isn't pointing at the server yet, or ports 80/443 are blocked.

**Fix:**
1. Confirm `dig +short api.kimberim.com` returns the server's IP.
2. Confirm ports are open: from your local machine,
   `curl -v http://api.kimberim.com/` should connect (even if it 502s).
3. Restart Caddy after fixing: `docker compose restart caddy`.

### API can't connect to Postgres

**Symptom:** `docker compose logs api` shows `connection refused` or
`password authentication failed`.

**Fix:** The `POSTGRES_PASSWORD` in `.env` must match what was set when
Postgres first started. If you changed it after the first `up`, the volume
still has the old password. Reset it:
```bash
docker compose down -v   # ⚠️ wipes the database (fine on first deploy)
docker compose up -d --build
```

### The form on kimberim.com still says "API isn't live"

1. Confirm the API is up: `curl https://api.kimberim.com/health`
2. Clear the site cache: visit `kimberim.com/?v=2` (cache-bust query param)
3. Check the browser console (F12) for the actual fetch error — CORS errors
   mean the `CORSMiddleware` origins in `server.py` need updating.

### Certificate renewal

Caddy handles this automatically (certs renew 30 days before expiry). No
action needed. Verify with:
```bash
docker compose logs caddy | grep -i rene
```

---

## Architecture notes

- **No port exposure on the API or Postgres** — only Caddy talks to the
  internet. The API and DB are on an internal Docker network.
- **Postgres data** persists in a named volume (`pgdata`) — survives
  container rebuilds and restarts. Only `docker compose down -v` wipes it.
- **Caddy certificate data** persists in `caddy_data` / `caddy_config`
  volumes — certs survive restarts without re-provisioning.
- **Migrations** are idempotent (`IF NOT EXISTS` / `ADD COLUMN IF NOT EXISTS`)
  and run on every container start. Safe to run repeatedly.
- **Cost cap**: the `HARNESS_COST_CAP_USD` env var (default $5) is a safety
  brake — the engine aborts a run if LLM costs exceed it. Tune as needed.
