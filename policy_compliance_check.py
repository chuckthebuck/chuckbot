#!/usr/bin/env python3
"""Basic policy-hook verification for chuckbot Toolforge deployment."""

from __future__ import annotations

import os

REQUIRED_ENV = [
    "CTB_DATA_DIR",
    "CTB_ALLOWED_WIKIS",
    "CTB_ALLOWED_REQUESTERS",
]


def _check_auth_mode() -> tuple[bool, str]:
    mode = os.environ.get("CTB_AUTH_MODE", "forwarded_user").strip().lower()
    if mode == "forwarded_user":
        header = os.environ.get("CTB_FORWARDED_USER_HEADER", "X-Forwarded-User").strip()
        return (bool(header), "forwarded_user mode requires CTB_FORWARDED_USER_HEADER")

    if mode == "bearer":
        inline = os.environ.get("CTB_API_TOKEN_MAP_JSON", "").strip()
        file_path = os.environ.get("CTB_API_TOKEN_MAP_FILE", "").strip()
        has_map = bool(inline or file_path)
        return (has_map, "bearer mode requires CTB_API_TOKEN_MAP_JSON or CTB_API_TOKEN_MAP_FILE")

    return (False, "CTB_AUTH_MODE must be 'forwarded_user' or 'bearer'")


def main() -> int:
    missing = [name for name in REQUIRED_ENV if not os.environ.get(name)]
    if missing:
        print("Missing required configuration variables:", ", ".join(missing))
        return 1

    auth_ok, auth_message = _check_auth_mode()
    if not auth_ok:
        print(f"Auth configuration check failed: {auth_message}")
        return 1

    require_hmac = os.environ.get("CTB_REQUIRE_HMAC", "0").strip() in {"1", "true", "yes", "on"}
    if require_hmac and not os.environ.get("CTB_HMAC_SECRET", "").strip():
        print("Auth configuration check failed: CTB_REQUIRE_HMAC=1 requires CTB_HMAC_SECRET")
        return 1

    print("Required configuration hooks are present.")
    if not os.environ.get("CTB_HMAC_SECRET", "").strip():
        print("Warning: CTB_HMAC_SECRET is not set (acceptable for userscript-only deployments).")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
