#!/usr/bin/env python3
"""Hardened Toolforge ingress API for queued rollback requests.

Security model (default):
- Trust identity from a proxy-injected header (e.g. OAuth-authenticated web endpoint).
- Require strict freshness + unique request IDs (anti-replay).
- Validate payload schema and queue bounds before writing jobs.
- Write append-only JSONL audit trail for accepted/rejected requests.

Bearer-token mode exists for non-proxy environments, but is intentionally explicit.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from flask import Flask, jsonify, request

APP = Flask(__name__)

REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9._:-]{16,128}$")
WIKI_RE = re.compile(r"^[a-z0-9_]{2,32}$")
USER_RE = re.compile(r"^[^\n\r\t]{1,85}$")


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _parse_csv_set(value: str) -> frozenset[str]:
    return frozenset(item.strip() for item in value.split(",") if item.strip())


def _load_token_map() -> dict[str, str]:
    """Return mapping of bearer token -> authenticated username.

    Sources:
    - CTB_API_TOKEN_MAP_JSON='{"token":"Username"}'
    - CTB_API_TOKEN_MAP_FILE with one `token:username` per line
    """

    token_map: dict[str, str] = {}

    inline_json = os.environ.get("CTB_API_TOKEN_MAP_JSON", "").strip()
    if inline_json:
        try:
            parsed = json.loads(inline_json)
            if isinstance(parsed, dict):
                for token, username in parsed.items():
                    if isinstance(token, str) and isinstance(username, str):
                        token, username = token.strip(), username.strip()
                        if token and username:
                            token_map[token] = username
        except json.JSONDecodeError:
            return {}

    path = os.environ.get("CTB_API_TOKEN_MAP_FILE", "").strip()
    if path:
        try:
            with open(path, "r", encoding="utf-8") as handle:
                for raw_line in handle:
                    line = raw_line.strip()
                    if not line or line.startswith("#"):
                        continue
                    if ":" not in line:
                        continue
                    token, username = line.split(":", 1)
                    token, username = token.strip(), username.strip()
                    if token and username:
                        token_map[token] = username
        except OSError:
            # Fail closed if explicitly configured and unreadable.
            return {}

    return token_map


@dataclass(frozen=True)
class Settings:
    data_dir: Path = Path(os.environ.get("CTB_DATA_DIR", "./data"))
    auth_mode: str = os.environ.get("CTB_AUTH_MODE", "forwarded_user").strip().lower()
    forwarded_user_header: str = os.environ.get("CTB_FORWARDED_USER_HEADER", "X-Forwarded-User").strip()
    api_token_map: dict[str, str] = None  # set below

    allowed_wikis: frozenset[str] = _parse_csv_set(os.environ.get("CTB_ALLOWED_WIKIS", "enwiki"))
    allowed_requesters: frozenset[str] = _parse_csv_set(os.environ.get("CTB_ALLOWED_REQUESTERS", ""))

    clock_skew_seconds: int = int(os.environ.get("CTB_CLOCK_SKEW_SECONDS", "300"))
    request_ttl_seconds: int = int(os.environ.get("CTB_REQUEST_ID_TTL_SECONDS", "86400"))

    max_targets: int = int(os.environ.get("CTB_MAX_TARGETS", "10000"))
    max_rate_per_minute: int = int(os.environ.get("CTB_MAX_RATE_PER_MIN", "60"))
    max_summary_chars: int = int(os.environ.get("CTB_MAX_SUMMARY_CHARS", "500"))

    hmac_secret: str = os.environ.get("CTB_HMAC_SECRET", "").strip()
    require_hmac: bool = _env_bool("CTB_REQUIRE_HMAC", False)

    require_user_agent: bool = _env_bool("CTB_REQUIRE_USER_AGENT", True)
    user_agent_min_len: int = int(os.environ.get("CTB_USER_AGENT_MIN_LEN", "12"))


SETTINGS = Settings(api_token_map=_load_token_map())


def _init_paths(base: Path) -> dict[str, Path]:
    paths = {
        "pending": base / "queue" / "pending",
        "logs": base / "logs",
        "db": base / "request_guard.sqlite3",
    }
    for key in ("pending", "logs"):
        paths[key].mkdir(parents=True, exist_ok=True)
    return paths


PATHS = _init_paths(SETTINGS.data_dir)


def _json_error(status: int, message: str):
    return jsonify({"ok": False, "error": message}), status


def _audit(event: str, *, status: str, detail: str, request_id: str | None = None, user: str | None = None) -> None:
    record = {
        "event": event,
        "status": status,
        "detail": detail,
        "requestId": request_id,
        "user": user,
        "remoteAddr": request.headers.get("X-Forwarded-For", request.remote_addr or ""),
        "userAgent": request.headers.get("User-Agent", ""),
        "timestamp": int(time.time()),
    }
    with (PATHS["logs"] / "rollback_requests.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def _db() -> sqlite3.Connection:
    conn = sqlite3.connect(PATHS["db"])
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS request_ids (
          request_id TEXT PRIMARY KEY,
          created_at INTEGER NOT NULL
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_request_ids_created_at ON request_ids(created_at)")
    return conn


def _reserve_request_id(request_id: str) -> bool:
    now = int(time.time())
    with _db() as conn:
        try:
            conn.execute("INSERT INTO request_ids(request_id, created_at) VALUES (?, ?)", (request_id, now))
            conn.execute("DELETE FROM request_ids WHERE created_at < ?", (now - SETTINGS.request_ttl_seconds,))
            conn.commit()
            return True
        except sqlite3.IntegrityError:
            return False


def _extract_bearer() -> str | None:
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return None
    token = auth[7:].strip()
    return token or None


def _verify_hmac(raw_body: bytes) -> bool:
    signature_header = request.headers.get("X-CTB-Signature", "").strip()

    if not SETTINGS.require_hmac:
        return True

    if not SETTINGS.hmac_secret:
        return False

    if not signature_header:
        return False

    # Accept either raw hex or "sha256=<hex>".
    provided = signature_header.split("=", 1)[1] if signature_header.startswith("sha256=") else signature_header
    expected = hmac.new(SETTINGS.hmac_secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(provided, expected)


def _authenticate_user() -> tuple[str | None, str]:
    if SETTINGS.auth_mode == "forwarded_user":
        user = request.headers.get(SETTINGS.forwarded_user_header, "").strip()
        if not user:
            return None, f"missing {SETTINGS.forwarded_user_header}"
        if not USER_RE.fullmatch(user):
            return None, "malformed forwarded user"
        return user, "forwarded_user"

    if SETTINGS.auth_mode == "bearer":
        token = _extract_bearer()
        if not token:
            return None, "missing bearer token"
        user = SETTINGS.api_token_map.get(token)
        if not user:
            return None, "invalid bearer token"
        if not USER_RE.fullmatch(user):
            return None, "malformed mapped user"
        return user, "bearer"

    return None, "invalid CTB_AUTH_MODE"


def _validate_timestamp() -> tuple[bool, str]:
    raw = request.headers.get("X-CTB-Timestamp", "").strip()
    if not raw:
        return False, "missing X-CTB-Timestamp"
    try:
        ts = int(raw)
    except ValueError:
        return False, "invalid X-CTB-Timestamp"
    now = int(time.time())
    if abs(now - ts) > SETTINGS.clock_skew_seconds:
        return False, "stale X-CTB-Timestamp"
    return True, "ok"


def _validate_user_agent() -> bool:
    if not SETTINGS.require_user_agent:
        return True
    ua = request.headers.get("User-Agent", "")
    return len(ua.strip()) >= SETTINGS.user_agent_min_len


def _validate_request_id() -> tuple[str | None, str]:
    req_id = request.headers.get("X-CTB-Request-Id", "").strip()
    if not req_id:
        return None, "missing X-CTB-Request-Id"
    if not REQUEST_ID_RE.fullmatch(req_id):
        return None, "invalid X-CTB-Request-Id format"
    return req_id, "ok"


def _validate_payload(payload: Any, authenticated_user: str) -> tuple[dict[str, Any] | None, str]:
    if not isinstance(payload, dict):
        return None, "JSON body must be an object"

    if payload.get("command") != "rollback":
        return None, "Only command='rollback' is supported"

    wiki = payload.get("wiki")
    if not isinstance(wiki, str) or not WIKI_RE.fullmatch(wiki):
        return None, "wiki must be a valid wiki database name"
    if SETTINGS.allowed_wikis and wiki not in SETTINGS.allowed_wikis:
        return None, "wiki is not allowed"

    claimed_requester = payload.get("requestedBy")
    if claimed_requester is not None:
        if not isinstance(claimed_requester, str) or not USER_RE.fullmatch(claimed_requester.strip()):
            return None, "requestedBy is malformed"
        if claimed_requester.strip() != authenticated_user:
            return None, "requestedBy does not match authenticated user"

    requested_by = authenticated_user
    if SETTINGS.allowed_requesters and requested_by not in SETTINGS.allowed_requesters:
        return None, "authenticated user is not allowed"

    rpm = payload.get("maxRollbacksPerMinute", 10)
    if not isinstance(rpm, int) or rpm < 1 or rpm > SETTINGS.max_rate_per_minute:
        return None, f"maxRollbacksPerMinute must be 1..{SETTINGS.max_rate_per_minute}"

    max_lag = payload.get("maxLag", payload.get("maxlag", 5))
    if not isinstance(max_lag, int) or max_lag < 0 or max_lag > 30:
        return None, "maxLag must be an integer between 0 and 30"

    summary = payload.get("editSummary")
    if summary is not None and (not isinstance(summary, str) or len(summary) > SETTINGS.max_summary_chars):
        return None, f"editSummary must be <= {SETTINGS.max_summary_chars} chars"

    targets = payload.get("targets")
    if not isinstance(targets, list) or not targets:
        return None, "targets must be a non-empty list"
    if len(targets) > SETTINGS.max_targets:
        return None, f"targets exceeds max allowed ({SETTINGS.max_targets})"

    normalized_targets: list[dict[str, str]] = []
    seen_pairs: set[tuple[str, str]] = set()
    for item in targets:
        if not isinstance(item, dict):
            return None, "each target must be an object"
        title = item.get("title")
        user = item.get("user")
        if not isinstance(title, str) or not title.strip() or len(title) > 512:
            return None, "target.title must be 1..512 chars"
        if not isinstance(user, str) or not USER_RE.fullmatch(user.strip()):
            return None, "target.user is missing or malformed"

        normalized = (title.strip(), user.strip())
        if normalized in seen_pairs:
            continue
        seen_pairs.add(normalized)
        normalized_targets.append({"title": normalized[0], "user": normalized[1]})

    if not normalized_targets:
        return None, "targets list has no valid entries"

    sanitized = {
        "command": "rollback",
        "wiki": wiki,
        "requestedBy": requested_by,
        "claimedRequester": claimed_requester.strip() if isinstance(claimed_requester, str) else None,
        "maxRollbacksPerMinute": rpm,
        "maxLag": max_lag,
        "editSummary": summary,
        "targets": normalized_targets,
    }
    return sanitized, "ok"


@APP.get("/healthz")
def healthz():
    return jsonify(
        {
            "ok": True,
            "service": "buckbot-queue-api",
            "authMode": SETTINGS.auth_mode,
            "requireHmac": SETTINGS.require_hmac,
            "maxTargets": SETTINGS.max_targets,
        }
    )


@APP.post("/rollback-requests")
def rollback_requests():
    raw_body = request.get_data(cache=False)

    if not _validate_user_agent():
        _audit("enqueue", status="denied", detail="invalid user-agent")
        return _json_error(400, "User-Agent header is required and too short/missing")

    auth_user, auth_detail = _authenticate_user()
    if not auth_user:
        _audit("enqueue", status="denied", detail=auth_detail)
        return _json_error(401, "authentication failed")

    ts_ok, ts_detail = _validate_timestamp()
    if not ts_ok:
        _audit("enqueue", status="denied", detail=ts_detail, user=auth_user)
        return _json_error(401, ts_detail)

    if not _verify_hmac(raw_body):
        _audit("enqueue", status="denied", detail="hmac verification failed", user=auth_user)
        return _json_error(401, "invalid signature")

    request_id, reqid_detail = _validate_request_id()
    if not request_id:
        _audit("enqueue", status="denied", detail=reqid_detail, user=auth_user)
        return _json_error(400, reqid_detail)

    if not _reserve_request_id(request_id):
        _audit("enqueue", status="duplicate", detail="request id already seen", request_id=request_id, user=auth_user)
        return _json_error(409, "duplicate request id")

    try:
        payload = json.loads(raw_body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        _audit("enqueue", status="denied", detail="invalid json", request_id=request_id, user=auth_user)
        return _json_error(400, "Body must be valid UTF-8 JSON")

    sanitized_payload, validation_detail = _validate_payload(payload, auth_user)
    if not sanitized_payload:
        _audit("enqueue", status="denied", detail=validation_detail, request_id=request_id, user=auth_user)
        return _json_error(400, validation_detail)

    envelope = {
        "requestId": request_id,
        "receivedAt": int(time.time()),
        "auth": {
            "mode": SETTINGS.auth_mode,
            "authenticatedUser": auth_user,
        },
        "requestMeta": {
            "userAgent": request.headers.get("User-Agent", ""),
            "remoteAddr": request.headers.get("X-Forwarded-For", request.remote_addr or ""),
        },
        "payload": sanitized_payload,
    }

    pending_file = PATHS["pending"] / f"{request_id}.json"
    try:
        with pending_file.open("x", encoding="utf-8") as handle:
            json.dump(envelope, handle, ensure_ascii=False)
    except FileExistsError:
        _audit("enqueue", status="duplicate", detail="pending file already exists", request_id=request_id, user=auth_user)
        return _json_error(409, "duplicate request id")

    _audit(
        "enqueue",
        status="accepted",
        detail=f"targets={len(sanitized_payload['targets'])}; auth={auth_detail}",
        request_id=request_id,
        user=auth_user,
    )

    return jsonify({"ok": True, "queued": True, "requestId": request_id, "targetCount": len(sanitized_payload["targets"])})


if __name__ == "__main__":
    APP.run(host="0.0.0.0", port=8000)
