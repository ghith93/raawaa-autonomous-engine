"""Run one exact, read-only supervised task on the Staging queue from Cron only.

This is deliberately not the general queue worker: it enqueues and claims only
its own idempotent supervised_probe task, so an unrelated Staging queue row is
never admitted or recovered by this probe.
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Callable, Mapping

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from engine.remote_task_store import RemoteTaskStore
from engine.supervised_task import complete_supervised_task

STAGING_API_URL = "https://raawaa-autonomous-engine-staging.ghith-shikhkhaled.workers.dev"
TASK_TYPE = "supervised_probe"
OPERATION = "python_platform_probe"
TITLE = "Scheduled Staging read-only process probe"
IDEMPOTENCY_PREFIX = "staging-cron-supervised-probe:"
LEASE_TIMEOUT_SECONDS = 120


class ScheduledProbeError(RuntimeError):
    """The scheduled Staging probe failed a fail-closed precondition."""


def _payload(task: Mapping[str, Any]) -> dict[str, Any]:
    raw = task.get("payload_json")
    if not isinstance(raw, str):
        raise ScheduledProbeError("Staging queue returned an invalid probe payload")
    try:
        payload = json.loads(raw)
    except (TypeError, ValueError):
        raise ScheduledProbeError("Staging queue returned invalid probe JSON") from None
    if payload != {"operation": OPERATION}:
        raise ScheduledProbeError("Staging queue returned an unexpected probe operation")
    return payload


def validate_environment(environ: Mapping[str, str]) -> tuple[str, str]:
    """Require the reviewed Staging target and a real main-branch Cron event."""
    if environ.get("GITHUB_EVENT_NAME") != "schedule":
        raise ScheduledProbeError("This probe runs only from a GitHub schedule event")
    if environ.get("GITHUB_REF") != "refs/heads/main":
        raise ScheduledProbeError("This probe runs only from reviewed main")
    run_id = environ.get("GITHUB_RUN_ID", "")
    if not re.fullmatch(r"[0-9]{1,20}", run_id):
        raise ScheduledProbeError("Invalid scheduled workflow run identifier")
    api_url = environ.get("RAAWAA_ENGINE_API_URL", "").rstrip("/")
    if api_url != STAGING_API_URL:
        raise ScheduledProbeError("Refusing any API target other than the isolated Staging Worker")
    if environ.get("RAAWAA_ENGINE_PROCESS_SUPERVISOR", "").strip().lower() != "enabled":
        raise ScheduledProbeError("The process supervisor switch must be explicitly enabled for this probe")
    if environ.get("RAAWAA_ENGINE_LIVE_INFERENCE", "").strip().lower() != "disabled":
        raise ScheduledProbeError("Live inference must be explicitly disabled for this probe")
    if any(environ.get(name, "") for name in ("GROQ_API_KEY", "GEMINI_API_KEY", "OPENAI_API_KEY")):
        raise ScheduledProbeError("Provider credentials must not be present in this process")
    token = environ.get("RAAWAA_ENGINE_API_TOKEN", "")
    if len(token) < 32:
        raise ScheduledProbeError("A temporary Staging-only API token is required")
    return token, IDEMPOTENCY_PREFIX + run_id


def run_scheduled_probe(
    environ: Mapping[str, str] | None = None,
    *,
    store_factory: Callable[..., Any] = RemoteTaskStore,
    processor: Callable[..., dict[str, Any]] = complete_supervised_task,
) -> dict[str, Any]:
    env = dict(os.environ if environ is None else environ)
    token, idempotency_key = validate_environment(env)
    store = store_factory(
        STAGING_API_URL, token, timeout=30, reject_redirects=True,
    )
    task = store.enqueue(
        TASK_TYPE,
        TITLE,
        {"operation": OPERATION},
        idempotency_key=idempotency_key,
        max_attempts=1,
        priority=100,
    )
    if not isinstance(task, dict):
        raise ScheduledProbeError("Staging queue omitted the enqueued task")
    task_id = task.get("id")
    if not isinstance(task_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", task_id):
        raise ScheduledProbeError("Staging queue returned an invalid task id")
    if (task.get("task_type") != TASK_TYPE or task.get("title") != TITLE
            or task.get("idempotency_key") != idempotency_key):
        raise ScheduledProbeError("Staging queue returned a task outside the reviewed probe envelope")
    _payload(task)

    # A workflow rerun with the same run_id must not launch a second process.
    if task.get("status") == "succeeded":
        return {
            "task_id": task_id,
            "status": "succeeded",
            "classification": "already_settled",
            "replayed": True,
            "provider_calls": 0,
            "external_writes": 0,
        }
    if task.get("status") != "pending":
        raise ScheduledProbeError("The idempotent Staging probe task is not pending")

    claimed = store.claim_supervised_probe(task_id)
    if claimed is None:
        raise ScheduledProbeError("The exact Staging probe task was not claimable")
    if (claimed.get("id") != task_id or claimed.get("task_type") != TASK_TYPE
            or claimed.get("status") != "running" or claimed.get("execution_mode") != "dry_run"):
        raise ScheduledProbeError("The exact Staging probe claim failed its task/mode checks")
    _payload(claimed)

    result = processor(
        store, claimed, environ=env, lease_timeout_seconds=LEASE_TIMEOUT_SECONDS,
    )
    if (not isinstance(result, dict) or result.get("id") != task_id
            or result.get("status") != "succeeded"
            or result.get("classification") != "succeeded"
            or result.get("operation") != OPERATION
            or not isinstance(result.get("dispatch_id"), str)
            or result.get("replayed") is not False):
        raise ScheduledProbeError("The supervised Staging task did not produce a fresh successful read-only result")
    return {
        "task_id": task_id,
        "status": "succeeded",
        "classification": "succeeded",
        "operation": OPERATION,
        "dispatch_id": result["dispatch_id"],
        "replayed": False,
        "provider_calls": 0,
        "external_writes": 0,
    }


def main() -> int:
    try:
        result = run_scheduled_probe()
    except Exception as exc:  # Fail closed; never print exception messages that could contain request data.
        print(f"Scheduled Staging process probe failed: {type(exc).__name__}", file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
