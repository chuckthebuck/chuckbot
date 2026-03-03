#!/usr/bin/env python3
"""Toolforge queue ingress API for Buckbot rollback requests.

Run as a Toolforge webservice (python/flask). Receives signed/authenticated
rollback requests from the userscript and writes queued job files for offline
execution by chuckbot's Pywikibot worker.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import sqlite3
import time
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import redis
from flask import Flask, jsonify, request


APP = Flask(__name__)

try:
    import pymysql
except ImportError:  # optional dependency
    pymysql = None

try:
    import redis
except ImportError:  # optional dependency
    redis = None




def _load_tokens() -> frozenset[str]:
    inline = [x.strip() for x in os.environ.get("CTB_API_TOKENS", "").split(",") if x.strip()]
    token_file = os.environ.get("CTB_API_TOKENS_FILE", "").strip()
    file_tokens: list[str] = []

    if token_file:
        try:
            with open(token_file, "r", encoding="utf-8") as f:
                for line in f:
                    token = line.strip()
                    if token and not token.startswith("#"):
                        file_tokens.append(token)
        except OSError:
            # Fail closed if file was configured but unreadable.
            return frozenset()

    return frozenset(inline + file_tokens)

@dataclass(frozen=True)
class Settings:
    data_dir: Path = Path(os.environ.get("CTB_DATA_DIR", "./data"))
    max_targets: int = int(os.environ.get("CTB_MAX_TARGETS", "100000"))
    max_rate_per_minute: int = int(os.environ.get("CTB_MAX_RATE_PER_MIN", "60"))
    allowed_wikis: frozenset[str] = frozenset(
        x.strip() for x in os.environ.get("CTB_ALLOWED_WIKIS", "enwiki").split(",") if x.strip()
    )
    allowed_requesters: frozenset[str] = frozenset(
        x.strip() for x in os.environ.get("CTB_ALLOWED_REQUESTERS", "").split(",") if x.strip()
    )
    api_tokens: frozenset[str] = _load_tokens()
    forwarded_user_header: str = os.environ.get("CTB_FORWARDED_USER_HEADER", "X-Forwarded-User")
    require_requester_match: bool = os.environ.get("CTB_REQUIRE_REQUESTER_MATCH", "1") == "1"
    clock_skew_seconds: int = int(os.environ.get("CTB_CLOCK_SKEW_SECONDS", "300"))
    hmac_secret: str | None = os.environ.get("CTB_HMAC_SECRET")
    redis_url: str = os.environ.get("CTB_REDIS_URL", "").strip()
    request_id_ttl_seconds: int = int(os.environ.get("CTB_REQUEST_ID_TTL_SECONDS", "86400"))
    request_guard_backend: str = os.environ.get("CTB_REQUEST_GUARD_BACKEND", "sqlite").strip().lower()
    redis_url: str = os.environ.get("CTB_REDIS_URL", "redis://127.0.0.1:6379/0")
    redis_request_ttl_seconds: int = int(os.environ.get("CTB_REDIS_REQUEST_ID_TTL", "86400"))
    toolsdb_host: str = os.environ.get("CTB_TOOLSDB_HOST", "tools-db")
    toolsdb_port: int = int(os.environ.get("CTB_TOOLSDB_PORT", "3306"))
    toolsdb_name: str = os.environ.get("CTB_TOOLSDB_NAME", "tools")
    toolsdb_user: str = os.environ.get("CTB_TOOLSDB_USER", "")
    toolsdb_password: str = os.environ.get("CTB_TOOLSDB_PASSWORD", "")
    toolsdb_table: str = os.environ.get("CTB_TOOLSDB_REQUEST_TABLE", "ctb_request_ids")


SETTINGS = Settings()


def _init_paths() -> dict[str, Path]:
    base = SETTINGS.data_dir
    paths = {
        "base": base,
        "pending": base / "queue" / "pending",
        "processing": base / "queue" / "processing",
        "done": base / "queue" / "done",
        "failed": base / "queue" / "failed",
        "logs": base / "logs",
        "db": base / "request_guard.sqlite3",
    }
    for key in ("pending", "processing", "done", "failed", "logs"):
        paths[key].mkdir(parents=True, exist_ok=True)
    return paths


PATHS = _init_paths()


class ReplayGuard:
    def reserve(self, request_id: str) -> bool:
        raise NotImplementedError


class SQLiteReplayGuard(ReplayGuard):
    def reserve(self, request_id: str) -> bool:
        created_at = int(time.time())
        with _db() as conn:
            try:
                conn.execute("INSERT INTO request_ids(request_id, created_at) VALUES (?, ?)", (request_id, created_at))
                conn.execute(
                    "DELETE FROM request_ids WHERE created_at < ?",
                    (created_at - SETTINGS.request_id_ttl_seconds,),
                )
                conn.commit()
                return True
            except sqlite3.IntegrityError:
                return False


class RedisReplayGuard(ReplayGuard):
    def __init__(self, redis_url: str, ttl_seconds: int):
        self.redis = redis.Redis.from_url(redis_url, decode_responses=True)
        self.ttl_seconds = ttl_seconds

    def reserve(self, request_id: str) -> bool:
        key = f"ctb:reqid:{request_id}"
        return bool(self.redis.set(key, "1", nx=True, ex=self.ttl_seconds))


def _build_replay_guard() -> ReplayGuard:
    if SETTINGS.redis_url:
        return RedisReplayGuard(SETTINGS.redis_url, SETTINGS.request_id_ttl_seconds)
    return SQLiteReplayGuard()


REPLAY_GUARD = _build_replay_guard()


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


class RequestGuardStore(ABC):
    @abstractmethod
    def reserve_request_id(self, request_id: str, created_at: int) -> bool:
        raise NotImplementedError


class SQLiteRequestGuardStore(RequestGuardStore):
    def reserve_request_id(self, request_id: str, created_at: int) -> bool:
        with _db() as conn:
            try:
                conn.execute("INSERT INTO request_ids(request_id, created_at) VALUES (?, ?)", (request_id, created_at))
                conn.execute("DELETE FROM request_ids WHERE created_at < ?", (created_at - 86400,))
                conn.commit()
                return True
            except sqlite3.IntegrityError:
                return False


class RedisRequestGuardStore(RequestGuardStore):
    def __init__(self) -> None:
        if redis is None:
            raise RuntimeError("CTB_REQUEST_GUARD_BACKEND=redis requires the 'redis' package")
        self.client = redis.Redis.from_url(SETTINGS.redis_url, decode_responses=True)

    def reserve_request_id(self, request_id: str, created_at: int) -> bool:
        key = f"ctb:request-id:{request_id}"
        return bool(self.client.set(key, str(created_at), nx=True, ex=SETTINGS.redis_request_ttl_seconds))


class ToolsDBRequestGuardStore(RequestGuardStore):
    def __init__(self) -> None:
        if pymysql is None:
            raise RuntimeError("CTB_REQUEST_GUARD_BACKEND=toolsdb requires the 'pymysql' package")
        if not SETTINGS.toolsdb_user:
            raise RuntimeError("CTB_TOOLSDB_USER is required for CTB_REQUEST_GUARD_BACKEND=toolsdb")
        if not re.fullmatch(r"[A-Za-z0-9_]+", SETTINGS.toolsdb_table):
            raise RuntimeError("CTB_TOOLSDB_REQUEST_TABLE must match [A-Za-z0-9_]+")

    def _conn(self):
        return pymysql.connect(
            host=SETTINGS.toolsdb_host,
            port=SETTINGS.toolsdb_port,
            user=SETTINGS.toolsdb_user,
            password=SETTINGS.toolsdb_password,
            database=SETTINGS.toolsdb_name,
            autocommit=False,
            charset="utf8mb4",
        )

    def reserve_request_id(self, request_id: str, created_at: int) -> bool:
        table = SETTINGS.toolsdb_table
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    CREATE TABLE IF NOT EXISTS `{table}` (
                        request_id VARCHAR(255) PRIMARY KEY,
                        created_at BIGINT NOT NULL,
                        INDEX idx_created_at (created_at)
                    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
                    """
                )
                cur.execute(
                    f"INSERT IGNORE INTO `{table}` (request_id, created_at) VALUES (%s, %s)",
                    (request_id, created_at),
                )
                inserted = cur.rowcount == 1
                if inserted:
                    cur.execute(f"DELETE FROM `{table}` WHERE created_at < %s", (created_at - 86400,))
                conn.commit()
                return inserted


