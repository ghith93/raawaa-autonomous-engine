import unittest
from unittest.mock import patch

from engine.remote_task_store import RemoteTaskStore
from engine.task_invocation_store import TaskInvocationStore


class TaskInvocationStoreTests(unittest.TestCase):
    def setUp(self):
        self.remote = RemoteTaskStore("https://engine.example", "test-token", lane_token="general-lane-token-0123456789abcdef0123456789")
        self.store = TaskInvocationStore(self.remote)

    def test_prepare_returns_durable_route_independent_identity(self):
        expected = {
            "invocation": {
                "invocation_id": "00000000-0000-4000-8000-000000000001",
                "request_id": "00000000-0000-4000-8000-000000000002",
                "step_key": "research:primary",
                "status": "prepared",
            }
        }
        with patch.object(self.remote, "_request", return_value=expected) as call:
            result = self.store.prepare("task-1", "lease-1", "research:primary", "research")
        self.assertEqual(result["request_id"], expected["invocation"]["request_id"])
        self.assertEqual(call.call_args.args[0], "/v1/tasks/task-1/invocations")
        self.assertEqual(call.call_args.args[2]["action"], "prepare")
        self.assertEqual(call.call_args.args[2]["worker_lane"], "general")
        self.assertEqual(call.call_args.kwargs["worker_lane"], "general")

    def test_invalid_prepare_arguments_are_rejected_before_network(self):
        with patch.object(self.remote, "_request") as call:
            with self.assertRaises(ValueError):
                self.store.prepare("task-1", "", "step", "research")
        call.assert_not_called()

    def test_start_reject_settle_and_ambiguous_use_lifecycle_actions(self):
        with patch.object(self.remote, "_request", return_value={"ok": True}) as call:
            self.store.start("task-1", "lease-1", "invocation-1", "groq:model-a")
            self.assertEqual(call.call_args.args[2]["action"], "start")
            self.store.reject("task-1", "lease-1", "invocation-1", "groq:model-a", "http_429")
            self.assertEqual(call.call_args.args[2]["action"], "reject")
            self.store.mark_ambiguous("task-1", "lease-1", "invocation-1")
            self.assertEqual(call.call_args.args[2]["action"], "ambiguous")
            self.store.settle("task-1", "lease-1", "invocation-1")
            self.assertEqual(call.call_args.args[2]["action"], "settle")
        self.assertEqual(call.call_count, 4)

    def test_missing_invocation_payload_is_rejected(self):
        with patch.object(self.remote, "_request", return_value={}) as call:
            with self.assertRaisesRegex(Exception, "omitted"):
                self.store.prepare("task-1", "lease-1", "step", "research")
        call.assert_called_once()


if __name__ == "__main__":
    unittest.main()
