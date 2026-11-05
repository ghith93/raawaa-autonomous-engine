"""Exercise the Python remote supervisor against the local Worker/D1 harness only."""
from __future__ import annotations

import json
import os
import sys

from engine.remote_task_store import RemoteTaskStore
from engine.supervised_task import SUPERVISED_TASK_TYPE, SUPERVISOR_SWITCH, complete_supervised_task


def main() -> int:
    base_url = os.environ.get("RAAWAA_LOCAL_WORKER_URL", "")
    token = os.environ.get("RAAWAA_LOCAL_WORKER_TOKEN", "")
    if not base_url.startswith("http://127.0.0.1:") or not token:
        raise SystemExit("local D1 fixture requires an explicit loopback Worker URL and token")

    # Bypass only the production HTTPS origin validator in this isolated test
    # fixture. No production/runtime path opts into insecure transport.
    store = RemoteTaskStore.__new__(RemoteTaskStore)
    store.base_url = base_url.rstrip("/")
    store.token = token
    store.access_client_id = ""
    store.access_client_secret = ""
    store.timeout = 10.0
    store.reject_redirects = True

    task = store.enqueue(
        SUPERVISED_TASK_TYPE,
        "Local D1 supervised process integration",
        {"operation": "python_platform_probe"},
        idempotency_key="local-process-dispatch-" + os.urandom(8).hex(),
        max_attempts=2,
        priority=100,
    )
    claimed = store.claim_next(execution_mode="dry_run")
    if not claimed or claimed.get("id") != task.get("id"):
        raise SystemExit("local D1 did not claim the supervised process fixture task")

    outcome = complete_supervised_task(
        store, claimed, environ={SUPERVISOR_SWITCH: "enabled"},
    )
    if outcome.get("status") != "succeeded" or outcome.get("classification") != "succeeded":
        raise SystemExit("remote supervised process did not complete: " + json.dumps(outcome))
    stored = next((item for item in store.list_tasks(100) if item.get("id") == task.get("id")), None)
    if not stored or stored.get("status") != "succeeded":
        raise SystemExit("remote task result was not durably settled")
    if outcome.get("replayed") is not False or not outcome.get("dispatch_id"):
        raise SystemExit("remote process execution did not return its durable dispatch identity")

    print(json.dumps({
        "task_id": task["id"], "task_status": stored["status"],
        "classification": outcome["classification"],
        "dispatch_id": outcome["dispatch_id"], "replayed": outcome["replayed"],
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
