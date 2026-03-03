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
from typing import Any


@dataclass
class RollbackTarget:
    title: str
    user: str


class RollbackCommandError(Exception):
    """Raised when a rollback command payload is malformed."""


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


def execute_rollback_command(payload: dict[str, Any], dry_run: bool = False) -> int:
    if payload.get("command") != "rollback":
        raise RollbackCommandError("Unsupported command. Expected command='rollback'")

    max_per_minute = payload.get("maxRollbacksPerMinute", 10)
    if not isinstance(max_per_minute, int) or max_per_minute <= 0:
        max_per_minute = 10

    summary = payload.get("editSummary")
    if not isinstance(summary, str):
        summary = None

    targets = _parse_targets(payload)

    delay_seconds = 60.0 / max_per_minute
    success_count = 0

    if dry_run:
        for i, target in enumerate(targets):
            print(f"[DRY-RUN] Would rollback {target.title} by {target.user}")
            success_count += 1
            if i < len(targets) - 1:
                time.sleep(delay_seconds)
        return success_count

    import pywikibot

    site = pywikibot.Site()
    for i, target in enumerate(targets):
        page = pywikibot.Page(site, target.title)
        site.rollbackpage(page, target.user, summary=summary, markbot=True)
        pywikibot.info(f"Rolled back {target.title} by {target.user}")
        success_count += 1

        if i < len(targets) - 1:
            time.sleep(delay_seconds)

    return success_count


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--command-file", help="Path to JSON command payload")
    parser.add_argument("--command-json", help="JSON command payload string")
    parser.add_argument("--dry-run", action="store_true", help="Validate and print actions without calling API")
    args = parser.parse_args()

    try:
        payload = _load_command(args)
        completed = execute_rollback_command(payload, dry_run=args.dry_run)
        print(f"Completed {completed} rollback request(s).")
        return 0
    except (json.JSONDecodeError, OSError, RollbackCommandError, ImportError) as err:
        print(str(err), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
