#!/usr/bin/env python3
"""Basic policy-hook verification for chuckbot Toolforge deployment."""

from __future__ import annotations

import os
import sys

REQUIRED_ENV = [
    "CTB_DATA_DIR",
    "CTB_ALLOWED_WIKIS",
    "CTB_ALLOWED_REQUESTERS",
    "CTB_API_TOKENS",
]

OPTIONAL_STRONGLY_RECOMMENDED = [
    "CTB_HMAC_SECRET",
]


def main() -> int:
    missing = [k for k in REQUIRED_ENV if not os.environ.get(k)]
    weak = [k for k in OPTIONAL_STRONGLY_RECOMMENDED if not os.environ.get(k)]

    if missing:
        print("Missing required configuration variables:", ", ".join(missing))
        print("Deployment is NOT compliant until required auth/allowlist hooks are configured.")
        return 1

    print("Required configuration hooks are present.")
    if weak:
        print("Warning: missing strongly-recommended hardening:", ", ".join(weak))
    else:
        print("Strong hardening hooks are configured.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
