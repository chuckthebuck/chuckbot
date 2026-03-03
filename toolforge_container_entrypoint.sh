#!/usr/bin/env bash
set -euo pipefail

ROLE="${1:-web}"
shift || true

if [[ "$ROLE" == "web" ]]; then
  exec gunicorn --bind 0.0.0.0:8000 --workers "${GUNICORN_WORKERS:-2}" app:app "$@"
elif [[ "$ROLE" == "queue" ]]; then
  exec python3 process_queue.py "$@"
elif [[ "$ROLE" == "worker" ]]; then
  exec python3 buckbot_rollback_worker.py "$@"
elif [[ "$ROLE" == "check" ]]; then
  exec python3 policy_compliance_check.py "$@"
else
  echo "Unknown role: $ROLE" >&2
  exit 2
fi
