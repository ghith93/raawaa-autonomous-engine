"""SQLite-backed task queue with idempotent enqueue and an audit trail."""
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
              id TEXT PRIMARY KEY,
              idempotency_key TEXT NOT NULL UNIQUE,
              task_type TEXT NOT NULL,
              title TEXT NOT NULL,
              payload_json TEXT NOT NULL,
              status TEXT NOT NULL CHECK(status IN ('pending','running','succeeded','failed','blocked')),
              attempts INTEGER NOT NULL DEFAULT 0,
              max_attempts INTEGER NOT NULL DEFAULT 2,
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL,
              result_json TEXT,
              error TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_tasks_status_created ON tasks(status, created_at);
            CREATE TABLE IF NOT EXISTS audit_log (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              task_id TEXT,
              event TEXT NOT NULL,
              details_json TEXT NOT NULL,
              created_at TEXT NOT NULL,
              FOREIGN KEY(task_id) REFERENCES tasks(id)
            );
            """)

    def _audit(self, db, task_id: str | None, event: str, details: dict[str, Any]):
        db.execute(
            "INSERT INTO audit_log(task_id,event,details_json,created_at) VALUES(?,?,?,?)",
            (task_id, event, json.dumps(details, sort_keys=True), utc_now()),
        )

    def enqueue(self, task_type: str, title: str, payload: dict[str, Any] | None = None,
                idempotency_key: str | None = None, max_attempts: int = 2) -> dict[str, Any]:
        if not task_type.strip() or not title.strip():
            raise ValueError("task_type and title are required")
        if max_attempts < 1 or max_attempts > 5:
            raise ValueError("max_attempts must be between 1 and 5")
        key = idempotency_key or str(uuid.uuid4())
        task_id = str(uuid.uuid4())
        now = utc_now()
        with self._connect() as db:
            existing = db.execute("SELECT * FROM tasks WHERE idempotency_key=?", (key,)).fetchone()
            if existing:
                return dict(existing)
            db.execute(
                """INSERT INTO tasks(id,idempotency_key,task_type,title,payload_json,status,
                   attempts,max_attempts,created_at,updated_at)
                   VALUES(?,?,?,?,?,'pending',0,?,?,?)""",
                (task_id, key, task_type, title, json.dumps(payload or {}, sort_keys=True),
                 max_attempts, now, now),
            )
            self._audit(db, task_id, "task_enqueued", {"task_type": task_type, "title": title})
            return dict(db.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone())

    def claim_next(self) -> dict[str, Any] | None:
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT * FROM tasks WHERE status='pending' ORDER BY created_at,id LIMIT 1"
            ).fetchone()
            if row is None:
                return None
            now = utc_now()
            db.execute(
                "UPDATE tasks SET status='running',attempts=attempts+1,updated_at=? WHERE id=? AND status='pending'",
                (now, row["id"]),
            )
            self._audit(db, row["id"], "task_claimed", {"attempt": row["attempts"] + 1})
            return dict(db.execute("SELECT * FROM tasks WHERE id=?", (row["id"],)).fetchone())

    def finish(self, task_id: str, status: str, result: dict[str, Any] | None = None,
               error: str | None = None):
        if status not in {"succeeded", "failed", "blocked"}:
            raise ValueError("invalid terminal status")
        with self._connect() as db:
            row = db.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
            if row is None:
                raise KeyError(task_id)
            if row["status"] != "running":
                raise ValueError("only running tasks can be completed")
            # Failed tasks are retried only when explicitly returned to pending.
            db.execute(
                "UPDATE tasks SET status=?,result_json=?,error=?,updated_at=? WHERE id=?",
                (status, json.dumps(result, sort_keys=True) if result is not None else None,
                 error, utc_now(), task_id),
            )
            self._audit(db, task_id, "task_" + status, {"error": error} if error else (result or {}))

    def retry_or_fail(self, task_id: str, error: str) -> str:
        """Return a failed running task to pending, or fail it after its attempt budget."""
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
            if row is None:
                raise KeyError(task_id)
            if row["status"] != "running":
                raise ValueError("only running tasks can be retried or failed")
            status = "pending" if row["attempts"] < row["max_attempts"] else "failed"
            db.execute(
                "UPDATE tasks SET status=?,error=?,updated_at=? WHERE id=?",
                (status, error, utc_now(), task_id),
            )
            self._audit(db, task_id, "task_retry_scheduled" if status == "pending" else "task_failed",
                        {"attempt": row["attempts"], "max_attempts": row["max_attempts"],
                         "error": error})
            return status

    def recover_stale(self, lease_timeout_seconds: int = 900) -> dict[str, int]:
        """Recover abandoned running tasks using updated_at as a conservative lease."""
        if lease_timeout_seconds < 1:
            raise ValueError("lease_timeout_seconds must be positive")
        cutoff = (datetime.now(timezone.utc) - timedelta(seconds=lease_timeout_seconds)).isoformat()
        recovered = failed = 0
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            rows = db.execute(
                "SELECT * FROM tasks WHERE status='running' AND updated_at < ? ORDER BY updated_at,id",
                (cutoff,),
            ).fetchall()
            for row in rows:
                status = "pending" if row["attempts"] < row["max_attempts"] else "failed"
                db.execute(
                    "UPDATE tasks SET status=?,error=?,updated_at=? WHERE id=? AND status='running'",
                    (status, "Recovered stale worker lease.", utc_now(), row["id"]),
                )
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
                "SELECT * FROM tasks ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()]

    def audit_events(self, limit: int = 100) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit), 500))
        with self._connect() as db:
            return [dict(row) for row in db.execute(
                "SELECT * FROM audit_log ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()]
