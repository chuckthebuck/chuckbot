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
The userscript sends `POST` JSON to `CTBBuckbotEndpoint` with hardened headers:

- `X-CTB-Request-Id` (required idempotency key; strict format)
- `X-CTB-Timestamp` (required freshness guard)
- `Authorization: Bearer ...` (for non-userscript callers in `CTB_AUTH_MODE=bearer`)

Identity is **not** trusted from userscript-provided fields anymore. The userscript only formats and submits selected rollback targets; the API derives requester identity from authentication context. The API authenticates via:

- `CTB_AUTH_MODE=forwarded_user` (default; trusted proxy header such as `X-Forwarded-User`)
- `CTB_AUTH_MODE=bearer` (token mapped to a specific user account)

`toolforge_queue_api.py` now enforces strict auth, replay protection in SQLite (no Redis client dependency required), schema validation, malformed-user-agent rejection, and atomic queue-file writes under `${CTB_DATA_DIR}/queue/pending`.

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
export CTB_AUTH_MODE="forwarded_user"
export CTB_FORWARDED_USER_HEADER="X-Forwarded-User"
# Bearer mode (optional): map token -> username
# export CTB_AUTH_MODE="bearer"
# export CTB_API_TOKEN_MAP_FILE="$HOME/project/chuckbot-secrets/token_map.txt"
# file format: one "token:Username" per line
export CTB_CLOCK_SKEW_SECONDS=300
export CTB_REQUEST_ID_TTL_SECONDS=86400
export CTB_MAX_TARGETS=10000
export CTB_MAX_RATE_PER_MIN=60
export CTB_REQUIRE_USER_AGENT=1
# Optional payload signing (for server-to-server callers; not useful for public userscripts)
export CTB_REQUIRE_HMAC=0
# export CTB_HMAC_SECRET="another-long-random-secret"
```


### Token generation (only for bearer mode)
If you deploy with `CTB_AUTH_MODE=bearer`, generate strong tokens with:

```bash
python3 generate_api_token.py --count 2
```

Then place them in a token map file (0600 permissions):

```text
<token1>:YourMainAccount
<token2>:TrustedAltAccount
```

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
- No auth token is required for normal userscript usage (`CTB_AUTH_MODE=forwarded_user`)
- Non-userscript callers can add `Authorization: Bearer ...` when using bearer mode

---


### 7) Verify endpoint behavior

```bash
curl -sS "https://<toolname>.toolforge.org/healthz"
curl -sS -X POST "https://<toolname>.toolforge.org/rollback-requests" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer <token-mapped-to-allowed-user>" \
  -H "X-CTB-Request-Id: ctbmr-test-$(date +%s)-123456" \
  -H "X-CTB-Timestamp: $(date +%s)" \
  --data '{"command":"rollback","wiki":"enwiki","requestedBy":"<allowed-user>","maxRollbacksPerMinute":5,"targets":[{"title":"Sandbox","user":"Example"}]}'
```

A successful response should include `{"ok": true, "queued": true, ...}` and create a pending queue file.

---

## Wikimedia Cloud Services policy compliance hooks
This repository adds hooks needed to align with common Wikimedia Cloud Services expectations:

- **Access control / least privilege hook**: allowlist requesters (`CTB_ALLOWED_REQUESTERS`) and wikis (`CTB_ALLOWED_WIKIS`).
- **Authentication hook**: explicit auth mode (`forwarded_user` or `bearer`) and strict user binding.
- **Replay protection hook**: required timestamp + unique request ID tracked in SQLite with TTL cleanup.
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
- Dry-run mode no longer sleeps between targets, making large validation runs fast.

Process queue without live rollback API calls:

```bash
python3 process_queue.py --dry-run
```