def _request_guard_store() -> RequestGuardStore:
    backend = SETTINGS.request_guard_backend
    if backend == "sqlite":
        return SQLiteRequestGuardStore()
    if backend == "redis":
        return RedisRequestGuardStore()
    if backend == "toolsdb":
        return ToolsDBRequestGuardStore()
    raise RuntimeError("CTB_REQUEST_GUARD_BACKEND must be one of: sqlite, redis, toolsdb")


REQUEST_GUARD_STORE = _request_guard_store()


def _json_error(status: int, message: str):
    return jsonify({"ok": False, "error": message}), status


def _extract_bearer_token() -> str | None:
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        return auth[7:]
    return None


def _authenticated_user() -> str | None:
    token = _extract_bearer_token()
    if token and token in SETTINGS.api_tokens:
        return request.headers.get("X-CTB-Requester") or "token-authenticated"

    forwarded_user = request.headers.get(SETTINGS.forwarded_user_header)
    if forwarded_user:
        return forwarded_user

    return None


def _verify_hmac(body: bytes) -> bool:
    if not SETTINGS.hmac_secret:
        return True
    signature = request.headers.get("X-CTB-Signature")
    if not signature:
        return False
    expected = hmac.new(SETTINGS.hmac_secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(signature, expected)


def _verify_timestamp() -> bool:
    ts = request.headers.get("X-CTB-Timestamp")
    if not ts:
        return False
    try:
        ts_int = int(ts)
    except ValueError:
        return False
    now = int(time.time())
    return abs(now - ts_int) <= SETTINGS.clock_skew_seconds


def _reserve_request_id(request_id: str) -> bool:
    created_at = int(time.time())
    return REQUEST_GUARD_STORE.reserve_request_id(request_id, created_at)


def _validate_payload(payload: dict[str, Any]) -> str | None:
    if payload.get("command") != "rollback":
        return "Only command='rollback' is supported"

    wiki = payload.get("wiki")
    if not isinstance(wiki, str) or wiki not in SETTINGS.allowed_wikis:
        return "Wiki is missing or not allowed"

    requested_by = payload.get("requestedBy")
    if not isinstance(requested_by, str) or not requested_by:
        return "requestedBy is required"

    if SETTINGS.allowed_requesters and requested_by not in SETTINGS.allowed_requesters:
        return "Requester is not in allowlist"

    max_rate = payload.get("maxRollbacksPerMinute", 10)
    if not isinstance(max_rate, int) or max_rate <= 0 or max_rate > SETTINGS.max_rate_per_minute:
        return f"maxRollbacksPerMinute must be 1-{SETTINGS.max_rate_per_minute}"

    targets = payload.get("targets")
    if not isinstance(targets, list) or not targets:
        return "targets must be a non-empty list"
    if len(targets) > SETTINGS.max_targets:
        return f"targets exceeds max allowed ({SETTINGS.max_targets})"

    for target in targets:
        if not isinstance(target, dict):
            return "each target must be an object"
        title = target.get("title")
        user = target.get("user")
        if not isinstance(title, str) or not title.strip():
            return "target.title must be a non-empty string"
        if not isinstance(user, str) or not user.strip():
            return "target.user must be a non-empty string"

    summary = payload.get("editSummary")
    if summary is not None and (not isinstance(summary, str) or len(summary) > 500):
        return "editSummary must be <= 500 chars"

    return None


def _append_audit(entry: dict[str, Any]) -> None:
    audit_file = PATHS["logs"] / "rollback_requests.jsonl"
    with audit_file.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


@APP.get("/healthz")
def healthz():
    replay_guard = "redis" if SETTINGS.redis_url else "sqlite"
    return jsonify({"ok": True, "service": "buckbot-queue-api", "replayGuard": replay_guard})


@APP.post("/rollback-requests")
def rollback_requests():
    raw_body = request.get_data(cache=False)

    auth_user = _authenticated_user()
    if not auth_user:
        return _json_error(401, "Authentication required (Bearer token or trusted forwarded user)")

    if not _verify_timestamp():
        return _json_error(401, "Invalid or stale X-CTB-Timestamp")

    if not _verify_hmac(raw_body):
        return _json_error(401, "Invalid X-CTB-Signature")

    request_id = request.headers.get("X-CTB-Request-Id") or str(uuid.uuid4())
    if not REPLAY_GUARD.reserve(request_id):
        return _json_error(409, "Duplicate X-CTB-Request-Id")

    try:
        payload = json.loads(raw_body.decode("utf-8"))
    except json.JSONDecodeError:
        return _json_error(400, "Body must be valid JSON")

    if not isinstance(payload, dict):
        return _json_error(400, "JSON body must be an object")

    validation_error = _validate_payload(payload)
    if validation_error:
        return _json_error(400, validation_error)

    if SETTINGS.require_requester_match and payload.get("requestedBy") != auth_user:
        return _json_error(403, "requestedBy does not match authenticated identity")

    envelope = {
        "requestId": request_id,
        "authenticatedUser": auth_user,
        "receivedAt": int(time.time()),
        "payload": payload,
        "headers": {
            "User-Agent": request.headers.get("User-Agent", ""),
            "X-Forwarded-For": request.headers.get("X-Forwarded-For", ""),
        },
    }

    pending_file = PATHS["pending"] / f"{request_id}.json"
    with pending_file.open("x", encoding="utf-8") as f:
        json.dump(envelope, f, ensure_ascii=False)

    _append_audit(
        {
            "requestId": request_id,
            "status": "queued",
            "authenticatedUser": auth_user,
            "requestedBy": payload.get("requestedBy"),
            "wiki": payload.get("wiki"),
            "targetCount": len(payload.get("targets", [])),
            "timestamp": int(time.time()),
        }
    )

    return jsonify({"ok": True, "requestId": request_id, "queued": True})


if __name__ == "__main__":
    APP.run(host="0.0.0.0", port=8000)
