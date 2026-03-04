#!/usr/bin/env python3
"""Process queued rollback requests and execute them via buckbot_rollback_worker.py."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any


DATA_DIR = Path(os.environ.get("CTB_DATA_DIR", "./data"))
QUEUE_DIR = DATA_DIR / "queue"
PENDING = QUEUE_DIR / "pending"
PROCESSING = QUEUE_DIR / "processing"
DONE = QUEUE_DIR / "done"
FAILED = QUEUE_DIR / "failed"
LOCK_FILE = QUEUE_DIR / "processor.lock"
LOG_FILE = DATA_DIR / "logs" / "queue_processor.jsonl"
WORKER_SCRIPT = Path(__file__).resolve().parent / "buckbot_rollback_worker.py"
AUXILIARY_JSON_SUFFIXES = (".payload.json", ".progress.json")


for d in (PENDING, PROCESSING, DONE, FAILED, LOG_FILE.parent):
    d.mkdir(parents=True, exist_ok=True)


class QueueLockError(Exception):
    """Raised when queue processor lock acquisition fails."""


@contextmanager
def queue_lock() -> None:
    """Acquire a single-instance lock for queue processing."""
    lock_fd: int | None = None
    try:
        lock_fd = os.open(LOCK_FILE, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.write(lock_fd, f"{os.getpid()}\n".encode("utf-8"))
        os.fsync(lock_fd)
        yield
    except FileExistsError as err:
        raise QueueLockError(f"Queue processor lock already held: {LOCK_FILE}") from err
    finally:
        if lock_fd is not None:
            os.close(lock_fd)
            try:
                LOCK_FILE.unlink(missing_ok=True)
            except OSError:
                pass


def log_event(event: dict[str, Any]) -> None:
    with LOG_FILE.open("a", encoding="utf-8") as f:
        f.write(json.dumps(event, ensure_ascii=False) + "\n")


def _resume_from_progress(envelope: dict[str, Any], progress_file: Path) -> tuple[bool, int | None]:
    """Update envelope startIndex from worker progress and report if resumable."""
    if not progress_file.exists():
        return False, None

    try:
        progress = json.loads(progress_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False, None

    last_index = progress.get("lastProcessedIndex")
    if not isinstance(last_index, int):
        return False, None

    payload = envelope.get("payload")
    if not isinstance(payload, dict):
        return False, None

    targets = payload.get("targets")
    if not isinstance(targets, list):
        return False, None

    next_start = last_index + 1
    if next_start < 0 or next_start >= len(targets):
        return False, None

    payload["startIndex"] = next_start
    return True, next_start



def _discover_pending_jobs(max_jobs: int) -> list[Path]:
    jobs: list[Path] = []
    for candidate in sorted(PENDING.glob("*.json")):
        if candidate.name.endswith(AUXILIARY_JSON_SUFFIXES):
            continue
        jobs.append(candidate)
        if len(jobs) >= max_jobs:
            break
    return jobs

def run_one(job_path: Path, dry_run: bool = False) -> int:
    processing_path = PROCESSING / job_path.name
    os.replace(job_path, processing_path)

    with processing_path.open("r", encoding="utf-8") as f:
        envelope = json.load(f)

    payload = envelope.get("payload", {})
    request_id = envelope.get("requestId", processing_path.stem)

    payload_file = PROCESSING / f"{request_id}.payload.json"
    progress_file = PROCESSING / f"{request_id}.progress.json"
    with payload_file.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False)

    cmd = [
        "python3",
        str(WORKER_SCRIPT),
        "--command-file",
        str(payload_file),
        "--progress-file",
        str(progress_file),
    ]
    if dry_run:
        cmd.append("--dry-run")

    started = int(time.time())
    proc = subprocess.run(cmd, capture_output=True, text=True)
    ended = int(time.time())

    status = "done"
    resumed_to_start_index: int | None = None
    if proc.returncode == 0:
        os.replace(processing_path, DONE / processing_path.name)
        shutil.move(str(payload_file), DONE / payload_file.name)
        if progress_file.exists():
            shutil.move(str(progress_file), DONE / progress_file.name)
    else:
        can_resume, resumed_to_start_index = _resume_from_progress(envelope, progress_file)
        if can_resume:
            processing_path.write_text(json.dumps(envelope, ensure_ascii=False), encoding="utf-8")
            os.replace(processing_path, PENDING / processing_path.name)
            status = "requeued"
            if payload_file.exists():
                shutil.move(str(payload_file), PENDING / payload_file.name)
            if progress_file.exists():
                shutil.move(str(progress_file), PENDING / progress_file.name)
        else:
            os.replace(processing_path, FAILED / processing_path.name)
            if payload_file.exists():
                shutil.move(str(payload_file), FAILED / payload_file.name)
            if progress_file.exists():
                shutil.move(str(progress_file), FAILED / progress_file.name)
            status = "failed"

    log_event(
        {
            "requestId": request_id,
            "status": status,
            "started": started,
            "ended": ended,
            "exitCode": proc.returncode,
            "resumedToStartIndex": resumed_to_start_index,
            "stdout": proc.stdout[-4000:],
            "stderr": proc.stderr[-4000:],
        }
    )

    return 0 if status in {"done", "requeued"} else proc.returncode


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--max-jobs", type=int, default=20, help="Max jobs to process in one run")
    parser.add_argument("--dry-run", action="store_true", help="Run worker in dry-run mode")
    args = parser.parse_args()

    try:
        with queue_lock():
            pending_jobs = _discover_pending_jobs(args.max_jobs)
            if not pending_jobs:
                return 0

            failures = 0
            for job in pending_jobs:
                rc = run_one(job, dry_run=args.dry_run)
                if rc != 0:
                    failures += 1

            return 1 if failures else 0
    except QueueLockError as err:
        print(str(err), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
