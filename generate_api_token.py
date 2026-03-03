#!/usr/bin/env python3
"""Generate API tokens for CTB_API_TOKENS / CTB_API_TOKENS_FILE.

Uses `secrets` as the primary entropy source and optionally mixes UUID+time
material before hashing to produce a URL-safe token string.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import os
import secrets
import time
import uuid
from pathlib import Path


def generate_token(length: int = 32, mix_time_uuid: bool = True) -> str:
    seed = secrets.token_bytes(length)
    if mix_time_uuid:
        extra = f"{uuid.uuid4()}:{time.time_ns()}:{os.getpid()}".encode("utf-8")
        seed = hashlib.sha256(seed + extra).digest()
    return base64.urlsafe_b64encode(seed).decode("ascii").rstrip("=")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--count", type=int, default=1, help="Number of tokens to generate")
    parser.add_argument("--bytes", type=int, default=32, help="Seed bytes per token")
    parser.add_argument("--no-mix", action="store_true", help="Do not mix UUID/time material")
    parser.add_argument("--output-file", help="Append generated tokens to file (one token per line)")
    args = parser.parse_args()

    tokens = [generate_token(length=args.bytes, mix_time_uuid=not args.no_mix) for _ in range(args.count)]

    if args.output_file:
        path = Path(args.output_file)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            for token in tokens:
                f.write(token + "\n")
        os.chmod(path, 0o600)

    for token in tokens:
        print(token)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
