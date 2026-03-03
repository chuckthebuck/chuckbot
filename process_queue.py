#!/usr/bin/env python3
"""Process queued rollback requests and execute them via buckbot_rollback_worker.py."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import time
from pathlib import Path


DATA_DIR = Path(os.environ.get("CTB_DATA_DIR", "./data"))
QUEUE_DIR = DATA_DIR / "queue"
PENDING = QUEUE_DIR / "pending"
PROCESSING = QUEUE_DIR / "processing"
DONE = QUEUE_DIR / "done"
FAILED = QUEUE_DIR / "failed"
LOG_FILE = DATA_DIR / "logs" / "queue_processor.jsonl"
WORKER_SCRIPT = Path(__file__).resolve().parent / "buckbot_rollback_worker.py"


for d in (PENDING, PROCESSING, DONE, FAILED, LOG_FILE.parent):
    d.mkdir(parents=True, exist_ok=True)


def log_event(event: dict) -> None:
    with LOG_FILE.open("a", encoding="utf-8") as f:
        f.write(json.dumps(event, ensure_ascii=False) + "\n")


def run_one(job_path: Path, dry_run: bool = False) -> int:
    processing_path = PROCESSING / job_path.name
    os.replace(job_path, processing_path)

    with processing_path.open("r", encoding="utf-8") as f:
        envelope = json.load(f)

    payload = envelope.get("payload", {})
    request_id = envelope.get("requestId", processing_path.stem)

    payload_file = PROCESSING / f"{request_id}.payload.json"
    with payload_file.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False)

    cmd = ["python3", str(WORKER_SCRIPT), "--command-file", str(payload_file)]
    if dry_run:
        cmd.append("--dry-run")

    started = int(time.time())
    proc = subprocess.run(cmd, capture_output=True, text=True)
    ended = int(time.time())

    destination = DONE if proc.returncode == 0 else FAILED
    os.replace(processing_path, destination / processing_path.name)
    shutil.move(str(payload_file), destination / payload_file.name)

    log_event(
        {
            "requestId": request_id,
            "status": "done" if proc.returncode == 0 else "failed",
            "started": started,
            "ended": ended,
            "exitCode": proc.returncode,
            "stdout": proc.stdout[-4000:],
            "stderr": proc.stderr[-4000:],
        }
    )

    return proc.returncode


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--max-jobs", type=int, default=20, help="Max jobs to process in one run")
    parser.add_argument("--dry-run", action="store_true", help="Run worker in dry-run mode")
    args = parser.parse_args()

    pending_jobs = sorted(PENDING.glob("*.json"))[: args.max_jobs]
    if not pending_jobs:
        return 0

    failures = 0
    for job in pending_jobs:
        rc = run_one(job, dry_run=args.dry_run)
        if rc != 0:
            failures += 1

    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
