"""Offline-only SQLite pilot admission/claim prototype; not wired to a runner.

This is not D1, provider authorization, a sandbox, or a live inference path.
It intentionally offers no provider dispatch and never selects a generic task.
"""
from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timedelta, timezone

from engine.live_data_policy import validate_live_payload
from engine.task_store import TaskStore

_KEY = re.compile(r"research-pilot:[a-z0-9][a-z0-9-]{0,63}\Z")


class PilotContractError(ValueError):
    """Sanitized contract violation: never include payloads or credentials."""


def _validate(key, title, payload, policy):
    if not isinstance(key, str) or not _KEY.fullmatch(key):
        raise PilotContractError("invalid pilot idempotency key")
    allowed, _reason = validate_live_payload("research_brief", title, payload, policy)
    if not allowed:
        raise PilotContractError("public research payload rejected")
    try:
        return json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    except (ValueError, TypeError, UnicodeError):
        raise PilotContractError("public research payload rejected") from None


def admit_local(store: TaskStore, *, key: str, title: str, payload: dict, policy: dict):
    """Atomically create/replay one local task bound to all supplied task fields."""
    if type(store) is not TaskStore:
        raise PilotContractError("only local SQLite TaskStore is supported")
    encoded = _validate(key, title, payload, policy)
    now = datetime.now(timezone.utc).isoformat()
    with store._connect() as db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("SELECT * FROM tasks WHERE idempotency_key=?", (key,)).fetchone()
        if row is not None:
            try:
                old_payload = json.dumps(json.loads(row["payload_json"]), sort_keys=True,
                                         ensure_ascii=False, separators=(",", ":"), allow_nan=False)
            except (TypeError, ValueError, UnicodeError):
                raise PilotContractError("pilot identity conflict") from None
            if (row["task_type"] != "research_brief" or row["title"] != title
                    or old_payload != encoded or row["max_attempts"] != 1
                    or row["priority"] != 50 or row["dependencies_json"] != "[]"
                    or db.execute("SELECT 1 FROM task_dependencies WHERE task_id=? LIMIT 1", (row["id"],)).fetchone()):
                raise PilotContractError("pilot identity conflict")
            return row["id"], False
        task_id = str(uuid.uuid4())
        db.execute("""INSERT INTO tasks(id,idempotency_key,task_type,title,payload_json,status,
            attempts,max_attempts,created_at,updated_at,execution_mode,priority,dependencies_json)
            VALUES(?,?,?,?,?,'pending',0,1,?,?,'not_started',50,'[]')""",
            (task_id, key, "research_brief", title, encoded, now, now))
        store._audit(db, task_id, "pilot_local_admitted", {"contract": "offline-only-v1"})
        return task_id, True


def claim_local(store: TaskStore, *, task_id: str, key: str, title: str,
                payload: dict, policy: dict, lease_seconds: int = 60):
    """Claim the exact matching local identity only, without any provider I/O."""
    if type(store) is not TaskStore:
        raise PilotContractError("only local SQLite TaskStore is supported")
    encoded = _validate(key, title, payload, policy)
    if not isinstance(task_id, str) or not task_id or type(lease_seconds) is not int or not 1 <= lease_seconds <= 900:
        raise PilotContractError("invalid pilot claim")
    with store._connect() as db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("SELECT * FROM tasks WHERE id=? AND idempotency_key=?", (task_id, key)).fetchone()
        if row is None:
            return None
        try:
            old_payload = json.dumps(json.loads(row["payload_json"]), sort_keys=True,
                                     ensure_ascii=False, separators=(",", ":"), allow_nan=False)
        except (TypeError, ValueError, UnicodeError):
            raise PilotContractError("pilot identity conflict") from None
        if (row["task_type"] != "research_brief" or row["title"] != title or old_payload != encoded
                or row["max_attempts"] != 1 or row["priority"] != 50 or row["dependencies_json"] != "[]"
                or db.execute("SELECT 1 FROM task_dependencies WHERE task_id=? LIMIT 1", (task_id,)).fetchone()):
            raise PilotContractError("pilot identity conflict")
        if row["status"] != "pending" or row["attempts"] != 0:
            return None
        now = datetime.now(timezone.utc)
        token = str(uuid.uuid4())
        expiry = (now + timedelta(seconds=lease_seconds)).isoformat()
        updated = db.execute("""UPDATE tasks SET status='running',attempts=1,execution_mode='dry_run',
            updated_at=?,lease_token=?,lease_expires_at=? WHERE id=? AND status='pending' AND attempts=0""",
            (now.isoformat(), token, expiry, task_id)).rowcount
        if updated != 1:
            return None
        store._audit(db, task_id, "pilot_local_claimed", {"execution_mode": "dry_run"})
        return {"id": task_id, "lease_token": token, "lease_expires_at": expiry, "execution_mode": "dry_run"}
