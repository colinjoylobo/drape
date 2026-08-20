# Deploying Drape

Drape was built to run on one machine for one operator. This document covers what to change before
it runs anywhere else, and how to deploy it once you have.

## Read this first

**There is no authentication.** Every endpoint is open, and one of them spends money. If you put the
current build on a public address, anyone who finds it can generate images on your provider account
until the credits run out. Nothing in the app rate-limits, authenticates or caps spend.

Two consequences:

- Do not expose it to the internet without adding an auth layer (below).
- The provider session id in `backend/.env` is a capability. Anyone who can read that file, or who
  can reach the API, can spend against it.

The rest of this guide assumes you are fixing that first.

## What must change before exposing it

### 1. Authentication

The smallest thing that works is a shared secret checked on every `/api` route. In
`backend/app/main.py`:

```python
from fastapi import Header, HTTPException, Depends

API_TOKEN = os.getenv("DRAPE_API_TOKEN")

def require_token(x_drape_token: str = Header(None)):
    if not API_TOKEN or x_drape_token != API_TOKEN:
        raise HTTPException(401, "unauthorised")

for r in (sessions.router, garments.router, avatars.router,
          generations.router, library.router, export.router):
    app.include_router(r, prefix="/api", dependencies=[Depends(require_token)])
```

The frontend then needs to send it — add the header in `frontend/src/api.js`'s `req()`.

Better, if you have it: put the whole app behind an identity-aware proxy (Cloudflare Access,
Tailscale, an SSO reverse proxy) and skip application-level auth entirely. That also solves session
management, which a shared token does not.

### 2. CORS

`main.py` currently allows only `localhost:5173`, which is correct for development and wrong
everywhere else:

```python
allow_origins=[os.getenv("DRAPE_ORIGIN", "http://localhost:5173")]
```

If you serve the built frontend from the same origin as the API — recommended — you can drop the
CORS middleware entirely.

### 3. The file endpoint

`/api/file` serves any file under `STORAGE_DIR` or `DRAPE_ALLOWED_ROOT`. That is deliberate, so
garments can be referenced where they already sit instead of being copied. On a shared machine it
means anyone who can call the API can read anything under those roots.

Either leave `DRAPE_ALLOWED_ROOT` unset in production and upload garments properly, or point it at a
directory that contains nothing but garment photography.

### 4. Spend limits

There are none. Before other people use it, consider a per-day generation cap in
`pipeline.generate_for_look` — a count against `generations` for the last 24 hours, refusing past a
threshold. Cheap to add, and it turns a bad afternoon into an error message.

## Shape

Run it as **one instance on one machine**, and resist the urge to scale it horizontally.

- **SQLite in WAL mode** handles concurrent readers and one writer. It is right for this workload —
  a handful of operators, writes measured in dozens per hour — and wrong the moment two app
  instances share a filesystem. If you genuinely outgrow it, move to Postgres before adding a second
  instance, not after.
- **Background generation runs in-process** via FastAPI's `BackgroundTasks`. A batch is a Python loop
  inside the web process, so a restart or a crash abandons whatever is in flight. Those rows stay
  `running` forever and need clearing by hand. If you need generation to survive restarts, move the
  batch loop to a real queue (RQ or Celery with Redis) before deploying anywhere that redeploys often.
- **Storage is a local directory.** `backend/storage/` holds the database, uploads, crops and every
  generated image. It must be on a persistent volume, and it grows: budget roughly 5 MB per generated
  shot plus the source photography.

## Deploying on a single VM

Assumes Ubuntu, a domain, and that you have added auth.

### Build

```bash
git clone https://github.com/colinjoylobo/drape.git /opt/drape
cd /opt/drape/backend
python3 -m venv .venv && ./.venv/bin/pip install -r requirements.txt
cd ../frontend && npm ci && npm run build     # emits frontend/dist
```

### Configure

```bash
cp /opt/drape/backend/.env.example /opt/drape/backend/.env
chmod 600 /opt/drape/backend/.env             # it holds a spendable credential
$EDITOR /opt/drape/backend/.env
```

`DRAPE_SHARED_UTILS` must point at the directory containing the shared generation client module, or
generation fails at import with a message saying exactly that.

Confirm the configuration before wiring up a service:

```bash
cd /opt/drape/backend
./.venv/bin/python -c "from app.config import missing_credentials; print(missing_credentials() or 'ok')"
```

### Serve the API

`/etc/systemd/system/drape.service`:

```ini
[Unit]
Description=Drape
After=network.target

[Service]
User=drape
WorkingDirectory=/opt/drape/backend
ExecStart=/opt/drape/backend/.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8077
Restart=on-failure
# Deliberately one worker: SQLite takes a single writer, and background batches
# live in the process. More workers means duplicated batches and lock contention.

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl enable --now drape
```

Bind to `127.0.0.1`, never `0.0.0.0` — let the proxy be the only thing listening publicly.

### Serve the frontend

Same origin as the API, so there is no CORS to configure:

```nginx
server {
    listen 443 ssl;
    server_name drape.example.com;

    root /opt/drape/frontend/dist;
    index index.html;

    # Generation takes minutes; the default 60s proxy timeout would cut it off.
    location /api/ {
        proxy_pass http://127.0.0.1:8077;
        proxy_read_timeout 900s;
        proxy_send_timeout 900s;
        client_max_body_size 100M;      # garment uploads are many large photos
    }

    location / {
        try_files $uri /index.html;
    }
}
```

## Operating it

**Back up `backend/storage/`.** It holds the database, every generated image and every lesson the
tool has learned. Nothing there is reproducible without re-spending credits. WAL mode means copying
`drape.db` alone can catch a torn state, so either stop the service first or use
`sqlite3 drape.db ".backup /path/out.db"`.

**Clearing abandoned work.** After a crash or restart mid-batch:

```sql
UPDATE generations
   SET status='error', error='interrupted'
 WHERE status IN ('running','pending');
```

Those rows are otherwise invisible in the UI as permanently "generating".

**Upgrades.** Schema changes are applied idempotently on startup from the `MIGRATIONS` list in
`db.py`, so a deploy is: pull, reinstall dependencies, rebuild the frontend, restart. Never delete
the database to fix a schema problem — it contains the learned lessons.

**Watch the logs on first run.** `missing credentials` at startup means generation will fail later;
everything else keeps working, which is what makes it easy to miss.

## Cost

Image generation is the only expensive step; analysis and QC run on a cheap vision model. The app is
built to avoid spending twice — batches skip finished work, prompt preview is free, repairs are
user-approved — but none of that is a hard limit. Watch the provider account for the first week.

## Not ready for

Being honest about scope, since the failure modes are unpleasant rather than obvious:

- **Multiple concurrent operators.** SQLite plus a single writer holds up for a few people, but
  nothing coordinates two users editing the same garment.
- **Frequent redeploys.** Every restart abandons in-flight generations.
- **Untrusted users.** No auth, no per-user isolation, no spend caps. Everyone shares one provider
  account and one library.
- **Anything multi-tenant.** Sessions, models and lessons are global; there is no notion of an
  account or an owner anywhere in the schema.
