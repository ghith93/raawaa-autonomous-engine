"""Durable idempotency ledger for future side-effecting handlers.

This ledger coordinates local attempts. Exactly-once external effects still require
the downstream service to honor the same idempotency key or support reconciliation.
"""
from __future__ import annotations

import json
import sqlite3
import uuid
from pathlib import Path
from typing import Any

from engine.task_store import DEFAULT_DB


class OperationStore:
    """Persist operation intent and fence duplicate local execution attempts."""

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
            db.execute("""
                CREATE TABLE IF NOT EXISTS operations (
                    id TEXT PRIMARY KEY,
                    idempotency_key TEXT NOT NULL UNIQUE,
                    operation_type TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status IN
                        ('pending','executing','succeeded','failed','unknown')),
                    claim_token TEXT,
                    external_ref TEXT,
                    result_json TEXT,
                    error_code TEXT,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
            """)
            db.execute("""
                CREATE INDEX IF NOT EXISTS idx_operations_status_created
                ON operations(status, created_at)
            """)

    @staticmethod
    def _record(row) -> dict[str, Any]:
        return dict(row)

    def prepare(self, operation_type: str, idempotency_key: str,
                payload: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(operation_type, str) or not operation_type.strip():
            raise ValueError("operation_type is required")
        if not isinstance(idempotency_key, str) or not idempotency_key.strip():
            raise ValueError("idempotency_key is required")
        if not isinstance(payload, dict):
            raise ValueError("operation payload must be a JSON object")
        try:
            payload_json = json.dumps(payload, sort_keys=True, separators=(",", ":"),
                                      allow_nan=False)
        except (TypeError, ValueError) as exc:
            raise ValueError("operation payload must contain finite JSON values") from exc
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT * FROM operations WHERE idempotency_key=?", (idempotency_key,)
            ).fetchone()
            if row:
                if row["operation_type"] != operation_type or row["payload_json"] != payload_json:
                    raise ValueError("idempotency key already belongs to different operation input")
                return self._record(row)
            operation_id = str(uuid.uuid4())
            db.execute("""
                INSERT INTO operations(id,idempotency_key,operation_type,payload_json,status)
                VALUES(?,?,?,?,'pending')
            """, (operation_id, idempotency_key, operation_type, payload_json))
            return self._record(db.execute(
                "SELECT * FROM operations WHERE id=?", (operation_id,)
            ).fetchone())

    def claim(self, operation_id: str) -> dict[str, Any] | None:
        """Claim pending intent once. Executing/unknown records are never auto-replayed."""
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM operations WHERE id=?", (operation_id,)).fetchone()
            if row is None:
                raise KeyError(operation_id)
            if row["status"] != "pending":
                return None
            token = str(uuid.uuid4())
            changed = db.execute("""
                UPDATE operations SET status='executing', claim_token=?,
                    updated_at=CURRENT_TIMESTAMP
                WHERE id=? AND status='pending'
            """, (token, operation_id)).rowcount
            if changed != 1:
                return None
            return self._record(db.execute(
                "SELECT * FROM operations WHERE id=?", (operation_id,)
            ).fetchone())

    def mark_succeeded(self, operation_id: str, claim_token: str,
                       result: dict[str, Any] | None = None,
                       external_ref: str | None = None) -> None:
        self._finish(operation_id, claim_token, "succeeded", result, external_ref, None)

    def mark_failed(self, operation_id: str, claim_token: str, error_code: str) -> None:
        if not isinstance(error_code, str) or not error_code.strip():
            raise ValueError("error_code is required")
        self._finish(operation_id, claim_token, "failed", None, None, error_code)

    def mark_unknown(self, operation_id: str, claim_token: str) -> None:
        """Call only after the attempt has ended and its external outcome is uncertain."""
        self._finish(operation_id, claim_token, "unknown", None, None, "outcome_unconfirmed")

    def _finish(self, operation_id: str, claim_token: str, status: str,
                result: dict[str, Any] | None, external_ref: str | None,
                error_code: str | None) -> None:
        if not isinstance(claim_token, str) or not claim_token:
            raise ValueError("claim token is required")
        if result is not None and not isinstance(result, dict):
            raise ValueError("result must be a JSON object")
        result_json = None
        if result is not None:
            try:
                result_json = json.dumps(result, sort_keys=True, allow_nan=False)
            except (TypeError, ValueError) as exc:
                raise ValueError("result must contain finite JSON values") from exc
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            changed = db.execute("""
                UPDATE operations SET status=?, result_json=?, external_ref=?,
                    error_code=?, claim_token=NULL, updated_at=CURRENT_TIMESTAMP
                WHERE id=? AND status='executing' AND claim_token=?
            """, (status, result_json, external_ref, error_code,
                  operation_id, claim_token)).rowcount
            if changed != 1:
                raise ValueError("operation claim lost or already finalized")

    def reconcile(self, operation_id: str, outcome: str,
                  result: dict[str, Any] | None = None,
                  external_ref: str | None = None) -> None:
        """Resolve only an explicitly unknown operation after external verification.

        outcome='succeeded' confirms it happened.
        outcome='not_performed' confirms it did not happen and permits a fresh claim.
        """
        if outcome not in {"succeeded", "not_performed"}:
            raise ValueError("outcome must be succeeded or not_performed")
        if result is not None and not isinstance(result, dict):
            raise ValueError("result must be a JSON object")
        result_json = None
        if result is not None:
            try:
                result_json = json.dumps(result, sort_keys=True, allow_nan=False)
            except (TypeError, ValueError) as exc:
                raise ValueError("result must contain finite JSON values") from exc
        status = "succeeded" if outcome == "succeeded" else "pending"
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            changed = db.execute("""
                UPDATE operations SET status=?, result_json=?, external_ref=?,
                    error_code=NULL, claim_token=NULL, updated_at=CURRENT_TIMESTAMP
                WHERE id=? AND status='unknown'
            """, (status, result_json, external_ref, operation_id)).rowcount
            if changed != 1:
                raise ValueError("operation must be marked unknown before reconciliation")

    def get(self, operation_id: str) -> dict[str, Any]:
        with self._connect() as db:
            row = db.execute("SELECT * FROM operations WHERE id=?", (operation_id,)).fetchone()
            if row is None:
                raise KeyError(operation_id)
            return self._record(row)
