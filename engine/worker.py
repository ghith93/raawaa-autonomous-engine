"""Bounded dry-run worker. No external tools or model providers are called."""
import json

from engine.task_store import TaskStore


SAFE_TASKS = {"research_brief", "code_review", "content_draft", "system_audit"}


def run_once(store: TaskStore) -> dict | None:
    # Recover abandoned work before claiming another item. The lease is deliberately
    # conservative; deployments with long tasks must configure a longer timeout.
    recovery = store.recover_stale()
    task = store.claim_next()
    if task is None:
        return {"status": "idle", "recovery": recovery} if any(recovery.values()) else None

    task_type = task["task_type"]
    if task_type not in SAFE_TASKS:
        store.finish(task["id"], "blocked", error=f"Unsupported task type: {task_type}", lease_token=task["lease_token"])
        return {"id": task["id"], "status": "blocked", "reason": "unsupported_task_type"}

    try:
        payload = json.loads(task["payload_json"])
        if not isinstance(payload, dict):
            raise ValueError("task payload must be a JSON object")
        # Dry-run produces a structured work ticket rather than pretending an AI agent ran.
        result = {
            "mode": "dry_run",
            "task_type": task_type,
            "title": task["title"],
            "payload_received": payload,
            "next_step": "Connect a reviewed provider/tool adapter before enabling execution."
        }
        store.finish(task["id"], "succeeded", result=result, lease_token=task["lease_token"])
        return {"id": task["id"], "status": "succeeded", "mode": "dry_run",
                "recovery": recovery}
    except Exception as exc:
        # Persist only the exception class, not arbitrary exception text that could
        # contain prompt content, credentials, or customer data.
        reason = f"worker_error:{type(exc).__name__}"
        status = store.retry_or_fail(task["id"], reason, lease_token=task["lease_token"])
        return {"id": task["id"], "status": status, "reason": reason,
                "recovery": recovery}
