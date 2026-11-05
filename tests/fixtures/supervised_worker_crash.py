"""A real, disposable process worker used to prove crash/restart recovery.

This is not imported by any test. It is *run* as a separate operating-system
process and killed with ``SIGKILL``, because the property under test is exactly
"what survives when the worker does not get to run its cleanup". A worker that
has been killed with SIGKILL runs no exception handler, no ``finally`` block and
no atexit hook: whatever state it left behind is the state the durable store
holds, and that is the state the next worker has to interpret.

Usage:
    python tests/fixtures/supervised_worker_crash.py <db_path> run
    python tests/fixtures/supervised_worker_crash.py <db_path> resume

``run`` claims the queued task and starts a slow supervised probe, then blocks
inside ``time.sleep`` so the parent test can kill it mid-flight.

``resume`` is the second worker: it recovers the expired lease and claims the
task again, which is the moment the engine has to decide between replaying the
external operation and failing closed.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from engine.process_supervisor import (  # noqa: E402
    OperationRegistry,
    OperationSpec,
    ProcessSupervisor,
    SqliteDispatchLedger,
)
from engine.supervised_task import (  # noqa: E402
    SUPERVISED_TASK_TYPE,
    SUPERVISOR_SWITCH,
    complete_supervised_task,
)
from engine.task_store import TaskStore  # noqa: E402

OPERATION = "slow_probe"
ENVIRON = {SUPERVISOR_SWITCH: "enabled"}

#: Deliberately long: the parent test kills this worker long before the child
#: could finish on its own, and the child must still be running at that instant
#: for the test to mean anything.
REGISTRY = OperationRegistry([
    OperationSpec(
        name=OPERATION,
        executable=sys.executable,
        arguments=("-c", "import time;time.sleep(120)"),
        wall_seconds=90.0,
        grace_seconds=0.3,
    )
])


def _emit(payload: dict) -> None:
    print(json.dumps(payload, sort_keys=True), flush=True)


def main(argv: list[str]) -> int:
    if len(argv) != 3 or argv[2] not in {"run", "resume"}:
        _emit({"error": "usage: <db_path> run|resume"})
        return 2
    db_path, mode = argv[1], argv[2]
    store = TaskStore(db_path)

    if mode == "resume":
        recovery = store.recover_stale()

    task = store.claim_next(execution_mode="dry_run")
    if task is None:
        _emit({"phase": "idle"})
        return 0
    _emit({"phase": "claimed", "task_id": task["id"], "attempt": task["attempts"]})

    supervisor = ProcessSupervisor(REGISTRY, SqliteDispatchLedger(db_path),
                                   drain_grace_seconds=1.0)
    summary = complete_supervised_task(
        store, task, environ=ENVIRON, supervisor=supervisor,
        allowed_operations=frozenset({OPERATION}),
    )
    summary = dict(summary)
    if mode == "resume":
        summary["recovery"] = recovery
    _emit({"phase": "finished", **summary})
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
