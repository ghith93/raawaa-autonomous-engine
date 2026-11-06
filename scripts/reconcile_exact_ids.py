# STAGE C — EXACT-ID RECONCILIATION (READ-ONLY, METADATA ALLOWLIST)
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
HISTORICAL_IDS = (
    "237660ed-2a97-415f-8a24-fc3e20f5f641",
    "0bb6b3cb-c16e-4c12-92c8-2602206c7937",
)
_EVENT_NAME = re.compile(r"^[A-Za-z0-9_.:-]{1,80}$")


class ReconciliationError(RuntimeError):
    """A fail-closed precondition failed."""


def validate_environment(environ: Mapping[str, str]) -> str:
    api_url = environ.get("RAAWAA_ENGINE_API_URL", "").rstrip("/")
    if api_url != STAGING_API_URL:
        raise ReconciliationError("Refusing any API target other than the isolated Staging Worker")
    token = environ.get("RAAWAA_ENGINE_API_TOKEN", "")
    if len(token) < 32:
        raise ReconciliationError("A temporary Staging-only API token is required")
    return token


def _safe_failure_detail(exc: Exception) -> str | None:
    if not isinstance(exc, (ReconciliationError, RemoteTaskStoreError)):
        return None
    return " ".join(redact(str(exc)).split())[:320] or None


def _write_step_summary(line: str) -> None:
    path = os.environ.get("GITHUB_STEP_SUMMARY", "")
    if not path:
        return
    try:
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(line + "\n")
    except OSError:
        pass


def reconcile_exact_id(store: Any, task_id: str) -> dict[str, Any]:
    """Read one exact task and its audit event names; never return payloads."""
    tasks = store.list_tasks(limit=1, task_id=task_id)
    task = next(
        (item for item in tasks if isinstance(item, dict) and item.get("id") == task_id),
        None,
    )
    if task is None:
        return {
            "task_id": task_id,
            "status": "not_found",
            "task_type": None,
            "timestamps": None,
            "lease": None,
            "attempt": None,
            "audit_event_names": [],
        }

    events = store.audit_events(limit=100, task_id=task_id)
    event_names = sorted({
        row.get("event")
        for row in events
        if isinstance(row, dict)
        and row.get("task_id") == task_id
        and isinstance(row.get("event"), str)
        and _EVENT_NAME.fullmatch(row["event"])
    })

    return {
        "task_id": task_id,
        "status": task.get("status") if isinstance(task.get("status"), str) else None,
        "task_type": task.get("task_type") if isinstance(task.get("task_type"), str) else None,
        "timestamps": {
            key: task.get(key) if isinstance(task.get(key), str) else None
            for key in ("enqueued_at", "claimed_at", "finished_at")
        },
        "lease": {
            "timeout_seconds": task.get("lease_timeout_seconds")
            if isinstance(task.get("lease_timeout_seconds"), (int, float)) else None,
            "expired": task.get("lease_expired")
            if isinstance(task.get("lease_expired"), bool) else None,
        },
        "attempt": {
            "number": task.get("attempt") if isinstance(task.get("attempt"), int) else None,
            "max": task.get("max_attempts") if isinstance(task.get("max_attempts"), int) else None,
        },
        "audit_event_names": event_names,
    }


def main() -> int:
    try:
        token = validate_environment(os.environ)
        store = RemoteTaskStore(
            STAGING_API_URL, token, timeout=30, reject_redirects=True
        )
        results = [reconcile_exact_id(store, task_id) for task_id in HISTORICAL_IDS]
        output = {
            "operation": "exact_id_reconciliation",
            "historical_ids_processed": len(HISTORICAL_IDS),
            "results": results,
            "backend": "cloudflare_staging",
            "provider_calls": 0,
            "external_writes": 0,
        }
        encoded = json.dumps(output, sort_keys=True)
        print(encoded)
        _write_step_summary("STAGE C Exact-ID Reconciliation: " + encoded)
        return 0
    except Exception as exc:
        detail = _safe_failure_detail(exc)
        suffix = f": {detail}" if detail else ""
        print(f"STAGE C reconciliation failed: {type(exc).__name__}{suffix}", file=sys.stderr)
        _write_step_summary(f"STAGE C reconciliation FAILED: {type(exc).__name__}{suffix}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
