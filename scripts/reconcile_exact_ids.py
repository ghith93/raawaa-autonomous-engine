# STAGE C — EXACT-ID RECONCILIATION SCRIPT (READ-ONLY)
# Purpose: Implement strictly read-only, exact-ID Staging reconciliation path
# for historical Task IDs 237660ed-2a97-415f-8a24-fc3e20f5f641 and
# 0bb6b3cb-c16e-4c12-92c8-2602206c7937 (Issue #464 Comment 6696725386).
# Returns status, task type, timestamps, lease/attempt metadata, and audit events.
# Secrets and payloads redacted. No enqueue, claim, recover, mutate, or sweep.

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from engine.redaction import redact
from engine.remote_task_store import RemoteTaskStore, RemoteTaskStoreError

STAGING_API_URL = "https://raawaa-autonomous-engine-staging.ghith-shikhkhaled.workers.dev"
TASK_TYPE = "supervised_probe"
OPERATION = "python_platform_probe"

# Historical IDs from Issue #464 Comment 6696725386
HISTORICAL_IDS = [
    "237660ed-2a97-415f-8a24-fc3e20f5f641",
    "0bb6b3cb-c16e-4c12-92c8-2602206c7937"
]

class ReconciliationError(RuntimeError):
    """The exact-ID reconciliation failed a fail-closed precondition."""

def validate_environment(environ: Mapping[str, str]) -> str:
    """Require a reviewed Staging target and token (read-only mode)."""
    api_url = environ.get("RAAWAA_ENGINE_API_URL", "").rstrip("/")
    if api_url != STAGING_API_URL:
        raise ReconciliationError("Refusing any API target other than the isolated Staging Worker")
    token = environ.get("RAAWAA_ENGINE_API_TOKEN", "")
    if len(token) < 32:
        raise ReconciliationError("A temporary Staging-only API token is required for read-only access")
    return token

def _safe_failure_detail(exc: Exception) -> str | None:
    if not isinstance(exc, (ReconciliationError, RemoteTaskStoreError)):
        return None
    detail = " ".join(redact(str(exc)).split())[:320]
    return detail or None

def _write_step_summary(line: str) -> None:
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY", "")
    if not summary_path:
        return
    try:
        with open(summary_path, "a", encoding="utf-8") as handle:
            handle.write(line + "\n")
    except OSError:
        pass

def reconcile_exact_id(store: RemoteTaskStore, task_id: str) -> dict[str, Any]:
    """Read-only reconciliation for a single historical task ID."""
    # Retrieve task details (read-only) using list_tasks filter
    tasks = store.list_tasks(limit=1, task_id=task_id)
    if not tasks:
        return {
            "task_id": task_id,
            "status": "not_found",
            "type": None,
            "timestamps": None,
            "lease_metadata": None,
            "attempt_metadata": None,
            "audit_events": [],
            "redacted_payload": None,
        }
    task = tasks[0]
    if task.get("id") != task_id:
        return {
            "task_id": task_id,
            "status": "not_found",
            "type": None,
            "timestamps": None,
            "lease_metadata": None,
            "attempt_metadata": None,
            "audit_events": [],
            "redacted_payload": None,
        }

    # Retrieve audit events (read-only)
    audit_events = store.audit_events(limit=100, task_id=task_id)
    event_names = [
        row.get("event") for row in audit_events
        if isinstance(row, dict) and row.get("task_id") == task_id
    ]

    # Extract payload (redacted)
    raw_payload = task.get("payload_json")
    redacted_payload = redact(str(raw_payload)) if isinstance(raw_payload, str) else None

    # Timestamps and metadata extraction from task dict
    timestamps = {
        "enqueued_at": task.get("enqueued_at"),
        "claimed_at": task.get("claimed_at"),
        "finished_at": task.get("finished_at"),
    }
    lease_metadata = {
        "lease_timeout": task.get("lease_timeout_seconds"),
        "lease_expired": task.get("lease_expired", False),
    }
    attempt_metadata = {
        "attempt": task.get("attempt", 1),
        "max_attempts": task.get("max_attempts", 1),
    }

    return {
        "task_id": task_id,
        "status": task.get("status"),
        "type": task.get("task_type"),
        "timestamps": timestamps,
        "lease_metadata": lease_metadata,
        "attempt_metadata": attempt_metadata,
        "audit_events": event_names,
        "redacted_payload": redacted_payload,
    }

def main() -> int:
    try:
        env = dict(os.environ)
        token = validate_environment(env)
        store = RemoteTaskStore(STAGING_API_URL, token, timeout=30, reject_redirects=True)

        results = []
        for task_id in HISTORICAL_IDS:
            result = reconcile_exact_id(store, task_id)
            results.append(result)

        output = {
            "operation": "exact_id_reconciliation",
            "historical_ids_processed": len(HISTORICAL_IDS),
            "results": results,
            "backend": "cloudflare_staging",
            "provider_calls": 0,
            "external_writes": 0,
        }

        print(json.dumps(output, sort_keys=True))
        _write_step_summary("STAGE C Exact-ID Reconciliation: " + json.dumps(output, sort_keys=True))
        return 0

    except Exception as exc:
        detail = _safe_failure_detail(exc)
        suffix = f": {detail}" if detail else ""
        print(f"STAGE C Exact-ID Reconciliation failed: {type(exc).__name__}{suffix}", file=sys.stderr)
        _write_step_summary(f"STAGE C Exact-ID Reconciliation FAILED: {type(exc).__name__}{suffix}")
        return 1

if __name__ == "__main__":
    raise SystemExit(main())
