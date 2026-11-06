"""Remote Staging proof for generic/specialist task-lane isolation.

Requires a Staging-only API token and an ephemeral researcher lane token.
Creates isolated dry-run tasks in both lanes, proves bidirectional exclusion, verifies wrong-lane
heartbeat/retry/finish are rejected, and completes the specialist task correctly.
"""
from __future__ import annotations

import json
import os
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from engine.remote_task_store import RemoteTaskStore, RemoteTaskStoreError

SMOKE_TASK_IDEMPOTENCY_KEY = "groq-free-inference-staging-specialist-smoke-2026-11-06-v8"
SMOKE_TASK_TITLE = "Single public Groq Free-tier inference connectivity check"
SMOKE_TASK_PAYLOAD = {
    "data_classification": "public",
    "topic": "Confirm one bounded free-tier model inference and report the selected model.",
    "questions": ["Return a concise confirmation that the smoke test completed."],
    "source_urls": [],
}
SPECIALIST_PROOF_PRIORITY = 100


def _active_task_summary(tasks: list[dict]) -> list[dict]:
    """Emit bounded operational metadata only; omit task titles, keys and payloads."""
    fields = ("id", "task_type", "status", "execution_lane", "created_at", "updated_at")
    return [{field: task.get(field) for field in fields} for task in tasks[:10]]


def _queue_state(tasks: list[dict]) -> tuple[list[dict], list[dict]]:
    """Return pending rows that block a safe claim proof and running rows to leave untouched."""
    pending = [task for task in tasks if task.get("status") == "pending"]
    running = [task for task in tasks if task.get("status") == "running"]
    return pending, running


def _reserved_smoke_pending(tasks: list[dict]) -> list[dict]:
    """Recognize only the exact, untouched researcher smoke task reserved for the next workflow."""
    reserved = []
    for task in tasks:
        if not (
            isinstance(task, dict)
            and task.get("task_type") == "research_brief"
            and task.get("title") == SMOKE_TASK_TITLE
            and task.get("idempotency_key") == SMOKE_TASK_IDEMPOTENCY_KEY
            and task.get("status") == "pending"
            and task.get("attempts") == 0
            and task.get("max_attempts") == 1
            and task.get("execution_mode") == "not_started"
            and task.get("priority") == 50
            and task.get("execution_lane") == "specialist:researcher"
            and task.get("dependencies_json") in ("[]", None)
        ):
            continue
        try:
            if json.loads(task.get("payload_json", "")) == SMOKE_TASK_PAYLOAD:
                reserved.append(task)
        except (TypeError, ValueError):
            continue
    return reserved


