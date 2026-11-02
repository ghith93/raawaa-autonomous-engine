"""Bounded batch execution for scheduled or manually triggered worker runs."""
from __future__ import annotations

import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from collections.abc import Callable
from typing import Any

from engine.task_store import TaskStore
from engine.worker import run_once


def run_batch(
    store: TaskStore,
    max_tasks: int = 5,
    max_seconds: int = 60,
    runner: Callable[[TaskStore], dict[str, Any] | None] | None = None,
    clock: Callable[[], float] = time.monotonic,
    max_parallel: int = 1,
) -> dict[str, Any]:
    """Run at most max_tasks tasks and admit no new task after max_seconds.

    A synchronous task already in progress cannot be interrupted; max_seconds is
    therefore an admission budget, not a hard execution timeout.
    """
    if isinstance(max_tasks, bool) or not isinstance(max_tasks, int) or not 1 <= max_tasks <= 20:
        raise ValueError("max_tasks must be an integer between 1 and 20")
    if isinstance(max_seconds, bool) or not isinstance(max_seconds, int) or not 1 <= max_seconds <= 300:
        raise ValueError("max_seconds must be an integer between 1 and 300")
    if isinstance(max_parallel, bool) or not isinstance(max_parallel, int) or not 1 <= max_parallel <= 5:
        raise ValueError("max_parallel must be an integer between 1 and 5")

    execute = runner or run_once
    started = clock()
    results: list[dict[str, Any]] = []
    stop_reason = "task_limit"

    if max_parallel == 1:
        # Preserve the original deterministic single-worker behavior by default.
        while len(results) < max_tasks:
            if clock() - started >= max_seconds:
                stop_reason = "time_limit"
                break
            result = execute(store)
            if result is None or result.get("status") == "idle":
                stop_reason = "queue_idle"
                if result is not None:
                    results.append(result)
                break
            results.append(result)
    else:
        active = set()
        queue_idle = False
        with ThreadPoolExecutor(max_workers=max_parallel, thread_name_prefix="raawaa-agent") as pool:
            while len(results) + len(active) < max_tasks:
                while (
                    not queue_idle
                    and len(active) < max_parallel
                    and len(results) + len(active) < max_tasks
                    and clock() - started < max_seconds
                ):
                    active.add(pool.submit(execute, store))

                if not active:
                    stop_reason = "queue_idle" if queue_idle else "time_limit"
                    break

                completed, active = wait(active, return_when=FIRST_COMPLETED)
                for future in completed:
                    result = future.result()
                    if result is None:
                        queue_idle = True
                    else:
                        results.append(result)
                        if result.get("status") == "idle":
                            queue_idle = True

                if queue_idle:
                    # Let already-claimed tasks finish; never admit more work after idle.
                    while active:
                        completed, active = wait(active, return_when=FIRST_COMPLETED)
                        for future in completed:
                            result = future.result()
                            if result is not None:
                                results.append(result)
                    stop_reason = "queue_idle"
                    break

                if clock() - started >= max_seconds:
                    while active:
                        completed, active = wait(active, return_when=FIRST_COMPLETED)
                        for future in completed:
                            result = future.result()
                            if result is not None:
                                results.append(result)
                    stop_reason = "time_limit"
                    break
            else:
                stop_reason = "task_limit"

    elapsed = max(0.0, clock() - started)
    return {
        "status": "completed",
        "stop_reason": stop_reason,
        "tasks_processed": sum(item.get("status") != "idle" for item in results),
        "elapsed_seconds": round(elapsed, 3),
        "results": results,
    }
