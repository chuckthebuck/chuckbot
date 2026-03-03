#!/usr/bin/env python3
"""Basic policy-hook verification for chuckbot Toolforge deployment."""

from __future__ import annotations

import os
import sys

REQUIRED_ENV = [
    "CTB_DATA_DIR",
    "CTB_ALLOWED_WIKIS",
    "CTB_ALLOWED_REQUESTERS",
]

OPTIONAL_STRONGLY_RECOMMENDED = [
    "CTB_HMAC_SECRET",
]


def main() -> int:
    missing = [k for k in REQUIRED_ENV if not os.environ.get(k)]
    weak = [k for k in OPTIONAL_STRONGLY_RECOMMENDED if not os.environ.get(k)]
    token_inline = os.environ.get("CTB_API_TOKENS", "").strip()
    token_file = os.environ.get("CTB_API_TOKENS_FILE", "").strip()

    if missing:
        print("Missing required configuration variables:", ", ".join(missing))
        print("Deployment is NOT compliant until required auth/allowlist hooks are configured.")
        return 1

    if not token_inline and not token_file:
        print("Missing token configuration: set CTB_API_TOKENS or CTB_API_TOKENS_FILE")
        return 1

    print("Required configuration hooks are present.")
    if weak:
        print("Warning: missing strongly-recommended hardening:", ", ".join(weak))
    else:
        print("Strong hardening hooks are configured.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
