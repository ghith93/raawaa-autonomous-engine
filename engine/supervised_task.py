"""One real task, executed end to end through the existing task lifecycle.

The supervisor in :mod:`engine.process_supervisor` can run a bounded process and
say precisely what became of it. This module connects that answer to the queue
the engine already has, so the chain is real rather than a diagram:

``enqueue → claim (lease) → fence → supervise → classify → result → audit``

What it deliberately does not do is invent a second lifecycle. The task row,
the lease, the retry policy, the audit log and the idempotency key all belong to
:class:`engine.task_store.TaskStore`. This module adds exactly one thing: a task
type whose *handler* is a supervised process instead of a dry-run contract.

Why the switch defaults to off
------------------------------
Starting a child process is a capability, and a capability that switches itself
on is not a decision. ``RAAWAA_ENGINE_PROCESS_SUPERVISOR`` must read ``enabled``
for the handler to run at all; otherwise the task is blocked with a reason that
says so. The task row is not consumed, no dispatch is recorded and no process
exists. Turning the feature off is therefore always safe, in every environment,
without a code change.

Which operations a task may request
-----------------------------------
A task payload names an operation; it never names an executable, a path or an
argument. The worker-level allowlist below is *narrower* than the supervisor's
registry and contains only parameter-free, read-only, local probes. A payload
with any key other than ``operation`` is refused outright, so there is no
"parameters" channel to abuse — when a parameterised operation is needed, it
will be added here as a specific, reviewed entry, not as a general capability.

Task outcomes, and the one case that writes nothing
---------------------------------------------------
The supervisor's classification is mapped onto the task store's terminal states
deterministically, and ``ambiguous`` never becomes ``succeeded``:

===========================  ==================================================
classification               task status
===========================  ==================================================
``succeeded``                ``succeeded``, with the bounded outcome as result
``failed`` / ``timed_out``   ``failed`` — no automatic re-dispatch, because a
                             retry would replay the recorded failure under the
                             same dispatch identity rather than retry the work
``refused``                  ``blocked`` — nothing ran; the request is wrong
``ambiguous``                ``blocked`` — it ran and we cannot attribute it
``cancelled`` / lost lease   **nothing is written** — the task stays ``running``
                             and the store's stale-lease recovery owns it
===========================  ==================================================

That last row is the important one. Writing a terminal status after the lease is
gone would either fail (the store refuses it) or, worse, succeed and overwrite
the state of the worker that now owns the task. Returning without writing is the
only correct answer, and the existing ``recover_stale`` path already knows how to
requeue the task.

``execution_mode`` keeps its existing meaning
---------------------------------------------
``tasks.execution_mode`` describes *inference* — ``dry_run`` versus
``live_inference``. A supervised read-only probe performs no inference, so the
row keeps ``dry_run`` and the outcome records ``"execution":
"supervised_process:v1"`` for anyone who needs to know a real process ran.
Conflating the two would quietly widen what ``live_inference`` has always meant.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Mapping

from engine.lease_heartbeat import LeaseHeartbeatLost
from engine.process_supervisor import (
    LeaseWatchdog,
    ProcessSupervisor,
    SqliteDispatchLedger,
    SupervisorRefusal,
    default_registry,
)
from engine.task_store import TaskStore

__all__ = [
    "RESULT_OUTPUT_LIMIT",
    "SUPERVISED_TASK_TYPE",
    "SUPERVISOR_EXECUTION_LABEL",
    "SUPERVISOR_SWITCH",
    "WORKER_OPERATIONS",
    "complete_supervised_task",
    "supervisor_enabled",
]

#: The task type whose handler is a supervised external process.
SUPERVISED_TASK_TYPE = "supervised_probe"

#: Explicit opt-in. Anything other than ``enabled`` keeps the handler inert.
SUPERVISOR_SWITCH = "RAAWAA_ENGINE_PROCESS_SUPERVISOR"

SUPERVISOR_EXECUTION_LABEL = "supervised_process:v1"

#: Task payloads may request only these operations. Every entry is read-only,
#: local, deterministic and free; none of them takes a parameter, so the payload
#: has no channel through which a value could reach an argument vector.
WORKER_OPERATIONS = frozenset({"git_head_revision", "python_platform_probe"})

#: Captured output is already bounded by the supervisor; this bounds what is
#: written into the durable task row and echoed back by the API.
RESULT_OUTPUT_LIMIT = 4000

REPOSITORY_ROOT = Path(__file__).resolve().parent.parent


class SupervisedTaskError(ValueError):
    """A supervised task whose request cannot be honoured."""


def supervisor_enabled(environ: Mapping[str, str] | None = None) -> bool:
    source = os.environ if environ is None else environ
    return str(source.get(SUPERVISOR_SWITCH, "")).strip().lower() == "enabled"


def _bounded(text: str, limit: int = RESULT_OUTPUT_LIMIT) -> tuple[str, bool]:
    if len(text) <= limit:
        return text, False
    return text[:limit] + "\n[result truncated]", True


def _payload(task: dict[str, Any]) -> dict[str, Any]:
    raw = task.get("payload_json")
    if isinstance(raw, str) and raw.strip():
        try:
            decoded = json.loads(raw)
        except (TypeError, ValueError):
            raise SupervisedTaskError("task payload is not valid JSON") from None
        if isinstance(decoded, dict):
            return decoded
    raise SupervisedTaskError("task payload must be a JSON object")


def _resolve_operation(
    task: dict[str, Any], allowed_operations: frozenset[str]
) -> str:
    payload = _payload(task)
    if set(payload) != {"operation"}:
        # A "parameters" key is not rejected as a typo: it is the exact shape a
        # caller would use to try to widen what the child receives.
        raise SupervisedTaskError("payload must contain exactly one key: operation")
    operation = payload.get("operation")
    if not isinstance(operation, str) or not operation:
        raise SupervisedTaskError("payload operation must be a non-empty string")
    if operation not in allowed_operations:
        raise SupervisedTaskError(f"operation {operation!r} is not enabled for tasks")
    return operation


def _result_payload(outcome, operation: str) -> dict[str, Any]:
    stdout, stdout_cut = _bounded(outcome.stdout)
    stderr, stderr_cut = _bounded(outcome.stderr)
    return {
        "execution": SUPERVISOR_EXECUTION_LABEL,
        "operation": operation,
        "classification": outcome.classification,
        "observed_classification": outcome.observed_classification,
        "exit_code": outcome.exit_code,
        "signal_number": outcome.signal_number,
        "stdout": stdout,
        "stderr": stderr,
        "result_truncated": bool(stdout_cut or stderr_cut or outcome.output_truncated),
        "duration_seconds": round(outcome.duration_seconds, 3),
        "descendants_killed": outcome.descendants_killed,
        "dispatch_id": outcome.dispatch_id,
        "replayed": outcome.replayed,
        "fence": dict(outcome.fence),
        "supervisor_version": "process-supervisor:v1",
        "inference": "none",
        "provider_calls": 0,
        "external_writes": 0,
    }


def complete_supervised_task(
    store: TaskStore,
    task: dict[str, Any],
    *,
    environ: Mapping[str, str] | None = None,
    supervisor: ProcessSupervisor | None = None,
    watchdog: LeaseWatchdog | None = None,
    allowed_operations: frozenset[str] | None = None,
    lease_timeout_seconds: int = 900,
) -> dict[str, Any]:
    """Handle one claimed ``supervised_probe`` task and return a summary.

    ``supervisor``, ``watchdog`` and ``allowed_operations`` exist so tests can
    supply fault-injecting fixtures. They are constructor arguments of trusted
    code and are never read from a task payload; a payload cannot reach them.
    """
    task_id = task.get("id")
    lease_token = task.get("lease_token")
    if not isinstance(task_id, str) or not isinstance(lease_token, str):
        return {"id": task_id, "status": "blocked", "reason": "invalid_claim"}

    if not supervisor_enabled(environ):
        store.finish(
            task_id, "blocked",
            error=f"Process supervisor is disabled: set {SUPERVISOR_SWITCH}=enabled to run this task type.",
            lease_token=lease_token,
        )
        return {"id": task_id, "status": "blocked", "reason": "process_supervisor_disabled"}

    db_path = getattr(store, "db_path", None)
    if supervisor is None and db_path is None:
        # The dispatch ledger is the at-most-once guarantee; a remote store with
        # no local ledger file cannot provide it, so this fails closed.
        store.finish(
            task_id, "blocked",
            error="Supervised execution requires a local task store with a dispatch ledger.",
            lease_token=lease_token,
        )
        return {"id": task_id, "status": "blocked", "reason": "supervisor_requires_local_store"}

    operations = WORKER_OPERATIONS if allowed_operations is None else allowed_operations
    try:
        operation = _resolve_operation(task, operations)
    except SupervisedTaskError as exc:
        store.finish(task_id, "blocked", error=str(exc), lease_token=lease_token)
        return {"id": task_id, "status": "blocked", "reason": "invalid_supervised_task",
                "detail": str(exc)}

    if supervisor is None:
        ledger = SqliteDispatchLedger(db_path)
        supervisor = ProcessSupervisor(default_registry(REPOSITORY_ROOT), ledger)
    if watchdog is None:
        watchdog = LeaseWatchdog(
            store, task_id, lease_token,
            renew_interval_seconds=max(1.0, min(30.0, lease_timeout_seconds / 3.0)),
            lease_timeout_seconds=lease_timeout_seconds,
        )

    try:
        outcome = supervisor.run_operation(
            operation,
            {},
            task_id=task_id,
            lease_token=lease_token,
            # Ownership is confirmed on both sides of the child's lifetime. The
            # cancel check is the same question asked continuously, because a
            # 30-second operation must not outlive a lease that ended after 2.
            fence_before=watchdog.confirm,
            fence_after=watchdog.confirm,
            cancel_check=watchdog.cancel_requested,
        )
    except (SupervisorRefusal, LeaseHeartbeatLost):
        return _abandon(task_id, "supervisor_refused_before_execution")
    except Exception as exc:
        return _abandon(task_id, f"supervisor_error:{type(exc).__name__}")

    result = _result_payload(outcome, operation)

    if watchdog.lease_lost or outcome.classification == "cancelled":
        # Never write after the lease is gone. The task stays running and the
        # store's stale-lease recovery requeues it; a terminal write here would
        # either be rejected or race the worker that owns the task now.
        return {"id": task_id, "status": "lease_lost",
                "reason": "supervised_execution_cancelled", "recovery": "stale_lease"}

    classification = outcome.classification
    if classification == "succeeded":
        status = "succeeded"
        error = None
    elif classification in {"failed", "timed_out"}:
        status = "failed"
        error = outcome.reason or f"supervised_{classification}"
    else:  # refused, ambiguous
        status = "blocked"
        error = outcome.reason or f"supervised_{classification}"

    try:
        store.finish(task_id, status, result=result, error=error, lease_token=lease_token)
    except (ValueError, KeyError) as exc:
        # The lease moved between the fence and the write. Same rule as above:
        # report, do not retry, do not write a second time.
        return {"id": task_id, "status": "lease_lost",
                "reason": f"finish_refused:{type(exc).__name__}"}

    return {
        "id": task_id,
        "status": status,
        "classification": classification,
        "operation": operation,
        "dispatch_id": outcome.dispatch_id,
        "replayed": outcome.replayed,
        "reason": outcome.reason,
    }


def _abandon(task_id: str, reason: str) -> dict[str, Any]:
    """A task whose supervisor call raised: leave the row to stale recovery."""
    return {"id": task_id, "status": "lease_lost", "reason": reason,
            "recovery": "stale_lease"}
