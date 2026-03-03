# chuckbot
Chuckbot is the umbrella title for automated tools used by alachuckthebuck to do things on wikimedia projects.

## Buckbot rollback command flow (Toolforge)
This repo now supports a complete request → queue → worker pipeline:

1. **Userscript request** (`Massrollback chuckbot`) sends rollback requests from Special:Contributions.
2. **Toolforge ingress API** (`toolforge_queue_api.py`) validates/authenticates and stores queued JSON jobs.
3. **Queue processor** (`process_queue.py`) drains queued jobs.
4. **Pywikibot worker** (`buckbot_rollback_worker.py`) executes rollbacks as chuckbot.

---

## Communication model
The userscript sends `POST` JSON to `CTBBuckbotEndpoint` with headers:

- `X-CTB-Request-Id` (idempotency / anti-replay key)
- `X-CTB-Timestamp` (freshness guard)
- `X-CTB-Requester` (declared requester)
- `Authorization: Bearer ...` (optional token hook)

See `queueRollbackRequestCTBMR` in `Massrollback chuckbot`.

`toolforge_queue_api.py` verifies auth + timestamp + replay guard, validates payload fields, and writes queue files under `${CTB_DATA_DIR}/queue/pending`.

For high-volume usage (for example up to 100,000 rollback targets in a request), set `CTB_REDIS_URL` so replay protection uses Redis `SET NX EX` instead of local SQLite.
Replay guard backends:
- `sqlite` (default): local `${CTB_DATA_DIR}/request_guard.sqlite3`
- `redis`: uses `CTB_REDIS_URL` + TTL for request ID keys
- `toolsdb`: uses Toolforge MySQL-compatible ToolsDB table for request ID dedupe

---


## Toolforge container image build/deploy path
If you prefer Toolforge Build Service (recommended for reproducibility), this repo now includes a `Containerfile` and runtime entrypoint.

### Build image from this repo
From your Toolforge tool account (repository checked out in `$HOME/chuckbot` for example):

```bash
cd $HOME/chuckbot
# Build using Toolforge Build Service from local source tree
# (follow Help:Toolforge/Building_container_images for your selected workflow)
toolforge build start --file Containerfile .
```

### Run the API as a webservice from built image
```bash
webservice --backend=kubernetes --image tool-<toolname>/chuckbot:latest start
```

The default container command launches the API (`gunicorn app:app`) and exposes:
- `GET /healthz`
- `POST /rollback-requests`

### Run queue drain job using same image
```bash
toolforge-jobs run buckbot-rollback-queue   --image tool-<toolname>/chuckbot:latest   --command "/srv/chuckbot/toolforge_container_entrypoint.sh queue --max-jobs 20"   --schedule "* * * * *"
```

### Optional one-off checks in image
```bash
# verify policy env
/srv/chuckbot/toolforge_container_entrypoint.sh check
```

---

## Toolforge setup: spool up services

### 1) Create and activate a virtual environment
```bash
cd $HOME/www/python/src/chuckbot
python3 -m venv venv
source venv/bin/activate
pip install flask pywikibot
```

### 2) Configure environment
Create `.env` (or export variables in your Toolforge shell/profile):

```bash
export CTB_DATA_DIR="$HOME/project/chuckbot-data"
export CTB_ALLOWED_WIKIS="enwiki,commonswiki"
export CTB_ALLOWED_REQUESTERS="YourMainAccount"
export CTB_API_TOKENS="long-random-token"
# or load from a 0600 file with one token per line
export CTB_API_TOKENS_FILE="$HOME/project/chuckbot-secrets/api_tokens.txt"
export CTB_REQUIRE_REQUESTER_MATCH=1
export CTB_CLOCK_SKEW_SECONDS=300
export CTB_MAX_TARGETS=100000
export CTB_REDIS_URL="redis://127.0.0.1:6379/0"
export CTB_REQUEST_ID_TTL_SECONDS=86400
# Replay/idempotency backend: sqlite (default), redis, or toolsdb
export CTB_REQUEST_GUARD_BACKEND="sqlite"
# Redis backend settings
export CTB_REDIS_URL="redis://127.0.0.1:6379/0"
export CTB_REDIS_REQUEST_ID_TTL=86400
# ToolsDB backend settings
export CTB_TOOLSDB_HOST="tools-db"
export CTB_TOOLSDB_PORT=3306
export CTB_TOOLSDB_NAME="tools"
export CTB_TOOLSDB_USER="<tool-account>"
export CTB_TOOLSDB_PASSWORD="<toolsdb-password>"
export CTB_TOOLSDB_REQUEST_TABLE="ctb_request_ids"
# Optional stronger hook (if requests are signed server-to-server)
export CTB_HMAC_SECRET="another-long-random-secret"
```


