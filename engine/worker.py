"""Bounded dry-run worker. No external tools or model providers are called."""
from engine.task_store import TaskStore
from engine.agent_runtime import build_task_contract, load_agent_roster


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
        # Assign a specialist deterministically, but do not imply that an agent or
        # model has run. The default worker remains a zero-network dry run.
        result = build_task_contract(task, load_agent_roster())
        store.finish(task["id"], "succeeded", result=result, lease_token=task["lease_token"])
        return {"id": task["id"], "status": "succeeded", "mode": "dry_run",
                "agent_id": result["agent_id"], "recovery": recovery}
    except Exception as exc:
        # Persist only the exception class, not arbitrary exception text that could
        # contain prompt content, credentials, or customer data.
        reason = f"worker_error:{type(exc).__name__}"
        status = store.retry_or_fail(task["id"], reason, lease_token=task["lease_token"])
        return {"id": task["id"], "status": status, "reason": reason,
                "recovery": recovery}
