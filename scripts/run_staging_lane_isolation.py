"""Remote Staging proof for generic/specialist task-lane isolation.

Requires a Staging-only API token and an ephemeral researcher lane token.
Creates isolated dry-run tasks in both lanes, proves bidirectional exclusion, verifies wrong-lane
heartbeat/retry/finish are rejected, and completes the specialist task correctly.
"""
from __future__ import annotations

import os
import uuid

from engine.remote_task_store import RemoteTaskStore, RemoteTaskStoreError


def main() -> None:
    if os.environ.get("RAAWAA_ENGINE_LIVE_INFERENCE", "disabled").lower() not in {"", "0", "false", "disabled", "off"}:
        raise SystemExit("Refusing lane proof unless live inference is disabled")
    general_token = os.environ.get("RAAWAA_ENGINE_GENERAL_LANE_TOKEN", "")
    researcher_token = os.environ.get("RAAWAA_ENGINE_LANE_TOKEN", "")
    if len(general_token) < 32:
        raise SystemExit("Missing ephemeral general lane token")
    if len(researcher_token) < 32:
        raise SystemExit("Missing ephemeral researcher lane token")
    general_store = RemoteTaskStore(lane_token=general_token, worker_lane="general")
    researcher_store = RemoteTaskStore(
        lane_token=researcher_token, worker_lane="specialist:researcher"
    )
    general_before = general_store.list_tasks(100)
    researcher_before = researcher_store.list_tasks(100)
    if len(general_before) >= 100 or len(researcher_before) >= 100:
        raise SystemExit("Refusing lane proof because bounded queue readback may be incomplete")
    active = [task for task in general_before + researcher_before
              if task.get("status") in {"pending", "running"}]
    if active:
        raise SystemExit("Refusing to claim unrelated Staging work; relevant queue lanes are not idle")
    suffix = uuid.uuid4().hex
    general = general_store.enqueue(
        "research_brief", "Staging general lane isolation proof",
        {"topic": "deterministic lane isolation test"},
        idempotency_key=f"staging-lane-general:{suffix}", priority=100,
    )
    general_sentinel = general_store.enqueue(
        "research_brief", "Staging general lane sentinel",
        {"topic": "deterministic lane isolation test"},
        idempotency_key=f"staging-lane-general-sentinel:{suffix}", priority=90,
    )
    specialist = researcher_store.enqueue(
        "research_brief", "Staging researcher lane isolation proof",
        {"topic": "deterministic lane isolation test"},
        idempotency_key=f"staging-lane-researcher:{suffix}",
        execution_lane="specialist:researcher",
    )

    general_claim = general_store.claim_next(execution_mode="dry_run", worker_lane="general")
    if not general_claim or general_claim.get("id") != general.get("id"):
        raise SystemExit("General consumer claimed the wrong task")
    general_store.finish(general_claim["id"], "succeeded", {"mode": "dry_run", "proof": "general"},
                 lease_token=general_claim["lease_token"], worker_lane="general")

    # While a specialist task is pending, the general consumer must still
    # select only its own lane.
    general_sentinel_claim = general_store.claim_next(execution_mode="dry_run", worker_lane="general")
    if not general_sentinel_claim or general_sentinel_claim.get("id") != general_sentinel.get("id"):
        raise SystemExit("General consumer crossed into the specialist lane")
    general_store.finish(general_sentinel_claim["id"], "succeeded",
                 {"mode": "dry_run", "proof": "general-sentinel"},
                 lease_token=general_sentinel_claim["lease_token"], worker_lane="general")

    if general_store.claim_next(execution_mode="dry_run", worker_lane="general") is not None:
        raise SystemExit("General consumer claimed the pending researcher task")

    # Add a higher-priority general task while the researcher task is pending.
    # The researcher must still claim its own lower-priority task.
    general_sentinel_last = general_store.enqueue(
        "research_brief", "Staging general lane final sentinel",
        {"topic": "deterministic lane isolation test"},
        idempotency_key=f"staging-lane-general-final:{suffix}", priority=80,
    )

    specialist_claim = researcher_store.claim_next(
        execution_mode="dry_run", worker_lane="specialist:researcher"
    )
    if not specialist_claim or specialist_claim.get("id") != specialist.get("id"):
        raise SystemExit("Researcher consumer did not claim its own task")

    for action, call in (
        ("heartbeat", lambda: general_store.heartbeat(
            specialist_claim["id"], specialist_claim["lease_token"], worker_lane="general")),
        ("retry", lambda: general_store.retry_or_fail(
            specialist_claim["id"], "wrong-lane proof", specialist_claim["lease_token"],
            worker_lane="general")),
        ("finish", lambda: general_store.finish(
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

    researcher_store.finish(
        specialist_claim["id"], "succeeded", {"mode": "dry_run", "proof": "researcher"},
        lease_token=specialist_claim["lease_token"], worker_lane="specialist:researcher",
    )

    # The researcher did not consume the higher-priority general task; the
    # general consumer can still claim it after the researcher completes.
    general_final_claim = general_store.claim_next(execution_mode="dry_run", worker_lane="general")
    if not general_final_claim or general_final_claim.get("id") != general_sentinel_last.get("id"):
        raise SystemExit("Researcher consumer claimed or blocked a general-lane task")
    general_store.finish(
        general_final_claim["id"], "succeeded", {"mode": "dry_run", "proof": "general-final"},
        lease_token=general_final_claim["lease_token"], worker_lane="general",
    )
    if general_store.claim_next(execution_mode="dry_run", worker_lane="general") is not None:
        raise SystemExit("General consumer claimed an unexpected task after both lanes completed")

    general_tasks = {task["id"]: task for task in general_store.list_tasks(100)}
    researcher_tasks = {
        task["id"]: task for task in researcher_store.list_tasks(100)
    }
    for task_id in (general["id"], general_sentinel["id"], general_sentinel_last["id"]):
        if general_tasks[task_id]["status"] != "succeeded":
            raise SystemExit("General task did not complete")
    if researcher_tasks[specialist["id"]]["status"] != "succeeded":
        raise SystemExit("Specialist task did not complete")
    if researcher_tasks[specialist["id"]].get("execution_lane") != "specialist:researcher":
        raise SystemExit("Specialist lane was not persisted")
    print("PASS: general/specialist isolation, authenticated claim, wrong-lane lifecycle rejection, and readback")


if __name__ == "__main__":
    main()
