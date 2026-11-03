"""SQLite task queue with idempotency, fenced worker leases, and audit events."""
from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

DEFAULT_DB = Path("data/engine.db")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class TaskStore:
    def __init__(self, db_path: str | Path = DEFAULT_DB):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self):
        conn = sqlite3.connect(self.db_path, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def _initialize(self):
        with self._connect() as db:
            db.executescript("""
            CREATE TABLE IF NOT EXISTS tasks (
              id TEXT PRIMARY KEY, idempotency_key TEXT NOT NULL UNIQUE,
              task_type TEXT NOT NULL, title TEXT NOT NULL, payload_json TEXT NOT NULL,
              status TEXT NOT NULL CHECK(status IN ('pending','running','succeeded','failed','blocked')),
              attempts INTEGER NOT NULL DEFAULT 0, max_attempts INTEGER NOT NULL DEFAULT 2,
              created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
              result_json TEXT, error TEXT, lease_token TEXT, lease_expires_at TEXT,
              execution_mode TEXT NOT NULL DEFAULT 'unknown'
                CHECK(execution_mode IN ('unknown','not_started','dry_run','live_inference'))
            );
            CREATE INDEX IF NOT EXISTS idx_tasks_status_created ON tasks(status, created_at);
            CREATE TABLE IF NOT EXISTS audit_log (
              id INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT, event TEXT NOT NULL,
              details_json TEXT NOT NULL, created_at TEXT NOT NULL,
              FOREIGN KEY(task_id) REFERENCES tasks(id)
            );
            """)
            # Forward-compatible migration for databases created by runtime v1.
            columns = {row["name"] for row in db.execute("PRAGMA table_info(tasks)")}
            if "lease_token" not in columns:
                db.execute("ALTER TABLE tasks ADD COLUMN lease_token TEXT")
            if "lease_expires_at" not in columns:
                db.execute("ALTER TABLE tasks ADD COLUMN lease_expires_at TEXT")
            if "execution_mode" not in columns:
                db.execute("""ALTER TABLE tasks ADD COLUMN execution_mode TEXT NOT NULL
                    DEFAULT 'unknown' CHECK(execution_mode IN
                    ('unknown','not_started','dry_run','live_inference'))""")

    def _audit(self, db, task_id: str | None, event: str, details: dict[str, Any]):
        db.execute("INSERT INTO audit_log(task_id,event,details_json,created_at) VALUES(?,?,?,?)",
                   (task_id, event, json.dumps(details, sort_keys=True), utc_now()))

    def enqueue(self, task_type: str, title: str, payload: dict[str, Any] | None = None,
                idempotency_key: str | None = None, max_attempts: int = 2) -> dict[str, Any]:
        if not task_type.strip() or not title.strip():
            raise ValueError("task_type and title are required")
        if max_attempts < 1 or max_attempts > 5:
            raise ValueError("max_attempts must be between 1 and 5")
        key, task_id, now = idempotency_key or str(uuid.uuid4()), str(uuid.uuid4()), utc_now()
        with self._connect() as db:
            existing = db.execute("SELECT * FROM tasks WHERE idempotency_key=?", (key,)).fetchone()
            if existing:
                return dict(existing)
            db.execute("""INSERT INTO tasks(id,idempotency_key,task_type,title,payload_json,status,
                       attempts,max_attempts,created_at,updated_at,execution_mode)
                       VALUES(?,?,?,?,?,'pending',0,?,?,?,'not_started')""",
                       (task_id, key, task_type, title, json.dumps(payload or {}, sort_keys=True),
                        max_attempts, now, now))
            self._audit(db, task_id, "task_enqueued", {"task_type": task_type, "title": title})
            return dict(db.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone())

    def claim_next(self, lease_timeout_seconds: int = 900,
                   execution_mode: str = "unknown") -> dict[str, Any] | None:
        if lease_timeout_seconds < 1:
            raise ValueError("lease_timeout_seconds must be positive")
        if execution_mode not in {"unknown", "dry_run", "live_inference"}:
            raise ValueError("invalid execution_mode")
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM tasks WHERE status='pending' ORDER BY created_at,id LIMIT 1").fetchone()
            if row is None:
                return None
            now = datetime.now(timezone.utc)
            token = str(uuid.uuid4())
            expires = (now + timedelta(seconds=lease_timeout_seconds)).isoformat()
            changed = db.execute("""UPDATE tasks SET status='running',attempts=attempts+1,
                         execution_mode=?,updated_at=?,lease_token=?,lease_expires_at=?
                         WHERE id=? AND status='pending'""",
                         (execution_mode, now.isoformat(), token, expires, row["id"])).rowcount
            if changed != 1:
                return None
            self._audit(db, row["id"], "task_claimed", {"attempt": row["attempts"] + 1,
                        "lease_expires_at": expires})
            return dict(db.execute("SELECT * FROM tasks WHERE id=?", (row["id"],)).fetchone())

    def heartbeat(self, task_id: str, lease_token: str, lease_timeout_seconds: int = 900) -> str:
        if not lease_token or lease_timeout_seconds < 1:
            raise ValueError("valid lease token and positive timeout are required")
        now = datetime.now(timezone.utc)
        expires = (now + timedelta(seconds=lease_timeout_seconds)).isoformat()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            changed = db.execute("""UPDATE tasks SET updated_at=?,lease_expires_at=?
                WHERE id=? AND status='running' AND lease_token=? AND lease_expires_at>=?""",
                (now.isoformat(), expires, task_id, lease_token, now.isoformat())).rowcount
            if changed != 1:
                raise ValueError("lease lost or expired")
            self._audit(db, task_id, "task_heartbeat", {"lease_expires_at": expires})
        return expires

    def _assert_lease(self, row, lease_token: str | None):
        if row["status"] != "running":
            raise ValueError("only running tasks can be modified")
        if not lease_token or row["lease_token"] != lease_token:
            raise ValueError("lease ownership lost")
        if not row["lease_expires_at"] or row["lease_expires_at"] < utc_now():
            raise ValueError("lease expired; worker must not complete the task")

    def finish(self, task_id: str, status: str, result: dict[str, Any] | None = None,
               error: str | None = None, lease_token: str | None = None,
               execution_mode: str | None = None):
        if status not in {"succeeded", "failed", "blocked"}:
            raise ValueError("invalid terminal status")
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
            if row is None:
                raise KeyError(task_id)
            self._assert_lease(row, lease_token)
            now = utc_now()
            mode = execution_mode or (
                result.get("mode") if isinstance(result, dict) and
                result.get("mode") in {"dry_run", "live_inference"} else row["execution_mode"]
            )
            if mode not in {"unknown", "not_started", "dry_run", "live_inference"}:
                raise ValueError("invalid execution_mode")
            changed = db.execute("""UPDATE tasks SET status=?,result_json=?,error=?,execution_mode=?,updated_at=?,
                lease_token=NULL,lease_expires_at=NULL
                WHERE id=? AND status='running' AND lease_token=?""",
                (status, json.dumps(result, sort_keys=True) if result is not None else None,
                 error, mode, now, task_id, lease_token)).rowcount
            if changed != 1:
                raise ValueError("lease ownership lost")
            self._audit(db, task_id, "task_" + status, {"error": error} if error else (result or {}))

    def retry_or_fail(self, task_id: str, error: str, lease_token: str | None = None) -> str:
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
            if row is None:
                raise KeyError(task_id)
            self._assert_lease(row, lease_token)
            status = "pending" if row["attempts"] < row["max_attempts"] else "failed"
            db.execute("""UPDATE tasks SET status=?,error=?,updated_at=?,lease_token=NULL,
                lease_expires_at=NULL WHERE id=? AND status='running' AND lease_token=?""",
                (status, error, utc_now(), task_id, lease_token))
            self._audit(db, task_id, "task_retry_scheduled" if status == "pending" else "task_failed",
                        {"attempt": row["attempts"], "max_attempts": row["max_attempts"], "error": error})
            return status

    def recover_stale(self, lease_timeout_seconds: int = 900) -> dict[str, int]:
        """Requeue expired leases; the old worker is fenced by clearing its ownership token."""
        if lease_timeout_seconds < 1:
            raise ValueError("lease_timeout_seconds must be positive")
        now = datetime.now(timezone.utc).isoformat()
        recovered = failed = 0
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            rows = db.execute("""SELECT * FROM tasks WHERE status='running'
                AND (lease_expires_at IS NULL OR lease_expires_at < ?) ORDER BY updated_at,id""",
                (now,)).fetchall()
            for row in rows:
                status = "pending" if row["attempts"] < row["max_attempts"] else "failed"
                db.execute("""UPDATE tasks SET status=?,error=?,updated_at=?,lease_token=NULL,
                    lease_expires_at=NULL WHERE id=? AND status='running' AND lease_token IS ?""",
                    (status, "Recovered expired worker lease.", now, row["id"], row["lease_token"]))
                self._audit(db, row["id"], "task_lease_recovered",
                            {"new_status": status, "attempt": row["attempts"],
                             "max_attempts": row["max_attempts"]})
                if status == "pending":
                    recovered += 1
                else:
                    failed += 1
        return {"requeued": recovered, "failed": failed}

    def list_tasks(self, limit: int = 50) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit), 200))
        with self._connect() as db:
            return [dict(row) for row in db.execute(
                "SELECT * FROM tasks ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()]

    def audit_events(self, limit: int = 100) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit), 500))
        with self._connect() as db:
            return [dict(row) for row in db.execute(
                "SELECT * FROM audit_log ORDER BY id DESC LIMIT ?", (limit,)).fetchall()]
