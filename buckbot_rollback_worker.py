#!/usr/bin/env python3
"""Execute queued rollback commands using a Pywikibot account.

This worker is intended to run under the chuckbot account and consume JSON
rollback commands produced by the "Massrollback chuckbot" userscript.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class RollbackTarget:
    title: str
    user: str


class RollbackCommandError(Exception):
    """Raised when a rollback command payload is malformed."""


@dataclass
class RollbackFailure:
    index: int
    title: str
    user: str
    error: str


@dataclass
class RollbackExecutionResult:
    success_count: int
    failure_count: int
    failures: list[RollbackFailure]
    start_index: int
    processed_count: int


def _load_command(args: argparse.Namespace) -> dict[str, Any]:
    if args.command_json:
        return json.loads(args.command_json)

    if args.command_file:
        with open(args.command_file, "r", encoding="utf-8") as f:
            return json.load(f)

    return json.load(sys.stdin)


def _parse_targets(payload: dict[str, Any]) -> list[RollbackTarget]:
    raw_targets = payload.get("targets")
    if not isinstance(raw_targets, list) or not raw_targets:
        raise RollbackCommandError("Command must contain a non-empty 'targets' list")

    targets: list[RollbackTarget] = []
    for item in raw_targets:
        if not isinstance(item, dict):
            continue
        title = item.get("title")
        user = item.get("user")
        if isinstance(title, str) and isinstance(user, str) and title and user:
            targets.append(RollbackTarget(title=title, user=user))

    if not targets:
        raise RollbackCommandError("No valid rollback targets were found")

    return targets


def _write_progress(
    progress_path: Path,
    *,
    start_index: int,
    processed_count: int,
    success_count: int,
    failures: list[RollbackFailure],
) -> None:
    last_processed_index = start_index + processed_count - 1 if processed_count else start_index - 1
    checkpoint = {
        "startIndex": start_index,
        "processedCount": processed_count,
        "lastProcessedIndex": last_processed_index,
        "successCount": success_count,
        "failureCount": len(failures),
        "failures": [failure.__dict__ for failure in failures],
        "updatedAt": int(time.time()),
    }
    progress_path.parent.mkdir(parents=True, exist_ok=True)
    progress_path.write_text(json.dumps(checkpoint, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def execute_rollback_command(
    payload: dict[str, Any],
    dry_run: bool = False,
    progress_file: str | None = None,
    start_index_override: int | None = None,
) -> RollbackExecutionResult:
    if payload.get("command") != "rollback":
        raise RollbackCommandError("Unsupported command. Expected command='rollback'")

    max_per_minute = payload.get("maxRollbacksPerMinute", 10)
    if not isinstance(max_per_minute, int) or max_per_minute <= 0:
        max_per_minute = 10

    summary = payload.get("editSummary")
    if not isinstance(summary, str):
        summary = None

    targets = _parse_targets(payload)

    start_index = payload.get("startIndex", 0)
    if not isinstance(start_index, int) or start_index < 0:
        raise RollbackCommandError("startIndex must be a non-negative integer")
    if start_index_override is not None:
        if start_index_override < 0:
            raise RollbackCommandError("--start-index must be a non-negative integer")
        start_index = start_index_override

    selected_targets = targets[start_index:]

    delay_seconds = 60.0 / max_per_minute
    success_count = 0
    failures: list[RollbackFailure] = []
    processed_count = 0
    progress_path = Path(progress_file) if progress_file else None

    def checkpoint() -> None:
        if progress_path:
            _write_progress(
                progress_path,
                start_index=start_index,
                processed_count=processed_count,
                success_count=success_count,
                failures=failures,
            )

    if dry_run:
        for i, target in enumerate(selected_targets, start=start_index):
            print(f"[DRY-RUN] Would rollback {target.title} by {target.user}")
            success_count += 1
            processed_count += 1
            if processed_count % 50 == 0:
                checkpoint()
        checkpoint()
        return RollbackExecutionResult(
            success_count=success_count,
            failure_count=0,
            failures=[],
            start_index=start_index,
            processed_count=processed_count,
        )

    import pywikibot

    site = pywikibot.Site()
    for i, target in enumerate(selected_targets, start=start_index):
        try:
            page = pywikibot.Page(site, target.title)
            site.rollbackpage(page, target.user, summary=summary, markbot=True)
            pywikibot.info(f"Rolled back {target.title} by {target.user}")
            success_count += 1
        except Exception as err:  # noqa: BLE001 - keep worker running for bulk operations
            failures.append(RollbackFailure(index=i, title=target.title, user=target.user, error=str(err)))
            pywikibot.error(f"Failed rollback {target.title} by {target.user}: {err}")

        processed_count += 1
        if processed_count % 50 == 0:
            checkpoint()

        if processed_count < len(selected_targets):
            time.sleep(delay_seconds)

    checkpoint()

    return RollbackExecutionResult(
        success_count=success_count,
        failure_count=len(failures),
        failures=failures,
        start_index=start_index,
        processed_count=processed_count,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--command-file", help="Path to JSON command payload")
    parser.add_argument("--command-json", help="JSON command payload string")
    parser.add_argument("--dry-run", action="store_true", help="Validate and print actions without calling API")
    parser.add_argument("--start-index", type=int, help="Override payload startIndex and resume from this target offset")
    parser.add_argument("--progress-file", help="Write JSON checkpoints every 50 processed targets")
    args = parser.parse_args()

    try:
        payload = _load_command(args)
        result = execute_rollback_command(
            payload,
            dry_run=args.dry_run,
            progress_file=args.progress_file,
            start_index_override=args.start_index,
        )
        print(
            "Completed "
            f"{result.success_count} rollback request(s); "
            f"{result.failure_count} failure(s) from index {result.start_index}."
        )
        if result.failure_count:
            print(json.dumps({"failures": [f.__dict__ for f in result.failures]}, ensure_ascii=False), file=sys.stderr)
            return 2
        return 0
    except (json.JSONDecodeError, OSError, RollbackCommandError, ImportError) as err:
        print(str(err), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
