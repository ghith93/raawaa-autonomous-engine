"""Local D1 crash drill worker; only the test harness invokes this fixture."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from engine.process_supervisor import OperationRegistry, OperationSpec, ProcessSupervisor
from engine.remote_process_dispatch_ledger import RemoteProcessDispatchLedger
from engine.remote_task_store import RemoteTaskStore
from engine.supervised_task import SUPERVISOR_SWITCH, complete_supervised_task

CHILD = Path(__file__).with_name("remote_process_crash_child.py")
OPERATION = "python_platform_probe"


def local_store() -> RemoteTaskStore:
    base_url = os.environ.get("RAAWAA_LOCAL_WORKER_URL", "")
    token = os.environ.get("RAAWAA_LOCAL_WORKER_TOKEN", "")
    if not base_url.startswith("http://127.0.0.1:") or not token:
        raise RuntimeError("local crash fixture requires an explicit loopback Worker and token")
    # Bypass the production HTTPS validator only for this isolated local test.
    store = RemoteTaskStore.__new__(RemoteTaskStore)
    store.base_url = base_url.rstrip("/")
    store.token = token
    store.access_client_id = ""
    store.access_client_secret = ""
    store.timeout = 5.0
    store.reject_redirects = True
    return store


def make_supervisor(store: RemoteTaskStore, marker_path: str) -> ProcessSupervisor:
    registry = OperationRegistry([
        OperationSpec(
            name=OPERATION,
            executable=sys.executable,
            arguments=(str(CHILD), marker_path),
            wall_seconds=90.0,
            grace_seconds=0.3,
            output_bytes=1024,
            purpose="Fixed local crash-recovery test child; no network or task-supplied arguments.",
        ),
    ])
    return ProcessSupervisor(registry, RemoteProcessDispatchLedger(store))


def load_exact_task(store: RemoteTaskStore, task_id: str) -> dict:
    tasks = store.list_tasks(100)
    matches = [task for task in tasks if task.get("id") == task_id
               and task.get("task_type") == "supervised_probe"
               and task.get("title") == "Scheduled Staging read-only process probe"
               and task.get("idempotency_key", "").startswith("staging-cron-supervised-probe:")]
    if len(matches) != 1:
        raise SystemExit("expected exactly one exact daily supervised-probe fixture")
    return matches[0]


def main(argv: list[str]) -> int:
    if len(argv) < 3 or argv[1] not in {"start", "claim", "resume"}:
        raise SystemExit("usage: remote_process_crash_worker.py start|claim|resume <marker_path> [task_id lease_token]")
    mode, marker_path = argv[1], argv[2]
    if not os.path.isabs(marker_path):
        raise SystemExit("marker path must be absolute")
    store = local_store()

    if mode == "start":
        tasks = store.list_tasks(100)
        matches = [
            task for task in tasks
            if task.get("task_type") == "supervised_probe"
            and task.get("idempotency_key", "").startswith("staging-cron-supervised-probe:")
            and task.get("status") == "pending"
        ]
        if len(matches) != 1:
            raise SystemExit("expected exactly one pending daily supervised-probe fixture")
        task_id = matches[0]["id"]
        claimed = store.claim_supervised_probe(task_id)
        if claimed is None or claimed.get("id") != task_id:
            raise SystemExit("exact supervised-probe claim failed")
        lease_token = claimed["lease_token"]
    elif mode == "claim":
        if len(argv) != 4:
            raise SystemExit("claim mode requires one exact task id")
        task_id = argv[3]
        claimed = store.claim_supervised_probe(task_id)
        if claimed is None or claimed.get("id") != task_id:
            raise SystemExit("exact supervised-probe claim failed")
        print(json.dumps({"task_id": task_id, "lease_token": claimed["lease_token"],
                          "attempts": claimed.get("attempts")}, sort_keys=True), flush=True)
        return 0
    else:
        if len(argv) != 5:
            raise SystemExit("resume mode requires the exact task id and newly issued lease token")
        task_id, lease_token = argv[3], argv[4]
        claimed = load_exact_task(store, task_id)
        if claimed.get("status") != "running" or claimed.get("execution_mode") != "dry_run":
            raise SystemExit("resume mode requires the new exact running dry-run lease")
        claimed = {**claimed, "lease_token": lease_token}

    supervisor = make_supervisor(store, marker_path)
    result = complete_supervised_task(
        store,
        claimed,
        environ={SUPERVISOR_SWITCH: "enabled"},
        supervisor=supervisor,
        allowed_operations=frozenset({OPERATION}),
        lease_timeout_seconds=120,
    )
    print(json.dumps({"mode": mode, **result}, sort_keys=True), flush=True)
    if mode == "resume" and (result.get("status") != "blocked"
                              or result.get("classification") != "ambiguous"):
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
