import unittest
from unittest.mock import Mock

from engine.batch_runner import run_batch


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

    def test_rejects_unbounded_limits(self):
        for kwargs in ({"max_tasks": 0}, {"max_tasks": 21},
                       {"max_seconds": 0}, {"max_seconds": 301},
                       {"max_tasks": True}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                run_batch(object(), runner=Mock(), **kwargs)


if __name__ == "__main__":
    unittest.main()
