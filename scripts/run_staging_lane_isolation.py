"""Remote Staging proof for generic/specialist task-lane isolation.

Requires a Staging-only API token and an ephemeral researcher lane token.
Creates two harmless dry-run tasks, proves generic exclusion, verifies wrong-lane
heartbeat/retry/finish are rejected, and completes the specialist task correctly.
"""
from __future__ import annotations

import os
import uuid

from engine.remote_task_store import RemoteTaskStore, RemoteTaskStoreError


def main() -> None:
    if os.environ.get("RAAWAA_ENGINE_LIVE_INFERENCE", "disabled").lower() not in {"", "0", "false", "disabled", "off"}:
        raise SystemExit("Refusing lane proof unless live inference is disabled")
    if not os.environ.get("RAAWAA_ENGINE_LANE_TOKEN"):
        raise SystemExit("Missing ephemeral researcher lane token")
    store = RemoteTaskStore()
    suffix = uuid.uuid4().hex
    general = store.enqueue(
        "research_brief", "Staging general lane isolation proof",
        {"topic": "deterministic lane isolation test"},
        idempotency_key=f"staging-lane-general:{suffix}",
    )
    specialist = store.enqueue(
        "research_brief", "Staging researcher lane isolation proof",
        {"topic": "deterministic lane isolation test"},
        idempotency_key=f"staging-lane-researcher:{suffix}",
        execution_lane="specialist:researcher",
    )

    general_claim = store.claim_next(execution_mode="dry_run", worker_lane="general")
    if not general_claim or general_claim.get("id") != general.get("id"):
        raise SystemExit("General consumer claimed the wrong task")
    store.finish(general_claim["id"], "succeeded", {"mode": "dry_run", "proof": "general"},
                 lease_token=general_claim["lease_token"], worker_lane="general")

    if store.claim_next(execution_mode="dry_run", worker_lane="general") is not None:
        raise SystemExit("General consumer claimed a specialist-lane task")

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
    tasks = {task["id"]: task for task in store.list_tasks(100)}
    if tasks[general["id"]]["status"] != "succeeded":
        raise SystemExit("General task did not complete")
    if tasks[specialist["id"]]["status"] != "succeeded":
        raise SystemExit("Specialist task did not complete")
    if tasks[specialist["id"]].get("execution_lane") != "specialist:researcher":
        raise SystemExit("Specialist lane was not persisted")
    print("PASS: general/specialist isolation, authenticated claim, wrong-lane lifecycle rejection, and readback")


if __name__ == "__main__":
    main()
