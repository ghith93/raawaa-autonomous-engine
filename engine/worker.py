"""Bounded dry-run worker. No external tools or model providers are called."""
import json

from engine.task_store import TaskStore


SAFE_TASKS = {"research_brief", "code_review", "content_draft", "system_audit"}


def run_once(store: TaskStore) -> dict | None:
    task = store.claim_next()
    if task is None:
        return None

    task_type = task["task_type"]
    if task_type not in SAFE_TASKS:
        store.finish(task["id"], "blocked", error=f"Unsupported task type: {task_type}")
        return {"id": task["id"], "status": "blocked", "reason": "unsupported_task_type"}

    payload = json.loads(task["payload_json"])
    # Dry-run produces a structured work ticket rather than pretending an AI agent ran.
    result = {
        "mode": "dry_run",
        "task_type": task_type,
        "title": task["title"],
        "payload_received": payload,
        "next_step": "Connect a reviewed provider/tool adapter before enabling execution."
    }
    store.finish(task["id"], "succeeded", result=result)
    return {"id": task["id"], "status": "succeeded", "mode": "dry_run"}
