import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import Mock

from engine.batch_runner import run_batch
from engine.task_store import TaskStore


class BatchRunnerTests(unittest.TestCase):
    def test_stops_when_queue_is_idle(self):
        runner = Mock(side_effect=[{"status": "succeeded"}, None])
        result = run_batch(object(), max_tasks=5, runner=runner)
        self.assertEqual(result["stop_reason"], "queue_idle")
        self.assertEqual(result["tasks_processed"], 1)
        self.assertEqual(runner.call_count, 2)

    def test_respects_task_limit(self):
        runner = Mock(return_value={"status": "succeeded"})
        result = run_batch(object(), max_tasks=3, runner=runner)
        self.assertEqual(result["stop_reason"], "task_limit")
        self.assertEqual(result["tasks_processed"], 3)
        self.assertEqual(runner.call_count, 3)

    def test_does_not_start_new_task_after_time_budget(self):
        ticks = iter([0.0, 0.0, 1.0, 1.0])
        runner = Mock(return_value={"status": "succeeded"})
        result = run_batch(object(), max_tasks=5, max_seconds=1,
                           runner=runner, clock=lambda: next(ticks))
        self.assertEqual(result["stop_reason"], "time_limit")
        self.assertEqual(runner.call_count, 1)

    def test_runs_bounded_tasks_in_parallel(self):
        lock = threading.Lock()
        active = 0
        maximum_active = 0
        pair_barrier = threading.Barrier(2)

        def runner(_store):
            nonlocal active, maximum_active
            with lock:
                active += 1
                maximum_active = max(maximum_active, active)
            try:
                pair_barrier.wait(timeout=2)
                time.sleep(0.005)
                return {"status": "succeeded"}
            finally:
                with lock:
                    active -= 1

        result = run_batch(object(), max_tasks=4, max_parallel=2, runner=runner)
        self.assertEqual(result["tasks_processed"], 4)
        self.assertEqual(result["stop_reason"], "task_limit")
        self.assertEqual(maximum_active, 2)

    def test_parallel_workers_claim_distinct_real_sqlite_tasks(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = TaskStore(Path(tmp) / "tasks.db")
            for index in range(5):
                store.enqueue(
                    "research_brief",
                    f"Parallel task {index}",
                    {"topic": f"topic-{index}"},
                    idempotency_key=f"parallel-{index}",
                )

            result = run_batch(store, max_tasks=5, max_parallel=3)

            tasks = store.list_tasks()
            self.assertEqual(result["tasks_processed"], 5)
            self.assertEqual(result["stop_reason"], "task_limit")
            self.assertEqual(len(tasks), 5)
            self.assertTrue(all(task["status"] == "succeeded" for task in tasks))
            self.assertTrue(all(task["attempts"] == 1 for task in tasks))
            self.assertEqual(len({task["id"] for task in tasks}), 5)

    def test_rejects_unbounded_limits(self):
        for kwargs in ({"max_tasks": 0}, {"max_tasks": 21},
                       {"max_seconds": 0}, {"max_seconds": 301},
                       {"max_tasks": True}, {"max_parallel": 0},
                       {"max_parallel": 6}, {"max_parallel": True}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                run_batch(object(), runner=Mock(), **kwargs)


if __name__ == "__main__":
    unittest.main()