def main() -> None:
    if os.environ.get("RAAWAA_ENGINE_LIVE_INFERENCE", "disabled").lower() not in {"", "0", "false", "disabled", "off"}:
        raise SystemExit("Refusing lane proof unless live inference is disabled")
    if not os.environ.get("RAAWAA_ENGINE_LANE_TOKEN"):
        raise SystemExit("Missing ephemeral researcher lane token")
    store = RemoteTaskStore()
    general_before = store.list_tasks(100)
    researcher_before = store.list_tasks(100, worker_lane="specialist:researcher")
    if len(general_before) >= 100 or len(researcher_before) >= 100:
        raise SystemExit("Refusing lane proof because bounded queue readback may be incomplete")
    queue_rows = general_before + researcher_before
    pending, running = _queue_state(queue_rows)
    reserved_smoke = _reserved_smoke_pending(researcher_before)
    reserved_ids = {task["id"] for task in reserved_smoke}
    unrelated_pending = [task for task in pending if task.get("id") not in reserved_ids]
    if len(reserved_smoke) > 1 or unrelated_pending:
        print(json.dumps({
            "status": "blocked_unrelated_pending_staging_tasks",
            "pending_task_metadata": _active_task_summary(unrelated_pending),
            "reserved_smoke_task_left_untouched": _active_task_summary(reserved_smoke),
            "running_tasks_left_untouched": _active_task_summary(running),
        }, sort_keys=True), flush=True)
        raise SystemExit("Refusing to claim unrelated pending Staging work")
    if reserved_smoke:
        print(json.dumps({
            "status": "reserved_smoke_task_preserved_for_one_shot_inference",
            "reserved_smoke_task_metadata": _active_task_summary(reserved_smoke),
        }, sort_keys=True), flush=True)
    if running:
        # Running leases are not candidates for claim_next. Leave them untouched;
        # with no unrelated pending rows, the proof's own dependency-free tasks
        # are the only claimable rows in each tested lane.
        print(json.dumps({
            "status": "existing_running_tasks_left_untouched",
            "running_task_metadata": _active_task_summary(running),
        }, sort_keys=True), flush=True)
    suffix = uuid.uuid4().hex
    general = store.enqueue(
        "research_brief", "Staging general lane isolation proof",
        {"topic": "deterministic lane isolation test"},
        idempotency_key=f"staging-lane-general:{suffix}", priority=100,
    )
    general_sentinel = store.enqueue(
        "research_brief", "Staging general lane sentinel",
        {"topic": "deterministic lane isolation test"},
        idempotency_key=f"staging-lane-general-sentinel:{suffix}", priority=90,
    )
    general_sentinel_last = store.enqueue(
        "research_brief", "Staging general lane final sentinel",
        {"topic": "deterministic lane isolation test"},
        idempotency_key=f"staging-lane-general-final:{suffix}", priority=80,
    )
    specialist = store.enqueue(
        "research_brief", "Staging researcher lane isolation proof",
        {"topic": "deterministic lane isolation test"},
        idempotency_key=f"staging-lane-researcher:{suffix}",
        execution_lane="specialist:researcher",
        priority=SPECIALIST_PROOF_PRIORITY,
    )

    general_claim = store.claim_next(execution_mode="dry_run", worker_lane="general")
    if not general_claim or general_claim.get("id") != general.get("id"):
        raise SystemExit("General consumer claimed the wrong task")
    store.finish(general_claim["id"], "succeeded", {"mode": "dry_run", "proof": "general"},
                 lease_token=general_claim["lease_token"], worker_lane="general")

    # While a specialist task is pending, the general consumer must still
    # select only its own lane.
    general_sentinel_claim = store.claim_next(execution_mode="dry_run", worker_lane="general")
    if not general_sentinel_claim or general_sentinel_claim.get("id") != general_sentinel.get("id"):
        raise SystemExit("General consumer crossed into the specialist lane")
    store.finish(general_sentinel_claim["id"], "succeeded",
                 {"mode": "dry_run", "proof": "general-sentinel"},
                 lease_token=general_sentinel_claim["lease_token"], worker_lane="general")

    specialist_claim = store.claim_next(
        execution_mode="dry_run", worker_lane="specialist:researcher"
    )
    if not specialist_claim or specialist_claim.get("id") != specialist.get("id"):
        raise SystemExit("Researcher consumer did not claim its own task")

    for action, call in (
        ("heartbeat", lambda: store.heartbeat(
            specialist_claim["id"], specialist_claim["lease_token"], worker_lane="general")),
        ("retry", lambda: store.retry_or_fail(
            specialist_claim["id"], "wrong-lane proof", specialist_claim["lease_token"],
            worker_lane="general")),
        ("finish", lambda: store.finish(
            specialist_claim["id"], "succeeded", {"mode": "dry_run"},
            lease_token=specialist_claim["lease_token"], worker_lane="general")),
    ):
        try:
            call()
        except RemoteTaskStoreError as exc:
            if exc.status_code != 403:
                raise SystemExit(f"Wrong-lane {action} returned unexpected HTTP status")
        else:
            raise SystemExit(f"Wrong-lane {action} unexpectedly succeeded")

    store.finish(
        specialist_claim["id"], "succeeded", {"mode": "dry_run", "proof": "researcher"},
        lease_token=specialist_claim["lease_token"], worker_lane="specialist:researcher",
    )

    # The researcher must not claim the final pending general task.
    general_sentinel_last_claim = store.claim_next(execution_mode="dry_run", worker_lane="general")
    if not general_sentinel_last_claim or general_sentinel_last_claim.get("id") != general_sentinel_last.get("id"):
        raise SystemExit("General consumer could not claim its own final pending task")
    store.finish(general_sentinel_last_claim["id"], "succeeded",
                 {"mode": "dry_run", "proof": "general-final"},
                 lease_token=general_sentinel_last_claim["lease_token"], worker_lane="general")
    if store.claim_next(execution_mode="dry_run", worker_lane="general") is not None:
        raise SystemExit("General consumer claimed an unexpected task")

    general_tasks = {task["id"]: task for task in store.list_tasks(100)}
    researcher_tasks = {
        task["id"]: task for task in store.list_tasks(100, worker_lane="specialist:researcher")
    }
    for task_id in (general["id"], general_sentinel["id"], general_sentinel_last["id"]):
        if general_tasks[task_id]["status"] != "succeeded":
            raise SystemExit("General task did not complete")
    if researcher_tasks[specialist["id"]]["status"] != "succeeded":
        raise SystemExit("Specialist task did not complete")
    if researcher_tasks[specialist["id"]].get("execution_lane") != "specialist:researcher":
        raise SystemExit("Specialist lane was not persisted")
    if reserved_smoke:
        reserved_id = reserved_smoke[0]["id"]
        preserved = researcher_tasks.get(reserved_id)
        if (not preserved or preserved.get("status") != "pending"
                or preserved.get("idempotency_key") != SMOKE_TASK_IDEMPOTENCY_KEY
                or preserved.get("attempts") != 0):
            raise SystemExit("Lane proof modified or claimed the reserved Groq smoke task")
    print("PASS: general/specialist isolation, protected smoke-task preservation, authenticated claim, wrong-lane lifecycle rejection, and readback")


if __name__ == "__main__":
    main()
