# chuckbot
Chuckbot is the umbrella title for automated tools used by alachuckthebuck to do things on wikimedia projects.

## Buckbot rollback command flow
This repository now includes a request/worker flow that lets you request rollbacks from the browser while having the actual rollback action run from the chuckbot Toolforge account via Pywikibot.

### 1) Request rollbacks from your userscript session
`Massrollback chuckbot` now supports queue mode:

- Set `CTBUseBuckbotQueue = true` (default).
- Set `CTBBuckbotEndpoint` to your Toolforge endpoint URL that accepts JSON POST requests.
- Use **Rollback all** or **Rollback selected** on Contributions like normal.

Instead of immediately calling `action=rollback` from your own session, the script now sends a payload like:

```json
{
  "command": "rollback",
  "tool": "buckbot",
  "wiki": "enwiki",
  "mode": "selected",
  "requestedBy": "YourUsername",
  "relevantUser": "TargetUser",
  "maxRollbacksPerMinute": 10,
  "editSummary": "optional summary",
  "targets": [
    { "title": "Page title", "user": "TargetUser" }
  ],
  "requestedAt": "2026-01-01T12:34:56.000Z"
}
```

### 2) Execute rollbacks as chuckbot with Pywikibot
Use `buckbot_rollback_worker.py` in your Toolforge tool environment (logged in as chuckbot in your Pywikibot config):

```bash
python3 buckbot_rollback_worker.py --command-file request.json
```

You can also pass JSON directly:

```bash
python3 buckbot_rollback_worker.py --command-json '{"command":"rollback","targets":[{"title":"Example","user":"ExampleUser"}]}'
```

Dry-run mode for testing parser/queue integration:

```bash
python3 buckbot_rollback_worker.py --command-file request.json --dry-run
```

The worker enforces the per-minute rate by sleeping between rollback calls and marks successful rollback actions with `markbot=True`.
