"""Run one local, deterministic task lifecycle for the scheduled runner.

This proves the queue/claim/heartbeat/finish/audit path against temporary SQLite.
It is not remote D1 evidence and never invokes a provider or external service.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any

from engine.batch_runner import run_batch
from engine.task_store import TaskStore
from engine.worker import run_once


def run_local_task_cycle(run_id: str) -> dict[str, Any]:
    if not isinstance(run_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", run_id):
        raise ValueError("run_id must be a bounded identifier")
    if os.environ.get("RAAWAA_ENGINE_API_URL"):
        raise RuntimeError("local task cycle refuses a remote Engine API URL")
    if os.environ.get("RAAWAA_ENGINE_LIVE_INFERENCE", "").strip().lower() == "enabled":
        raise RuntimeError("local task cycle refuses live inference mode")

    with tempfile.TemporaryDirectory(prefix="raawaa-local-task-cycle-") as temporary:
        db_path = Path(temporary) / "engine.db"
        idempotency_key = "scheduled-local-smoke:" + run_id
        store = TaskStore(db_path)
        created = store.enqueue(
            "system_audit",
            "Scheduled local fake-executor smoke",
            {"kind": "synthetic_smoke", "network": False, "external_writes": False},
            idempotency_key=idempotency_key,
            max_attempts=1,
            execution_lane="general",
        )
        batch = run_batch(
            store,
            max_tasks=1,
            max_seconds=30,
            runner=lambda current: run_once(
                current,
                live_gateway=None,
                environ={"RAAWAA_ENGINE_WORKER_LANE": "general"},
                worker_lane="general",
            ),
            max_parallel=1,
        )

        # Re-open the on-disk store to prove the terminal acknowledgement survived
        # the worker's completion, then replay the same enqueue key to prove
        # idempotency does not create a second task or attempt.
        reopened = TaskStore(db_path)
        saved = next((task for task in reopened.list_tasks(limit=10)
                      if task.get("id") == created.get("id")), None)
        if saved is None or saved.get("status") != "succeeded":
            raise RuntimeError("local task did not reach a durable succeeded state")
        if saved.get("execution_mode") != "dry_run" or saved.get("attempts") != 1:
            raise RuntimeError("local task did not complete exactly once in dry_run mode")
        try:
            result = json.loads(saved.get("result_json") or "{}")
        except (TypeError, ValueError) as exc:
            raise RuntimeError("local task result was not valid JSON") from exc
        if result.get("mode") != "dry_run":
            raise RuntimeError("local task result does not prove dry_run execution")

        replay = reopened.enqueue(
            "system_audit",
            "Scheduled local fake-executor smoke",
            {"kind": "synthetic_smoke", "network": False, "external_writes": False},
            idempotency_key=idempotency_key,
            max_attempts=1,
            execution_lane="general",
        )
        after_replay = next(task for task in reopened.list_tasks(limit=10)
                            if task.get("id") == created.get("id"))
        events = reopened.audit_events(limit=20)
        event_names = {event.get("event") for event in events if event.get("task_id") == created["id"]}
        if replay.get("id") != created.get("id") or after_replay.get("attempts") != 1:
            raise RuntimeError("idempotent replay created a duplicate task or attempt")
        if not {"task_claimed", "task_succeeded"}.issubset(event_names):
            raise RuntimeError("local task lifecycle audit events are incomplete")
        if batch.get("tasks_processed") != 1 or not batch.get("results") or batch["results"][0].get("status") != "succeeded":
            raise RuntimeError("bounded batch runner did not report one successful task")

        return {
            "status": "succeeded",
            "mode": "dry_run",
            "tasks_processed": batch["tasks_processed"],
            "durable_reopen_verified": True,
            "idempotent_replay_verified": True,
            "audit_events": ["task_claimed", "task_succeeded"],
            "network_calls": 0,
            "provider_calls": 0,
            "remote_writes": 0,
        }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", default=os.environ.get("GITHUB_RUN_ID", "local"))
    args = parser.parse_args()
    result = run_local_task_cycle(args.run_id)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