### Token generation (recommended)
You can generate strong API tokens with the helper script:

```bash
python3 generate_api_token.py --count 1
```

Persist token(s) in a file for easier rotation:

```bash
python3 generate_api_token.py --count 2 --output-file "$HOME/project/chuckbot-secrets/api_tokens.txt"
chmod 600 "$HOME/project/chuckbot-secrets/api_tokens.txt"
export CTB_API_TOKENS_FILE="$HOME/project/chuckbot-secrets/api_tokens.txt"
```

Notes:
- The generator uses `secrets` for cryptographic entropy.
- It can additionally mix UUID+time material and hash the result.
- Time/UUID alone are **not** sufficient; cryptographic randomness is required.

### 3) Create Toolforge web entrypoint (`~/www/python/src/app.py`)
Toolforge Python webservice expects your WSGI entrypoint in `~/www/python/src/`.
Use this repo's `app.py` as the source:

```bash
mkdir -p "$HOME/www/python/src"
cp app.py "$HOME/www/python/src/app.py"
cp toolforge_queue_api.py "$HOME/www/python/src/toolforge_queue_api.py"
```

### 4) Start ingress API on Toolforge webservice
```bash
webservice --backend=kubernetes python3.11 start
```

This exposes:
- `GET /healthz`
- `POST /rollback-requests`

### 5) Run queue processor as a scheduled job
Run every minute (recommended):

```bash
toolforge-jobs run buckbot-rollback-queue \
  --command "cd $HOME/www/python/src/chuckbot && ./venv/bin/python process_queue.py --max-jobs 20" \
  --image tf-python311 \
  --schedule "* * * * *"
```

### 6) Userscript wiring
In your wiki userscript config:

- Set `CTBBuckbotEndpoint` to your Toolforge endpoint `.../rollback-requests`
- Set `CTBBuckbotAuthToken` to one token from `CTB_API_TOKENS`

---


### 7) Verify endpoint behavior

```bash
curl -sS "https://<toolname>.toolforge.org/healthz"
curl -sS -X POST "https://<toolname>.toolforge.org/rollback-requests" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer <token-from-CTB_API_TOKENS>" \
  -H "X-CTB-Requester: <allowed-user>" \
  -H "X-CTB-Request-Id: test-$(date +%s)" \
  -H "X-CTB-Timestamp: $(date +%s)" \
  --data '{"command":"rollback","wiki":"enwiki","requestedBy":"<allowed-user>","maxRollbacksPerMinute":5,"targets":[{"title":"Sandbox","user":"Example"}]}'
```

A successful response should include `{"ok": true, "queued": true, ...}` and create a pending queue file.

---

## Wikimedia Cloud Services policy compliance hooks
This repository adds hooks needed to align with common Wikimedia Cloud Services expectations:

- **Access control / least privilege hook**: allowlist requesters (`CTB_ALLOWED_REQUESTERS`) and wikis (`CTB_ALLOWED_WIKIS`).
- **Authentication hook**: bearer token auth and/or trusted forwarded-user header.
- **Replay protection hook**: required timestamp + unique request ID tracked in SQLite by default, or Redis when `CTB_REDIS_URL` is configured.
- **Rate limiting hook**: max rollbacks/min enforced by ingress validation and worker pacing.
- **Audit logging hook**: JSONL request + processor logs under `${CTB_DATA_DIR}/logs`.
- **Bot accountability hook**: request envelope preserves requester identity + metadata.

> Important: final policy compliance depends on your Toolforge deployment config (secrets management, public access controls, and operator runbooks). The code now contains required enforcement points/hooks, but you must configure them correctly in production.

---

## Local/ops checks

Run static compliance checks:

```bash
python3 policy_compliance_check.py
```

Dry-run a queued payload:

```bash
python3 buckbot_rollback_worker.py --command-file request.json --dry-run --start-index 0 --progress-file ./rollback-progress.json
```

Notes:
- `startIndex` in the payload (or `--start-index`) lets operators resume large rollback batches.
- `--progress-file` writes a checkpoint every 50 processed targets and once at completion.
- `process_queue.py` now passes a progress file automatically and requeues jobs from the latest checkpoint when the worker exits non-zero with resumable progress.
- Set payload `maxLag` (or `maxlag`) to enforce MediaWiki maxlag behavior via Pywikibot during live rollbacks.
- Dry-run mode no longer sleeps between targets, making large validation runs fast.

Process queue without live rollback API calls:

```bash
python3 process_queue.py --dry-run
```
